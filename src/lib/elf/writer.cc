// Writing the ELF image.
#include "elf/writer.hh"

#include <algorithm>
#include <format>

#include "elf/const.hh"
#include "elf/dynamic.hh"
#include "elf/relr.hh"
#include "elf/structs.hh"
#include "mc/layout.hh"
#include "util/fdio.hh"


namespace pl4g::elf {

  namespace {

    // The symbols the startup code of a position-independent image names
    // (target/relocate.hh, which the writer does not include: the image format
    // sits below the targets).
    constexpr std::string_view target_image_start = "__pl4g_image_start";
    constexpr std::string_view target_relr_start = "__pl4g_relr_start";
    constexpr std::string_view target_relr_end = "__pl4g_relr_end";
    constexpr std::string_view target_relro_start = "__pl4g_relro_start";
    constexpr std::string_view target_relro_end = "__pl4g_relro_end";
    // And the section of what the constructors alone write (target/relro.hh).
    constexpr std::string_view target_relro_section = ".pl4g.relro";

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
    bool pie = settings.kind == ImageKind::pie;
    bool shared = settings.kind == ImageKind::shared;
    if (pie || shared)
      collect_relocations();
    if (! shared)
      // An executable's constructors are run by its own entry point, which
      // makes these read-only once they have: with what is read-only but for
      // being relocated where there is any, in a segment of their own.  A
      // shared object's are run by the loader after it has acted on
      // PT_GNU_RELRO, so there they are writable data like any other.
      for (auto section : sections)
        if (section->alloc && section->name == target_relro_section)
          relro.push_back(section);

    // Mapped sections are grouped by the permissions they need, since a
    // segment carries one set of permissions for all of it; a group that
    // needs nothing gets no segment.
    std::vector<std::pair<std::vector<mc::MCSection*>, std::uint32_t>> groups;
    for (auto [executable, writable, flags] : { std::tuple(false, false, pf_r), std::tuple(true, false, pf_r | pf_x),
                                               std::tuple(false, true, pf_r | pf_w) }) {
      auto found = mapped(executable, writable);
      if (writable && ! relro.empty()) {
        // What is read-only but for being relocated comes first, in a segment
        // of its own, so that its pages are none of the writable data's.
        std::erase_if(found, [&](auto one) { return std::ranges::contains(relro, one); });
        groups.emplace_back(relro, pf_r | pf_w);
      }
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
    // A position-independent image with anything to relocate has its table in
    // a segment of its own, last, so that nothing else moves with its size.  A
    // shared object's tables are in two segments of their own, last, and the
    // dynamic section is said where it is by a header of its own.
    std::uint64_t phnum = groups.size() + 1 + (pie && ! relocated.empty() ? 1 : 0) + (shared ? 3 : 0)
                          + (relro.empty() ? 0 : 1);
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
      if (! relro.empty() && group == relro)
        relro_at = loads.size() - 1;
    }
    if (! shared)
      relro_bounds(loads);
    if (pie)
      offset = place_relocations(layout, offset, loads);
    std::optional<SegmentPlan> dynamic;
    if (shared)
      std::tie(offset, dynamic) = place_dynamic(layout, offset, loads);
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
    if (dynamic)
      layout.segments.insert(layout.segments.begin() + std::ptrdiff_t(loads.size()), *dynamic);
    if (! relro.empty()) {
      // What is read-only once relocated, said as a loader would be told it;
      // the startup code is what acts on it, from the symbols given it.
      const auto& held = loads[relro_at];
      layout.segments.push_back(SegmentPlan{ pt_gnu_relro, pf_r, 1, held.p_offset, held.p_vaddr, held.p_filesz,
                                             held.p_memsz });
    }
    if (shared) {
      link_dynamic(layout);
      // No entry point: the program that loads it calls what it offers.
      layout.entry_vaddr = 0;
    } else
      layout.entry_vaddr = entry_vaddr();
    layout.total_size = offset;
    return layout;
  }


  void ElfWriter::collect_relocations()
  {
    relocated.clear();
    relro.clear();
    // What the image is called in what is said about it.
    std::string_view image = settings.kind == ImageKind::shared ? "a shared object" : "a position-independent image";
    for (auto section : sections)
      for (const auto& fragment : section->fragments) {
        auto fixups = mc::fixups_of(*fragment);
        if (fixups == nullptr)
          continue;
        for (const auto& fixup : *fixups) {
          if (fixup.kind->base != mc::FixupBase::absolute || fixup.kind->within_a_page)
            continue;
          auto where = std::format("'{}' at {}", section->name, fragment->offset + std::uint64_t(fixup.offset));
          if (section->executable) [[unlikely]]
            throw ImageError(std::format("{} holds an absolute address in its code, in {}", image, where));
          if (fixup.kind->size != 8) [[unlikely]]
            throw ImageError(std::format("{} holds a {}-byte absolute address, in {}; only eight bytes can be relocated",
                                         image, fixup.kind->size, where));
          relocated.emplace_back(section, fragment->offset + std::uint64_t(fixup.offset));
          // The startup code or the loader writes it, so it is where writing
          // is allowed --
          // and what was read-only data is then what linkers call data
          // read-only once relocated, which is what a reader expects it named.
          if (! section->writable) {
            section->writable = true;
            relro.push_back(section);
          }
          if (section->name == ".rodata" || section->name.starts_with(".rodata."))
            section->name = ".data.rel.ro" + section->name.substr(std::string_view(".rodata").size());
        }
      }
  }


  std::uint64_t ElfWriter::place_relocations(ImageLayout& layout, std::uint64_t offset,
                                             std::vector<SegmentPlan>& loads)
  {
    // The image is linked at nought: its first byte is where it was loaded.
    define(target_image_start, settings.base_vaddr);
    if (relocated.empty()) {
      relr.clear();
      define(target_relr_start, 0);
      define(target_relr_end, 0);
      return offset;
    }
    std::vector<std::uint64_t> places;
    for (const auto& [section, at] : relocated) {
      auto address = section->vaddr + at;
      if (address % 8 != 0) [[unlikely]]
        throw ImageError(std::format("an absolute address at {:#x} in '{}' is not eight-byte aligned, which a "
                                     "position-independent image cannot relocate",
                                     address, section->name));
      places.push_back(address);
    }
    std::ranges::sort(places);
    relr = encode_relr(places);
    offset = align_up(offset, 8);
    auto end_vaddr = loads.back().p_vaddr + loads.back().p_memsz;
    auto bias = align_up(end_vaddr, settings.page_size) + (offset % settings.page_size) - offset;
    SectionPlan plan;
    plan.name = std::string(relr_section);
    plan.sh_type = sht_relr;
    plan.sh_flags = shf_alloc;
    plan.sh_addralign = 8;
    plan.sh_entsize = 8;
    plan.alloc = true;
    plan.offset = offset;
    plan.addr = offset + bias;
    plan.size = relr.size() * 8;
    layout.sections.push_back(plan);
    add_chunk(layout, plan.name, plan.size, 8, offset);
    loads.push_back(SegmentPlan{ pt_load, pf_r, settings.page_size, offset, plan.addr, plan.size, plan.size });
    define(target_relr_start, plan.addr);
    define(target_relr_end, plan.addr + plan.size);
    return offset + plan.size;
  }


  std::size_t ElfWriter::dynamic_entries() const
  {
    // What it needs, the five every one has, and the terminator; the four that
    // say where the relocations are where there are any; and two for each
    // table of constructors or destructors.
    std::size_t count = settings.needed.size() + 5 + 1;
    if (! relocated.empty() || ! settings.imports.empty())
      count += 4;
    for (std::string_view name : { ".init_array", ".fini_array" })
      if (std::ranges::any_of(sections, [&](auto section) { return section->name == name; }))
        count += 2;
    return count;
  }


  std::pair<std::uint64_t, SegmentPlan> ElfWriter::place_dynamic(ImageLayout& layout, std::uint64_t offset,
                                                                 std::vector<SegmentPlan>& loads)
  {
    // The symbols are the null one, what it needs and then what it offers; the
    // strings are the libraries' names and then the symbols'.  Every size is
    // known here, every address only once this is done.
    auto page = settings.page_size;
    dynamic_names = { "" };
    for (const auto& [_, name] : settings.imports)
      dynamic_names.push_back(name);
    dynamic_names.insert(dynamic_names.end(), settings.exports.begin(), settings.exports.end());
    dynstr = StringTable();
    for (const auto& name : settings.needed)
      dynstr.add(name);
    for (const auto& name : dynamic_names)
      dynstr.add(name);
    auto relocations = relocated.size() + settings.imports.size();
    struct Table {
      std::string_view name;
      std::uint32_t sh_type;
      std::uint64_t size;
      std::uint64_t align;
      std::uint64_t entsize;
    };
    std::vector<Table> tables{ { hash_section, sht_hash, hash_size(dynamic_names.size()), 8, 4 },
                               { dynsym_section, sht_dynsym, dynamic_names.size() * sym_size, 8, sym_size },
                               { dynstr_section, sht_strtab, dynstr.size(), 1, 0 } };
    if (relocations != 0)
      tables.push_back({ rela_section, sht_rela, relocations * rela_size, 8, rela_size });
    std::vector<Table> held{ { dynamic_section, sht_dynamic, dynamic_entries() * dyn_size, 8, dyn_size } };
    std::uint64_t start = 0, bias = 0;
    for (const auto& [group, flags] : { std::pair(&tables, pf_r), std::pair(&held, pf_r | pf_w) }) {
      offset = align_up(offset, 8);
      auto end_vaddr = loads.back().p_vaddr + loads.back().p_memsz;
      bias = align_up(end_vaddr, page) + (offset % page) - offset;
      start = offset;
      for (const auto& table : *group) {
        offset = align_up(offset, table.align);
        SectionPlan plan;
        plan.name = std::string(table.name);
        plan.sh_type = table.sh_type;
        plan.sh_flags = shf_alloc | ((flags & pf_w) != 0 ? shf_write : 0);
        plan.sh_addralign = table.align;
        plan.alloc = true;
        plan.sh_entsize = table.entsize;
        plan.offset = offset;
        plan.addr = offset + bias;
        plan.size = table.size;
        layout.sections.push_back(plan);
        add_chunk(layout, plan.name, plan.size, table.align, offset, plan.addr);
        offset += table.size;
      }
      loads.push_back(SegmentPlan{ pt_load, flags, page, start, start + bias, offset - start, offset - start });
    }
    auto size = offset - start;
    return { offset, SegmentPlan{ pt_dynamic, pf_r | pf_w, 8, start, start + bias, size, size } };
  }


  void ElfWriter::link_dynamic(ImageLayout& layout)
  {
    // Each table to the ones it indexes, by the indices the headers have.
    OrderedMap<std::string, std::uint32_t> indices;
    for (std::size_t i = 0; i < layout.sections.size(); ++i)
      indices[layout.sections[i].name] = std::uint32_t(i + 1);
    for (auto& plan : layout.sections)
      if (plan.name == hash_section || plan.name == rela_section)
        plan.sh_link = *indices.get(std::string(dynsym_section));
      else if (plan.name == dynsym_section || plan.name == dynamic_section) {
        plan.sh_link = *indices.get(std::string(dynstr_section));
        // The first symbol that is not local, which is the one after the null
        // one.
        plan.sh_info = plan.name == dynsym_section ? 1 : 0;
      }
  }


  void ElfWriter::relro_bounds(std::span<const SegmentPlan> loads)
  {
    // The pages to make read-only, from the first page of their segment -- its
    // own, the segment before ending on a page before -- to their end; nothing
    // where there are none.
    if (relro.empty()) {
      define(target_relro_start, 0);
      define(target_relro_end, 0);
    } else {
      const auto& held = loads[relro_at];
      define(target_relro_start, held.p_vaddr / settings.page_size * settings.page_size);
      define(target_relro_end, held.p_vaddr + held.p_memsz);
    }
  }


  void ElfWriter::define(std::string_view name, std::uint64_t vaddr)
  {
    // What the startup code names, where it names it: nothing else needs it.
    for (auto symbol : symbols)
      if (symbol->name == name && ! symbol->defined)
        symbol->vaddr = vaddr;
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
                            std::uint64_t offset, std::optional<std::uint64_t> vaddr)
  {
    layout.chunks.push_back(Chunk{ name, size, align, 0, offset, vaddr });
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
    header.e_type = layout.kind == ImageKind::pie || layout.kind == ImageKind::shared ? et_dyn : et_exec;
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

    if (! relr.empty()) {
      auto at = layout.chunk(std::string(relr_section)).file_offset;
      for (auto entry : relr)
        for (int byte = 0; byte < 8; ++byte)
          out[at++] = std::uint8_t(entry >> (8 * byte));
    }

    if (layout.kind == ImageKind::shared)
      write_dynamic(out, layout);
    if (layout.has_chunk(".shstrtab"))
      write_tables(out, layout);
    return out;
  }


  void ElfWriter::write_dynamic(mc::Bytes& out, const ImageLayout& layout)
  {
    OrderedMap<std::string, const mc::MCSymbol*> by_name;
    for (auto symbol : symbols)
      by_name[symbol->name] = symbol;
    auto copy = [&](const std::vector<std::uint8_t>& bytes, std::string_view chunk) {
      std::ranges::copy(bytes, out.begin() + std::ptrdiff_t(layout.chunk(std::string(chunk)).file_offset));
    };

    // The null symbol is the zeroes already there; then what it needs, and
    // what it offers.
    auto position = layout.chunk(std::string(dynsym_section)).file_offset + sym_size;
    for (const auto& [_, name] : settings.imports) {
      pack(out, position, Sym{ dynstr.add(name), st_info(stb_global, stt_func), stv_default, shn_undef, 0, 0 });
      position += sym_size;
    }
    for (const auto& name : settings.exports) {
      auto found = by_name.get(name);
      const mc::MCSymbol* symbol = found == nullptr ? nullptr : *found;
      if (symbol == nullptr || ! symbol->vaddr || symbol->section == nullptr) [[unlikely]]
        throw ImageError("'" + name + "' is offered but never defined", name);
      pack(out, position,
           Sym{ dynstr.add(name), st_info(stb_global, kind_of(symbol->kind)), stv_default,
                index_of(layout, symbol->section->name), *symbol->vaddr, symbol->size });
      position += sym_size;
    }
    copy(hash_table(dynamic_names), hash_section);
    copy(dynstr.bytes_of(), dynstr_section);

    // The words that hold an address first, in the order of their places, each
    // adding where the object was put to what it holds; then the slots, each
    // filled with where its symbol is.
    OrderedMap<std::string, std::uint64_t> offsets;
    for (const auto& plan : layout.sections)
      offsets[plan.name] = plan.offset;
    std::vector<std::pair<std::uint64_t, std::uint64_t>> places;
    for (const auto& [section, at] : relocated)
      places.emplace_back(section->vaddr + at, *offsets.get(section->name) + at);
    std::ranges::sort(places);
    std::vector<Relocation> relocations;
    for (const auto& [address, in_file] : places) {
      if (address % 8 != 0) [[unlikely]]
        throw ImageError(std::format("an absolute address at {:#x} is not eight-byte aligned, which a shared object "
                                     "cannot relocate",
                                     address));
      std::uint64_t held = 0;
      for (int byte = 7; byte >= 0; --byte)
        held = held << 8 | out[in_file + std::uint64_t(byte)];
      relocations.push_back(Relocation{ address, r_relative(layout.machine), 0, std::int64_t(held) });
    }
    for (std::size_t index = 0; index < settings.imports.size(); ++index) {
      const auto& slot = settings.imports[index].first;
      auto found = by_name.get(slot);
      const mc::MCSymbol* symbol = found == nullptr ? nullptr : *found;
      if (symbol == nullptr || ! symbol->vaddr) [[unlikely]]
        throw ImageError("'" + slot + "' is referenced but never defined", slot);
      relocations.push_back(Relocation{ *symbol->vaddr, r_glob_dat(layout.machine), std::uint32_t(index + 1), 0 });
    }
    if (! relocations.empty()) {
      std::vector<std::uint8_t> table;
      for (const auto& one : relocations)
        one.pack_into(table);
      copy(table, rela_section);
    }

    auto vaddr_of = [&](std::string_view chunk) { return layout.chunk(std::string(chunk)).vaddr.value_or(0); };
    std::vector<std::uint8_t> entries;
    for (const auto& name : settings.needed)
      pack_dynamic_entry(entries, dt_needed, dynstr.add(name));
    pack_dynamic_entry(entries, dt_hash, vaddr_of(hash_section));
    pack_dynamic_entry(entries, dt_strtab, vaddr_of(dynstr_section));
    pack_dynamic_entry(entries, dt_symtab, vaddr_of(dynsym_section));
    pack_dynamic_entry(entries, dt_strsz, dynstr.size());
    pack_dynamic_entry(entries, dt_syment, sym_size);
    if (! relocations.empty()) {
      pack_dynamic_entry(entries, dt_rela, vaddr_of(rela_section));
      pack_dynamic_entry(entries, dt_relasz, rela_size * relocations.size());
      pack_dynamic_entry(entries, dt_relaent, rela_size);
      pack_dynamic_entry(entries, dt_relacount, places.size());
    }
    for (auto [name, tag, size_tag] : { std::tuple(".init_array", dt_init_array, dt_init_arraysz),
                                        std::tuple(".fini_array", dt_fini_array, dt_fini_arraysz) })
      for (auto section : sections)
        if (section->name == name) {
          pack_dynamic_entry(entries, tag, section->vaddr);
          pack_dynamic_entry(entries, size_tag, section->size);
        }
    pack_dynamic_entry(entries, dt_null, 0);
    copy(entries, dynamic_section);
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
