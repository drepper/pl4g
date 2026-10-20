// The AArch64 encoding table.  A port of pypl4g/target/aarch64/opcodes.py.
//
// Each row is a 32-bit template plus the bits its operands occupy.  The
// templates were checked against the GNU assembler, and pypl4g's tests keep
// checking them.
#pragma once

#include <cstdint>
#include <span>

#include "mc/desc.hh"


namespace pl4g::target::aarch64 {

  // The word the architecture reserves as permanently undefined, which is what
  // padding is filled with, so that falling into padding traps.
  inline constexpr std::uint32_t udf_word = 0x00000000;
  inline constexpr std::uint8_t pad_byte = 0x00;

  // Every row, in the order the table writes them.
  std::span<const mc::InstDesc* const> aarch64_instrs();

} // namespace pl4g::target::aarch64
