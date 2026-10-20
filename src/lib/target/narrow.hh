// How a value narrower than a register is held, and what has to keep it so: a
// value of an integer type of width w is held with every bit above w the zero-
// or sign-extension of the value.  A port of pypl4g/target/narrow.py.
#pragma once

#include "ir/types.hh"
#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // Put the bits above a narrow unsigned value back to zero, as a complement
  // needs; nothing where there are none.
  void normalize(mc::Assembler& asm_, const ir::Type* ty, mc::Reg destination, int register_bits, Span span);

} // namespace pl4g::target
