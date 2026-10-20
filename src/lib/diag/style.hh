// What a diagnostic looks like on a terminal that can show colour.
//
// Colour is decoration and never information: everything a colour says here is
// said by the text as well.  The default is to look -- a terminal gets colour
// and a pipe does not -- and `NO_COLOR` is honoured whatever the option says.
// A port of pypl4g's diag/style.py.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>


namespace pl4g {

  // When the compiler writes colour.
  enum class ColourWhen : std::uint8_t { yes, no, automatic };

  // The word a command line gives for one, or nothing for another word.
  std::optional<ColourWhen> colour_named(std::string_view word);


  // The codes a diagnostic is written with, or nothing where there is none:
  // without colour every method hands back what it was given.
  class Palette {
  public:
    Palette() = default;
    explicit Palette(bool on) : on(on) {}

    // The palette a run writes with under `when`, the standard output being
    // the descriptor `watched`: "is a person watching this run".
    static Palette chosen(ColourWhen when, int watched);

    bool is_on() const noexcept { return on; }

    std::string severity(std::string_view word) const;
    std::string message(std::string_view text) const;
    std::string where(std::string_view text) const;
    std::string number(std::string_view text) const;
    std::string gutter(std::string_view text) const;
    std::string caret(std::string_view text) const;
    // A piece of source, written as what the highlighter called it: a name
    // the palette does not know falls back to what it is a kind of --
    // `keyword.modifier` to `keyword` -- and then to no colour at all.
    std::string capture(std::string_view name, std::string_view text) const;

  private:
    std::string in(std::string_view code, std::string_view text) const;

    bool on = false;
  };

} // namespace pl4g
