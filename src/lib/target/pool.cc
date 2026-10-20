// Constants that have to be in memory rather than in an instruction.
#include "target/pool.hh"
#include "target/emit.hh"

#include <algorithm>


namespace pl4g::target {

  std::string Constants::symbol(const mc::Bytes& data, std::uint64_t alignment)
  {
    if (auto found = by_bytes.get(data))
      return found->first;
    auto name = ".Lconst." + std::to_string(by_bytes.size());
    by_bytes[data] = { name, alignment };
    return name;
  }


  void Constants::emit(mc::Assembler& asm_) const
  {
    if (by_bytes.empty())
      return;
    std::uint64_t widest = 0;
    for (const auto& [_, one] : by_bytes)
      widest = std::max(widest, one.second);
    asm_.section(std::string(pool_section), placed(false, widest));
    for (const auto& [data, one] : by_bytes) {
      asm_.align(one.second);
      auto symbol = asm_.label(one.first, mc::SymBinding::local, mc::SymKind::object, mc::SymVisibility::hidden);
      asm_.bytes(data);
      asm_.end_label(symbol);
    }
  }

} // namespace pl4g::target
