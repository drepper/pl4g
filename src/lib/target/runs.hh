// Arithmetic over a whole run of elements, and asking whether any lane went
// past: there are no flags for sixteen lanes, so the question is asked of the
// answer, every lane at once, and the top bit of each lane gathered.  A port of
// pypl4g/target/runs.py.
#pragma once

#include <cstdint>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"
#include "target/scratch.hh"


namespace pl4g::target {

  // Do `op` to every lane at once and stop the program if any went past.
  void lower_trapping_run(mc::Assembler& asm_, ir::BinOp op, const ir::VecType* ty, const mc::MCOperand& left,
                          const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, Fault& fault,
                          const ir::DataLayout& layout, Span span);

  // Which bytes of a register hold the answer for a lane the run covers: a
  // mask with the top byte of each lane set.
  std::uint64_t top_bytes(std::uint64_t stride, std::uint64_t lanes, int bits);

} // namespace pl4g::target
