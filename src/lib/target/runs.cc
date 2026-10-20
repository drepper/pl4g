// Arithmetic over a whole run of elements.
#include "target/runs.hh"

#include <algorithm>


namespace pl4g::target {

  namespace {

    using ir::BinOp;

    // Which ordinary operation each of these is built from.
    const mc::Op* plain(BinOp op)
    {
      switch (op) {
      case BinOp::add: case BinOp::sat_add: return mc::ops::plus;
      case BinOp::sub: case BinOp::sat_sub: return mc::ops::minus;
      default: throw Unsupported("arithmetic over a run that is neither a sum nor a difference");
      }
    }

    // How wide a register holding this run is.
    int run_bits(const ir::VecType* ty, const ir::DataLayout& layout)
    {
      return std::max<int>(128, int(ty->lanes * ir::stride_of(ty->element, layout) * 8));
    }

    // One operation over a whole run, into a register of its own.
    mc::MCReg one_op(mc::Assembler& asm_, const mc::Op* what, const ir::IntType* lane, const mc::MCOperand& left,
                     const mc::MCOperand& right, int held, Scratch& scratch, Span span)
    {
      auto into = scratch.run_scratch(held);
      asm_.run_op(what, into, left, right, int(lane->bits), span);
      return mc::MCReg{ into, held };
    }

    // `outer` of `inner` applied to each pair, which is both signed questions.
    mc::MCReg both(mc::Assembler& asm_, const mc::Op* inner, const mc::Op* outer, const ir::IntType* lane,
                   std::pair<mc::MCOperand, mc::MCOperand> first, std::pair<mc::MCOperand, mc::MCOperand> second,
                   int held, Scratch& scratch, Span span)
    {
      auto one = one_op(asm_, inner, lane, first.first, first.second, held, scratch, span);
      auto other = one_op(asm_, inner, lane, second.first, second.second, held, scratch, span);
      return one_op(asm_, outer, lane, one, other, held, scratch, span);
    }

    // A run whose every lane's top bit says whether that lane went past.
    mc::MCReg went_past(mc::Assembler& asm_, BinOp op, const ir::IntType* lane, const mc::MCOperand& left,
                        const mc::MCOperand& right, const mc::MCOperand& answer, int held, Scratch& scratch,
                        Span span)
    {
      bool adding = plain(op) == mc::ops::plus;
      if (lane->is_signed) {
        auto first = adding ? std::pair{ left, answer } : std::pair{ left, right };
        auto second = adding ? std::pair{ right, answer } : std::pair{ left, answer };
        return both(asm_, mc::ops::xor_, mc::ops::and_, lane, first, second, held, scratch, span);
      }
      auto ones = scratch.run_scratch(held);
      asm_.run_ones(ones, span);
      mc::MCReg whole{ ones, held };
      auto op_ = [&](const mc::Op* what, const mc::MCOperand& a, const mc::MCOperand& b) {
        return one_op(asm_, what, lane, a, b, held, scratch, span);
      };
      if (adding) {
        // The carry out of the top bit: both operands had it, or one of them had
        // it and the answer did not.
        auto not_answer = op_(mc::ops::xor_, answer, whole);
        auto carried = op_(mc::ops::and_, left, right);
        auto either = op_(mc::ops::or_, left, right);
        auto lost = op_(mc::ops::and_, either, not_answer);
        return op_(mc::ops::or_, carried, lost);
      }
      // The borrow into the top bit.
      auto not_left = op_(mc::ops::xor_, left, whole);
      auto taken = op_(mc::ops::and_, not_left, right);
      auto differ = op_(mc::ops::xor_, left, right);
      auto alike = op_(mc::ops::xor_, differ, whole);
      auto borrowed = op_(mc::ops::and_, alike, answer);
      return op_(mc::ops::or_, taken, borrowed);
    }

  } // anonymous namespace


  void lower_trapping_run(mc::Assembler& asm_, ir::BinOp op, const ir::VecType* ty, const mc::MCOperand& left,
                          const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, Fault& fault,
                          const ir::DataLayout& layout, Span span)
  {
    auto lane = ir::as<ir::IntType>(ty->element);
    if (lane == nullptr) [[unlikely]]
      throw Unsupported("arithmetic over a run of something that is not an integer");
    auto held = run_bits(ty, layout);
    asm_.run_op(plain(op), destination, left, right, int(lane->bits), span);
    mc::MCReg answer{ destination, held };
    auto bad = went_past(asm_, op, lane, left, right, answer, held, scratch, span);
    // Only the lanes the run covers are asked about.
    auto found = scratch.scratch();
    asm_.run_any_lane(found, bad, int(ir::stride_of(lane, layout)), int(ty->lanes), span);
    auto carry_on = asm_.reserve_label("in.range");
    asm_.branch(mc::Condition::eq, mc::MCReg{ found, 64 }, mc::MCImm{ 0, 64, false }, carry_on, span);
    fault.out_of_range(asm_, span);
    asm_.block(carry_on);
  }


  std::uint64_t top_bytes(std::uint64_t stride, std::uint64_t lanes, int bits)
  {
    std::uint64_t mask = 0;
    for (std::uint64_t lane = 0; lane < std::min<std::uint64_t>(lanes, std::uint64_t(bits) / 8 / stride); ++lane)
      mask |= std::uint64_t(1) << ((lane + 1) * stride - 1);
    return mask;
  }

} // namespace pl4g::target
