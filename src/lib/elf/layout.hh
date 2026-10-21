// The layout model of a generated image: every offset, address and size,
// computed before a byte is written.  A port of pypl4g/elf/layout.py.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "util/ordered.hh"


namespace pl4g::elf {

  // The kind of image to produce: an executable at a fixed address, one that
  // may be loaded anywhere and relocates itself, or a shared object -- linked
  // at nought, with what it offers and needs in a dynamic section a loader
  // reads, and no entry point.
  enum class ImageKind : std::uint8_t { executable, pie, shared };

  // One contiguous run of bytes in the image.
  struct Chunk {
    std::string name;
    std::uint64_t size = 0;
    std::uint64_t align = 1;
    // Growth slack reserved after the chunk.
    std::uint64_t padding = 0;
    std::uint64_t file_offset = 0;
    std::optional<std::uint64_t> vaddr;

    std::uint64_t total_size() const noexcept { return size + padding; }
  };

  // One section of the image.
  struct SectionPlan {
    std::string name;
    std::uint32_t sh_type = 0;
    std::uint64_t sh_flags = 0;
    std::uint64_t sh_addralign = 1;
    std::uint64_t sh_entsize = 0;
    std::uint32_t sh_link = 0;
    std::uint32_t sh_info = 0;
    std::size_t index = 0;
    std::uint64_t addr = 0;
    std::uint64_t offset = 0;
    std::uint64_t size = 0;
    // A section that occupies memory when the program runs.
    bool alloc = false;
  };

  // One program header.
  struct SegmentPlan {
    std::uint32_t p_type = 0;
    std::uint32_t p_flags = 0;
    std::uint64_t p_align = 1;
    std::uint64_t p_offset = 0;
    std::uint64_t p_vaddr = 0;
    std::uint64_t p_filesz = 0;
    std::uint64_t p_memsz = 0;
  };

  // One entry of the symbol table.
  struct SymbolPlan {
    std::string name;
    std::uint64_t value = 0;
    std::uint64_t size = 0;
    std::uint8_t binding = 0;
    std::uint8_t kind = 0;
    std::uint16_t shndx = 0;
    std::uint8_t visibility = 0;
  };

  // Where one function lives in the image, and how much room it has to grow.
  struct FunctionExtent {
    std::string name;
    std::uint64_t file_offset = 0;
    std::uint64_t vaddr = 0;
    std::uint64_t size = 0;
    std::uint64_t padding = 0;
  };

  // Everything about the image, computed before any byte is written.
  struct ImageLayout {
    ImageKind kind = ImageKind::executable;
    std::uint16_t machine = 0;
    std::uint64_t base_vaddr = 0;
    std::uint64_t page_size = 0;
    std::string entry_symbol;
    std::uint64_t entry_vaddr = 0;
    std::vector<Chunk> chunks;
    std::vector<SectionPlan> sections;
    std::vector<SegmentPlan> segments;
    std::vector<SymbolPlan> symbols;
    std::vector<FunctionExtent> functions;
    // Which chunk is called what: an index into `chunks`.
    OrderedMap<std::string, std::size_t> by_name;
    std::uint64_t total_size = 0;

    // The chunk called `name`.
    const Chunk& chunk(const std::string& name) const { return chunks.at(*by_name.get(name)); }
    bool has_chunk(const std::string& name) const { return by_name.contains(name); }
  };

  // Round `value` up to the next multiple of `alignment`.
  constexpr std::uint64_t align_up(std::uint64_t value, std::uint64_t alignment) noexcept
  {
    if (alignment <= 1)
      return value;
    auto remainder = value % alignment;
    return remainder == 0 ? value : value + alignment - remainder;
  }

} // namespace pl4g::elf
