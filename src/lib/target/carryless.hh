// The product of two words without carries, with the instruction where there
// is one and ordinary multiplications arranged around the carries where there
// is none.  A port of pypl4g/target/carryless.py.
#pragma once

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "target/scratch.hh"


namespace pl4g::target {

  // Whether `op` is the operation this answers for.
  inline bool is_carryless(ir::BinOp op) noexcept
  {
    return op == ir::BinOp::clmul;
  }

  // Emit the product of `left` and `right` without carries, its lower half
  // into `low` and its upper half into `high`.
  void lower_carryless(mc::Assembler& asm_, mc::Reg low, mc::Reg high, const mc::MCOperand& left,
                       const mc::MCOperand& right, Scratch& scratch, Span span);

} // namespace pl4g::target
