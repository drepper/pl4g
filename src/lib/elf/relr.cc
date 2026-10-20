// ELF's compact table of relative relocations.
#include "elf/relr.hh"


namespace pl4g::elf {

  namespace {

    // How many places one bitmap covers, and how far that reaches.
    constexpr std::uint64_t bits_per_bitmap = 63;
    constexpr std::uint64_t word = 8;

  } // anonymous namespace


  std::vector<std::uint64_t> encode_relr(std::span<const std::uint64_t> addresses)
  {
    std::vector<std::uint64_t> entries;
    std::size_t at = 0;
    while (at < addresses.size()) {
      // An address, and then bitmaps for as long as places follow closely
      // enough for one to cover them.
      entries.push_back(addresses[at]);
      std::uint64_t base = addresses[at] + word;
      ++at;
      while (true) {
        std::uint64_t bitmap = 0;
        for (; at < addresses.size(); ++at) {
          auto delta = addresses[at] - base;
          if (delta >= bits_per_bitmap * word || delta % word != 0)
            break;
          bitmap |= std::uint64_t(1) << (delta / word);
        }
        if (bitmap == 0)
          break;
        entries.push_back((bitmap << 1) | 1);
        base += bits_per_bitmap * word;
      }
    }
    return entries;
  }


  std::vector<std::uint64_t> decode_relr(std::span<const std::uint64_t> entries)
  {
    std::vector<std::uint64_t> places;
    std::uint64_t where = 0;
    for (auto entry : entries) {
      if ((entry & 1) == 0) {
        places.push_back(entry);
        where = entry + word;
        continue;
      }
      for (std::uint64_t bit = 0; (entry >>= 1) != 0; ++bit)
        if ((entry & 1) != 0)
          places.push_back(where + bit * word);
      where += bits_per_bitmap * word;
    }
    return places;
  }

} // namespace pl4g::elf
