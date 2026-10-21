// Counting the bits of a value -- how many are set, how many zeroes stand
// above the highest that is and below the lowest -- with the instruction where
// there is one and without a branch where there is none.  A port of pypl4g/target/bitcount.py.
#pragma once

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "target/scratch.hh"


namespace pl4g::target {

  // How wide the register the counting is done in is.
  inline constexpr int count_width = 64;

  // Whether `op` is one of the three operations this answers for.
  inline bool is_counting(ir::UnOp op) noexcept
  {
    return op == ir::UnOp::count_ones || op == ir::UnOp::count_leading || op == ir::UnOp::count_trailing;
  }

  // Emit the count `op` asks for over a value of `bits` bits into
  // `destination`, counted in a whole register and moved in at the end.
  void lower_count(mc::Assembler& asm_, ir::UnOp op, const mc::MCOperand& value, int bits, bool is_signed,
                   mc::Reg destination, Scratch& scratch, Span span);

} // namespace pl4g::target
