// The source files of one compilation.
#pragma once

#include <deque>
#include <expected>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "source/location.hh"


namespace pl4g {

  // One source file and what is needed to resolve positions inside it.
  struct SourceFile {
    FileId ident = 0;
    std::filesystem::path path;
    // The path as it is shown in a diagnostic.
    std::string shown;
    std::string text;
    Loc base = 0;
    // The offset within `text` of the first byte of each line.
    std::vector<std::uint32_t> line_starts;

    // The location just past the last byte of the file.
    Loc end() const noexcept { return base + Loc(text.size()); }

    // A span covering the whole file.
    Span whole() const noexcept { return { base, end() }; }

    // The file's text from `span`, which must lie inside it.
    std::string_view slice(Span span) const noexcept
    {
      return std::string_view(text).substr(std::size_t(span.start - base), std::size_t(span.end - span.start));
    }
  };


  // Why a file could not be read.
  struct ReadFailure {
    enum class Kind { unreadable, not_utf8 } kind;
    // For `unreadable`, what the system said.
    std::string reason;
    // For `not_utf8`, the offset of the first byte that is not.
    std::size_t offset = 0;
  };


  // Owns every source file of a compilation.  Files are laid out end to end in
  // one coordinate space, so a location still says which file it is in.
  class SourceManager {
  public:
    // Register `text` as the contents of `path` and return its record, which
    // stays where it is for the life of the manager.
    const SourceFile& add(std::filesystem::path path, std::string text);

    // Read `path`, check that it is UTF-8, and register it.
    std::expected<const SourceFile*, ReadFailure> read(const std::filesystem::path& path);

    // The file containing `loc`, if any.
    const SourceFile* file_of(Loc loc) const noexcept;

    // Resolve `loc` to a path, a line and a column, counted from one.
    std::optional<Position> position(Loc loc) const noexcept;

    // The text of the line containing `loc`, without its end of line.
    std::optional<std::string_view> line_text(Loc loc) const noexcept;

    // The source text `span` covers.
    std::optional<std::string_view> snippet(Span span) const noexcept;

    // Every file, in the order they were added.
    const std::deque<SourceFile>& files() const noexcept { return all; }

  private:
    std::deque<SourceFile> all;
    Loc next_base = 0;
  };

} // namespace pl4g
