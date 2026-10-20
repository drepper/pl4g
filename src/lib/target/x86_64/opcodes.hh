// The x86-64 encoding table: pypl4g's target/x86_64/opcodes.py.
#pragma once

#include <span>

#include "target/x86_64/desc.hh"


namespace pl4g::target::x86_64 {

  // Every row, in the order the table is searched: the first that takes the
  // operands is the one chosen.
  std::span<const mc::InstDesc* const> x86_instrs();

} // namespace pl4g::target::x86_64
