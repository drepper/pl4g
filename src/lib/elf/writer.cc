// Writing the ELF image.
#include "elf/writer.hh"

#include <algorithm>

#include "elf/const.hh"
#include "elf/structs.hh"
#include "mc/layout.hh"
#include "util/fdio.hh"


namespace pl4g::elf {

  namespace {

    std::uint8_t binding_of(mc::SymBinding binding) noexcept
    {
      switch (binding) {
      case mc::SymBinding::local: return stb_local;
      case mc::SymBinding::global: return stb_global;
      case mc::SymBinding::weak: return stb_weak;
      }
      return stb_local;
    }

    std::uint8_t visibility_of(mc::SymVisibility visibility) noexcept
    {
      switch (visibility) {
      case mc::SymVisibility::default_: return stv_default;
      case mc::SymVisibility::internal: return stv_internal;
      case mc::SymVisibility::hidden: return stv_hidden;
      case mc::SymVisibility::protected_: return stv_protected;
      }
      return stv_default;
    }

    std::uint8_t kind_of(mc::SymKind kind) noexcept
    {
      switch (kind) {
      case mc::SymKind::notype: return stt_notype;
      case mc::SymKind::func: return stt_func;
      case mc::SymKind::object: return stt_object;
      case mc::SymKind::file: return stt_file;
      case mc::SymKind::section: return stt_notype;
      }
      return stt_notype;
    }

  } // anonymous namespace


  ElfWriter::ElfWriter(ImageSettings settings, std::span<mc::MCSection* const> given,
                       std::span<mc::MCSymbol* const> symbols, std::vector<std::string> source_paths,
                       mc::FixupApplier apply_fixup)
    : settings(std::move(settings)),
      symbols(symbols.begin(), symbols.end()),
      source_paths(std::move(source_paths)),
      apply_fixup(std::move(apply_fixup))
  {
    for (auto section : given)
      if (! section->fragments.empty())
        sections.push_back(section);
  }


  // -- planning ---------------------------------------------------------------

  ImageLayout ElfWriter::plan()
  {
    if (settings.kind == ImageKind::pie) [[unlikely]]
      throw ImageError("a position-independent executable is not made yet");
    shstrtab = StringTable();
    strtab = StringTable();
    ImageLayout layout;
    layout.kind = settings.kind;
    layout.machine = settings.machine;
    layout.base_vaddr = settings.base_vaddr;
    layout.page_size = settings.page_size;
    layout.entry_symbol = settings.entry_symbol;
    for (auto section : sections) {
      mc::layout_section(*section);
      mc::resolve_symbol_offsets(*section, symbols);
    }

    // Mapped sections are grouped by the permissions they need, since a
    // segment carries one set of permissions for all of it; a group that
    // needs nothing gets no segment.
    std::vector<std::pair<std::vector<mc::MCSection*>, std::uint32_t>> groups;
    for (auto [executable, writable, flags] : { std::tuple(false, false, pf_r), std::tuple(true, false, pf_r | pf_x),
                                               std::tuple(false, true, pf_r | pf_w) }) {
      auto found = mapped(executable, writable);
      if (! found.empty())
        groups.emplace_back(std::move(found), flags);
    }
    std::size_t placed = 0;
    for (const auto& [group, _] : groups)
      placed += group.size();
    if (placed != std::size_t(std::ranges::count_if(sections, [](auto s) { return s->alloc; }))) [[unlikely]]
      // The only combination left is writable and executable, which nothing
      // here produces and no loader should be asked to grant.
      throw ImageError("a mapped section asks for permissions no segment gives");
    std::uint64_t phnum = groups.size() + 1;
    std::uint64_t offset = ehdr_size + phnum * phdr_size;

    add_chunk(layout, "ehdr", ehdr_size, 1, 0);
    add_chunk(layout, "phdrs", phnum * phdr_size, 1, ehdr_size);

    // The first group follows the headers in the file and its segment starts
    // at the start of the file, so that the headers are mapped; every later
    // group starts on a page of its own.
    std::vector<SegmentPlan> loads;
    auto page = settings.page_size;
    auto bias = settings.base_vaddr;
    for (const auto& [group, flags] : groups) {
      if (! loads.empty()) {
        auto end_vaddr = loads.back().p_vaddr + loads.back().p_memsz;
        std::uint64_t widest = 0;
        for (auto section : group)
          widest = std::max(widest, section->alignment);
        offset = align_up(offset, widest);
        bias = align_up(end_vaddr, page) + (offset % page) - offset;
      }
      offset = place_group(layout, group, offset, bias, flags, page, loads);
    }
    place_symbols();
    layout.functions = function_extents(layout);

    std::vector<SymbolPlan> planned;
    if (settings.with_symbols)
      planned = symbol_plans(layout, strtab);
    layout.symbols = planned;

    if (settings.with_symbols) {
      // Sections that take no room when the program runs and that something
      // emitted rather than this writer, before the tables, so that their
      // names are among the names the table of names holds.
      for (auto section : sections)
        if (! section->alloc)
          offset = add_nonalloc(layout, section->name, section->sh_type, section->size,
                                std::max<std::uint64_t>(section->alignment, 1), offset);
      for (const char* name : { ".shstrtab", ".symtab", ".strtab" })
        shstrtab.add(name);
      for (const auto& plan : layout.sections)
        shstrtab.add(plan.name);

      offset = add_nonalloc(layout, ".shstrtab", sht_strtab, shstrtab.size(), 1, offset);
      offset = add_nonalloc(layout, ".symtab", sht_symtab, planned.size() * sym_size, 8, offset, sym_size);
      offset = add_nonalloc(layout, ".strtab", sht_strtab, strtab.size(), 1, offset);
      link_symtab(layout, planned);
      offset = align_up(offset, 8);
      add_chunk(layout, "shdrs", (layout.sections.size() + 1) * shdr_size, 8, offset);
      offset += (layout.sections.size() + 1) * shdr_size;
    }

    layout.segments = loads;
    // Its presence without the executable bit is what makes the stack not
    // executable; how much room it says is what the program was built to want.
    layout.segments.push_back(SegmentPlan{ pt_gnu_stack, pf_r | pf_w, 0x10, 0, 0, 0, settings.stack_size });
    layout.entry_vaddr = entry_vaddr();
    layout.total_size = offset;
    return layout;
  }


  std::vector<mc::MCSection*> ElfWriter::mapped(bool executable, bool writable) const
  {
    std::vector<mc::MCSection*> found;
    for (auto section : sections)
      if (section->alloc && section->executable == executable && section->writable == writable)
        found.push_back(section);
    return found;
  }


  std::uint64_t ElfWriter::place_group(ImageLayout& layout, std::span<mc::MCSection* const> group,
                                       std::uint64_t offset, std::uint64_t bias, std::uint32_t flags,
                                       std::uint64_t page_size, std::vector<SegmentPlan>& loads)
  {
    if (group.empty())
      return offset;
    auto start = offset;
    for (auto section : group) {
      auto align = std::max<std::uint64_t>(section->alignment, 1);
      offset = align_up(offset, align);
      SectionPlan plan;
      plan.name = section->name;
      plan.sh_type = section->sh_type;
      plan.sh_flags = section_flags(*section);
      plan.sh_addralign = align;
      plan.alloc = true;
      plan.sh_entsize = section->sh_entsize;
      plan.offset = offset;
      plan.addr = offset + bias;
      plan.size = section->size;
      section->vaddr = plan.addr;
      layout.sections.push_back(plan);
      add_chunk(layout, section->name, section->size, align, offset);
      offset += section->size;
    }
    // The first segment begins at the start of the file so that the headers
    // are mapped with it; a later one begins where its own sections do.
    auto segment_start = loads.empty() ? 0 : start;
    auto size = offset - segment_start;
    loads.push_back(SegmentPlan{ pt_load, flags, page_size, segment_start, segment_start + bias, size, size });
    return offset;
  }


  std::uint64_t ElfWriter::section_flags(const mc::MCSection& section)
  {
    auto flags = shf_alloc;
    if (section.executable)
      flags |= shf_execinstr;
    if (section.writable)
      flags |= shf_write;
    return flags;
  }


  void ElfWriter::add_chunk(ImageLayout& layout, const std::string& name, std::uint64_t size, std::uint64_t align,
                            std::uint64_t offset)
  {
    layout.chunks.push_back(Chunk{ name, size, align, 0, offset, std::nullopt });
    layout.by_name[name] = layout.chunks.size() - 1;
  }


  std::uint64_t ElfWriter::add_nonalloc(ImageLayout& layout, const std::string& name, std::uint32_t sh_type,
                                        std::uint64_t size, std::uint64_t align, std::uint64_t offset,
                                        std::uint64_t entsize)
  {
    offset = align_up(offset, align);
    SectionPlan plan;
    plan.name = name;
    plan.sh_type = sh_type;
    plan.sh_addralign = align;
    plan.sh_entsize = entsize;
    plan.offset = offset;
    plan.size = size;
    layout.sections.push_back(plan);
    add_chunk(layout, name, size, align, offset);
    return offset + size;
  }


  void ElfWriter::link_symtab(ImageLayout& layout, std::span<const SymbolPlan> planned)
  {
    OrderedMap<std::string, std::size_t> indices;
    for (std::size_t i = 0; i < layout.sections.size(); ++i)
      indices[layout.sections[i].name] = i + 1;
    std::size_t first_global = planned.size();
    for (std::size_t i = 0; i < planned.size(); ++i)
      if (planned[i].binding != stb_local) {
        first_global = i;
        break;
      }
    for (auto& plan : layout.sections)
      if (plan.name == ".symtab") {
        plan.sh_link = std::uint32_t(*indices.get(".strtab"));
        plan.sh_info = std::uint32_t(first_global);
      }
    // A section that said which other one its contents point into is linked
    // to it here, where the indices are finally known.
    for (auto section : sections)
      if (! section->sh_link_to.empty())
        for (auto& plan : layout.sections)
          if (plan.name == section->name)
            plan.sh_link = std::uint32_t(indices.get_or(section->sh_link_to, 0));
  }


  void ElfWriter::place_symbols()
  {
    for (auto symbol : symbols)
      if (symbol->defined && symbol->section != nullptr)
        symbol->vaddr = symbol->section->vaddr + symbol->offset;
  }


  std::uint64_t ElfWriter::entry_vaddr() const
  {
    for (auto symbol : symbols)
      if (symbol->name == settings.entry_symbol && symbol->vaddr)
        return *symbol->vaddr;
    throw ImageError("the entry point '" + settings.entry_symbol + "' is not defined", settings.entry_symbol);
  }


  std::vector<FunctionExtent> ElfWriter::function_extents(const ImageLayout& layout) const
  {
    std::vector<FunctionExtent> extents;
    OrderedMap<std::string, std::uint64_t> offsets;
    for (const auto& plan : layout.sections)
      offsets[plan.name] = plan.offset;
    for (auto symbol : symbols) {
      if (! symbol->defined || symbol->kind != mc::SymKind::func)
        continue;
      if (symbol->section == nullptr || ! symbol->vaddr)
        continue;
      auto base = offsets.get(symbol->section->name);
      if (base == nullptr)
        continue;
      extents.push_back(FunctionExtent{ symbol->name, *base + symbol->offset, *symbol->vaddr, symbol->size,
                                        slack_after(*symbol) });
    }
    return extents;
  }


  std::uint64_t ElfWriter::slack_after(const mc::MCSymbol& symbol) const
  {
    if (symbol.section == nullptr)
      return 0;
    auto end = symbol.offset + symbol.size;
    for (const auto& fragment : symbol.section->fragments)
      if (fragment->kind == mc::MCFragment::Kind::padding && fragment->offset == end)
        return static_cast<const mc::MCPaddingFragment&>(*fragment).reserved;
    return 0;
  }


  std::vector<SymbolPlan> ElfWriter::symbol_plans(const ImageLayout& layout, StringTable& table) const
  {
    OrderedMap<std::string, std::size_t> indices;
    for (std::size_t i = 0; i < layout.sections.size(); ++i)
      indices[layout.sections[i].name] = i + 1;
    std::vector<SymbolPlan> entries{ SymbolPlan{ "", 0, 0, 0, 0, shn_undef, 0 } };
    table.add("");
    std::vector<SymbolPlan> locals;
    std::vector<SymbolPlan> globals;
    for (const auto& path : source_paths) {
      table.add(path);
      locals.push_back(SymbolPlan{ path, 0, 0, stb_local, stt_file, shn_abs, 0 });
    }
    for (auto symbol : symbols) {
      if (! symbol->defined || symbol->temporary || symbol->section == nullptr)
        continue;
      if (! symbol->vaddr)
        continue;
      table.add(symbol->name);
      SymbolPlan plan{ symbol->name,
                       *symbol->vaddr,
                       symbol->size,
                       binding_of(symbol->binding),
                       kind_of(symbol->kind),
                       std::uint16_t(indices.get_or(symbol->section->name, shn_undef)),
                       visibility_of(symbol->visibility) };
      (plan.binding == stb_local ? locals : globals).push_back(std::move(plan));
    }
    entries.insert(entries.end(), locals.begin(), locals.end());
    entries.insert(entries.end(), globals.begin(), globals.end());
    return entries;
  }


  // -- materializing ----------------------------------------------------------

  mc::Bytes ElfWriter::materialize(const ImageLayout& layout)
  {
    resolve_fixups();
    mc::Bytes out(layout.total_size, 0);

    Ehdr header;
    header.e_type = et_exec;
    header.e_machine = layout.machine;
    header.e_entry = layout.entry_vaddr;
    header.e_phoff = ehdr_size;
    header.e_shoff = layout.has_chunk("shdrs") ? layout.chunk("shdrs").file_offset : 0;
    header.e_phnum = std::uint16_t(layout.segments.size());
    header.e_shnum = layout.sections.empty() ? 0 : std::uint16_t(layout.sections.size() + 1);
    header.e_shstrndx = index_of(layout, ".shstrtab");
    header.e_flags = settings.header_flags;
    pack(out, 0, header);

    auto position = layout.chunk("phdrs").file_offset;
    for (const auto& segment : layout.segments) {
      pack(out, position,
           Phdr{ segment.p_type, segment.p_flags, segment.p_offset, segment.p_vaddr, segment.p_filesz,
                 segment.p_memsz, segment.p_align });
      position += phdr_size;
    }

    for (auto section : sections) {
      if (! layout.has_chunk(section->name))
        continue;
      const auto& chunk = layout.chunk(section->name);
      auto data = mc::section_bytes(*section);
      std::ranges::copy(data, out.begin() + std::ptrdiff_t(chunk.file_offset));
    }

    if (layout.has_chunk(".shstrtab"))
      write_tables(out, layout);
    return out;
  }


  void ElfWriter::write_tables(mc::Bytes& out, const ImageLayout& layout)
  {
    const auto& shstr = shstrtab.bytes_of();
    std::ranges::copy(shstr, out.begin() + std::ptrdiff_t(layout.chunk(".shstrtab").file_offset));
    const auto& strs = strtab.bytes_of();
    std::ranges::copy(strs, out.begin() + std::ptrdiff_t(layout.chunk(".strtab").file_offset));

    auto position = layout.chunk(".symtab").file_offset;
    for (const auto& symbol : layout.symbols) {
      pack(out, position,
           Sym{ strtab.add(symbol.name), st_info(symbol.binding, symbol.kind), symbol.visibility, symbol.shndx,
                symbol.value, symbol.size });
      position += sym_size;
    }

    position = layout.chunk("shdrs").file_offset;
    pack(out, position, Shdr{});
    position += shdr_size;
    for (const auto& plan : layout.sections) {
      pack(out, position,
           Shdr{ shstrtab.add(plan.name), plan.sh_type, plan.sh_flags, plan.alloc ? plan.addr : 0, plan.offset,
                 plan.size, plan.sh_link, plan.sh_info, plan.sh_addralign, plan.sh_entsize });
      position += shdr_size;
    }
  }


  std::uint16_t ElfWriter::index_of(const ImageLayout& layout, const std::string& name)
  {
    for (std::size_t index = 0; index < layout.sections.size(); ++index)
      if (layout.sections[index].name == name)
        return std::uint16_t(index + 1);
    return 0;
  }


  void ElfWriter::resolve_fixups()
  {
    for (auto section : sections)
      for (auto& fragment : section->fragments) {
        auto fixups = mc::fixups_of(*fragment);
        auto target = mc::patched_bytes_of(*fragment);
        if (fixups == nullptr || target == nullptr)
          continue;
        auto base = section->vaddr + fragment->offset;
        for (const auto& fixup : *fixups) {
          __int128 value;
          try {
            value = mc::fixup_value(fixup, base + std::uint64_t(fixup.offset));
          } catch (const mc::UnresolvedSymbol& exc) {
            throw ImageError("'" + exc.name + "' is referenced but never defined", exc.name);
          }
          try {
            apply_fixup(*target, std::size_t(fixup.offset), fixup, value);
          } catch (const mc::FixupRangeError& exc) {
            throw ImageError(exc.what(), std::nullopt, std::pair(mc::decimal(exc.value), exc.kind->name));
          }
        }
      }
  }


  std::pair<mc::Bytes, ImageLayout> write_image(const ImageSettings& settings, std::span<mc::MCSection* const> sections,
                                                std::span<mc::MCSymbol* const> symbols,
                                                std::vector<std::string> source_paths, mc::FixupApplier apply_fixup)
  {
    ElfWriter writer(settings, sections, symbols, std::move(source_paths), std::move(apply_fixup));
    auto layout = writer.plan();
    auto bytes = writer.materialize(layout);
    return { std::move(bytes), std::move(layout) };
  }


  ImageLayout write_image_to(int fd, const ImageSettings& settings, std::span<mc::MCSection* const> sections,
                             std::span<mc::MCSymbol* const> symbols, std::vector<std::string> source_paths,
                             mc::FixupApplier apply_fixup)
  {
    auto [bytes, layout] = write_image(settings, sections, symbols, std::move(source_paths), std::move(apply_fixup));
    if (! write_all(fd, std::string_view(reinterpret_cast<const char*>(bytes.data()), bytes.size()))) [[unlikely]]
      throw ImageError("the image could not be written");
    return layout;
  }

} // namespace pl4g::elf
