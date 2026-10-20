// String tables.
#include "elf/strtab.hh"


namespace pl4g::elf {

  StringTable::StringTable()
  {
    offsets[""] = 0;
  }


  std::uint32_t StringTable::add(std::string_view text)
  {
    std::string key(text);
    if (auto found = offsets.get(key))
      return *found;
    // The first string added that ends in this one, as the table is walked in
    // the order things were added to it.
    for (const auto& [known, offset] : offsets)
      if (known.ends_with(text) && known != text) {
        auto shared = std::uint32_t(offset + known.size() - text.size());
        offsets[key] = shared;
        return shared;
      }
    auto offset = std::uint32_t(data.size());
    data.insert(data.end(), text.begin(), text.end());
    data.push_back(0);
    offsets[key] = offset;
    return offset;
  }

} // namespace pl4g::elf
