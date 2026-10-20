// The AArch64 relocations, and how they are stored.  A port of
// pypl4g/target/aarch64/fixups.py.
//
// A relocation here is not a field of its own: the value goes into bits of an
// instruction word that already holds the opcode and the registers.  Storing
// one is therefore a read, an insert and a write back, and never an overwrite.
#pragma once

#include "mc/fixup.hh"
#include "target/runtime.hh"


namespace pl4g::target::aarch64 {

  // An unconditional branch or a call: a signed offset in units of four
  // bytes, measured from the instruction, in the low twenty-six bits.
  extern const mc::FixupKind branch26;
  // A conditional branch or a compare-and-branch: nineteen bits at bit five.
  extern const mc::FixupKind branch19;
  // The page an address lies in, relative to the page the instruction lies in,
  // and the offset of an address within its page for the add that follows.
  extern const mc::FixupKind adr_page21;
  extern const mc::FixupKind add_lo12;
  // The same offset for a load or a store that follows the page, in units of
  // what it touches: one kind per width.
  extern const mc::FixupKind ldst8_lo12;
  extern const mc::FixupKind ldst16_lo12;
  extern const mc::FixupKind ldst32_lo12;
  extern const mc::FixupKind ldst64_lo12;
  extern const mc::FixupKind ldst128_lo12;

  // Store a computed fixup value into the instruction word that holds it.
  void apply_fixup(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value);

  // Every kind a packaged patch may name, by the name the patch carries.
  const FixupKinds& by_name();

} // namespace pl4g::target::aarch64
