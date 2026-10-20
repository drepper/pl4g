// Where the compiler's idea of a place and the editor's meet: a port of
// pypl4g/lsp/places.py.
//
// The compiler counts characters and numbers lines and columns from one.  The
// protocol counts lines from nought and, along a line, counts in whatever unit
// the two ends agreed on -- sixteen-bit units by default.  This converts.
#pragma once

#include <cstddef>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>

#include "lsp/json.hh"
#include "source/manager.hh"


namespace pl4g::lsp {

  // How far along a line a position is counted, as the protocol spells it.
  enum class Encoding { utf8, utf16, utf32 };

  // The name the protocol gives `encoding`.
  std::string_view name_of(Encoding encoding) noexcept;

  // What the protocol means where neither end said anything.
  inline constexpr Encoding default_encoding = Encoding::utf16;

  // The encoding to use, given what the client said it could read (its
  // `positionEncodings`, or nothing).
  Encoding agreed(const Json* offered);

  // How far along `line` a column of `characters` is, in `encoding`'s units.
  std::size_t along(std::string_view line, std::size_t characters, Encoding encoding);
  // The other way: how many characters into `line` an `offset` is.  One that
  // falls inside a character answers with the character it fell into.
  std::size_t characters(std::string_view line, std::int64_t offset, Encoding encoding);

  // One end of a span, as a position the protocol understands.
  std::optional<Json> position_of(const SourceManager& sources, Loc loc, Encoding encoding);
  // A span of the compiler's as a range of the editor's; nothing for a span
  // the compiler could not place.
  std::optional<Json> range_of(const SourceManager& sources, Span span, Encoding encoding);
  // Where a diagnostic goes that names no place in the text: the start of the file.
  Json whole_file();

  // The name an editor knows a file by, given the name the compiler does.
  std::string to_uri(const std::filesystem::path& path);
  // And back again, for the only scheme a compiler can read from.
  std::optional<std::filesystem::path> from_uri(std::string_view uri);

} // namespace pl4g::lsp
