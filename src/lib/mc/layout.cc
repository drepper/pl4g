// Laying fragments out within their sections.
#include "mc/layout.hh"

#include <algorithm>
#include <stdexcept>


namespace pl4g::mc {

  std::uint64_t layout_section(MCSection& section)
  {
    std::uint64_t offset = 0;
    for (auto& fragment : section.fragments) {
      if (fragment->kind == MCFragment::Kind::align) {
        auto& align = static_cast<MCAlignFragment&>(*fragment);
        align.set_size(align_up(offset, align.alignment) - offset);
      }
      fragment->offset = offset;
      offset += fragment->size();
    }
    section.size = offset;
    return offset;
  }


  void resolve_symbol_offsets(MCSection& section, std::span<MCSymbol* const> symbols)
  {
    // The byte offset of the fragment at `index`, or the section's end.
    auto offset_at = [&](std::optional<std::size_t> index) -> std::optional<std::uint64_t> {
      if (! index)
        return std::nullopt;
      if (*index >= section.fragments.size())
        return section.size;
      return section.fragments[*index]->offset;
    };
    for (auto symbol : symbols) {
      if (! symbol->defined || symbol->section != &section)
        continue;
      auto start = offset_at(symbol->fragment_index);
      if (! start)
        continue;
      symbol->offset = *start;
      if (auto end = offset_at(symbol->end_fragment_index))
        symbol->size = *end > *start ? *end - *start : 0;
    }
  }


  void place_symbols(std::span<MCSection* const>, std::span<MCSymbol* const> symbols)
  {
    for (auto symbol : symbols)
      if (symbol->defined && symbol->section != nullptr)
        symbol->vaddr = symbol->section->vaddr + symbol->offset;
  }


  int relax(std::span<MCSection* const> sections, const Relaxer& relaxer, int max_rounds)
  {
    for (int round = 1; round <= max_rounds; ++round) {
      bool changed = false;
      for (auto section : sections)
        layout_section(*section);
      for (auto section : sections)
        for (auto& fragment : section->fragments)
          if (fragment->kind == MCFragment::Kind::inst) {
            auto& inst = static_cast<MCInstFragment&>(*fragment);
            if (inst.relaxable && relaxer(inst, section->vaddr + inst.offset))
              changed = true;
          }
      if (! changed)
        return round;
    }
    throw std::runtime_error("instruction relaxation did not converge");
  }


  void apply_fixups(std::span<MCSection* const> sections,
                    const std::function<void(MCFragment& fragment, std::uint64_t vaddr)>& patcher)
  {
    for (auto section : sections)
      for (auto& fragment : section->fragments)
        if (fragment->kind == MCFragment::Kind::inst || fragment->kind == MCFragment::Kind::data)
          patcher(*fragment, section->vaddr + fragment->offset);
  }


  Bytes section_bytes(const MCSection& section)
  {
    Bytes out(section.size, 0);
    for (const auto& fragment : section.fragments) {
      auto data = fragment->bytes_of();
      std::ranges::copy(data, out.begin() + std::ptrdiff_t(fragment->offset));
    }
    return out;
  }

} // namespace pl4g::mc
