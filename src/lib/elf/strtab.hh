// String tables, a string sharing the bytes of an earlier one it is a suffix
// of.  A port of pypl4g/elf/strtab.py.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "util/ordered.hh"


namespace pl4g::elf {

  // Builds an ELF string table.
  class StringTable {
  public:
    StringTable();

    // The offset of `text`, appending it if it is not there yet.
    std::uint32_t add(std::string_view text);
    // The number of bytes the table occupies.
    std::uint64_t size() const noexcept { return data.size(); }
    const std::vector<std::uint8_t>& bytes_of() const noexcept { return data; }

  private:
    std::vector<std::uint8_t> data{ 0 };
    OrderedMap<std::string, std::uint32_t> offsets;
  };

} // namespace pl4g::elf
