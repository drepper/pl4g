// Small things every shared emitter builds the same way: where emission goes,
// and a memory operand of a base and a displacement.
#pragma once

#include <cstdint>

#include "diag/engine.hh"
#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // The options of a section that is allocated, of the given permissions and
  // alignment.
  inline mc::SectionOptions placed(bool writable, std::uint64_t alignment, bool executable = false)
  {
    mc::SectionOptions options;
    options.writable = writable;
    options.alignment = alignment;
    options.executable = executable;
    return options;
  }

  // `bits` of memory `disp` bytes from where `base` points.
  inline mc::MCMem memory(mc::Reg base, std::int64_t disp, int bits)
  {
    mc::MCMem made;
    made.base = base;
    made.disp = disp;
    made.size_bits = bits;
    return made;
  }

} // namespace pl4g::target
