// The hash a table's keys are hashed with, worked out while compiling.
//
// pypl4g's sema/rapidhash.py computes in Python what the runtime's `pl4g_hash`
// does; here the runtime is linked into the library, so the compiler asks it
// and the two cannot drift apart.
#pragma once

#include <cstdint>
#include <span>

#include "rt/runtime.hh"


namespace pl4g::sema {

  // The hash of `data` with `seed`, as `pl4g_hash` answers it (`rapidhash`).
  inline std::uint64_t rapidhash(std::span<const unsigned char> data, std::uint64_t seed) noexcept
  {
    return ::pl4g_hash(data.data(), data.size(), seed);
  }

  // The hash of a key that is one word: its eight bytes as they are in memory,
  // little-endian (`word_hash`).
  inline std::uint64_t word_hash(std::uint64_t word, std::uint64_t seed) noexcept
  {
    unsigned char bytes[8];
    for (unsigned i = 0; i < 8; ++i)
      bytes[i] = static_cast<unsigned char>(word >> (8 * i));
    return rapidhash(bytes, seed);
  }

} // namespace pl4g::sema
