// The RISC-V 64-bit encoding table: pypl4g's target/riscv64/opcodes.py.
//
// Each row is a 32-bit template plus the bits its operands occupy.  Several
// rows are what the architecture calls a pseudo-instruction -- `li`, `mv`,
// `ret` -- written as rows of their own because that is how code generation
// asks for them; the template carries the fixed operand.
#pragma once

#include <cstdint>
#include <span>

#include "mc/desc.hh"
#include "mc/fixedwidth.hh"
#include "mc/ops.hh"


namespace pl4g::target::riscv64 {

  // What padding is filled with, so that falling into it traps.
  inline constexpr std::uint8_t pad_byte = 0x00;

  // The range of the twelve-bit signed immediate most instructions carry.
  inline constexpr std::int64_t imm12_min = -2048;
  inline constexpr std::int64_t imm12_max = 2047;

  // Every row, in the order pypl4g's table has them.
  std::span<const mc::InstDesc* const> instructions();

  // The table selection asks, built once.
  const mc::InstrTable& table();

  // Enters the kernel: the call number in a7, the arguments from a0 on.
  const mc::Op* environment_call();

} // namespace pl4g::target::riscv64
