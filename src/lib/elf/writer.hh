// Writing the ELF image.  A port of pypl4g/elf/writer.py.
//
// The writer plans first and materializes second: `plan` computes every
// offset, address and size without emitting a byte; `materialize` makes one
// buffer of the final size and writes each chunk where the plan said.
#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "elf/layout.hh"
#include "elf/strtab.hh"
#include "mc/fixup.hh"
#include "mc/fragment.hh"


namespace pl4g::elf {

  // The image cannot be produced.
  struct ImageError : std::runtime_error {
    std::string detail;
    // The symbol nothing defines, where that was the trouble.
    std::optional<std::string> symbol;
    // The value and the field, where a value did not fit the field.
    std::optional<std::pair<std::string, std::string>> out_of_range;

    explicit ImageError(std::string detail, std::optional<std::string> symbol = std::nullopt,
                        std::optional<std::pair<std::string, std::string>> out_of_range = std::nullopt)
      : std::runtime_error(detail), detail(std::move(detail)), symbol(std::move(symbol)),
        out_of_range(std::move(out_of_range)) {}
  };


  // What the writer needs to know about the target and the request.
  struct ImageSettings {
    std::uint16_t machine = 0;
    std::uint64_t base_vaddr = 0;
    std::uint64_t page_size = 0;
    std::string entry_symbol;
    // The architecture's own flag word.
    std::uint32_t header_flags = 0;
    ImageKind kind = ImageKind::executable;
    // Whether to keep the section headers and the symbol table.
    bool with_symbols = true;
    // Growth slack to reserve after each function.
    std::uint64_t function_padding = 0;
    // How much stack the program wants, which PT_GNU_STACK says.
    std::uint64_t stack_size = 0;
    // For a shared object: the libraries it needs, the symbols it offers, and
    // the slots the loader fills with where a symbol it needs is -- each the
    // slot's own symbol and the one it is filled with.
    std::vector<std::string> needed;
    std::vector<std::string> exports;
    std::vector<std::pair<std::string, std::string>> imports;
  };


  // The sections a shared object's tables are in, in the order they are
  // placed.  The first four are read-only and share a segment; the dynamic
  // section is written by the loader, which adds where the object was put to
  // the addresses it holds, and is in a segment of its own.
  inline constexpr std::string_view hash_section = ".hash";
  inline constexpr std::string_view dynsym_section = ".dynsym";
  inline constexpr std::string_view dynstr_section = ".dynstr";
  inline constexpr std::string_view rela_section = ".rela.dyn";
  inline constexpr std::string_view dynamic_section = ".dynamic";


  // The table of a position-independent image, in the section of this name.
  inline constexpr std::string_view relr_section = ".relr.dyn";

  // Turns the assembled sections into an ELF image: an executable at a fixed
  // address, a position-independent one (`ImageKind::pie`) -- linked at
  // nought, with every absolute address it holds named in a table of relative
  // relocations (elf/relr.hh) that its startup code applies, and the symbols
  // of target/relocate.hh saying where the table and the image are -- or a
  // shared object (`ImageKind::shared`), linked at nought too, whose relative
  // relocations and dynamic tables (elf/dynamic.hh) are the loader's to read.
  class ElfWriter {
  public:
    ElfWriter(ImageSettings settings, std::span<mc::MCSection* const> sections, std::span<mc::MCSymbol* const> symbols,
              std::vector<std::string> source_paths, mc::FixupApplier apply_fixup = mc::apply_little_endian);

    // Compute the whole layout without writing anything.
    ImageLayout plan();
    // Write the planned image into one buffer.
    mc::Bytes materialize(const ImageLayout& layout);

  private:
    std::vector<mc::MCSection*> mapped(bool executable, bool writable) const;
    std::uint64_t place_group(ImageLayout& layout, std::span<mc::MCSection* const> sections, std::uint64_t offset,
                              std::uint64_t bias, std::uint32_t flags, std::uint64_t page_size,
                              std::vector<SegmentPlan>& loads);
    static std::uint64_t section_flags(const mc::MCSection& section);
    static void add_chunk(ImageLayout& layout, const std::string& name, std::uint64_t size, std::uint64_t align,
                          std::uint64_t offset, std::optional<std::uint64_t> vaddr = std::nullopt);
    std::uint64_t add_nonalloc(ImageLayout& layout, const std::string& name, std::uint32_t sh_type,
                               std::uint64_t size, std::uint64_t align, std::uint64_t offset,
                               std::uint64_t entsize = 0);
    void link_symtab(ImageLayout& layout, std::span<const SymbolPlan> symbols);
    void place_symbols();
    std::uint64_t entry_vaddr() const;
    std::vector<FunctionExtent> function_extents(const ImageLayout& layout) const;
    std::uint64_t slack_after(const mc::MCSymbol& symbol) const;
    std::vector<SymbolPlan> symbol_plans(const ImageLayout& layout, StringTable& strtab) const;
    void write_tables(mc::Bytes& out, const ImageLayout& layout);
    static std::uint16_t index_of(const ImageLayout& layout, const std::string& name);
    void resolve_fixups();
    // What a position-independent image adds its load address to, and the
    // table that says so.
    void collect_relocations();
    std::uint64_t place_relocations(ImageLayout& layout, std::uint64_t offset, std::vector<SegmentPlan>& loads);
    // Give the entry point the bounds of the pages it makes read-only.
    void relro_bounds(std::span<const SegmentPlan> loads);
    void define(std::string_view name, std::uint64_t vaddr);
    // A shared object's tables: how many entries its dynamic section has,
    // where they all go, which tables index which, and their bytes.
    std::size_t dynamic_entries() const;
    std::pair<std::uint64_t, SegmentPlan> place_dynamic(ImageLayout& layout, std::uint64_t offset,
                                                        std::vector<SegmentPlan>& loads);
    static void link_dynamic(ImageLayout& layout);
    void write_dynamic(mc::Bytes& out, const ImageLayout& layout);

    ImageSettings settings;
    std::vector<mc::MCSection*> sections;
    std::vector<mc::MCSymbol*> symbols;
    std::vector<std::string> source_paths;
    mc::FixupApplier apply_fixup;
    StringTable shstrtab;
    StringTable strtab;
    // The eight-byte places holding an absolute address, by section and offset
    // in it, and the table made of where they ended up.
    std::vector<std::pair<mc::MCSection*, std::uint64_t>> relocated;
    std::vector<std::uint64_t> relr;
    // The sections that are read-only but for being relocated: in a segment
    // of their own, which the startup code makes read-only again once it has
    // relocated them.
    std::vector<mc::MCSection*> relro;
    std::size_t relro_at = 0;
    // For a shared object: the names of its dynamic symbols, the null one
    // first, and their strings.
    std::vector<std::string> dynamic_names;
    StringTable dynstr;
  };


  // Plan and write the image, answering the bytes and the layout.
  std::pair<mc::Bytes, ImageLayout> write_image(const ImageSettings& settings, std::span<mc::MCSection* const> sections,
                                                std::span<mc::MCSymbol* const> symbols,
                                                std::vector<std::string> source_paths,
                                                mc::FixupApplier apply_fixup = mc::apply_little_endian);
  // The same, the bytes written to the descriptor `fd`; answers the layout.
  ImageLayout write_image_to(int fd, const ImageSettings& settings, std::span<mc::MCSection* const> sections,
                             std::span<mc::MCSymbol* const> symbols, std::vector<std::string> source_paths,
                             mc::FixupApplier apply_fixup = mc::apply_little_endian);

} // namespace pl4g::elf
