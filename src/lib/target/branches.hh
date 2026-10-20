// Turning the branches of the representation into machine branches, written
// once: which way round a branch is written, and what a condition is.  What
// differs -- which instruction tests a condition -- is behind
// `Assembler::branch`.  A port of pypl4g/target/branches.py.
#pragma once

#include <functional>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // A branch shape no back end can yet lower, for the back end to report with
  // its own span.
  struct UnsupportedBranch : std::runtime_error {
    std::string what_;
    Span span;
    UnsupportedBranch(std::string what, Span span) : std::runtime_error(what), what_(std::move(what)), span(span) {}
  };

  // What each comparison of the representation tests, in the assembler's terms.
  mc::Condition condition_for(ir::CmpPred pred) noexcept;

  // How a back end turns a value of the representation into an operand.
  class Operands {
  public:
    virtual ~Operands() = default;
    // The operand for `value`, a constant being an immediate.
    virtual mc::MCOperand value(ir::Value* value, Span span) = 0;
    // The operand for `value`, put in a register if it is not in one.
    virtual mc::MCOperand in_register(ir::Value* value, Span span) = 0;
    // The register a value written rather than read -- a block parameter -- is
    // computed into.
    virtual mc::Reg destination(ir::Value* value) = 0;
    // The register holding one of a value's several parts.
    virtual mc::Reg part_of(ir::Value* value, std::size_t index, std::optional<Span> span) = 0;
  };

  // The label the block at `index` of `symbol` is known by, and every block's.
  std::string block_label(std::string_view symbol, std::size_t index, const ir::BasicBlock& block);
  std::vector<std::string> labels_of(std::string_view symbol, const ir::Function& func);

  // Whether `value` is read by exactly one instruction of `func`, and whether
  // it is a comparison the branch reading it absorbs.
  bool condition_used_once(const ir::Function& func, const ir::Value* value);
  bool folded_into_branch(const ir::Function& func, const ir::Value* value);

  // Compute the truth value of the comparison `inst` into `destination`.
  void lower_comparison(mc::Assembler& asm_, const ir::Instruction* inst, Operands& operands, mc::Reg destination);

  // Emit the machine form of one block's terminator; false where it is one
  // this does not handle.  `zero` is what a condition that is a value is
  // compared with.
  bool lower_branch(mc::Assembler& asm_, const ir::Function& func, std::span<const std::string> labels,
                    std::size_t index, const ir::Instruction* terminator, Operands& operands, const mc::MCImm& zero);

  // One register a branch writes, and what it writes there.
  struct Move {
    mc::Reg into;
    mc::MCOperand source;
  };

  // The same moves in an order in which none reads what another has written,
  // a cycle broken by a copy into `spare`.
  std::vector<Move> sequenced(std::span<const Move> moves, const std::function<mc::Reg(mc::Reg)>& spare);

  // What a branch, or a check, tests, and the two things it tests.
  std::tuple<mc::Condition, mc::MCOperand, mc::MCOperand> condition_of(const ir::Function& func,
                                                                       const ir::Instruction* terminator,
                                                                       Operands& operands, const mc::MCImm& zero,
                                                                       Span span);

} // namespace pl4g::target
