// Instruction selection for AArch64.
#include "target/aarch64/isel.hh"

#include <algorithm>
#include <array>
#include <format>
#include <map>

#include "ir/builder.hh"
#include "ir/layout.hh"
#include "util/reflect.hh"
#include "target/aarch64/abi.hh"
#include "target/aarch64/ops.hh"
#include "target/aarch64/regs.hh"
#include "target/aarch64/startup.hh"
#include "target/bitcount.hh"
#include "target/branches.hh"
#include "target/emit.hh"
#include "target/globals.hh"
#include "target/narrow.hh"
#include "target/ordering.hh"
#include "target/runs.hh"
#include "target/saturate.hh"
#include "target/scratch.hh"
#include "target/statuses.hh"


namespace pl4g::target::aarch64 {

  namespace {

    using mc::Condition;
    using mc::Insts;
    using mc::MCImm;
    using mc::MCMem;
    using mc::MCOperand;
    using mc::MCReg;
    using mc::MCSymRef;
    using mc::Reg;

    // How wide a floating-point register is declared to be.  What an
    // instruction names is a view of it at the width of the value, so the
    // whole is what the value's own register is.
    constexpr int float_register_bits = 128;

    // What a branch compares against where its condition is a value rather
    // than a comparison.
    const MCImm zero_immediate{ 0, 12, false };

    // The mnemonic that implements each architecture-neutral binary
    // operation, and each that takes one source.
    std::string_view binary_mnemonic(const mc::Op* op)
    {
      if (op == mc::ops::plus)
        return "add";
      if (op == mc::ops::minus)
        return "sub";
      if (op == mc::ops::times)
        return "mul";
      if (op == mc::ops::xor_)
        return "eor";
      if (op == mc::ops::and_)
        return "and";
      if (op == mc::ops::or_)
        return "orr";
      return {};
    }

    std::string_view unary_mnemonic(const mc::Op* op)
    {
      return op == mc::ops::not_ ? "mvn" : std::string_view();
    }

    // The architecture's own name for each condition: unsigned orderings are
    // spelled with the carry flag, which is what "higher" and "lower" mean.
    std::string_view condition_suffix(Condition cond)
    {
      switch (cond) {
      case Condition::eq: return "eq";
      case Condition::ne: return "ne";
      case Condition::slt: return "lt";
      case Condition::sle: return "le";
      case Condition::sgt: return "gt";
      case Condition::sge: return "ge";
      case Condition::ult: return "lo";
      case Condition::ule: return "ls";
      case Condition::ugt: return "hi";
      case Condition::uge: return "hs";
      }
      return {};
    }

    // The branch that follows a comparison, the instruction that writes a
    // condition into a register, and the select that takes a bound.
    std::string conditional(Condition cond) { return std::format("b.{}", condition_suffix(cond)); }
    std::string set_mnemonic(Condition cond) { return std::format("cset.{}", condition_suffix(cond)); }
    std::string csel(Condition cond) { return std::format("csel.{}", condition_suffix(cond)); }

    // Which condition a floating-point comparison is read with: a
    // not-a-number leaves the flags saying "unordered", which the unsigned
    // readings of below and below-or-equal answer correctly.
    Condition float_condition(Condition cond)
    {
      if (cond == Condition::slt)
        return Condition::ult;
      if (cond == Condition::sle)
        return Condition::ule;
      return cond;
    }

    // How the sixteen bytes are divided into lanes, by how wide one lane is.
    std::string_view arrangement(int bits)
    {
      switch (bits) {
      case 8: return "16b";
      case 16: return "8h";
      case 32: return "4s";
      case 64: return "2d";
      default: return {};
      }
    }

    // The bitwise three, whose answer does not depend on how the bits are
    // divided, and the arithmetic, which does.
    std::string_view run_operation(const mc::Op* op)
    {
      if (op == mc::ops::and_)
        return "and.16b";
      if (op == mc::ops::or_)
        return "orr.16b";
      if (op == mc::ops::xor_)
        return "eor.16b";
      return {};
    }

    std::string_view run_arithmetic(const mc::Op* op)
    {
      if (op == mc::ops::plus)
        return "add";
      if (op == mc::ops::minus)
        return "sub";
      // The low half of the product, which is all a multiplication that may
      // wrap wants.  There is no form for the widest lane.
      if (op == mc::ops::times)
        return "mul";
      return {};
    }

    // Which load reads a value of a given width, and how it widens it; which
    // store writes it; and the ordered forms.
    std::string_view load_mnemonic(int width, bool is_signed)
    {
      switch (width) {
      case 8: return is_signed ? "ldrsb" : "ldrb";
      case 16: return is_signed ? "ldrsh" : "ldrh";
      case 32: return "ldr";
      case 64: return "ldr";
      default: return {};
      }
    }

    std::string_view store_mnemonic(int width)
    {
      switch (width) {
      case 8: return "strb";
      case 16: return "strh";
      case 32: return "str";
      case 64: return "str";
      default: return {};
      }
    }

    std::string_view acquire_mnemonic(int width)
    {
      switch (width) {
      case 8: return "ldarb";
      case 16: return "ldarh";
      case 32: return "ldar";
      case 64: return "ldar";
      default: return {};
      }
    }

    std::string_view release_mnemonic(int width)
    {
      switch (width) {
      case 8: return "stlrb";
      case 16: return "stlrh";
      case 32: return "stlr";
      case 64: return "stlr";
      default: return {};
      }
    }

    // What each of the three shifts is called here.
    std::string_view shift_mnemonic(const mc::Op* op)
    {
      if (op == mc::ops::shift_left)
        return "lslv";
      if (op == mc::ops::shift_right)
        return "lsrv";
      return "asrv";
    }

    // What each operation is called for floating-point values.
    std::string_view float_binary(const mc::Op* op)
    {
      if (op == mc::ops::plus)
        return "fadd";
      if (op == mc::ops::minus)
        return "fsub";
      if (op == mc::ops::times)
        return "fmul";
      if (op == mc::ops::divide)
        return "fdiv";
      if (op == mc::ops::larger)
        return "fmax";
      if (op == mc::ops::smaller)
        return "fmin";
      return {};
    }

    // Which instruction each rounding is: three that name a direction and one
    // that asks the processor's own mode.
    std::string_view round_mnemonic(mc::Rounding how)
    {
      switch (how) {
      case mc::Rounding::down: return "frintm";
      case mc::Rounding::up: return "frintp";
      case mc::Rounding::nearest: return "frintn";
      case mc::Rounding::current: return "frinti";
      }
      return {};
    }

    const MCReg* as_reg(const MCOperand& operand) { return std::get_if<MCReg>(&operand); }
    const MCImm* as_imm(const MCOperand& operand) { return std::get_if<MCImm>(&operand); }
    const MCMem* as_mem(const MCOperand& operand) { return std::get_if<MCMem>(&operand); }
    const MCSymRef* as_symref(const MCOperand& operand) { return std::get_if<MCSymRef>(&operand); }

    bool is_float(Reg reg) { return reg.cls() == regs().vec; }

    // Whether `operand` already names the register `dst`.
    bool same_register(Reg dst, const MCOperand& operand)
    {
      auto found = as_reg(operand);
      return found != nullptr && found->reg == dst;
    }

    MCReg named(Reg reg, std::optional<int> bits = std::nullopt) { return MCReg{ reg, bits }; }

    MCImm offset_of(std::int64_t disp) { return MCImm{ disp, 12, false }; }

    Reg new_gpr(int bits = 64) { return regs().info.new_virtual(regs().gpr, bits); }
    Reg new_vec(int bits = 128) { return regs().info.new_virtual(regs().vec, bits); }

    // A run register named as the whole of one, which is what these read.
    MCOperand run_whole(const MCOperand& operand)
    {
      if (auto found = as_reg(operand))
        return named(found->reg, 128);
      return operand;
    }

    // The same operand, named at `bits` where naming it is what it is.
    MCOperand named_at(const MCOperand& operand, int bits)
    {
      if (auto found = as_reg(operand))
        return named(found->reg, bits);
      return operand;
    }

    // How wide a register holding one of these operands has to be: the width
    // of the first register among them, or thirty-two.
    int operand_width(std::span<const MCOperand> operands)
    {
      for (const auto& operand : operands)
        if (auto found = as_reg(operand))
          return found->bits ? *found->bits : found->reg.bits();
      return 32;
    }

    Insts joined(Insts first, const Insts& second)
    {
      first.insert(first.end(), second.begin(), second.end());
      return first;
    }

  } // anonymous namespace


  // -- the selector -------------------------------------------------------------

  mc::InstRef A64Selector::inst(std::string_view mnemonic, std::vector<MCOperand> operands, Span span) const
  {
    const mc::InstDesc* desc;
    try {
      desc = table.select(mnemonic, operands);
    } catch (const mc::SelectionError& error) {
      throw UnsupportedOperation(error.what(), span.is_valid() ? std::optional(span) : std::nullopt);
    }
    return mc::make_inst(desc, std::move(operands), span);
  }


  Insts A64Selector::select_move(Reg dst, const MCOperand& src, Span span)
  {
    if (is_float(dst))
      return select_float_move(dst, src, span);
    if (auto found = as_reg(src); found != nullptr && is_float(found->reg))
      throw UnsupportedOperation("moving a floating-point value into an ordinary register", span);
    if (auto place = as_mem(src))
      return select_load(dst, *place, span);
    if (same_register(dst, src))
      return {};
    if (as_reg(src) != nullptr)
      return { inst("mov", { named(dst), src }, span) };
    if (auto value = as_imm(src)) {
      if (0 <= value->value && value->value <= max_move_immediate)
        return { inst("movz", { named(dst), src }, span) };
      return materialize(dst, value->value, span);
    }
    throw UnsupportedOperation("moving from this kind of operand", span);
  }


  Insts A64Selector::select_float_move(Reg dst, const MCOperand& src, Span span)
  {
    // An integer load builds the address it needs in its own destination,
    // which this one cannot: the address is an ordinary value and the
    // destination is not an ordinary register, so a register of its own is
    // asked for.  A move between registers moves eight bytes whatever the
    // width of the value.
    if (auto place = as_mem(src)) {
      int width = place->size_bits.value_or(64);
      if (width != 32 && width != 64)
        throw UnsupportedOperation(std::format("reading {} bits of floating point", width), span);
      MCReg held = named(dst, width);
      MCReg address = named(new_gpr());
      MCImm offset = offset_of(place->disp);
      if (place->disp_sym == nullptr) {
        MCReg base = ! place->base.is_none() ? named(place->base, 64) : address;
        return { inst("ldr", { held, base, offset }, span) };
      }
      MCSymRef symbol{ place->disp_sym };
      return { inst("adrp", { address, symbol }, span), inst("add.lo12", { address, address, symbol }, span),
               inst("ldr", { held, address, offset }, span) };
    }
    if (auto found = as_reg(src)) {
      if (same_register(dst, src))
        return {};
      return { inst("fmov", { named(dst, 64), named(found->reg, 64) }, span) };
    }
    if (auto imm = std::get_if<MCImm>(&src)) {
      // A number written out, which is the nought standing for the answer of
      // an error that nothing reads.  Nothing puts one straight into a
      // floating-point register, so it goes into an ordinary one and its bits
      // are moved across.
      Reg carried = new_gpr(64);
      Insts made = select_move(carried, MCImm{ imm->value, 64, false }, span);
      auto bits = select_move_bits(dst, named(carried, 64), 64, span);
      made.insert(made.end(), bits.begin(), bits.end());
      return made;
    }
    throw UnsupportedOperation("putting that kind of operand in a floating-point register", span);
  }


  Insts A64Selector::select_run_move(const std::variant<Reg, MCMem>& dst, const MCOperand& src, int bits, Span span)
  {
    if (bits != 32 && bits != 64 && bits != 128)
      throw UnsupportedOperation(std::format("moving {} bits of a run of elements", bits), span);
    if (auto place = std::get_if<MCMem>(&dst)) {
      auto found = as_reg(src);
      if (found == nullptr)
        throw UnsupportedOperation("writing a run that is not in a register", span);
      return run_place("str", named(found->reg, bits), *place, span);
    }
    Reg into = std::get<Reg>(dst);
    if (auto place = as_mem(src))
      return run_place("ldr", named(into, bits), *place, span);
    if (auto found = as_reg(src)) {
      if (same_register(into, src))
        return {};
      // The whole of one register goes, whatever the run is.  There is no move
      // between two of these; an `or` of a register with itself is how it is
      // written.
      MCReg whole = named(found->reg, 128);
      return { inst("orr.16b", { named(into, 128), whole, whole }, span) };
    }
    throw UnsupportedOperation("putting that kind of operand in a run register", span);
  }


  Insts A64Selector::run_place(std::string_view mnemonic, const MCReg& held, const MCMem& place, Span span)
  {
    // No instruction here can name an address outright, so one is built a page
    // at a time; and the register it is built in cannot be the destination.
    MCReg base = named(new_gpr());
    MCImm offset = offset_of(place.disp);
    if (place.disp_sym == nullptr) {
      MCReg where = ! place.base.is_none() ? named(place.base, 64) : base;
      return { inst(mnemonic, { held, where, offset }, span) };
    }
    MCSymRef symbol{ place.disp_sym };
    return { inst("adrp", { base, symbol }, span), inst("add.lo12", { base, base, symbol }, span),
             inst(mnemonic, { held, base, offset }, span) };
  }


  Insts A64Selector::select_run_op(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right,
                                   int bits, Span span)
  {
    std::string mnemonic(run_operation(op));
    if (mnemonic.empty()) {
      auto arithmetic = run_arithmetic(op);
      auto lanes = arrangement(bits);
      if (arithmetic.empty() || lanes.empty())
        throw UnsupportedOperation(std::format("'{}' over a whole run of {}-bit elements", op->name, bits), span);
      mnemonic = std::format("{}.{}", arithmetic, lanes);
    }
    return { inst(mnemonic, { named(dst, 128), run_whole(left), run_whole(right) }, span) };
  }


  Insts A64Selector::select_run_saturating(Reg dst, const MCOperand& left, const MCOperand& right, bool adding,
                                           bool is_signed, int bits, Span span)
  {
    auto lanes = arrangement(bits);
    if (lanes.empty())
      throw UnsupportedOperation(std::format("saturating arithmetic over a run of {}-bit elements", bits), span);
    auto name = std::format("{}q{}.{}", is_signed ? "s" : "u", adding ? "add" : "sub", lanes);
    return { inst(name, { named(dst, 128), run_whole(left), run_whole(right) }, span) };
  }


  Insts A64Selector::select_run_splat(Reg dst, const MCOperand& src, int bits, Span span)
  {
    auto lanes = arrangement(bits);
    if (lanes.empty())
      throw UnsupportedOperation(std::format("one value of {} bits in every lane", bits), span);
    Insts before;
    MCOperand given = src;
    if (as_imm(src) != nullptr) {
      Reg carried = new_gpr();
      before = select_move(carried, src, span);
      given = named(carried);
    }
    auto found = as_reg(given);
    if (found == nullptr)
      throw UnsupportedOperation("spreading that kind of operand over every lane", span);
    before.push_back(inst(std::format("dup.{}", lanes), { named(dst, 128), named(found->reg, bits == 64 ? 64 : 32) },
                          span));
    return before;
  }


  Insts A64Selector::select_run_ones(Reg dst, Span span)
  {
    // A register exclusive-ored with itself is every bit clear, and turned
    // round is every bit set.
    MCReg target = named(dst, 128);
    return { inst("eor.16b", { target, target, target }, span), inst("not.16b", { target, target }, span) };
  }


  Insts A64Selector::select_run_compare(Condition cond, Reg dst, const MCOperand& left, const MCOperand& right,
                                        int bits, Span span)
  {
    // One instruction for each of the orders, the less ones being the greater
    // ones the other way round; "not equal" is equality turned round.
    if (bits != 8) [[unlikely]]
      throw UnsupportedOperation(std::format("comparing a run of {}-bit elements", bits), span);
    std::string_view mnemonic;
    bool exchanged = false;
    switch (cond) {
    case Condition::eq: case Condition::ne: mnemonic = "cmeq.16b"; break;
    case Condition::sgt: mnemonic = "cmgt.16b"; break;
    case Condition::slt: mnemonic = "cmgt.16b"; exchanged = true; break;
    case Condition::sge: mnemonic = "cmge.16b"; break;
    case Condition::sle: mnemonic = "cmge.16b"; exchanged = true; break;
    case Condition::ugt: mnemonic = "cmhi.16b"; break;
    case Condition::ult: mnemonic = "cmhi.16b"; exchanged = true; break;
    case Condition::uge: mnemonic = "cmhs.16b"; break;
    case Condition::ule: mnemonic = "cmhs.16b"; exchanged = true; break;
    }
    MCReg target = named(dst, 128);
    Insts made{ inst(mnemonic, { target, run_whole(exchanged ? right : left), run_whole(exchanged ? left : right) },
                     span) };
    if (cond == Condition::ne)
      made.push_back(inst("not.16b", { target, target }, span));
    return made;
  }


  Insts A64Selector::select_run_mask(Reg dst, const MCOperand& src, int lanes, Span span)
  {
    // What it is handed is already a byte per lane holding the bit that lane
    // stands for, the caller having anded the comparison with the weights:
    // three pairwise additions leave the first eight lanes' bits in the low
    // byte and the next eight's in the one above it.
    auto found = as_reg(src);
    if (found == nullptr) [[unlikely]]
      throw UnsupportedOperation("gathering the lanes of something not in a register", span);
    if (lanes > 16) [[unlikely]]
      throw UnsupportedOperation(std::format("gathering {} lanes of one register", lanes), span);
    MCReg held = named(found->reg, 128);
    MCReg summed = named(new_vec(), 128);
    return { inst("addp.16b", { summed, held, held }, span), inst("addp.16b", { summed, summed, summed }, span),
             inst("addp.16b", { summed, summed, summed }, span), inst("umov.h0", { named(dst, 32), summed }, span) };
  }


  Insts A64Selector::select_run_truths(Reg dst, const MCOperand& src, Span span)
  {
    // Every byte taken from nought, which is one instruction.
    return { inst("neg.16b", { named(dst, 128), run_whole(src) }, span) };
  }


  Insts A64Selector::select_run_any_lane(Reg dst, const MCOperand& src, int stride, int lanes, Span span)
  {
    // Each lane is first turned into every bit of itself set or every bit
    // clear, by the comparison with zero that asks what the top bit answers;
    // then what is left is whether any byte of the run is not zero.
    auto found = as_reg(src);
    if (found == nullptr)
      throw UnsupportedOperation("asking about the lanes of something not in a register", span);
    auto lanes_named = arrangement(stride * 8);
    if (lanes_named.empty())
      throw UnsupportedOperation(std::format("asking about lanes of {} bytes", stride), span);
    MCReg held = named(found->reg, 128);
    Insts made{ inst(std::format("cmlt.{}", lanes_named), { held, held }, span) };
    int covered = stride * lanes;
    if (covered > 8) {
      // More than one ordinary register's worth: the largest byte of the
      // sixteen answers for all of them.
      made.push_back(inst("umaxv.16b", { held, held }, span));
      covered = 4;
    }
    made.push_back(inst("fmov", { named(dst, covered > 4 ? 64 : 32), named(found->reg, covered > 4 ? 64 : 32) },
                        span));
    return made;
  }


  Insts A64Selector::materialize(Reg dst, __int128 value, Span span)
  {
    // A constant wider than sixteen bits a quarter of a word at a time: the
    // first instruction sets a quarter and clears the rest, each after sets a
    // quarter and leaves the rest alone, and a quarter already right is
    // skipped.  Where the value has more quarters of ones than of zeroes, the
    // first is the one that turns every bit round instead.
    auto pattern = static_cast<std::uint64_t>(static_cast<unsigned __int128>(value));
    std::array<std::uint64_t, 4> quarters{};
    std::array<std::uint64_t, 4> turned{};
    for (int index = 0; index < 4; ++index) {
      quarters[index] = (pattern >> (16 * index)) & 0xFFFF;
      turned[index] = quarters[index] ^ 0xFFFF;
    }
    MCReg wide = named(dst, 64);
    auto set = [&](int index, std::uint64_t quarter, std::string_view mnemonic) {
      return inst(mnemonic, { wide, MCImm{ quarter, 16, false }, MCImm{ 16 * index, 8, false } }, span);
    };
    auto nonzero = [](const std::array<std::uint64_t, 4>& of) {
      return std::ranges::count_if(of, [](std::uint64_t q) { return q != 0; });
    };
    if (nonzero(turned) < nonzero(quarters)) {
      int first = 0;
      for (int index = 0; index < 4; ++index)
        if (turned[index] != 0) {
          first = index;
          break;
        }
      Insts built{ set(first, turned[first], "movn") };
      for (int index = 0; index < 4; ++index)
        if (index != first && quarters[index] != 0xFFFF)
          built.push_back(set(index, quarters[index], "movk"));
      return built;
    }
    std::vector<int> present;
    for (int index = 0; index < 4; ++index)
      if (quarters[index] != 0)
        present.push_back(index);
    if (present.empty())
      return { set(0, 0, "movz") };
    Insts built{ set(present[0], quarters[present[0]], "movz") };
    for (std::size_t at = 1; at < present.size(); ++at)
      built.push_back(set(present[at], quarters[present[at]], "movk"));
    return built;
  }


  Insts A64Selector::select_load(Reg dst, const MCMem& src, Span span)
  {
    int width = src.size_bits.value_or(64);
    auto mnemonic = load_mnemonic(width, src.is_signed);
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("reading {} bits from memory", width), span);
    // The address is built in the destination register: the load consumes it
    // and overwrites it.  And the destination is named at the width the load
    // writes rather than at the register's own.
    MCReg address = named(dst, 64);
    MCReg held = named(dst, width >= 64 ? 64 : 32);
    if (src.disp_sym == nullptr) {
      MCReg base = ! src.base.is_none() ? named(src.base, 64) : address;
      return { inst(mnemonic, { held, base, offset_of(src.disp) }, span) };
    }
    MCSymRef symbol{ src.disp_sym };
    return { inst("adrp", { address, symbol }, span), inst("add.lo12", { address, address, symbol }, span),
             inst(mnemonic, { held, address, offset_of(src.disp) }, span) };
  }


  std::pair<Insts, std::vector<MCOperand>> A64Selector::accepting(std::string_view mnemonic,
                                                                  std::vector<MCOperand> operands, Span span)
  {
    // An immediate is used where the table has a form that carries one, and
    // put in a register where it has not.
    try {
      (void) table.select(mnemonic, operands);
      return { Insts{}, std::move(operands) };
    } catch (const mc::SelectionError&) {
    }
    Insts before;
    std::vector<MCOperand> rewritten;
    for (const auto& operand : operands) {
      if (as_imm(operand) == nullptr) {
        rewritten.push_back(operand);
        continue;
      }
      Reg carried = new_gpr(operand_width(operands));
      auto moved = select_move(carried, operand, span);
      before.insert(before.end(), moved.begin(), moved.end());
      rewritten.push_back(named(carried));
    }
    return { std::move(before), std::move(rewritten) };
  }


  Insts A64Selector::select_op(const mc::Op* op, Reg dst, std::span<const MCOperand> sources, Span span)
  {
    if (op == supervisor_call())
      return { inst("svc", { MCImm{ 0, 16, false } }, span) };
    if (op == mc::ops::trap)
      // One rather than zero, so that a trap the compiler put there is told
      // apart in a disassembly from the padding between functions.
      return { inst("udf", { MCImm{ 1, 16, false } }, span) };
    if (op == mc::ops::move) {
      if (dst.is_none())
        throw UnsupportedOperation("a move needs a destination", span);
      return select_move(dst, sources[0], span);
    }
    if (auto mnemonic = binary_mnemonic(op); ! mnemonic.empty()) {
      if (dst.is_none() || sources.size() != 2)
        throw UnsupportedOperation(std::format("'{}' needs a destination and two sources", op->name), span);
      // A place in the frame past what twelve bits say: not the number in a
      // register and the register form, which reads register 31 as nought.
      if (op == mc::ops::plus)
        if (auto first = as_reg(sources[0]); first != nullptr && first->reg == Reg(regs().sp))
          if (auto second = std::get_if<MCImm>(&sources[1]); second != nullptr && second->value > 0xFFF)
            return stack_plus(dst, std::int64_t(second->value), span);
      auto [before, ready] = accepting(mnemonic, { named(dst), sources[0], sources[1] }, span);
      before.push_back(inst(mnemonic, std::move(ready), span));
      return before;
    }
    if (auto mnemonic = unary_mnemonic(op); ! mnemonic.empty()) {
      if (dst.is_none() || sources.size() != 1)
        throw UnsupportedOperation(std::format("'{}' needs a destination and one source", op->name), span);
      auto [before, ready] = accepting(mnemonic, { named(dst), sources[0] }, span);
      before.push_back(inst(mnemonic, std::move(ready), span));
      return before;
    }
    throw UnsupportedOperation(std::format("no AArch64 selection rule for '{}'", op->name), span);
  }


  Insts A64Selector::select_store(const MCMem& address, const MCOperand& value, Span span)
  {
    // The address is built in a register the allocator places, which is what
    // keeps this from setting a register aside that nothing else may use.
    int width = address.size_bits.value_or(64);
    if (auto found = as_reg(value); found != nullptr && is_float(found->reg))
      return select_float_store(address, found->reg, width, span);
    auto mnemonic = store_mnemonic(width);
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("writing {} bits to memory", width), span);
    auto [before, held] = value_in_register(value, width, span);
    if (address.disp_sym == nullptr) {
      MCReg base = ! address.base.is_none() ? named(address.base, 64) : named(new_gpr());
      before.push_back(inst(mnemonic, { held, base, offset_of(address.disp) }, span));
      return before;
    }
    MCReg place = named(new_gpr());
    MCSymRef symbol{ address.disp_sym };
    before.push_back(inst("adrp", { place, symbol }, span));
    before.push_back(inst("add.lo12", { place, place, symbol }, span));
    before.push_back(inst(mnemonic, { held, place, offset_of(address.disp) }, span));
    return before;
  }


  std::pair<Insts, MCReg> A64Selector::flat_address(const MCMem& address, Reg into, Span span)
  {
    // The ordered forms carry no offset, so where the ordinary ones put one in
    // the instruction this adds it first, into `into`.
    MCReg place = named(into, 64);
    Insts made;
    if (address.disp_sym != nullptr) {
      MCSymRef symbol{ address.disp_sym };
      made.push_back(inst("adrp", { place, symbol }, span));
      made.push_back(inst("add.lo12", { place, place, symbol }, span));
    } else if (! address.base.is_none()) {
      MCReg base = named(address.base, 64);
      if (address.disp == 0)
        return { Insts{}, base };
      made.push_back(inst("add", { place, base, offset_of(address.disp) }, span));
      return { std::move(made), place };
    } else
      throw UnsupportedOperation(
        "an ordered access to a place that is neither a symbol nor an address held in a register", span);
    if (address.disp != 0)
      made.push_back(inst("add", { place, place, offset_of(address.disp) }, span));
    return { std::move(made), place };
  }


  Insts A64Selector::select_acquire(Reg dst, const MCMem& address, Span span)
  {
    int width = address.size_bits.value_or(64);
    auto mnemonic = acquire_mnemonic(width);
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("reading {} bits from memory in order", width), span);
    auto [before, place] = flat_address(address, dst, span);
    before.push_back(inst(mnemonic, { named(dst), place }, span));
    return before;
  }


  Insts A64Selector::select_release(const MCMem& address, const MCOperand& value, Span span)
  {
    int width = address.size_bits.value_or(64);
    auto mnemonic = release_mnemonic(width);
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("writing {} bits to memory in order", width), span);
    auto [held, written] = value_in_register(value, width, span);
    auto [before, place] = flat_address(address, new_gpr(), span);
    auto made = joined(std::move(held), before);
    made.push_back(inst(mnemonic, { written, place }, span));
    return made;
  }


  std::pair<Insts, MCReg> A64Selector::value_in_register(const MCOperand& value, int width, Span span)
  {
    if (auto found = as_reg(value))
      return { Insts{}, *found };
    Reg held = new_gpr(width <= 32 ? 32 : 64);
    auto moved = select_move(held, value, span);
    return { std::move(moved), named(held) };
  }


  Insts A64Selector::select_float_store(const MCMem& address, Reg value, int width, Span span)
  {
    if (width != 32 && width != 64)
      throw UnsupportedOperation(std::format("writing {} bits of floating point", width), span);
    MCReg held = named(value, width);
    MCReg place = named(new_gpr());
    MCImm offset = offset_of(address.disp);
    if (address.disp_sym == nullptr) {
      MCReg base = ! address.base.is_none() ? named(address.base, 64) : place;
      return { inst("str", { held, base, offset }, span) };
    }
    MCSymRef symbol{ address.disp_sym };
    return { inst("adrp", { place, symbol }, span), inst("add.lo12", { place, place, symbol }, span),
             inst("str", { held, place, offset }, span) };
  }


  Insts A64Selector::select_call(const MCOperand& target, Span span)
  {
    // A call through a register: what is called is whatever it holds.
    if (as_symref(target) == nullptr)
      return { inst("blr", { target }, span) };
    return { inst("bl", { target }, span) };
  }


  Insts A64Selector::select_return(Span span)
  {
    return { inst("ret", {}, span) };
  }


  Insts A64Selector::select_jump(const MCOperand& target, Span span)
  {
    if (! std::holds_alternative<MCSymRef>(target))
      // Through a register: wherever it holds.
      return { inst("br", { target }, span) };
    return { inst("b", { target }, span) };
  }


  Insts A64Selector::select_branch(Condition cond, const MCOperand& lhs_given, const MCOperand& rhs_given,
                                   const MCSymRef& target, Span span)
  {
    // Comparing a register with zero for equality has an instruction of its
    // own that needs no flags.
    MCOperand lhs = lhs_given;
    MCOperand rhs = rhs_given;
    if (as_imm(lhs) != nullptr && as_imm(rhs) == nullptr) {
      std::swap(lhs, rhs);
      cond = mc::swapped(cond);
    }
    if (auto value = as_imm(rhs); as_reg(lhs) != nullptr && value != nullptr && value->value == 0
                                  && (cond == Condition::eq || cond == Condition::ne)) {
      std::string_view mnemonic = cond == Condition::eq ? "cbz" : "cbnz";
      auto known = table.mnemonics();
      if (std::ranges::find(known, mnemonic) != known.end())
        return { inst(mnemonic, { lhs, target }, span) };
    }
    auto made = select_compare(lhs, rhs, span);
    made.push_back(inst(conditional(cond), { target }, span));
    return made;
  }


  Insts A64Selector::select_address(Reg dst, const MCSymRef& symbol, Span span)
  {
    // A page at a time and then added to.
    MCReg address = named(dst, 64);
    return { inst("adrp", { address, symbol }, span), inst("add.lo12", { address, address, symbol }, span) };
  }


  Insts A64Selector::select_widen(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    // Writing a word-wide view clears the word above it, so widening an
    // unsigned value is an ordinary move; a signed one is the bitfield move
    // spelled `sxtw`.
    if (bits >= 64)
      return select_move(dst, src, span);
    if (as_imm(src) != nullptr)
      return select_move(dst, src, span);
    auto found = as_reg(src);
    if (found == nullptr) {
      // A place in memory, which a load narrows on its own.
      MCMem narrow = std::get<MCMem>(src);
      narrow.size_bits = bits;
      if (! is_signed)
        return select_move(dst, narrow, span);
      if (bits == 32)
        return { inst("ldrsw", { named(dst, 64), narrow }, span) };
      auto held = select_move(dst, narrow, span);
      return joined(std::move(held), select_widen(dst, named(dst, 32), bits, true, span));
    }
    if (! is_signed)
      return { inst("mov", { named(dst, 32), named(found->reg, 32) }, span) };
    std::string_view mnemonic = bits == 8 ? "sxtb" : bits == 16 ? "sxth" : bits == 32 ? "sxtw" : "";
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("widening a value of {} bits", bits), span);
    return { inst(mnemonic, { named(dst, 64), named(found->reg, 32) }, span) };
  }


  Insts A64Selector::select_clamp(Condition cond, Reg dst, const MCOperand& lhs_given, const MCOperand& rhs_given,
                                  const MCOperand& bound_given, Span span)
  {
    MCOperand lhs = lhs_given;
    MCOperand rhs = rhs_given;
    if (as_imm(lhs) != nullptr && as_imm(rhs) == nullptr) {
      std::swap(lhs, rhs);
      cond = mc::swapped(cond);
    }
    Insts before;
    Reg bound;
    if (auto found = as_reg(bound_given))
      bound = found->reg;
    else {
      Reg carried = new_gpr();
      before = select_move(carried, bound_given, span);
      bound = carried;
    }
    MCReg whole = named(dst, 64);
    auto made = joined(std::move(before), select_compare(lhs, rhs, span));
    made.push_back(inst(csel(cond), { whole, named(bound, 64), whole }, span));
    return made;
  }


  Insts A64Selector::select_select(Reg dst, const MCOperand& chooser, const MCOperand& if_true,
                                   const MCOperand& if_false, bool floating, int bits, Span span)
  {
    Insts before;
    auto in_a_register = [&](const MCOperand& operand) -> MCReg {
      if (auto found = as_reg(operand))
        return *found;
      Reg carried = new_gpr();
      auto moved = select_move(carried, operand, span);
      before.insert(before.end(), moved.begin(), moved.end());
      return named(carried);
    };
    MCReg asked = in_a_register(chooser);
    if (floating) {
      auto first = as_reg(if_true);
      auto second = as_reg(if_false);
      auto made = joined(std::move(before), select_compare(asked, MCImm{ 0, asked.width() }, span));
      made.push_back(inst("fcsel.ne", { named(dst, bits), named(first->reg, bits), named(second->reg, bits) },
                          span));
      return made;
    }
    MCReg first = in_a_register(if_true);
    MCReg second = in_a_register(if_false);
    auto made = joined(std::move(before), select_compare(asked, MCImm{ 0, asked.width() }, span));
    made.push_back(inst(csel(Condition::ne), { named(dst, 64), named(first.reg, 64), named(second.reg, 64) }, span));
    return made;
  }


  Insts A64Selector::select_compare(const MCOperand& lhs, const MCOperand& rhs, Span span)
  {
    // A comparison carries twelve bits of constant, so anything wider is built
    // in a register first, as wide as the left one.
    if (as_imm(rhs) != nullptr) {
      try {
        (void) table.select("cmp", std::vector<MCOperand>{ lhs, rhs });
      } catch (const mc::SelectionError&) {
        auto found = as_reg(lhs);
        int width = found != nullptr ? found->reg.bits() : 64;
        Reg carried = new_gpr(width);
        auto made = select_move(carried, rhs, span);
        made.push_back(inst("cmp", { lhs, named(carried) }, span));
        return made;
      }
    }
    return { inst("cmp", { lhs, rhs }, span) };
  }


  Insts A64Selector::select_op_at(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                                  Span span)
  {
    // `add` and `adds` are different instructions here, and only the second
    // says anything about what happened.
    std::string_view mnemonic = op == mc::ops::plus ? "adds" : op == mc::ops::minus ? "subs" : "";
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("'{}' in a form that writes the flags", op->name), span);
    auto [before, ready] = accepting(mnemonic, { named(dst, bits), named_at(left, bits), named_at(right, bits) }, span);
    before.push_back(inst(mnemonic, std::move(ready), span));
    return before;
  }


  Insts A64Selector::select_branch_if_in_range(const mc::Op* op, bool is_signed, const MCSymRef& target, Span span)
  {
    // A signed operation is answered by the overflow flag; an unsigned one by
    // the carry, which an addition sets where it carried out and a subtraction
    // where it did not borrow.
    if (is_signed)
      return { inst("b.vc", { target }, span) };
    return { inst(op == mc::ops::minus ? "b.hs" : "b.lo", { target }, span) };
  }


  Insts A64Selector::select_set(Condition cond, Reg dst, const MCOperand& lhs_given, const MCOperand& rhs_given,
                                Span span)
  {
    MCOperand lhs = lhs_given;
    MCOperand rhs = rhs_given;
    if (as_imm(lhs) != nullptr && as_imm(rhs) == nullptr) {
      std::swap(lhs, rhs);
      cond = mc::swapped(cond);
    }
    auto made = select_compare(lhs, rhs, span);
    made.push_back(inst(set_mnemonic(cond), { named(dst, 32) }, span));
    return made;
  }


  Insts A64Selector::select_float_round(mc::Rounding how, Reg dst, const MCOperand& src, int bits, Span span)
  {
    return { inst(round_mnemonic(how), { named(dst, bits), named_at(src, bits) }, span) };
  }


  Insts A64Selector::select_float_abs(Reg dst, const MCOperand& src, int bits, Span span)
  {
    return { inst("fabs", { named(dst, bits), named_at(src, bits) }, span) };
  }


  Insts A64Selector::select_float_extend(Reg dst, const MCOperand& src, int from_bits, int to_bits, Span span)
  {
    if (from_bits != 32 || to_bits != 64)
      throw UnsupportedOperation(
        std::format("widening {} bits of floating point to {}", from_bits, to_bits), span);
    return { inst("fcvt", { named(dst, 64), named_at(src, 32) }, span) };
  }


  Insts A64Selector::select_branch_if_finite(Reg value, int bits, const MCSymRef& target, Span span)
  {
    // A comparison of the difference against itself is equal where it is a
    // number and unordered where it is not.
    Reg held = regs().info.new_virtual(regs().vec, float_register_bits);
    MCReg view = named(value, bits);
    MCReg difference = named(held, bits);
    return { inst("fsub", { difference, view, view }, span), inst("fcmp", { difference, difference }, span),
             inst("b.eq", { target }, span) };
  }


  Insts A64Selector::select_float_op(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right,
                                     int bits, Span span)
  {
    auto mnemonic = float_binary(op);
    if (mnemonic.empty())
      throw UnsupportedOperation(std::format("'{}' on a floating-point value", op->name), span);
    return { inst(mnemonic, { named(dst, bits), named_at(left, bits), named_at(right, bits) }, span) };
  }


  Insts A64Selector::select_float_compare(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                                          int bits, Span span)
  {
    if (bits != 32 && bits != 64)
      throw std::out_of_range("no floating-point comparison of that width");
    return { inst("fcmp", { named_at(lhs, bits), named_at(rhs, bits) }, span),
             inst(set_mnemonic(float_condition(cond)), { named(dst, 32) }, span) };
  }


  Insts A64Selector::select_shift(const mc::Op* op, Reg dst, const MCOperand& value, const MCOperand& amount,
                                  int bits, Span span)
  {
    auto [held_value, before] = in_register(value, bits, span);
    auto [counted, more] = in_register(amount, bits, span);
    auto made = joined(std::move(before), more);
    made.push_back(inst(shift_mnemonic(op), { named(dst, bits), held_value, counted }, span));
    return made;
  }


  Insts A64Selector::select_wide_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right,
                                         bool is_signed, Span span)
  {
    // The upper half goes first: the low half may be asked for in a register
    // one of the factors is in, and the upper half still wants both.
    auto [first, before] = in_register(left, 64, span);
    auto [second, more] = in_register(right, 64, span);
    auto made = joined(std::move(before), more);
    made.push_back(inst(is_signed ? "smulh" : "umulh", { named(high, 64), first, second }, span));
    made.push_back(inst("mul", { named(low, 64), first, second }, span));
    return made;
  }


  Insts A64Selector::select_count_leading(Reg dst, const MCOperand& src, Span span)
  {
    auto [held, before] = in_register(src, 64, span);
    before.push_back(inst("clz", { named(dst, 64), held }, span));
    return before;
  }


  Insts A64Selector::select_count_trailing(Reg dst, const MCOperand& src, Span span)
  {
    // The bits put in the other order and the zeroes above the highest of
    // those counted, nought answering sixty-four as `clz` of nought does.
    auto [held, before] = in_register(src, 64, span);
    before.push_back(inst("rbit", { named(dst, 64), held }, span));
    before.push_back(inst("clz", { named(dst, 64), named(dst, 64) }, span));
    return before;
  }


  Insts A64Selector::select_int_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    auto [held, before] = in_register(src, 64, span);
    before.push_back(inst("scvtf", { named(dst, bits), held }, span));
    return before;
  }


  Insts A64Selector::select_unsigned_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    auto [held, before] = in_register(src, 64, span);
    before.push_back(inst("ucvtf", { named(dst, bits), held }, span));
    return before;
  }


  Insts A64Selector::select_truncate_to_int(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    return { inst(is_signed ? "fcvtzs" : "fcvtzu", { named(dst, 64), named_at(src, bits) }, span) };
  }


  Insts A64Selector::select_move_bits(Reg dst, const MCOperand& src, int bits, Span span)
  {
    // One `fmov` either way, at the width of the value.
    auto reg = std::get_if<MCReg>(&src);
    if (reg == nullptr) [[unlikely]]
      throw UnsupportedOperation("moving the bits of what is not in a register", span);
    return { inst("fmov", { named(dst, bits), named(reg->reg, bits) }, span) };
  }


  Insts A64Selector::select_divide(Reg dst, const MCOperand& left, const MCOperand& right, bool is_signed,
                                   bool remainder, int bits, Span span)
  {
    // There is a division and no remainder, so what is left over is the
    // dividend less the quotient times the divisor, which is one instruction.
    auto [dividend, before] = in_register(left, bits, span);
    auto [divisor, more] = in_register(right, bits, span);
    auto made = joined(std::move(before), more);
    MCReg quotient = ! remainder ? named(dst, bits) : named(new_gpr(), bits);
    made.push_back(inst(is_signed ? "sdiv" : "udiv", { quotient, dividend, divisor }, span));
    if (remainder)
      made.push_back(inst("msub", { named(dst, bits), quotient, divisor, dividend }, span));
    return made;
  }


  std::pair<MCReg, Insts> A64Selector::in_register(const MCOperand& operand, int bits, Span span)
  {
    if (auto found = as_reg(operand))
      return { named(found->reg, bits), Insts{} };
    Reg carried = new_gpr();
    auto moved = select_move(carried, operand, span);
    return { named(carried, bits), std::move(moved) };
  }


  std::int64_t A64Selector::frame_size(std::int64_t total, bool)
  {
    // A multiple of sixteen, which is what the stack pointer is aligned to
    // everywhere and not only where a call is made.
    return (total + 15) & ~std::int64_t(15);
  }


  std::pair<std::optional<std::int64_t>, std::int64_t> A64Selector::frame_walk(std::int64_t total, std::int64_t size,
                                                                               std::int64_t link)
  {
    // The call left the return address in a register, so it lies wherever the
    // function put it -- and nowhere where the function calls nothing.
    return { link != 0 ? std::optional(size) : std::nullopt, total };
  }


  const mc::PhysReg* A64Selector::frame_spare() const
  {
    // The first of the two the standard convention leaves to the linker for
    // its veneers: nothing in a function this compiler writes lives across one.
    static const mc::PhysReg* const found = reg("x16");
    return found;
  }


  const mc::PhysReg* A64Selector::spare_for(std::int64_t offset) const
  {
    if (spare == nullptr) [[unlikely]]
      throw mc::FrameOutOfReach(std::format("a place {} bytes into the frame", offset));
    return spare;
  }


  Insts A64Selector::stack_plus(Reg dst, std::int64_t offset, Span span)
  {
    // One `add` where twelve bits hold it, the upper twelve a page at a time
    // and the lower twelve after where twenty-four do, and the number built in
    // a register and added in the extended form beyond.
    auto stack = named(regs().sp);
    auto target = named(dst, 64);
    if (offset <= 0xFFF)
      return { inst("add", { target, stack, offset_of(offset) }, span) };
    if (offset <= 0xFFFFFF) {
      Insts made{ inst("add.lsl12", { target, stack, offset_of(offset >> 12) }, span) };
      if (offset & 0xFFF)
        made.push_back(inst("add", { target, target, offset_of(offset & 0xFFF) }, span));
      return made;
    }
    Reg holder = dst == Reg(regs().sp) ? Reg(spare_for(offset)) : dst;
    auto made = materialize(holder, offset, span);
    made.push_back(inst("add.uxtx", { target, stack, named(holder, 64) }, span));
    return made;
  }


  std::tuple<Insts, MCReg, std::int64_t> A64Selector::frame_place(std::int64_t offset, int bytes, Span span, Reg base)
  {
    if (offset % bytes == 0 && offset / bytes <= 0xFFF)
      return { Insts{}, named(regs().sp), offset };
    Reg holder = ! base.is_none() ? base : Reg(spare_for(offset));
    return { stack_plus(holder, offset, span), named(holder, 64), 0 };
  }


  Insts A64Selector::select_save_link(std::int64_t offset, Span span)
  {
    auto [before, base, at] = frame_place(offset, 8, span);
    before.push_back(inst("str", { named(regs().x30, 64), base, offset_of(at) }, span));
    return before;
  }


  Insts A64Selector::select_restore_link(std::int64_t offset, Span span)
  {
    auto [before, base, at] = frame_place(offset, 8, span, Reg(regs().x30));
    before.push_back(inst("ldr", { named(regs().x30, 64), base, offset_of(at) }, span));
    return before;
  }


  Insts A64Selector::select_incoming(Reg dst, std::int64_t offset, int bits, bool is_signed, Span span)
  {
    // One load from the stack pointer and the offset, which is scaled by the
    // size of the access and carried in twelve bits.  Beyond that the address
    // is built in the destination, which is about to be written anyway -- or,
    // for a floating-point value, in the register set aside.
    if (is_float(dst)) {
      auto [before, base, at] = frame_place(offset, bits / 8, span);
      before.push_back(inst("ldr", { named(dst, bits), base, offset_of(at) }, span));
      return before;
    }
    auto [before, base, at] = frame_place(offset, bits / 8, span, dst);
    before.push_back(inst(load_mnemonic(bits, is_signed), { named(dst, bits >= 64 ? 64 : 32), base, offset_of(at) },
                          span));
    return before;
  }


  Insts A64Selector::select_spill(std::int64_t slot, Reg source, Span span)
  {
    // Eight bytes of an ordinary register and the whole of one of the others,
    // which holds a floating-point value or a whole run.
    int bits = is_float(source) ? 128 : 64;
    auto [before, base, at] = frame_place(slot, bits / 8, span);
    before.push_back(inst("str", { named(source, bits), base, offset_of(at) }, span));
    return before;
  }


  Insts A64Selector::select_reload(Reg destination, std::int64_t slot, Span span)
  {
    bool floating = is_float(destination);
    int bits = floating ? 128 : 64;
    auto [before, base, at] = frame_place(slot, bits / 8, span, floating ? Reg() : destination);
    before.push_back(inst("ldr", { named(destination, bits), base, offset_of(at) }, span));
    return before;
  }


  Insts A64Selector::select_frame(std::int64_t size, Span span)
  {
    // One `sub` where twelve bits hold it, the pages and the rest where
    // twenty-four do, and the register set aside beyond.
    auto stack = named(regs().sp);
    if (size <= 0xFFF)
      return { inst("sub", { stack, stack, offset_of(size) }, span) };
    if (size <= 0xFFFFFF) {
      Insts made{ inst("sub.lsl12", { stack, stack, offset_of(size >> 12) }, span) };
      if (size & 0xFFF)
        made.push_back(inst("sub", { stack, stack, offset_of(size & 0xFFF) }, span));
      return made;
    }
    Reg holder(spare_for(size));
    auto made = materialize(holder, size, span);
    made.push_back(inst("sub.uxtx", { stack, stack, named(holder, 64) }, span));
    return made;
  }


  Insts A64Selector::select_unframe(std::int64_t size, Span span)
  {
    return stack_plus(Reg(regs().sp), size, span);
  }


  // -- lowering a function --------------------------------------------------------

  namespace {

    using ir::InstKind;

    // What each operation of the representation is called in the assembler;
    // the wrapping three are the same instructions with nothing asked
    // afterwards about what they came to.
    const mc::Op* operation_for(ir::BinOp op)
    {
      switch (op) {
      case ir::BinOp::add: case ir::BinOp::wrap_add: return mc::ops::plus;
      case ir::BinOp::sub: case ir::BinOp::wrap_sub: return mc::ops::minus;
      case ir::BinOp::mul: case ir::BinOp::wrap_mul: return mc::ops::times;
      case ir::BinOp::and_: return mc::ops::and_;
      case ir::BinOp::or_: return mc::ops::or_;
      case ir::BinOp::xor_: return mc::ops::xor_;
      default: return nullptr;
      }
    }

    // The ones a floating-point value answers to.  Only the signed pair of the
    // extrema ever stands over one: there is no unsigned float.
    const mc::Op* float_operation(ir::BinOp op)
    {
      switch (op) {
      case ir::BinOp::add: return mc::ops::plus;
      case ir::BinOp::sub: return mc::ops::minus;
      case ir::BinOp::mul: return mc::ops::times;
      case ir::BinOp::fdiv: return mc::ops::divide;
      case ir::BinOp::smax: return mc::ops::larger;
      case ir::BinOp::smin: return mc::ops::smaller;
      default: return nullptr;
      }
    }

    // Which rounding each of the four operations asks for.
    std::optional<mc::Rounding> rounding_of(ir::UnOp op)
    {
      switch (op) {
      case ir::UnOp::floor: return mc::Rounding::down;
      case ir::UnOp::ceil: return mc::Rounding::up;
      case ir::UnOp::nearest: return mc::Rounding::nearest;
      case ir::UnOp::rounded: return mc::Rounding::current;
      default: return std::nullopt;
      }
    }

    // What the representation calls an operation, as pypl4g's enumeration's
    // value says it.
    template<typename E>
    std::string_view enum_describe(E e)
    {
      return pl4g::describe(e);
    }

    // How many bits a value of `ty` occupies in memory.  A truth value is a
    // byte, which is what the layout says it is.
    int width_of(const ir::Type* ty)
    {
      if (auto integer = ir::as<ir::IntType>(ty))
        // What holds it: a type narrower than a machine width is read and
        // written at the width that contains it.
        return int(integer->held());
      if (auto floating = ir::as<ir::FloatType>(ty))
        return int(floating->bits);
      if (auto character = ir::as<ir::CharType>(ty))
        return int(character->holder()->bits);
      if (auto e = ir::as<ir::EnumType>(ty))
        return int(e->holder->bits);
      if (ir::is<ir::ProductType>(ty)) {
        // A record of one value is that value.
        auto inner = ir::parts_of(ty);
        if (inner.size() == 1 && inner[0] != ty && ! ir::made_of_parts(inner[0]))
          return width_of(inner[0]);
      }
      return ir::is<ir::BoolType>(ty) ? 8 : 64;
    }

    // Whether a narrow value of `ty` is widened by its sign when it is read.
    bool is_signed_type(const ir::Type* ty)
    {
      if (auto character = ir::as<ir::CharType>(ty))
        return character->holder()->is_signed;
      if (auto e = ir::as<ir::EnumType>(ty))
        return e->holder->is_signed;
      auto integer = ir::as<ir::IntType>(ty);
      return integer != nullptr && integer->is_signed;
    }

    // The number a constant stands for and its type, or nothing where it is
    // not a constant.  A truth value is one or zero.
    std::optional<std::pair<__int128, const ir::Type*>> number_of(const ir::Value* value)
    {
      switch (value->vkind) {
      case ir::ValueKind::int_const:
        return std::pair{ static_cast<const ir::IntConst*>(value)->value, value->ty };
      case ir::ValueKind::char_const:
        return std::pair{ __int128(static_cast<const ir::CharConst*>(value)->value), value->ty };
      case ir::ValueKind::bool_const:
        return std::pair{ __int128(static_cast<const ir::BoolConst*>(value)->value ? 1 : 0), value->ty };
      case ir::ValueKind::enum_const:
        return std::pair{ __int128(static_cast<const ir::EnumConst*>(value)->number()), value->ty };
      default:
        return std::nullopt;
      }
    }

    // The narrowest standard width that holds `value`: the width the encoding
    // uses, which is not the width of the access.
    int immediate_width(__int128 value, bool is_signed)
    {
      for (int bits : { 8, 16, 32, 64 }) {
        if (is_signed) {
          if (-(__int128(1) << (bits - 1)) <= value && value < (__int128(1) << (bits - 1)))
            return bits;
        } else if (0 <= value && value < (__int128(1) << bits))
          return bits;
      }
      return 64;
    }

    // Whether `ty` is a floating-point type, or a result answering with one.
    bool is_floating(const ir::Type* ty)
    {
      if (auto result = ir::as<ir::ResultType>(ty))
        ty = result->ok;
      return ir::is<ir::FloatType>(ty);
    }

    // How wide a floating-point type is.
    int bits_of(const ir::Type* ty)
    {
      return int(ir::as<ir::FloatType>(ty)->bits);
    }

    // How many bits of memory a whole run of elements occupies, and how wide a
    // register holding it is.
    int run_covers(const ir::VecType* ty, const ir::DataLayout& layout)
    {
      return int(ty->lanes * ir::stride_of(ty->element, layout) * 8);
    }

    int run_bits(const ir::VecType* ty, const ir::DataLayout& layout)
    {
      return std::max(float_register_bits, run_covers(ty, layout));
    }

    // `place` named at the width a value of `ty` is passed at.
    const mc::PhysReg* as_argument(const mc::PhysReg* place, const ir::Type* ty, mc::RegisterInfo& registers)
    {
      if (ir::is<ir::FloatType>(ty))
        // The whole register, which is the same register the callee computes
        // into.
        return registers.view(place->unit, float_register_bits);
      return registers.view(place->unit, std::max(32, width_of(ty)));
    }

    // Write one part of an argument into its slot at the bottom of the frame:
    // a whole number as the whole register, which is what it would have been
    // in a register of the convention, and a floating-point one at its own
    // width.  The callee reads only as much as the type holds.
    void pass_on_stack(mc::Assembler& asm_, const OnStack& place, const ir::Type* ty, const MCOperand& source,
                       Span span)
    {
      MCMem slot;
      slot.base = regs().sp;
      slot.disp = place.offset;
      if (ir::is<ir::FloatType>(ty)) {
        slot.size_bits = bits_of(ty);
        asm_.store(slot, source, span);
        return;
      }
      slot.size_bits = 64;
      asm_.store(slot, named(std::get<MCReg>(source).reg, 64), span);
    }

    // The register an instruction's result is put in; a value narrower than a
    // word lands in the word-wide view.
    const mc::PhysReg* result_register(const ir::Type* ty, const CallConvDesc& cconv, mc::RegisterInfo& registers,
                                       std::size_t index = 0)
    {
      if (ir::is<ir::FloatType>(ty))
        return registers.view(cconv.float_ret_regs.at(index)->unit, float_register_bits);
      return registers.view(cconv.int_ret_regs.at(index)->unit, std::max(32, width_of(ty)));
    }

    // How many registers the answer of a result takes.
    std::size_t answer_registers(const ir::Value* value)
    {
      auto result = ir::as<ir::ResultType>(value->ty);
      if (result == nullptr)
        return 1;
      return ir::leaves_of(result->ok).size();
    }

    // A register for a value the function computes, at least a word wide; the
    // hint says where the value is wanted anyway.
    const mc::VirtReg* new_value(const ir::Type* ty, mc::RegisterInfo& registers, const ir::DataLayout& layout,
                                 const mc::PhysReg* hint = nullptr)
    {
      if (ir::is<ir::ProductType>(ty) && ! ir::held_in_memory(ty)) {
        // A record travels as the values it is made of, so what arrives here is
        // a record whose values are one.
        auto inner = ir::parts_of(ty);
        if (inner.size() == 1 && inner[0] != ty && ! ir::made_of_parts(inner[0]))
          return new_value(inner[0], registers, layout, hint);
        throw UnsupportedOperation(std::format("a value of type '{}'", ty->written()));
      }
      if (auto run = ir::as<ir::VecType>(ty))
        return registers.new_virtual(regs().vec, run_bits(run, layout), hint);
      if (ir::is<ir::FloatType>(ty))
        return registers.new_virtual(regs().vec, float_register_bits, hint);
      return registers.new_virtual(regs().gpr, std::max(32, width_of(ty)), hint);
    }

    // The value the function returns, where it computes one.
    const ir::Value* returned_value(const ir::Function& func)
    {
      for (const auto& block : func.blocks)
        for (auto inst : block->insts)
          if (inst->kind == InstKind::RetInst && ! inst->operands.empty())
            return inst->operands[0];
      return nullptr;
    }

    std::optional<Span> valid(Span span)
    {
      return span.is_valid() ? std::optional(span) : std::nullopt;
    }

    std::vector<Reg> as_regs(const std::vector<const mc::PhysReg*>& found)
    {
      return { found.begin(), found.end() };
    }


    // How this back end answers for a value of the representation.
    class ValueOperands : public Operands, public Scratch {
    public:
      ValueOperands(mc::Assembler& asm_, mc::RegisterInfo& registers, Constants* constants,
                    const ir::DataLayout& layout)
        : asm_(asm_), registers(registers), constants(constants), layout(layout)
      {
      }

      // Where each value the function computes is held, and the registers of a
      // value that takes more than one, after the first.
      std::unordered_map<const ir::Value*, const mc::VirtReg*> held;
      std::unordered_map<const ir::Value*, std::vector<const mc::VirtReg*>> extra;

      MCOperand value(ir::Value* value, Span span) override { return operand(value, span); }

      // The operand for `value`, a constant being an immediate.
      MCOperand operand(const ir::Value* value, Span span)
      {
        if (value->vkind == ir::ValueKind::float_const)
          // No instruction carries a floating-point number, so one goes in the
          // image and is read from there.
          return named(floating(static_cast<const ir::FloatConst*>(value), span));
        if (auto constant = number_of(value)) {
          bool is_signed = is_signed_type(constant->second);
          return MCImm{ constant->first, immediate_width(constant->first, is_signed), is_signed };
        }
        return register_for(value, span);
      }

      MCOperand in_register(ir::Value* value, Span span) override { return register_for(value, span); }

      // The operand for `value`, put in a register if it is not in one.
      MCOperand register_for(const ir::Value* value, Span span)
      {
        if (value->vkind == ir::ValueKind::float_const)
          return named(floating(static_cast<const ir::FloatConst*>(value), span));
        if (auto constant = number_of(value)) {
          // Every comparison here wants a register on its left, and two
          // constants compared is what a program not folded looks like.
          auto [number, ty] = *constant;
          auto carried = new_value(ty, registers, layout);
          asm_.loadreg(carried, MCImm{ number, std::max(32, width_of(ty)), is_signed_type(ty) }, span);
          return named(carried);
        }
        auto found = held.find(value);
        if (found == held.end())
          throw UnsupportedOperation("a value this backend did not compute", valid(span));
        return named(found->second);
      }

      // The register a value the function computed is held in.
      const mc::VirtReg* register_of(const ir::Value* value, std::optional<Span> span)
      {
        auto found = held.find(value);
        if (found == held.end())
          throw UnsupportedOperation("a value this backend did not compute", span);
        return found->second;
      }

      // The register holding whether a result has an answer: after the
      // answer's own registers, of which there may be several.
      const mc::VirtReg* flag_of(const ir::Value* value, std::optional<Span> span)
      {
        return part(value, answer_registers(value), span);
      }

      Reg part_of(ir::Value* value, std::size_t index, std::optional<Span> span) override
      {
        return part(value, index, span);
      }

      // The register holding one of a value's several parts.
      const mc::VirtReg* part(const ir::Value* value, std::size_t index, std::optional<Span> span)
      {
        if (index == 0)
          return register_of(value, span);
        auto found = extra.find(value);
        if (found == extra.end() || index > found->second.size())
          throw UnsupportedOperation("a value of several parts this backend did not compute", span);
        return found->second[index - 1];
      }

      // The operand for `value`, where it may be one nothing may read: the
      // undefined one is written as zero.
      MCOperand undefined(const ir::Value* value, const ir::Type* ty, Span span)
      {
        if (value->vkind == ir::ValueKind::undef)
          return MCImm{ 0, std::max(32, width_of(ty)), false };
        return operand(value, span);
      }

      // The register a value is computed into, for one that is written: only a
      // block parameter is asked, and every one has its register already.
      Reg destination(ir::Value* value) override
      {
        auto found = held.find(value);
        if (found == held.end())
          throw UnsupportedOperation("a value this backend did not compute");
        return found->second;
      }

      // A register holding the floating-point constant `value`, read out of
      // the image.
      const mc::VirtReg* floating(const ir::FloatConst* value, Span span)
      {
        return floating_number(value->value, value->ty, span);
      }

      const mc::VirtReg* floating_number(double number, const ir::Type* ty, Span span)
      {
        if (constants == nullptr)
          throw UnsupportedOperation("a floating-point constant, with nowhere to put it");
        int bits = width_of(ty);
        auto symbol = constants->symbol(ir::encode_float(number, ty, plain_layout), std::uint64_t(bits / 8));
        auto made = new_value(ty, registers, layout);
        MCMem place;
        place.disp_sym = mc::sym_expr(asm_.streamer().symbol(symbol));
        place.rip_relative = true;
        place.size_bits = bits;
        asm_.loadreg(made, place, span);
        return made;
      }

      // A register of the full width, for a value with no name of its own,
      // and one holding a whole run.
      Reg scratch() override { return registers.new_virtual(regs().gpr, 64); }
      Reg run_scratch(int bits) override { return registers.new_virtual(regs().vec, bits); }

    private:
      mc::Assembler& asm_;
      mc::RegisterInfo& registers;
      Constants* constants;
      const ir::DataLayout& layout;
      const ir::DataLayout plain_layout{};
    };


    // What is emitted where an answer will not fit its type.
    class ValueFault : public Fault {
    public:
      ValueFault(std::string_view what, const ir::Function& func, Span span, const SourceManager* sources,
                 Messages* messages, mc::RegisterInfo& registers, int status = statuses::OVERFLOW)
        : text(describe(what, func.name, span, sources)), status(status), messages(messages), registers(registers)
      {
      }

      // Report the fault and come back, for a build that observes: the call is
      // given everything the convention lets it destroy.
      void observed(mc::Assembler& asm_, Span span)
      {
        if (messages == nullptr)
          throw UnsupportedOperation("an operation that can fault, with nowhere to report it");
        auto symbol = messages->symbol(text);
        const auto& theirs = lookup_cconv(ir::system_cconv);
        asm_.address(theirs.int_arg_regs[0], symbol, span);
        asm_.loadreg(theirs.int_arg_regs[1], MCImm{ __int128(text.size()), 32, false }, span);
        asm_.call(std::string(report_symbol), span, as_regs(destroyed_by(nullptr, theirs, registers, nullptr)));
      }

      // Report the fault and stop; this does not come back.  The runtime
      // follows one settled convention whatever the function reporting it does.
      void out_of_range(mc::Assembler& asm_, Span span) override
      {
        if (messages == nullptr)
          throw UnsupportedOperation("an operation that can fault, with nowhere to report it");
        auto symbol = messages->symbol(text);
        const auto& theirs = lookup_cconv(ir::system_cconv);
        asm_.address(theirs.int_arg_regs[0], symbol, span);
        asm_.loadreg(theirs.int_arg_regs[1], MCImm{ __int128(text.size()), 32, false }, span);
        asm_.loadreg(theirs.int_arg_regs[2], MCImm{ status, 32, false }, span);
        asm_.call(std::string(abort_symbol), span);
      }

    private:
      std::string text;
      int status;
      Messages* messages;
      mc::RegisterInfo& registers;
    };


    // Where one part of a value being put into a result comes from.
    MCOperand part_source(ValueOperands& operands, const ir::Value* value, std::size_t index, const ir::Type* ty,
                          std::size_t how_many, Span span)
    {
      if (how_many == 1)
        return operands.undefined(value, ty, span);
      if (value->vkind == ir::ValueKind::undef)
        return MCImm{ 0, std::max(32, width_of(ty)), false };
      return named(operands.part(value, index, span));
    }

  } // anonymous namespace


  void lower_function(mc::Assembler& asm_, ir::Function& func, const CallConvDesc& cconv,
                      mc::RegisterInfo& registers, Messages* messages, const SourceManager* sources,
                      Constants* constants, const Destroyed* known_clobbers, const ir::DataLayout& layout)
  {
    // The convention is the function's and not the image's.
    asm_.begin_function(ir::symbol_name(func), func.linkage == ir::Linkage::visible, 0,
                        mc::AllocationOrder::per_class(cconv.orders(regs().gpr->name, regs().vec->name)),
                        cconv.callee_saved);
    ValueOperands operands(asm_, registers, constants, layout);
    auto& held = operands.held;
    auto& extra = operands.extra;
    const ir::Value* returned = returned_value(func);
    auto symbol = ir::symbol_name(func);
    auto labels = labels_of(symbol, func);

    auto fault = [&](std::string_view what, Span span, int status = statuses::OVERFLOW) {
      return ValueFault(what, func, span, sources, messages, registers, status);
    };

    // Where in memory an address names, for a read or for a write: a variable
    // by its symbol, any other address by the register holding it.
    auto place_of = [&](const ir::Value* address, std::optional<Span> span) {
      MCMem place;
      if (address->vkind == ir::ValueKind::global) {
        place.disp_sym = mc::sym_expr(asm_.streamer().symbol(symbol_of(static_cast<const ir::GlobalVar*>(address))));
        place.rip_relative = true;
      } else
        place.base = operands.register_of(address, span);
      return place;
    };

    // A register for what `inst` computes, the one the function answers in
    // where that is what becomes of it.
    auto destination_for = [&](const ir::Instruction* inst, const ir::Type* ty) {
      return new_value(ty, registers, layout, inst == returned ? result_register(ty, cconv, registers) : nullptr);
    };

    // The operations of two operands, in the order pypl4g's selection asks
    // about them: a run of elements, a floating-point value, an address, and
    // then by what the operation is.
    auto lower_binary = [&](ir::Instruction* inst) {
      auto span = valid(inst->span);
      const ir::Type* ty = inst->ty;
      ir::BinOp op = inst->bin;
      if (auto vec = ir::as<ir::VecType>(ty)) {
        auto made = new_value(ty, registers, layout);
        held[inst] = made;
        if (is_trapping(op)) {
          try {
            auto told = fault(std::format("{} that does not fit", operation_name(op)), inst->span);
            auto left = operands.operand(inst->operands[0], inst->span);
            auto right = operands.operand(inst->operands[1], inst->span);
            lower_trapping_run(asm_, op, vec, left, right, made, operands, told, layout, inst->span);
          } catch (const Unsupported& unsupported) {
            throw UnsupportedOperation(unsupported.what_, span);
          }
          return;
        }
        if (is_saturating(op)) {
          auto left = operands.operand(inst->operands[0], inst->span);
          auto right = operands.operand(inst->operands[1], inst->span);
          asm_.run_saturating(made, left, right, op == ir::BinOp::sat_add, is_signed_type(vec->element),
                              width_of(vec->element), inst->span);
          return;
        }
        auto operation = operation_for(op);
        if (operation == nullptr)
          throw UnsupportedOperation(std::format("'{}' over a whole run", enum_describe(op)), span);
        auto left = operands.operand(inst->operands[0], inst->span);
        auto right = operands.operand(inst->operands[1], inst->span);
        asm_.run_op(operation, made, left, right, width_of(vec->element), inst->span);
        return;
      }
      if (op == ir::BinOp::wide_mul) {
        // Both halves, the upper one first as the tuple has them.
        auto high = registers.new_virtual(regs().gpr, 64);
        auto low = registers.new_virtual(regs().gpr, 64);
        held[inst] = high;
        extra[inst] = { low };
        auto left = operands.operand(inst->operands[0], inst->span);
        auto right = operands.operand(inst->operands[1], inst->span);
        asm_.wide_product(low, high, left, right, is_signed_type(inst->operands[0]->ty), inst->span);
        return;
      }
      if (is_floating(ty)) {
        auto result = ir::as<ir::ResultType>(ty);
        const ir::Type* answer = result != nullptr ? result->ok : ty;
        auto operation = float_operation(op);
        if (operation == nullptr)
          throw UnsupportedOperation(std::format("'{}' on a floating-point value", enum_describe(op)), span);
        auto made = destination_for(inst, answer);
        held[inst] = made;
        auto divisor = operands.register_for(inst->operands[1], inst->span);
        const mc::VirtReg* failed = nullptr;
        if (result != nullptr) {
          // The one pair a floating-point division has no answer for is a
          // divisor of zero; an overflowing answer is an infinity, which is
          // the fault below and not this.
          failed = new_value(ir::bool_type(), registers, layout,
                             inst == returned ? result_register(ir::bool_type(), cconv, registers, 1) : nullptr);
          extra[inst] = { failed };
          auto zero = operands.floating_number(0.0, answer, inst->span);
          asm_.float_compare(Condition::eq, failed, divisor, named(zero), bits_of(answer), inst->span);
        }
        auto dividend = operands.register_for(inst->operands[0], inst->span);
        asm_.float_op(operation, made, dividend, divisor, bits_of(answer), inst->span);
        if (operation == mc::ops::larger || operation == mc::ops::smaller)
          // They answer one of the two they were given, so there is nothing to
          // ask about what they came to.
          return;
        // An answer that is an infinity or a not-a-number is an answer the
        // operation did not have, and the program stops.
        auto carry_on = asm_.reserve_label("is.finite");
        if (failed != nullptr)
          asm_.branch(Condition::ne, named(failed), zero_immediate, carry_on, inst->span);
        asm_.branch_if_finite(made, bits_of(answer), carry_on, inst->span);
        fault(std::format("{} with no number for an answer", operation_name(op)), inst->span, statuses::NO_ANSWER)
          .out_of_range(asm_, inst->span);
        asm_.block(carry_on);
        return;
      }
      if (ir::is<ir::PtrType>(ty)) {
        // Arithmetic on an address does not go through the checked path: what
        // an address past its place names is not a place.
        auto made = destination_for(inst, ty);
        held[inst] = made;
        auto left = operands.operand(inst->operands[0], inst->span);
        auto right = operands.operand(inst->operands[1], inst->span);
        asm_.op(operation_for(op), made, { left, right }, inst->span);
        return;
      }
      if (is_trapping(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        try {
          auto left = operands.operand(inst->operands[0], inst->span);
          auto right = operands.operand(inst->operands[1], inst->span);
          auto told = fault(std::format("{} that does not fit", operation_name(op)), inst->span);
          lower_trapping(asm_, op, ty, left, right, made, operands, std::max(32, width_of(ty)), told, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (is_saturating(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        try {
          auto left = operands.operand(inst->operands[0], inst->span);
          auto right = operands.operand(inst->operands[1], inst->span);
          lower_saturating(asm_, op, ty, left, right, made, operands, std::max(32, width_of(ty)), inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (is_shift(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        try {
          auto left = operands.operand(inst->operands[0], inst->span);
          auto right = operands.operand(inst->operands[1], inst->span);
          auto told = fault(std::format("{} by more than the width of the type", operation_name(op)), inst->span);
          lower_shift(asm_, op, ty, left, right, made, operands, std::max(32, width_of(ty)), told, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (is_division(op)) {
        // The answer of a division is a result: the number where there is one,
        // and the truth value beside it that says whether there is.
        auto result = ir::as<ir::ResultType>(ty);
        const ir::Type* answer = result != nullptr ? result->ok : ty;
        auto made = destination_for(inst, answer);
        auto failed = new_value(ir::bool_type(), registers, layout,
                                inst == returned ? result_register(ir::bool_type(), cconv, registers, 1) : nullptr);
        held[inst] = made;
        extra[inst] = { failed };
        try {
          auto left = operands.operand(inst->operands[0], inst->span);
          auto right = operands.operand(inst->operands[1], inst->span);
          lower_division_result(asm_, op, answer, left, right, made, failed, operands, std::max(32, width_of(answer)),
                                inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (extremum_condition(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        auto left = operands.operand(inst->operands[0], inst->span);
        auto right = operands.operand(inst->operands[1], inst->span);
        lower_extremum(asm_, op, left, right, made, operands, inst->span);
        return;
      }
      if (is_wrapping(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        auto left = operands.operand(inst->operands[0], inst->span);
        auto right = operands.operand(inst->operands[1], inst->span);
        lower_wrapping(asm_, op, ty, left, right, made, std::max(32, width_of(ty)), inst->span);
        return;
      }
      auto operation = operation_for(op);
      if (operation == nullptr)
        throw UnsupportedOperation(std::format("the operation '{}'", enum_describe(op)), span);
      auto made = destination_for(inst, ty);
      held[inst] = made;
      auto left = operands.operand(inst->operands[0], inst->span);
      auto right = operands.operand(inst->operands[1], inst->span);
      asm_.op(operation, made, { left, right }, inst->span);
    };

    // The operations of one operand: a run of elements, then by what the
    // operation is.
    auto lower_unary = [&](ir::Instruction* inst) {
      auto span = valid(inst->span);
      const ir::Type* ty = inst->ty;
      ir::UnOp op = inst->un;
      if (op == ir::UnOp::mask) {
        auto gathered = ir::as<ir::VecType>(inst->operands[0]->ty);
        if (constants == nullptr) [[unlikely]]
          throw UnsupportedOperation("a mask, with nowhere to put the bits it weighs by", span);
        auto made = destination_for(inst, ty);
        held[inst] = made;
        // The bit each lane stands for, one byte per lane and nought beyond
        // the run, so that what the comparison said about lanes the run does
        // not cover weighs nothing.
        auto weights = registers.new_virtual(regs().vec, 128);
        mc::Bytes pattern;
        for (unsigned lane = 0; lane < 16; ++lane)
          pattern.push_back(lane < gathered->lanes ? std::uint8_t(1u << (lane % 8)) : std::uint8_t(0));
        auto symbol = constants->symbol(pattern, 16);
        MCMem place;
        place.disp_sym = mc::sym_expr(asm_.streamer().symbol(symbol));
        place.rip_relative = true;
        place.size_bits = 128;
        asm_.run_move(Reg(weights), place, 128, inst->span);
        auto weighed = registers.new_virtual(regs().vec, 128);
        asm_.run_op(mc::ops::and_, weighed, named(operands.register_of(inst->operands[0], inst->span), 128),
                    named(weights, 128), 8, inst->span);
        asm_.run_mask(made, named(weighed, 128), int(gathered->lanes), inst->span);
        return;
      }
      if (auto vec = ir::as<ir::VecType>(ty)) {
        if (op != ir::UnOp::not_)
          throw UnsupportedOperation(std::format("'{}' over a whole run", enum_describe(op)), span);
        auto made = new_value(ty, registers, layout);
        held[inst] = made;
        // Every bit of a register set, which this architecture has no constant
        // for and an instruction for.
        auto ones = new_value(ty, registers, layout);
        asm_.run_ones(ones, inst->span);
        auto value = operands.operand(inst->operands[0], inst->span);
        asm_.run_op(mc::ops::xor_, made, value, named(ones, run_bits(vec, layout)), width_of(vec->element),
                    inst->span);
        return;
      }
      if (is_counting(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        const ir::Type* counted = inst->operands[0]->ty;
        auto value = operands.operand(inst->operands[0], inst->span);
        lower_count(asm_, op, value, int(ir::as<ir::IntType>(counted)->bits), is_signed_type(counted), made, operands, inst->span);
        return;
      }
      if (auto how = rounding_of(op)) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        auto value = operands.register_for(inst->operands[0], inst->span);
        asm_.float_round(*how, made, value, bits_of(ty), inst->span);
        return;
      }
      if (op == ir::UnOp::fabs) {
        auto made = destination_for(inst, ty);
        held[inst] = made;
        auto value = operands.register_for(inst->operands[0], inst->span);
        asm_.float_abs(made, value, bits_of(ty), inst->span);
        return;
      }
      const mc::Op* unary = op == ir::UnOp::not_ ? mc::ops::not_ : op == ir::UnOp::neg ? mc::ops::neg : nullptr;
      if (unary == nullptr)
        throw UnsupportedOperation(std::format("the operation '{}'", enum_describe(op)), span);
      auto made = destination_for(inst, ty);
      held[inst] = made;
      auto value = operands.register_for(inst->operands[0], inst->span);
      asm_.op(unary, made, { value }, inst->span);
      // The complement of a narrow unsigned value sets the bits above it,
      // where the value it stands for has zeroes there.
      normalize(asm_, ty, made, std::max(32, width_of(ty)), inst->span);
    };

    // What each call passes where, asked once rather than at the call: the
    // room the arguments that go on the stack are written into is at the
    // bottom of this function's frame, where the stack pointer is at every
    // call, so it is taken before anything else of the frame is.
    auto placed = calls_placed(func, lookup_cconv);

    // A call: placed by the callee's convention, the arguments a parallel copy.
    auto lower_call = [&](ir::Instruction* inst) {
      auto span = valid(inst->span);
      const ir::Type* ty = inst->ty;
      ir::Function* callee = inst->callee;
      // A call through a value: what is called is whatever the register holds,
      // so the convention is the one every function of this language follows.
      ir::Value* through = callee == nullptr ? inst->operands[0] : nullptr;
      auto given = inst->arguments();
      const auto& [placed_by, going] = placed.at(inst);
      const auto& theirs = *placed_by;
      // What goes on the stack is written first, into the room at the bottom
      // of the frame; that reads values and writes no register, so it is no
      // part of the parallel copy.
      std::vector<Move> handed;
      for (std::size_t position = 0; position < given.size(); ++position) {
        const ir::Value* argument = given[position];
        auto pieces = ir::leaves_of(argument->ty);
        if (ir::made_of_parts(argument->ty)) {
          for (std::size_t at = 0; at < pieces.size() && at < going[position].size(); ++at) {
            MCOperand source = named(operands.part(argument, at, span));
            if (auto slot = std::get_if<OnStack>(&going[position][at])) {
              pass_on_stack(asm_, *slot, pieces[at], source, inst->span);
              continue;
            }
            handed.push_back({ as_argument(std::get<const mc::PhysReg*>(going[position][at]), pieces[at], registers),
                               source });
          }
          continue;
        }
        if (auto slot = std::get_if<OnStack>(&going[position].at(0))) {
          pass_on_stack(asm_, *slot, argument->ty, operands.register_for(argument, inst->span), inst->span);
          continue;
        }
        handed.push_back({ as_argument(std::get<const mc::PhysReg*>(going[position].at(0)), argument->ty, registers),
                           operands.operand(argument, inst->span) });
      }
      Reg called;
      if (through != nullptr) {
        // The register the callee is in is read by the call, so it goes into
        // the parallel copy with the arguments.
        called = asm_.temporary(operands.register_of(through, inst->span));
        handed.push_back({ called, operands.operand(through, inst->span) });
      }
      for (const auto& move : sequenced(handed, [&](Reg like) { return asm_.temporary(like); }))
        asm_.loadreg(move.into, move.source, inst->span);
      std::vector<Reg> reads;
      for (const auto& move : handed)
        if (through == nullptr || move.into != called)
          reads.push_back(move.into);
      auto destroyed = as_regs(destroyed_by(through == nullptr ? callee : nullptr, theirs, registers, known_clobbers));
      if (through != nullptr)
        asm_.call(MCOperand(named(called)), inst->span, destroyed, reads);
      else
        asm_.call(ir::symbol_name(*callee), inst->span, destroyed, reads);
      if (ir::made_of_parts(ty)) {
        auto pieces = ir::leaves_of(ty);
        std::vector<const mc::PhysReg*> places;
        try {
          places = result_places(theirs, ty);
        } catch (const TooManyArguments&) {
          throw UnsupportedOperation(
            std::format("a call answering with '{}', which wants more registers than the convention answers in",
                        ty->written()),
            span);
        }
        std::vector<const mc::VirtReg*> taken;
        for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at) {
          auto into = new_value(pieces[at], registers, layout);
          asm_.loadreg(into, named(as_argument(places[at], pieces[at], registers)), inst->span);
          taken.push_back(into);
        }
        held[inst] = taken.at(0);
        extra[inst] = { taken.begin() + 1, taken.end() };
      } else if (ty != ir::void_type()) {
        // Wanted where this function answers if that is what becomes of it,
        // and otherwise where the call left it.
        auto made = new_value(ty, registers, layout, result_register(ty, inst == returned ? cconv : theirs, registers));
        held[inst] = made;
        asm_.loadreg(made, named(result_register(ty, theirs, registers)), inst->span);
      }
    };

    // Every block parameter gets its register before any block is walked: a
    // branch writes the parameters of the block it goes to, which may come
    // later.  The entry block's are the function's own, arriving where the
    // convention says, and copied out at once.
    ir::BasicBlock* entry = func.blocks.empty() ? nullptr : func.blocks.front().get();
    Places arriving;
    if (entry != nullptr) {
      std::vector<const ir::Type*> types;
      for (const auto& param : entry->params)
        types.push_back(param->ty);
      arriving = argument_places(cconv, types, func.attrs.chained);
    }
    // The room for outgoing arguments, at offset nought and as large as the
    // largest call wants it.
    std::int64_t outgoing = 0;
    for (const auto& [_, one] : placed)
      outgoing = std::max(outgoing, stack_bytes(one.second));
    if (outgoing != 0 && asm_.frame_slot(outgoing, 16) != 0) [[unlikely]]
      throw InternalError("the room for outgoing arguments is not at the bottom");
    for (std::size_t index = 0; index < func.blocks.size(); ++index) {
      const auto& block = func.blocks[index];
      for (std::size_t position = 0; position < block->params.size(); ++position) {
        const ir::BlockParam* param = block->params[position].get();
        std::vector<const mc::VirtReg*> given;
        auto pieces = ir::leaves_of(param->ty);
        if (index != 0) {
          if (param->ty == ir::mem_type())
            continue;
          for (auto part : pieces)
            given.push_back(new_value(part, registers, layout));
        } else
          for (std::size_t at = 0; at < pieces.size() && at < arriving[position].size(); ++at) {
            auto place = std::get_if<const mc::PhysReg*>(&arriving[position][at]);
            given.push_back(new_value(pieces[at], registers, layout,
                                      place == nullptr ? nullptr : as_argument(*place, pieces[at], registers)));
          }
        held[param] = given.at(0);
        if (given.size() > 1)
          extra[param] = { given.begin() + 1, given.end() };
      }
    }
    if (entry != nullptr)
      for (std::size_t position = 0; position < entry->params.size(); ++position) {
        const ir::BlockParam* param = entry->params[position].get();
        auto pieces = ir::leaves_of(param->ty);
        for (std::size_t at = 0; at < pieces.size() && at < arriving[position].size(); ++at) {
          if (auto slot = std::get_if<OnStack>(&arriving[position][at])) {
            // Read where the caller left it, in its own frame.
            asm_.incoming(operands.part(param, at, std::nullopt), slot->offset, width_of(pieces[at]),
                          is_signed_type(pieces[at]), func.span);
            continue;
          }
          asm_.loadreg(operands.part(param, at, std::nullopt),
                       named(as_argument(std::get<const mc::PhysReg*>(arriving[position][at]), pieces[at], registers)),
                       func.span);
        }
      }

    for (std::size_t index = 0; index < func.blocks.size(); ++index) {
      const auto& block = func.blocks[index];
      if (index > 0)
        asm_.block(labels[index]);
      for (ir::Instruction* inst : block->insts) {
        auto span = valid(inst->span);
        if (auto refused = cannot_order(inst)) [[unlikely]]
          throw UnsupportedOperation(*refused, inst->span);
        const ir::Type* ty = inst->ty;
        auto vec = ir::as<ir::VecType>(ty);
        switch (inst->kind) {
        case InstKind::FrameInst: {
          // Room of this function's own: the stack pointer and how far in.
          auto made = destination_for(inst, ty);
          held[inst] = made;
          asm_.op(mc::ops::plus, made,
                  { named(regs().sp),
                    MCImm{ asm_.frame_slot(std::int64_t(ir::size_of(inst->held, layout)),
                                           std::int64_t(ir::align_of(inst->held, layout))),
                           32, false } },
                  inst->span);
          break;
        }
        case InstKind::AssertInst: {
          // Where it holds, nothing; where it does not, the program says what
          // was wanted and stops.  Failing is the case that jumps.
          auto holds = asm_.reserve_label("holds");
          auto [condition, lhs, rhs] = condition_of(func, inst, operands, zero_immediate, inst->span);
          asm_.branch(condition, lhs, rhs, holds, inst->span);
          auto told = fault(inst->what, inst->span, inst->status);
          if (inst->observing)
            told.observed(asm_, span.value_or(invalid_span));
          else
            told.out_of_range(asm_, span.value_or(invalid_span));
          asm_.block(holds);
          break;
        }
        case InstKind::MemStartInst:
          // Memory is not held in a register; there is nothing yet to order.
          break;
        case InstKind::CodeInst: {
          // Where a function's code is, as a value.
          auto made = destination_for(inst, ty);
          held[inst] = made;
          asm_.address(made, ir::symbol_name(*inst->callee), inst->span);
          break;
        }
        case InstKind::AddressInst: {
          auto made = destination_for(inst, ty);
          held[inst] = made;
          asm_.address(made, symbol_of(static_cast<const ir::GlobalVar*>(inst->operands[0])), inst->span);
          break;
        }
        case InstKind::LoadInst: {
          const ir::Value* address = inst->operands[1];
          if (vec != nullptr) {
            int bits = run_covers(vec, layout);
            auto made = new_value(ty, registers, layout);
            held[inst] = made;
            auto into = made;
            if (vec->element == ir::bool_type())
              // A truth value is a byte of one or nought in memory and a lane
              // of every bit set or clear in a register.
              into = new_value(ty, registers, layout);
            auto read = place_of(address, span);
            read.size_bits = bits;
            asm_.run_move(Reg(into), read, bits, inst->span);
            if (into != made)
              asm_.run_truths(made, named(into, 128), inst->span);
            break;
          }
          if (ir::made_of_parts(ty)) {
            // One read per part, at the offset the layout gives it.
            auto pieces = ir::leaves_of(ty);
            auto offsets = ir::leaf_offsets_of(ty, layout);
            std::vector<const mc::VirtReg*> taken;
            for (std::size_t at = 0; at < pieces.size() && at < offsets.size(); ++at) {
              auto into = new_value(pieces[at], registers, layout,
                                    inst == returned ? result_register(pieces[at], cconv, registers, at) : nullptr);
              auto read = place_of(address, span);
              read.disp = std::int64_t(offsets[at]);
              read.size_bits = width_of(pieces[at]);
              read.is_signed = is_signed_type(pieces[at]);
              asm_.loadreg(into, read, inst->span);
              taken.push_back(into);
            }
            held[inst] = taken.at(0);
            extra[inst] = { taken.begin() + 1, taken.end() };
            break;
          }
          auto made = destination_for(inst, ty);
          held[inst] = made;
          auto read = place_of(address, span);
          read.size_bits = width_of(ty);
          read.is_signed = is_signed_type(ty);
          if (inst->ordering == ir::Ordering::acquire)
            asm_.acquire(made, read, inst->span);
          else
            asm_.loadreg(made, read, inst->span);
          break;
        }
        case InstKind::StoreInst: {
          const ir::Value* address = inst->operands[1];
          const ir::Value* written = inst->operands[2];
          if (auto run = ir::as<ir::VecType>(written->ty)) {
            auto source = operands.register_of(written, inst->span);
            if (run->element == ir::bool_type()) {
              // And back to a byte of one or nought.
              auto bytes_of = new_value(written->ty, registers, layout);
              asm_.run_truths(bytes_of, named(source, 128), inst->span);
              source = bytes_of;
            }
            auto place = place_of(address, span);
            place.size_bits = run_covers(run, layout);
            asm_.run_store(place, named(source, run_bits(run, layout)), run_covers(run, layout), inst->span);
            break;
          }
          if (ir::made_of_parts(written->ty)) {
            // One write per part, at the offsets a read uses.
            auto pieces = ir::leaves_of(written->ty);
            auto offsets = ir::leaf_offsets_of(written->ty, layout);
            for (std::size_t at = 0; at < pieces.size() && at < offsets.size(); ++at) {
              auto place = place_of(address, span);
              place.disp = std::int64_t(offsets[at]);
              place.size_bits = width_of(pieces[at]);
              place.is_signed = is_signed_type(pieces[at]);
              asm_.store(place, named(operands.part(written, at, span)), inst->span);
            }
            break;
          }
          if (ir::is<ir::FloatType>(written->ty)) {
            // A floating-point value goes from a register of its own kind.
            auto place = place_of(address, span);
            place.size_bits = bits_of(written->ty);
            asm_.store(place, operands.register_for(written, inst->span), inst->span);
            break;
          }
          auto place = place_of(address, span);
          place.size_bits = width_of(written->ty);
          place.is_signed = is_signed_type(written->ty);
          MCOperand put;
          if (auto constant = number_of(written)) {
            bool is_signed = is_signed_type(written->ty);
            put = MCImm{ constant->first, immediate_width(constant->first, is_signed), is_signed };
          } else
            // A narrow store names the whole register and writes as much of it
            // as the width says.
            put = named(operands.register_of(written, span));
          if (inst->ordering == ir::Ordering::release)
            asm_.release(place, put, inst->span);
          else
            asm_.store(place, put, inst->span);
          break;
        }
        case InstKind::SplatInst: {
          auto made = new_value(ty, registers, layout);
          held[inst] = made;
          auto spread = made;
          if (vec->element == ir::bool_type())
            spread = new_value(ty, registers, layout);
          asm_.run_splat(spread, operands.operand(inst->operands[0], inst->span), width_of(vec->element), inst->span);
          if (spread != made)
            // A truth value spread over every lane is a run of them as memory
            // holds one.
            asm_.run_truths(made, named(spread, 128), inst->span);
          break;
        }
        case InstKind::RetInst: {
          if (inst->operands.empty()) {
            asm_.ret(inst->span);
            break;
          }
          const ir::Value* value = inst->operands[0];
          const ir::Type* answered = value->ty;
          auto pieces = ir::leaves_of(answered);
          if (ir::made_of_parts(answered)) {
            // One register per part.
            std::vector<const mc::PhysReg*> places;
            try {
              places = result_places(cconv, answered);
            } catch (const TooManyArguments&) {
              throw UnsupportedOperation(
                std::format("answering with '{}', which wants more registers than the convention answers in",
                            answered->written()),
                span);
            }
            std::vector<Reg> answering;
            for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at)
              answering.push_back(as_argument(places[at], pieces[at], registers));
            for (std::size_t at = 0; at < answering.size(); ++at)
              asm_.loadreg(answering[at], named(operands.part(value, at, span)), inst->span);
            asm_.ret(inst->span, answering);
            break;
          }
          if (ir::is<ir::FloatType>(answered)) {
            auto answer = result_register(answered, cconv, registers);
            asm_.loadreg(answer, operands.register_for(value, inst->span), inst->span);
            asm_.ret(inst->span, { answer });
            break;
          }
          if (! (ir::is<ir::IntType>(answered) || ir::is<ir::BoolType>(answered) || ir::is<ir::CharType>(answered)
                 || ir::is<ir::EnumType>(answered)
                 || ir::is<ir::PtrType>(answered) || ir::is<ir::SetType>(answered)
                 || ir::is<ir::DictType>(answered)))
            throw UnsupportedOperation(std::format("returning a value of type '{}'", answered->written()), span);
          auto result = result_register(answered, cconv, registers);
          if (auto constant = number_of(value))
            asm_.loadreg(result, MCImm{ constant->first, std::max(32, width_of(answered)), is_signed_type(answered) },
                         inst->span);
          else
            asm_.loadreg(result, named(operands.register_of(value, span)), inst->span);
          asm_.ret(inst->span, { result });
          break;
        }
        case InstKind::BinaryInst:
          lower_binary(inst);
          break;
        case InstKind::UnaryInst:
          lower_unary(inst);
          break;
        case InstKind::TupleInst: {
          // Several values made into one that travels together, which here is
          // several registers with nothing between them; a member that is
          // itself several values is that many registers.
          std::vector<const mc::VirtReg*> taken;
          for (auto part : ir::leaves_of(ty))
            taken.push_back(new_value(part, registers, layout));
          held[inst] = taken.at(0);
          extra[inst] = { taken.begin() + 1, taken.end() };
          std::vector<MCOperand> members;
          for (const ir::Value* value : inst->operands) {
            auto leaves = ir::leaves_of(value->ty);
            if (leaves.size() == 1)
              members.push_back(operands.operand(value, inst->span));
            else
              for (std::size_t at = 0; at < leaves.size(); ++at)
                members.push_back(named(operands.part(value, at, valid(inst->span))));
          }
          for (std::size_t at = 0; at < taken.size() && at < members.size(); ++at)
            asm_.loadreg(taken[at], members[at], inst->span);
          break;
        }
        case InstKind::ExtractInst: {
          const ir::Value* whole = inst->operands[0];
          if (ir::is<ir::ResultType>(whole->ty)) {
            // A member of a result, read by its place among the three: the
            // answer, which may be several registers, the truth value after
            // them, and the error after that.
            std::size_t first = inst->index == 0 ? 0 : answer_registers(whole) + std::size_t(inst->index) - 1;
            auto count = ir::leaves_of(ty).size();
            held[inst] = operands.part(whole, first, span);
            if (count > 1) {
              std::vector<const mc::VirtReg*> rest;
              for (std::size_t at = 1; at < count; ++at)
                rest.push_back(operands.part(whole, first + at, span));
              extra[inst] = std::move(rest);
            }
            break;
          }
          // Nothing to emit: the value asked for is already in a register.
          held[inst] = operands.part(whole, std::size_t(inst->index), span);
          break;
        }
        case InstKind::WrapInst: {
          // One value made of several: the answer's own registers, the truth
          // value after them, and what the error carries after that.
          auto result = ir::as<ir::ResultType>(ty);
          const ir::Type* answer = result->ok;
          auto answer_parts = ir::parts_of(answer);
          auto slots = ir::leaves_of(ty);
          bool simple = answer_parts.size() == 1;
          std::vector<const mc::VirtReg*> taken;
          for (std::size_t at = 0; at < slots.size(); ++at)
            taken.push_back(new_value(slots[at], registers, layout,
                                      inst == returned && simple ? result_register(slots[at], cconv, registers, at)
                                                                 : nullptr));
          held[inst] = taken.at(0);
          extra[inst] = { taken.begin() + 1, taken.end() };
          for (std::size_t at = 0; at < answer_parts.size(); ++at)
            asm_.loadreg(taken[at],
                         part_source(operands, inst->operands[0], at, answer_parts[at], answer_parts.size(),
                                     inst->span),
                         inst->span);
          asm_.loadreg(taken[answer_parts.size()], operands.operand(inst->operands[1], inst->span), inst->span);
          if (inst->operands.size() > 2) {
            auto carried = ir::parts_of(result->err);
            for (std::size_t at = 0; at < carried.size(); ++at)
              asm_.loadreg(taken[answer_parts.size() + 1 + at],
                           part_source(operands, inst->operands[2], at, carried[at], carried.size(), inst->span),
                           inst->span);
          }
          break;
        }
        case InstKind::UnwrapInst: {
          // Nothing to emit: the answer is already in registers of its own.
          auto how_many = answer_registers(inst->operands[0]);
          held[inst] = operands.register_of(inst->operands[0], span);
          if (how_many > 1) {
            std::vector<const mc::VirtReg*> rest;
            for (std::size_t at = 1; at < how_many; ++at)
              rest.push_back(operands.part(inst->operands[0], at, span));
            extra[inst] = std::move(rest);
          }
          break;
        }
        case InstKind::FailedInst:
          held[inst] = operands.flag_of(inst->operands[0], span);
          break;
        case InstKind::ErrorInst: {
          // What the error carries is already in registers of its own, after
          // the answer's and the truth value's.
          auto first = answer_registers(inst->operands[0]) + 1;
          held[inst] = operands.part(inst->operands[0], first, span);
          if (ir::made_of_parts(ty)) {
            std::vector<const mc::VirtReg*> rest;
            for (std::size_t at = 1; at < ir::parts_of(ty).size(); ++at)
              rest.push_back(operands.part(inst->operands[0], first + at, span));
            extra[inst] = std::move(rest);
          }
          break;
        }
        case InstKind::CastInst: {
          switch (inst->cast) {
          case ir::CastKind::bitcast: {
            // Nothing to emit: the bits asked for are the bits already in the
            // register.  A constant is put in one first.
            auto found = operands.register_for(inst->operands[0], inst->span);
            held[inst] = std::get<MCReg>(found).reg.virt();
            break;
          }
          case ir::CastKind::zext:
          case ir::CastKind::sext: {
            // This reads the part of the register that is the value and extends
            // it the way the cast says.
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto value = operands.register_for(inst->operands[0], inst->span);
            asm_.widen(made, value, width_of(inst->operands[0]->ty), inst->cast == ir::CastKind::sext, inst->span);
            break;
          }
          case ir::CastKind::trunc: {
            // The same instruction: the low part, extended the way the narrower
            // type says.
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto value = operands.register_for(inst->operands[0], inst->span);
            asm_.widen(made, value, width_of(ty), is_signed_type(ty), inst->span);
            break;
          }
          case ir::CastKind::fext: {
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto value = operands.register_for(inst->operands[0], inst->span);
            asm_.float_extend(made, value, bits_of(inst->operands[0]->ty), bits_of(ty), inst->span);
            break;
          }
          case ir::CastKind::itof: {
            // Every whole number widened to a word by its own sign and
            // converted from there, a `u64` by the conversion that reads a
            // word without one.
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto counted = ir::as<ir::IntType>(inst->operands[0]->ty);
            auto given = operands.register_for(inst->operands[0], inst->span);
            int width = width_of(counted);
            MCReg number{ std::get<MCReg>(given).reg, 64 };
            if (width < 64) {
              number = MCReg{ registers.new_virtual(regs().gpr, 64), 64 };
              asm_.widen(number.reg, given, width, counted->is_signed, inst->span);
            }
            if (counted->is_signed || width < 64)
              asm_.int_to_float(made, number, bits_of(ty), inst->span);
            else
              asm_.unsigned_to_float(made, number, bits_of(ty), inst->span);
            break;
          }
          case ir::CastKind::ftoi: {
            // Toward zero into a whole word, whose low bits are the number.
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto into = ir::as<ir::IntType>(ty);
            auto answer = registers.new_virtual(regs().gpr, 64);
            auto given = operands.register_for(inst->operands[0], inst->span);
            asm_.truncate_to_int(answer, given, bits_of(inst->operands[0]->ty), into->is_signed || width_of(into) < 64,
                                 inst->span);
            asm_.loadreg(made, MCReg{ answer, made->bits }, inst->span);
            break;
          }
          case ir::CastKind::fbits: {
            // The same bits in the other kind of register.
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto given = operands.register_for(inst->operands[0], inst->span);
            int bits = ir::is<ir::FloatType>(ty) ? bits_of(ty) : int(ir::as<ir::IntType>(ty)->bits);
            asm_.move_bits(made, given, bits, inst->span);
            break;
          }
          }
          break;
        }
        case InstKind::SelectInst: {
          // One of two values, which a truth value chooses, with no branch.
          auto made = destination_for(inst, ty);
          held[inst] = made;
          bool floating = ir::is<ir::FloatType>(ty);
          std::vector<MCOperand> chosen;
          for (std::size_t at = 1; at < inst->operands.size(); ++at)
            chosen.push_back(floating ? operands.register_for(inst->operands[at], inst->span)
                                      : operands.operand(inst->operands[at], inst->span));
          auto chooser = operands.register_for(inst->operands[0], inst->span);
          asm_.select(made, chooser, chosen[0], chosen[1], floating, floating ? bits_of(ty) : 64, inst->span);
          break;
        }
        case InstKind::CmpInst: {
          if (vec != nullptr) {
            auto compared = ir::as<ir::VecType>(inst->operands[0]->ty);
            auto made = new_value(ty, registers, layout);
            held[inst] = made;
            auto left = operands.operand(inst->operands[0], inst->span);
            auto right = operands.operand(inst->operands[1], inst->span);
            asm_.run_compare(condition_for(inst->pred), made, left, right, width_of(compared->element), inst->span);
            break;
          }
          if (auto floating = ir::as<ir::FloatType>(inst->operands[0]->ty)) {
            // Never folded into a branch: the answer a not-a-number gives needs
            // more than one reading of the flags.
            auto made = destination_for(inst, ty);
            held[inst] = made;
            auto lhs = operands.register_for(inst->operands[0], inst->span);
            auto rhs = operands.register_for(inst->operands[1], inst->span);
            asm_.float_compare(condition_for(inst->pred), made, lhs, rhs, int(floating->bits), inst->span);
            break;
          }
          // Read exactly once, a comparison is folded into the branch that
          // reads it and nothing is emitted here.
          if (folded_into_branch(func, inst))
            break;
          auto made = destination_for(inst, ty);
          held[inst] = made;
          lower_comparison(asm_, inst, operands, made);
          break;
        }
        case InstKind::SyscallInst: {
          // Everything in it is a machine word by now, and the kernel names the
          // registers.
          const auto& calls = syscalls();
          std::vector<Move> handed;
          handed.push_back({ registers.view(calls.number.phys()->unit, 64),
                             operands.operand(inst->operands[0], span.value_or(invalid_span)) });
          for (std::size_t at = 1; at < inst->operands.size(); ++at)
            handed.push_back(
              { calls.arguments[at - 1], operands.operand(inst->operands[at], span.value_or(invalid_span)) });
          for (const auto& move : sequenced(handed, [&](Reg like) { return asm_.temporary(like); }))
            asm_.loadreg(move.into, move.source, span.value_or(invalid_span));
          std::vector<Reg> clobbers{ calls.answer };
          clobbers.insert(clobbers.end(), calls.clobbers.begin(), calls.clobbers.end());
          std::vector<Reg> reads;
          for (const auto& move : handed)
            reads.push_back(move.into);
          asm_.kernel(calls.enter, span.value_or(invalid_span), clobbers, reads);
          auto taken_back = new_value(ty, registers, layout);
          held[inst] = taken_back;
          asm_.loadreg(taken_back, named(calls.answer), span.value_or(invalid_span));
          break;
        }
        case InstKind::CallInst:
          lower_call(inst);
          break;
        case InstKind::UnreachableInst:
          asm_.op(mc::ops::trap, Reg{}, {}, inst->span);
          break;
        case InstKind::BrInst:
        case InstKind::CondBrInst:
        case InstKind::SwitchInst:
          try {
            lower_branch(asm_, func, labels, index, inst, operands, zero_immediate);
          } catch (const UnsupportedBranch& unsupported) {
            throw UnsupportedOperation(unsupported.what_, valid(unsupported.span));
          }
          break;
        default:
          throw UnsupportedOperation(std::format("the instruction '{}'", inst->opcode()), span);
        }
      }
    }
    asm_.end_function();
  }
} // namespace pl4g::target::aarch64
