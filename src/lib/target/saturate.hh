// The saturating, trapping, wrapping, dividing and shifting operations,
// written once for all three targets: what differs between them is behind
// `Assembler::clamp`, `widen` and the tables each back end answers for
// itself.  A port of pypl4g/target/saturate.py.
#pragma once

#include <optional>
#include <string_view>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "target/scratch.hh"


namespace pl4g::target {

  // The operations that clamp, that stop the program instead, that answer
  // with the low bits whatever they came to, that move bits sideways, and that
  // divide.
  bool is_saturating(ir::BinOp op) noexcept;
  bool is_trapping(ir::BinOp op) noexcept;
  bool is_wrapping(ir::BinOp op) noexcept;
  bool is_shift(ir::BinOp op) noexcept;
  bool is_division(ir::BinOp op) noexcept;
  // The larger and the smaller of two, and the comparison each is asked with.
  std::optional<mc::Condition> extremum_condition(ir::BinOp op) noexcept;
  // What each of them is called where a message has to say which went wrong.
  std::string_view operation_name(ir::BinOp op) noexcept;
  // Which ordinary operation each is built from.
  const mc::Op* plain_op(ir::BinOp op);

  // Emit the saturating operation `op` on `ty` into `destination`;
  // `register_bits` is how wide the register holding such a value is.
  void lower_saturating(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                        const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits,
                        Span span);

  // Emit the ordinary operation `op` on `ty`, which stops the program where
  // the answer will not fit.
  void lower_trapping(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits,
                      Fault& fault, Span span);

  // Emit a division or a remainder, a divisor of nought and the one signed pair
  // whose quotient does not fit asked about first.
  void lower_division(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits,
                      Fault& fault, Fault& divide_by_zero, Span span);

  // Emit a division that answers with a result rather than stopping: `failed`
  // says whether there is an answer, and the answer is nought where not.
  void lower_division_result(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                             const mc::MCOperand& right, mc::Reg destination, mc::Reg failed, Scratch& scratch,
                             int register_bits, Span span);

  // Emit the operation with nothing asked afterwards, the answer put back into
  // its type where that is narrower than the register.
  void lower_wrapping(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, int register_bits, Span span);

  // Emit the larger or the smaller of two values.
  void lower_extremum(mc::Assembler& asm_, ir::BinOp op, const mc::MCOperand& left, const mc::MCOperand& right,
                      mc::Reg destination, Scratch& scratch, Span span);

  // Emit a shift or a rotation, with the distance checked first -- or, for the
  // wrapping forms, taken modulo the width instead.
  void lower_shift(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& value,
                   const mc::MCOperand& amount, mc::Reg destination, Scratch& scratch, int register_bits,
                   Fault& too_far, Span span);

} // namespace pl4g::target
