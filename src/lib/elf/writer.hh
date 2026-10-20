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
  };


  // Turns the assembled sections into an ELF executable.
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
                          std::uint64_t offset);
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

    ImageSettings settings;
    std::vector<mc::MCSection*> sections;
    std::vector<mc::MCSymbol*> symbols;
    std::vector<std::string> source_paths;
    mc::FixupApplier apply_fixup;
    StringTable shstrtab;
    StringTable strtab;
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
