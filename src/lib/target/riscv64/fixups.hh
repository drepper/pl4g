// The RISC-V relocations, and how they are stored: pypl4g's
// target/riscv64/fixups.py.
//
// The architecture scatters the bits of a jump offset across the instruction
// word, which is why storing a relocated value is the target's business.
#pragma once

#include <cstdint>

#include "mc/fixup.hh"
#include "target/runtime.hh"


namespace pl4g::target::riscv64 {

  // Every instruction of the base set is one word.
  inline constexpr int instruction_size = 4;

  // A jump or a call: a signed offset of twenty-one bits from the instruction.
  extern const mc::FixupKind jal_fixup;
  // A conditional branch: the same over thirteen bits, scattered differently.
  extern const mc::FixupKind branch_fixup;
  // The upper twenty bits of a PC-relative address, and the lower twelve for
  // the second instruction of the pair -- measured from the first -- in an
  // I-type and in a store.
  extern const mc::FixupKind pcrel_hi20;
  extern const mc::FixupKind pcrel_lo12_i;
  extern const mc::FixupKind pcrel_lo12_s;
  // An `auipc` and the `jalr` after it, one patch of two words.
  extern const mc::FixupKind call_fixup;

  // How far the second instruction of the pair sits after the first.
  inline constexpr std::int64_t pcrel_pair_distance = instruction_size;

  // Store a computed fixup value into the instruction word that holds it.
  void apply_fixup(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value);

  // Every kind a packaged patch of the runtime may name, by that name.
  const FixupKinds& by_name();

} // namespace pl4g::target::riscv64
