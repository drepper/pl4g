// How an AArch64 instruction is encoded.  A port of
// pypl4g/target/aarch64/desc.py and encoder.py.
//
// Every instruction is one 32-bit word, so the description is the shared
// fixed-width one: a template plus the bits each operand occupies.  What is
// specific here is the width, and the relocations (fixups.hh).
#pragma once

#include <utility>
#include <vector>

#include "mc/fixedwidth.hh"


namespace pl4g::target::aarch64 {

  // Every instruction is one word.
  inline constexpr int instruction_size = 4;

  // One row of the AArch64 encoding table.
  using A64InstDesc = mc::FixedWidthInstDesc;

  // Encode one instruction into its word and the fixups it leaves behind.
  inline std::pair<mc::Bytes, std::vector<mc::MCFixup>> encode(const mc::MCInst& inst)
  {
    return mc::encode_fixed_width(inst, instruction_size);
  }

} // namespace pl4g::target::aarch64
