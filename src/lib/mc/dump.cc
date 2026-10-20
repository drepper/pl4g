// A debugging dump of the symbolic representation.
#include "mc/dump.hh"

#include <format>
#include <unordered_set>
#include <vector>


namespace pl4g::mc {

  namespace {

    // The bytes in hexadecimal, a space between two, as Python's
    // `bytes.hex(" ")` writes them, padded to `width` as `ljust` pads.
    std::string hex_padded(const Bytes& data, std::size_t width)
    {
      std::string out;
      for (std::size_t i = 0; i < data.size(); ++i)
        out += std::format("{}{:02x}", i > 0 ? " " : "", data[i]);
      if (out.size() < width)
        out.append(width - out.size(), ' ');
      return out;
    }

    // Render one fragment.
    std::string dump_fragment(const MCFragment& fragment)
    {
      auto encoded = hex_padded(fragment.bytes_of(), 24);
      switch (fragment.kind) {
      case MCFragment::Kind::inst: {
        auto& inst = static_cast<const MCInstFragment&>(fragment);
        if (inst.inst == nullptr)
          break;
        std::string fixups;
        if (! inst.fixups.empty()) {
          fixups = "   \N{REFERENCE MARK} fixup ";
          for (std::size_t i = 0; i < inst.fixups.size(); ++i)
            fixups += (i > 0 ? ", " : "") + inst.fixups[i].kind->label() + " \N{RIGHTWARDS ARROW} "
                      + inst.fixups[i].target->render();
        }
        return "    " + encoded + " " + inst.inst->render() + fixups;
      }
      case MCFragment::Kind::align:
        return "    " + encoded + " \N{REFERENCE MARK} align "
               + std::to_string(static_cast<const MCAlignFragment&>(fragment).alignment);
      case MCFragment::Kind::padding:
        return "    " + encoded + " \N{REFERENCE MARK} growth slack "
               + std::to_string(static_cast<const MCPaddingFragment&>(fragment).reserved);
      case MCFragment::Kind::data:
        return "    " + encoded + " \N{REFERENCE MARK} data";
      }
      return "    \N{REFERENCE MARK} empty fragment";
    }

  } // anonymous namespace


  std::string dump_sections(std::span<MCSection* const> sections, std::span<MCSymbol* const> symbols)
  {
    std::vector<std::string> out{ "\N{REFERENCE MARK} symbolic assembler dump; internal form, not a syntax" };
    for (auto section : sections) {
      if (! section->alloc)
        continue;
      out.emplace_back();
      out.push_back("section " + section->name + (section->executable ? " executable" : "")
                    + (section->writable ? " writable" : ""));
      std::unordered_set<const MCSymbol*> shown;
      for (const auto& fragment : section->fragments) {
        for (auto symbol : symbols)
          if (symbol->defined && symbol->section == section && symbol->offset == fragment->offset
              && shown.insert(symbol).second)
            out.push_back(symbol->name + ":");
        out.push_back(dump_fragment(*fragment));
      }
    }
    out.emplace_back();
    std::string text;
    for (std::size_t i = 0; i < out.size(); ++i)
      text += (i > 0 ? "\n" : "") + out[i];
    return text;
  }

} // namespace pl4g::mc
