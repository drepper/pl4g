// The x86-64 encoder: pypl4g's target/x86_64/encoder.py.
//
// One generic function walks a fixed sequence of phases over a table row:
// legacy prefixes, then the prefix carrying the register extensions, then the
// opcode map escape and the opcode, then ModRM with its SIB byte and
// displacement, then the immediate or the branch displacement.
#pragma once

#include <utility>
#include <vector>

#include "mc/fixedwidth.hh"
#include "mc/fixup.hh"
#include "mc/inst.hh"


namespace pl4g::target::x86_64 {

  // An instruction cannot be encoded as written: the error every target's
  // encoder throws.
  using EncodingError = mc::EncodingError;

  // Encode one instruction into bytes and the fixups it leaves behind.
  std::pair<mc::Bytes, std::vector<mc::MCFixup>> encode(const mc::MCInst& inst);

} // namespace pl4g::target::x86_64
