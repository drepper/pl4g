// A debugging dump of the symbolic representation, exactly as pypl4g writes
// it (`--emit asm`).  A dump, not a syntax: nothing parses it.  A port of
// pypl4g/mc/dump.py.
#pragma once

#include <span>
#include <string>

#include "mc/fragment.hh"


namespace pl4g::mc {

  // Render every mapped section the program is made of.
  std::string dump_sections(std::span<MCSection* const> sections, std::span<MCSymbol* const> symbols);

} // namespace pl4g::mc
