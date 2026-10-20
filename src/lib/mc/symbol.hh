// Symbols and sections of the symbolic assembler.  A port of
// pypl4g/mc/symbol.py.  Both are compared by identity.
#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>


namespace pl4g::mc {

  struct MCFragment;
  struct MCSection;

  // How widely a symbol is visible.
  enum class SymBinding : std::uint8_t { local, global, weak };

  // How far a symbol is visible outside the image it is defined in.
  enum class SymVisibility : std::uint8_t { default_, internal, hidden, protected_ };

  // What a symbol names.
  enum class SymKind : std::uint8_t { notype, func, object, section, file };


  // A named place in the generated image.
  struct MCSymbol {
    std::string name;
    MCSection* section = nullptr;
    // Index of the fragment the symbol sits in front of, and of the one its
    // definition ends in front of.
    std::optional<std::size_t> fragment_index;
    std::optional<std::size_t> end_fragment_index;
    // Offset within the section, filled in when fragments are laid out.
    std::uint64_t offset = 0;
    std::uint64_t size = 0;
    bool defined = false;
    SymBinding binding = SymBinding::local;
    SymVisibility visibility = SymVisibility::default_;
    SymKind kind = SymKind::notype;
    // Temporary labels are for control flow and not put in the symbol table.
    bool temporary = false;
    // The address the symbol ends up at, filled in by the image layout.
    std::optional<std::uint64_t> vaddr;
  };


  // A run of fragments that end up contiguous in the image.
  struct MCSection {
    std::string name;
    // Whether the section occupies memory when the program runs.
    bool alloc = true;
    bool readable = true;
    bool writable = false;
    bool executable = false;
    std::uint64_t alignment = 1;
    // What kind of section it is, as the object format numbers those kinds.
    std::uint32_t sh_type = 1;
    // The section this one's contents point into, by name.
    std::string sh_link_to;
    // How big one entry of a section that holds a table of them is.
    std::uint64_t sh_entsize = 0;
    std::vector<std::unique_ptr<MCFragment>> fragments;
    // Offset within the section of the end of the last fragment laid out.
    std::uint64_t size = 0;
    std::uint64_t vaddr = 0;

    MCSection();
    MCSection(MCSection&&) noexcept;
    ~MCSection();
  };

} // namespace pl4g::mc
