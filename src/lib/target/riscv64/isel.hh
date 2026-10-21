// Instruction selection for RISC-V: pypl4g's target/riscv64/isel.py.
//
// The builder speaks in three-address form with the destination first, which is
// what this architecture's instructions already are.  There is one register
// width, so a narrow value is a whole register, sign extended, and it is the
// instruction that says how wide the operation is.
#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"
#include "source/manager.hh"
#include "target/callconv.hh"
#include "target/faults.hh"
#include "target/pool.hh"
#include "target/riscv64/isa.hh"


namespace pl4g::target::riscv64 {

  // The back end has no selection rule for this yet.
  struct UnsupportedOperation : std::runtime_error {
    std::string detail;
    std::optional<Span> span;
    UnsupportedOperation(std::string detail, std::optional<Span> span = std::nullopt)
      : std::runtime_error(detail), detail(std::move(detail)), span(span) {}
  };


  // Turns builder calls into RISC-V instructions.
  class RVSelector final : public mc::InstructionSelector {
  public:
    explicit RVSelector(isa::ISA built_for) : isa(std::move(built_for)) {}

    // What the program is built for; the extensions the code generator can
    // use are asked of it.
    isa::ISA isa;
    // The register the function being lowered keeps to itself, where it works
    // on runs; nothing in a function that does not.
    const mc::PhysReg* spare = nullptr;

    mc::Insts select_move(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_op(const mc::Op* op, mc::Reg dst, std::span<const mc::MCOperand> sources, Span span) override;
    mc::Insts select_acquire(mc::Reg dst, const mc::MCMem& address, Span span) override;
    mc::Insts select_release(const mc::MCMem& address, const mc::MCOperand& value, Span span) override;
    mc::Insts select_store(const mc::MCMem& address, const mc::MCOperand& value, Span span) override;
    mc::Insts select_call(const mc::MCOperand& target, Span span) override;
    mc::Insts select_return(Span span) override;
    mc::Insts select_jump(const mc::MCOperand& target, Span span) override;
    mc::Insts select_branch(mc::Condition cond, const mc::MCOperand& lhs, const mc::MCOperand& rhs,
                            const mc::MCSymRef& target, Span span) override;
    mc::Insts select_set(mc::Condition cond, mc::Reg dst, const mc::MCOperand& lhs, const mc::MCOperand& rhs,
                         Span span) override;
    mc::Insts select_address(mc::Reg dst, const mc::MCSymRef& symbol, Span span) override;
    mc::Insts select_widen(mc::Reg dst, const mc::MCOperand& src, int bits, bool is_signed, Span span) override;
    mc::Insts select_clamp(mc::Condition cond, mc::Reg dst, const mc::MCOperand& lhs, const mc::MCOperand& rhs,
                           const mc::MCOperand& bound, Span span) override;
    mc::Insts select_select(mc::Reg dst, const mc::MCOperand& chooser, const mc::MCOperand& if_true,
                            const mc::MCOperand& if_false, bool floating, int bits, Span span) override;
    mc::Insts select_float_abs(mc::Reg dst, const mc::MCOperand& src, int bits, Span span) override;
    mc::Insts select_float_extend(mc::Reg dst, const mc::MCOperand& src, int from_bits, int to_bits,
                                  Span span) override;
    mc::Insts select_branch_if_finite(mc::Reg value, int bits, const mc::MCSymRef& target, Span span) override;
    mc::Insts select_float_round(mc::Rounding how, mc::Reg dst, const mc::MCOperand& src, int bits,
                                 Span span) override;
    mc::Insts select_float_to_int(mc::Rounding how, mc::Reg dst, const mc::MCOperand& src, int bits,
                                  Span span) override;
    mc::Insts select_int_to_float(mc::Reg dst, const mc::MCOperand& src, int bits, Span span) override;
    mc::Insts select_unsigned_to_float(mc::Reg dst, const mc::MCOperand& src, int bits, Span span) override;
    mc::Insts select_truncate_to_int(mc::Reg dst, const mc::MCOperand& src, int bits, bool is_signed,
                                     Span span) override;
    mc::Insts select_move_bits(mc::Reg dst, const mc::MCOperand& src, int bits, Span span) override;
    // Both conversions read and answer an unsigned doubleword as well.
    bool converts_unsigned() const override { return true; }
    // The three counts are instructions where the program is built for Zbb, as
    // the application profiles are, and not in the base.
    bool counts_ones() const override { return isa.has("zbb"); }
    bool counts_leading() const override { return isa.has("zbb"); }
    bool counts_trailing() const override { return isa.has("zbb"); }
    mc::Insts select_count_ones(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_count_leading(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_count_trailing(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_float_copysign(mc::Reg dst, const mc::MCOperand& magnitude, const mc::MCOperand& sign,
                                    int bits, Span span) override;
    mc::Insts select_float_op(const mc::Op* op, mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right,
                              int bits, Span span) override;
    mc::Insts select_float_compare(mc::Condition cond, mc::Reg dst, const mc::MCOperand& lhs,
                                   const mc::MCOperand& rhs, int bits, Span span) override;
    mc::Insts select_shift(const mc::Op* op, mc::Reg dst, const mc::MCOperand& value, const mc::MCOperand& amount,
                           int bits, Span span) override;
    mc::Insts select_wide_product(mc::Reg low, mc::Reg high, const mc::MCOperand& left, const mc::MCOperand& right,
                                  bool is_signed, Span span) override;
    mc::Insts select_divide(mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right, bool is_signed,
                            bool remainder, int bits, Span span) override;
    std::int64_t link_slot_size() override { return 16; }
    // How large a frame of `total` bytes is made, the stack aligned as the
    // system's convention says.
    std::int64_t frame_size(std::int64_t total, bool calls) override;
    std::pair<std::optional<std::int64_t>, std::int64_t> frame_walk(std::int64_t total, std::int64_t size,
                                                                    std::int64_t link) override;
    mc::Insts select_save_link(std::int64_t offset, Span span) override;
    mc::Insts select_restore_link(std::int64_t offset, Span span) override;
    mc::Insts select_spill(std::int64_t slot, mc::Reg source, Span span) override;
    mc::Insts select_reload(mc::Reg destination, std::int64_t slot, Span span) override;
    mc::Insts select_frame(std::int64_t size, Span span) override;
    mc::Insts select_unframe(std::int64_t size, Span span) override;

    // -- runs, where the build has the vector extension ------------------------
    // Read `size` bytes of a run into `dst`, and write them out of `src`.
    mc::Insts select_vector_load(mc::Reg dst, const mc::MCMem& place, int size, Span span);
    mc::Insts select_vector_store(const mc::MCMem& place, mc::Reg src, int size, Span span);
    // A run of truth values held as bytes made a mask, and a mask made bytes.
    mc::Insts select_truths_in(mc::Reg dst, mc::Reg src, int lanes, Span span);
    mc::Insts select_truths_out(mc::Reg dst, mc::Reg src, int lanes, Span span);
    // One value of `bits` in each of `lanes` elements, and one truth value in
    // every lane of a mask.
    mc::Insts select_vector_splat(mc::Reg dst, const mc::MCOperand& src, int lanes, int bits, Span span);
    mc::Insts select_truth_splat(mc::Reg dst, const mc::MCOperand& src, int lanes, Span span);
    // One of the bitwise three over `count` bytes, or over the lanes of two
    // masks where `truths` says that is what they are; and the complement.
    mc::Insts select_vector_op(const mc::Op* op, mc::Reg dst, mc::Reg left, mc::Reg right, int count, bool truths,
                               Span span);
    mc::Insts select_vector_not(mc::Reg dst, mc::Reg src, int count, bool truths, Span span);
    // Compare `lanes` bytes of two runs into a mask, or two masks.
    mc::Insts select_vector_compare(mc::Condition cond, mc::Reg dst, mc::Reg left, mc::Reg right, int lanes,
                                    bool truths, Span span);
    // The first `lanes` bits of a mask in an ordinary register.
    mc::Insts select_vector_mask(mc::Reg dst, mc::Reg src, int lanes, Span span);

  private:
    mc::InstRef inst(std::string_view mnemonic, std::vector<mc::MCOperand> operands, Span span) const;
    mc::Insts float_move(mc::Reg dst, const mc::MCOperand& src, Span span);
    mc::Insts materialize(mc::Reg dst, __int128 value, Span span);
    mc::Insts load(mc::Reg dst, const mc::MCMem& src, Span span);
    std::pair<mc::Insts, std::vector<mc::MCOperand>> accepting(std::string_view mnemonic,
                                                               std::vector<mc::MCOperand> operands, Span span);
    mc::Insts float_store(const mc::MCMem& address, const mc::MCReg& value, int width, Span span);
    std::pair<mc::MCReg, mc::Insts> as_register(const mc::MCOperand& operand, Span span);
    std::tuple<mc::Insts, mc::MCReg, std::int64_t> in_frame(std::int64_t offset, Span span);
    mc::Insts frame_word(std::string_view mnemonic, mc::Reg reg, std::int64_t offset, Span span);
    mc::InstRef vset(int count, int bits, Span span);
    std::pair<mc::Insts, mc::MCReg> address(const mc::MCMem& place, Span span);
    mc::Insts added(const mc::MCReg& dst, const mc::MCReg& base, std::int64_t offset, Span span);
  };


  // Build the machine form of one IR function.
  // `works_on_runs` says the function did before the runs were brought down
  // to what the machine has: it then keeps one register to itself.
  void lower_function(mc::Assembler& asm_, const ir::Function& func, const CallConvDesc& cconv,
                      mc::RegisterInfo& registers, Messages* messages, const SourceManager* sources,
                      Constants* constants, const Destroyed* known_clobbers, const ir::DataLayout& layout,
                      bool works_on_runs = false);

} // namespace pl4g::target::riscv64
