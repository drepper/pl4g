// The streamer: where emitted instructions, data and symbols are collected.
// A port of pypl4g/mc/streamer.py.
#pragma once

#include <functional>
#include <memory>
#include <span>
#include <string>
#include <utility>
#include <vector>

#include "mc/fragment.hh"
#include "util/ordered.hh"


namespace pl4g::mc {

  // Encodes one instruction into bytes and the fixups it leaves behind: the
  // target's encoder.
  using Encoder = std::function<std::pair<Bytes, std::vector<MCFixup>>(const MCInst&)>;

  // What a section is made with, beside its name.
  struct SectionOptions {
    bool executable = false;
    bool writable = false;
    std::uint64_t alignment = 1;
    bool alloc = true;
    std::uint32_t sh_type = 1;
    std::string sh_link_to;
    std::uint64_t sh_entsize = 0;
  };


  // Collects the generated image, section by section.
  class MCStreamer {
  public:
    explicit MCStreamer(Encoder encode) : encode(std::move(encode)) {}
    MCStreamer(const MCStreamer&) = delete;
    MCStreamer& operator=(const MCStreamer&) = delete;

    // The named section, made if it is not there yet.
    MCSection* get_section(const std::string& name, const SectionOptions& options = {});
    // Direct subsequent emission into `section`.
    void switch_section(MCSection* section) noexcept { current = section; }
    // The symbol `name`, made undefined if it is not there yet.
    MCSymbol* symbol(const std::string& name);
    // Define a symbol at the current position of the current section.
    MCSymbol* define_symbol(const std::string& name, SymBinding binding = SymBinding::local,
                            SymKind kind = SymKind::notype, bool temporary = false,
                            SymVisibility visibility = SymVisibility::default_);
    // Mark where the symbol's definition ends.
    void set_symbol_size(MCSymbol* sym);

    // Encode and emit one instruction, or several.
    MCInstFragment* emit_inst(const InstRef& inst);
    void emit_insts(std::span<const InstRef> insts);
    // Emit literal bytes.
    MCDataFragment* emit_bytes(Bytes data, std::vector<MCFixup> fixups = {});
    // Emit padding up to the next multiple of `alignment`.
    MCAlignFragment* emit_align(std::uint64_t alignment, std::uint8_t fill = 0xCC);
    // Reserve growth slack, for later in-place patching.
    MCPaddingFragment* emit_padding(std::uint64_t reserved, std::uint8_t fill = 0xCC);
    // Every symbol that is referred to but never defined.
    std::vector<MCSymbol*> undefined_symbols() const;

    // The sections and the symbols, in the order they were first named.
    std::vector<MCSection*> section_list() const;
    std::vector<MCSymbol*> symbol_list() const;

    Encoder encode;
    OrderedMap<std::string, std::unique_ptr<MCSection>> sections;
    OrderedMap<std::string, std::unique_ptr<MCSymbol>> symbols;
    // Symbols in the order they were defined.
    std::vector<MCSymbol*> defined_order;
    MCSection* current = nullptr;

  private:
    template<typename F>
    F* append(std::unique_ptr<F> fragment)
    {
      fragment->section = current;
      F* raw = fragment.get();
      current->fragments.push_back(std::move(fragment));
      return raw;
    }
  };

} // namespace pl4g::mc
