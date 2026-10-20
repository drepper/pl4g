// Laying fragments out within their sections.  A port of pypl4g/mc/layout.py.
//
// Offsets are assigned first and only then are fixups resolved, so emission
// never waits for a symbol to be defined.  Instructions whose encoding can
// grow are re-encoded until nothing changes.
#pragma once

#include <cstdint>
#include <functional>
#include <span>

#include "mc/fragment.hh"


namespace pl4g::mc {

  // Re-encodes an instruction fragment at an address, answering whether its
  // size changed.
  using Relaxer = std::function<bool(MCInstFragment& fragment, std::uint64_t vaddr)>;

  // Round `value` up to the next multiple of `alignment`.
  constexpr std::uint64_t align_up(std::uint64_t value, std::uint64_t alignment) noexcept
  {
    if (alignment <= 1)
      return value;
    auto remainder = value % alignment;
    return remainder == 0 ? value : value + alignment - remainder;
  }

  // Assign every fragment of `section` its offset; answers the total size.
  std::uint64_t layout_section(MCSection& section);
  // Turn each symbol's fragment position into a byte offset and a size.
  void resolve_symbol_offsets(MCSection& section, std::span<MCSymbol* const> symbols);
  // Give every defined symbol the address its section and offset imply.
  void place_symbols(std::span<MCSection* const> sections, std::span<MCSymbol* const> symbols);
  // Re-encode growable instructions until every displacement fits; answers
  // how many rounds it took.
  int relax(std::span<MCSection* const> sections, const Relaxer& relaxer, int max_rounds = 8);
  // Patch every fixup of every fragment, now that addresses are known.
  void apply_fixups(std::span<MCSection* const> sections,
                    const std::function<void(MCFragment& fragment, std::uint64_t vaddr)>& patcher);
  // The bytes of a laid-out section.
  Bytes section_bytes(const MCSection& section);

} // namespace pl4g::mc
