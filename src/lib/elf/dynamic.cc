// The tables a shared object hands the loader.
#include "elf/dynamic.hh"


namespace pl4g::elf {

  namespace {

    // Append the `bytes` low bytes of `value`, the least significant first.
    void put(std::vector<std::uint8_t>& out, std::uint64_t value, int bytes)
    {
      for (int at = 0; at < bytes; ++at)
        out.push_back(std::uint8_t(value >> (8 * at)));
    }

  } // anonymous namespace


  std::uint32_t elf_hash(std::string_view name) noexcept
  {
    std::uint32_t value = 0;
    for (unsigned char byte : name) {
      value = (value << 4) + byte;
      auto high = value & 0xF0000000u;
      if (high != 0)
        value ^= high >> 24;
      value &= ~high;
    }
    return value;
  }


  std::vector<std::uint8_t> hash_table(std::span<const std::string> names)
  {
    auto count = std::uint32_t(names.size());
    std::vector<std::uint32_t> buckets(count, 0), chains(count, 0);
    for (std::uint32_t index = 1; index < count; ++index) {
      auto bucket = elf_hash(names[index]) % count;
      chains[index] = buckets[bucket];
      buckets[bucket] = index;
    }
    std::vector<std::uint8_t> out;
    out.reserve(hash_size(count));
    put(out, count, 4);
    put(out, count, 4);
    for (auto one : buckets)
      put(out, one, 4);
    for (auto one : chains)
      put(out, one, 4);
    return out;
  }


  void Relocation::pack_into(std::vector<std::uint8_t>& out) const
  {
    put(out, offset, 8);
    put(out, (std::uint64_t(symbol) << 32) | kind, 8);
    put(out, std::uint64_t(addend), 8);
  }


  void pack_dynamic_entry(std::vector<std::uint8_t>& out, std::int64_t tag, std::uint64_t value)
  {
    put(out, std::uint64_t(tag), 8);
    put(out, value, 8);
  }

} // namespace pl4g::elf
