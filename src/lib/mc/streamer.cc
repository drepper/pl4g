// The streamer.
#include "mc/streamer.hh"

#include <algorithm>


namespace pl4g::mc {

  std::vector<MCFixup>* fixups_of(MCFragment& fragment) noexcept
  {
    if (fragment.kind == MCFragment::Kind::data)
      return &static_cast<MCDataFragment&>(fragment).fixups;
    if (fragment.kind == MCFragment::Kind::inst)
      return &static_cast<MCInstFragment&>(fragment).fixups;
    return nullptr;
  }


  Bytes* patched_bytes_of(MCFragment& fragment) noexcept
  {
    if (fragment.kind == MCFragment::Kind::data)
      return &static_cast<MCDataFragment&>(fragment).contents;
    if (fragment.kind == MCFragment::Kind::inst)
      return &static_cast<MCInstFragment&>(fragment).encoded;
    return nullptr;
  }


  MCSection* MCStreamer::get_section(const std::string& name, const SectionOptions& options)
  {
    if (auto found = sections.get(name))
      return found->get();
    auto made = std::make_unique<MCSection>();
    made->name = name;
    made->executable = options.executable;
    made->writable = options.writable;
    made->alignment = options.alignment;
    made->alloc = options.alloc;
    made->sh_type = options.sh_type;
    made->sh_link_to = options.sh_link_to;
    made->sh_entsize = options.sh_entsize;
    MCSection* raw = made.get();
    sections.emplace(name, std::move(made));
    return raw;
  }


  MCSymbol* MCStreamer::symbol(const std::string& name)
  {
    if (auto found = symbols.get(name))
      return found->get();
    auto made = std::make_unique<MCSymbol>();
    made->name = name;
    MCSymbol* raw = made.get();
    symbols.emplace(name, std::move(made));
    return raw;
  }


  MCSymbol* MCStreamer::define_symbol(const std::string& name, SymBinding binding, SymKind kind, bool temporary,
                                      SymVisibility visibility)
  {
    auto sym = symbol(name);
    sym->section = current;
    sym->fragment_index = current->fragments.size();
    sym->defined = true;
    sym->binding = binding;
    sym->visibility = visibility;
    sym->kind = kind;
    sym->temporary = temporary;
    defined_order.push_back(sym);
    return sym;
  }


  void MCStreamer::set_symbol_size(MCSymbol* sym)
  {
    sym->end_fragment_index = sym->section->fragments.size();
  }


  MCInstFragment* MCStreamer::emit_inst(const InstRef& inst)
  {
    auto [encoded, fixups] = encode(*inst);
    auto fragment = std::make_unique<MCInstFragment>();
    fragment->inst = inst;
    fragment->encoded = std::move(encoded);
    fragment->fixups = std::move(fixups);
    return append(std::move(fragment));
  }


  void MCStreamer::emit_insts(std::span<const InstRef> insts)
  {
    for (const auto& inst : insts)
      emit_inst(inst);
  }


  MCDataFragment* MCStreamer::emit_bytes(Bytes data, std::vector<MCFixup> fixups)
  {
    auto fragment = std::make_unique<MCDataFragment>();
    fragment->contents = std::move(data);
    fragment->fixups = std::move(fixups);
    return append(std::move(fragment));
  }


  MCAlignFragment* MCStreamer::emit_align(std::uint64_t alignment, std::uint8_t fill)
  {
    auto fragment = std::make_unique<MCAlignFragment>();
    fragment->alignment = alignment;
    fragment->fill = fill;
    auto made = append(std::move(fragment));
    current->alignment = std::max(current->alignment, alignment);
    return made;
  }


  MCPaddingFragment* MCStreamer::emit_padding(std::uint64_t reserved, std::uint8_t fill)
  {
    auto fragment = std::make_unique<MCPaddingFragment>();
    fragment->reserved = reserved;
    fragment->fill = fill;
    return append(std::move(fragment));
  }


  std::vector<MCSymbol*> MCStreamer::undefined_symbols() const
  {
    std::vector<MCSymbol*> found;
    for (const auto& [_, sym] : symbols)
      if (! sym->defined)
        found.push_back(sym.get());
    return found;
  }


  std::vector<MCSection*> MCStreamer::section_list() const
  {
    std::vector<MCSection*> all;
    for (const auto& [_, section] : sections)
      all.push_back(section.get());
    return all;
  }


  std::vector<MCSymbol*> MCStreamer::symbol_list() const
  {
    std::vector<MCSymbol*> all;
    for (const auto& [_, sym] : symbols)
      all.push_back(sym.get());
    return all;
  }

} // namespace pl4g::mc
