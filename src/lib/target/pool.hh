// Constants that have to be in memory rather than in an instruction: the
// floating-point ones, read from the image by an address computed from the
// program counter.  A port of pypl4g/target/pool.py.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <utility>

#include "mc/asmbuilder.hh"
#include "util/ordered.hh"


namespace pl4g::target {

  // Where they go.
  inline constexpr std::string_view pool_section = ".rodata";

  // The constants a compilation had to put in memory, by their bytes: two
  // with the same bytes are one constant.
  struct Constants {
    OrderedMap<mc::Bytes, std::pair<std::string, std::uint64_t>> by_bytes;

    // The symbol `data` is stored under, adding it if it is new.
    std::string symbol(const mc::Bytes& data, std::uint64_t alignment);
    // Put every one of them into the image.
    void emit(mc::Assembler& asm_) const;
  };

} // namespace pl4g::target
