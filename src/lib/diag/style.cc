// What a diagnostic looks like on a terminal that can show colour.
#include "diag/style.hh"

#include <unistd.h>

#include <array>
#include <cstdlib>
#include <utility>


namespace pl4g {

  namespace {

    // What ends every run of colour.
    constexpr std::string_view reset = "\N{ESCAPE}[0m";

    constexpr std::string_view bold = "1";
    constexpr std::string_view dim = "2";
    constexpr std::string_view bold_red = "1;31";
    constexpr std::string_view bold_yellow = "1;33";
    constexpr std::string_view bold_cyan = "1;36";
    constexpr std::string_view bold_green = "1;32";
    constexpr std::string_view green = "32";
    constexpr std::string_view yellow = "33";
    constexpr std::string_view blue = "34";
    constexpr std::string_view magenta = "35";
    constexpr std::string_view cyan = "36";

    // How each thing the highlighter finds is written, by tree-sitter's
    // capture names.
    constexpr std::array<std::pair<std::string_view, std::string_view>, 10> captures{ {
      { "attribute", cyan },
      { "boolean", yellow },
      { "comment", dim },
      { "function", blue },
      { "keyword", magenta },
      { "module", blue },
      { "number", yellow },
      { "property", cyan },
      { "string", green },
      { "type", cyan },
    } };

  } // anonymous namespace


  std::optional<ColourWhen> colour_named(std::string_view word)
  {
    if (word == "yes")
      return ColourWhen::yes;
    if (word == "no")
      return ColourWhen::no;
    if (word == "auto")
      return ColourWhen::automatic;
    return std::nullopt;
  }


  Palette Palette::chosen(ColourWhen when, int watched)
  {
    if (auto no = std::getenv("NO_COLOR"); no != nullptr && *no != '\0')
      return Palette(false);
    if (when == ColourWhen::no)
      return Palette(false);
    if (when == ColourWhen::yes)
      return Palette(true);
    if (auto term = std::getenv("TERM"); term != nullptr && std::string_view(term) == "dumb")
      return Palette(false);
    return Palette(::isatty(watched) != 0);
  }


  std::string Palette::in(std::string_view code, std::string_view text) const
  {
    if (! on || text.empty())
      return std::string(text);
    std::string out = "\N{ESCAPE}[";
    out += code;
    out += 'm';
    out += text;
    out += reset;
    return out;
  }


  std::string Palette::severity(std::string_view word) const
  {
    if (word == "fatal" || word == "error")
      return in(bold_red, word);
    if (word == "warning")
      return in(bold_yellow, word);
    if (word == "note")
      return in(bold_cyan, word);
    return in(bold, word);
  }

  std::string Palette::message(std::string_view text) const { return in(bold, text); }
  std::string Palette::where(std::string_view text) const { return in(bold, text); }
  std::string Palette::number(std::string_view text) const { return in(dim, text); }
  std::string Palette::gutter(std::string_view text) const { return in(dim, text); }
  std::string Palette::caret(std::string_view text) const { return in(bold_green, text); }


  std::string Palette::capture(std::string_view name, std::string_view text) const
  {
    while (! name.empty()) {
      for (const auto& [known, code] : captures)
        if (known == name)
          return in(code, text);
      auto dot = name.rfind('.');
      name = dot == std::string_view::npos ? std::string_view() : name.substr(0, dot);
    }
    return std::string(text);
  }

} // namespace pl4g
