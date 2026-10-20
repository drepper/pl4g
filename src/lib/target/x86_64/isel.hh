// Instruction selection for x86-64: pypl4g's target/x86_64/isel.py.
//
// The builder speaks in three-address form with the destination first; x86-64
// instructions take two operands, so this is where that difference is
// resolved.  Every builder call turns into table rows, and the table decides
// which encoding is shortest.
#pragma once

#include <optional>
#include <stdexcept>
#include <string>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"
#include "mc/desc.hh"
#include "source/manager.hh"
#include "target/callconv.hh"
#include "target/faults.hh"
#include "target/pool.hh"


namespace pl4g::target::x86_64 {

  // The back end has no selection rule for this operation yet.
  struct UnsupportedOperation : std::runtime_error {
    std::string detail;
    std::optional<Span> span;
    UnsupportedOperation(std::string detail, std::optional<Span> span = std::nullopt)
      : std::runtime_error(detail), detail(std::move(detail)), span(span) {}
  };


  // Turns builder calls into x86-64 instructions.
  class X86Selector final : public mc::InstructionSelector {
  public:
    // `rounds` says whether the level has the rounding instruction (SSE4.1),
    // `counts_ones` and `counts_leading` whether it promises `popcnt` and
    // `lzcnt`.
    X86Selector(const mc::InstrTable& table, bool rounds = true, bool counts_ones = false,
                bool counts_leading = false)
      : table(table), rounds(rounds), counting_ones(counts_ones), counting_leading(counts_leading) {}

    mc::Insts select_move(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_op(const mc::Op* op, mc::Reg dst, std::span<const mc::MCOperand> sources, Span span) override;
    mc::Insts select_store(const mc::MCMem& address, const mc::MCOperand& value, Span span) override;
    mc::Insts select_acquire(mc::Reg dst, const mc::MCMem& address, Span span) override;
    mc::Insts select_release(const mc::MCMem& address, const mc::MCOperand& value, Span span) override;
    mc::Insts select_call(const mc::MCOperand& target, Span span) override;
    mc::Insts select_return(Span span) override;
    mc::Insts select_jump(const mc::MCSymRef& target, Span span) override;
    mc::Insts select_branch(mc::Condition cond, const mc::MCOperand& lhs, const mc::MCOperand& rhs,
                            const mc::MCSymRef& target, Span span) override;
    std::set<int> flagged_widths() const override { return { 8, 16, 32, 64 }; }
    mc::Insts select_op_at(const mc::Op* op, mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right,
                           int bits, Span span) override;
    mc::Insts select_branch_if_in_range(const mc::Op* op, bool is_signed, const mc::MCSymRef& target,
                                        Span span) override;
    mc::Insts select_run_move(const std::variant<mc::Reg, mc::MCMem>& dst, const mc::MCOperand& src, int bits,
                              Span span) override;
    mc::Insts select_run_op(const mc::Op* op, mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right,
                            int bits, Span span) override;
    mc::Insts select_run_splat(mc::Reg dst, const mc::MCOperand& src, int bits, Span span) override;
    mc::Insts select_run_saturating(mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right, bool adding,
                                    bool is_signed, int bits, Span span) override;
    mc::Insts select_run_any_lane(mc::Reg dst, const mc::MCOperand& src, int stride, int lanes, Span span) override;
    mc::Insts select_run_ones(mc::Reg dst, Span span) override;
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
    mc::Insts select_float_op(const mc::Op* op, mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right,
                              int bits, Span span) override;
    mc::Insts select_float_compare(mc::Condition cond, mc::Reg dst, const mc::MCOperand& lhs,
                                   const mc::MCOperand& rhs, int bits, Span span) override;
    mc::Insts select_shift(const mc::Op* op, mc::Reg dst, const mc::MCOperand& value, const mc::MCOperand& amount,
                           int bits, Span span) override;
    bool counts_ones() const override { return counting_ones; }
    bool counts_leading() const override { return counting_leading; }
    mc::Insts select_count_ones(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_count_leading(mc::Reg dst, const mc::MCOperand& src, Span span) override;
    mc::Insts select_divide(mc::Reg dst, const mc::MCOperand& left, const mc::MCOperand& right, bool is_signed,
                            bool remainder, int bits, Span span) override;
    mc::Insts select_wide_product(mc::Reg low, mc::Reg high, const mc::MCOperand& left, const mc::MCOperand& right,
                                  bool is_signed, Span span) override;
    std::int64_t link_slot_size() override { return 0; }
    std::pair<std::optional<std::int64_t>, std::int64_t> frame_walk(std::int64_t total, std::int64_t size,
                                                                    std::int64_t link) override;
    mc::Insts select_save_link(std::int64_t offset, Span span) override;
    mc::Insts select_restore_link(std::int64_t offset, Span span) override;
    mc::Insts select_spill(std::int64_t slot, mc::Reg source, Span span) override;
    mc::Insts select_reload(mc::Reg destination, std::int64_t slot, Span span) override;
    mc::Insts select_frame(std::int64_t size, Span span) override;
    mc::Insts select_unframe(std::int64_t size, Span span) override;

  private:
    mc::InstRef inst(std::string_view mnemonic, std::vector<mc::MCOperand> operands, Span span) const;
    mc::Insts select_float_move(mc::Reg dst, const mc::MCOperand& src, Span span);
    mc::Insts select_load(mc::Reg dst, const mc::MCMem& src, Span span);
    std::pair<mc::Insts, std::vector<mc::MCOperand>> accepting(std::string_view mnemonic,
                                                               std::vector<mc::MCOperand> operands, Span span);
    std::pair<mc::Insts, std::vector<mc::MCOperand>> accepting_pair(std::string_view mnemonic, mc::Reg dst,
                                                                    std::span<const mc::MCOperand> sources,
                                                                    Span span);
    mc::Insts two_address(std::string_view mnemonic, mc::Reg dst, const mc::MCOperand& lhs,
                          const mc::MCOperand& rhs, Span span);
    bool fits_a_store(const mc::MCMem& address, const mc::MCImm& value) const;
    mc::Insts run_two_or_three(std::string_view mnemonic, mc::Reg dst, const mc::MCOperand& left,
                               const mc::MCOperand& right, Span span);
    mc::Insts select_compare(mc::MCOperand lhs, mc::MCOperand rhs, mc::Condition cond, Span span);
    std::pair<mc::MCReg, mc::Insts> in_register(const mc::MCOperand& operand, int bits, Span span);

    const mc::InstrTable& table;
    bool rounds;
    bool counting_ones;
    bool counting_leading;
  };


  // Build the machine form of one IR function.  `messages`, `constants` and
  // `known_clobbers` are the image's: what a fault says, the constants read
  // out of it, and what each function built so far turned out to destroy.
  void lower_function(mc::Assembler& asm_, ir::Function& func, const CallConvDesc& cconv,
                      mc::RegisterInfo& registers, Messages* messages, const SourceManager* sources,
                      Constants* constants, const Destroyed* known_clobbers, const ir::DataLayout& layout);

} // namespace pl4g::target::x86_64
