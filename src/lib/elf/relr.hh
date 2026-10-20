// ELF's compact table of relative relocations, SHT_RELR: what a
// position-independent image says it has to add its load address to.
//
// Each entry is a word.  An even one is the address of an eight-byte place
// to fix, and the next place after it is where the bitmaps that follow go on
// from; an odd one is a bitmap of the 63 places after that, its lowest bit the
// mark that says it is one, bit k+1 standing for the place k words on.  Every
// place is eight bytes and eight-byte aligned, which is the only relocation
// the table can say -- and the only one a position-independent image here has.
#pragma once

#include <cstdint>
#include <span>
#include <vector>


namespace pl4g::elf {

  // The table for the places at `addresses`, which are sorted and each a
  // multiple of eight -- encoded as lld and glibc encode it.
  std::vector<std::uint64_t> encode_relr(std::span<const std::uint64_t> addresses);

  // The places the table `entries` names, in order: the inverse of the above.
  std::vector<std::uint64_t> decode_relr(std::span<const std::uint64_t> entries);

} // namespace pl4g::elf
