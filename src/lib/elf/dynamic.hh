// The tables a shared object hands the loader.  A port of
// pypl4g/elf/dynamic.py.
//
// A shared object is loaded at an address chosen when it is loaded, so it is
// linked at nought and says, in a relocation per word, which of its words hold
// an address the load address is added to.  It says what it offers and what it
// needs in a symbol table of its own -- the dynamic one, which is the only one a
// loader reads -- with a hash table to look a name up by, and it says where all
// of these are in the dynamic section, which is what the loader finds first.
//
// Everything here is a pure function of what the writer planned: the bytes are
// packed once every address is known, and their sizes are known before any is.
#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>


namespace pl4g::elf {

  // The System V hash of `name`, which is what a `DT_HASH` table is keyed by.
  std::uint32_t elf_hash(std::string_view name) noexcept;

  // How many bytes the hash table of `count` symbols takes, the null one among
  // them: a bucket per symbol, and a chain entry per symbol.
  constexpr std::uint64_t hash_size(std::uint64_t count) noexcept
  {
    return 4 * (2 + 2 * count);
  }

  // The hash table of the dynamic symbols called `names`, the null one first:
  // as many buckets as symbols, each symbol put at the head of its bucket's
  // chain, in order.
  std::vector<std::uint8_t> hash_table(std::span<const std::string> names);

  // One word the loader writes: where, how, which symbol and what to add.
  struct Relocation {
    std::uint64_t offset = 0;
    std::uint32_t kind = 0;
    std::uint32_t symbol = 0;
    std::int64_t addend = 0;

    // Append the entry as the table holds it.
    void pack_into(std::vector<std::uint8_t>& out) const;
  };

  // Append one entry of the dynamic section.
  void pack_dynamic_entry(std::vector<std::uint8_t>& out, std::int64_t tag, std::uint64_t value);

} // namespace pl4g::elf
