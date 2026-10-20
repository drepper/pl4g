// The source files of one compilation.
#pragma once

#include <deque>
#include <expected>
#include <map>
#include <memory>
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
    // How the text is shown, where it is not the reader's as it stands: what
    // to add to a line's number, and how many spaces were put before each of
    // its lines that a reader did not write -- the interactive session puts
    // what was typed into a function, indented, below a line of its own.
    int line_shift = 0;
    unsigned added_indent = 0;

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
    const SourceFile& add(std::filesystem::path path, std::string text, int line_shift = 0, unsigned added_indent = 0);

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

    // A module's file as it was parsed, for a second reader of it: the tree,
    // and what keeps the tree alive.  The source layer is below the front end
    // and does not know what a tree is.
    struct Parsed {
      const void* unit = nullptr;
      std::shared_ptr<void> keeper;
    };
    // The tree of the module at `path`, where it was read.
    const Parsed* parsed(const std::filesystem::path& path) const
    {
      auto it = parsed_units.find(path);
      return it == parsed_units.end() ? nullptr : &it->second;
    }
    // Keep the tree of the module at `path` for a second reader of it.
    void remember(const std::filesystem::path& path, Parsed found) { parsed_units[path] = std::move(found); }

    // One file that was read and what came of reading it, for the bill of
    // materials.
    struct ReadUnit {
      std::filesystem::path path;
      const void* unit = nullptr;
      bool module = false;
    };
    // Record that `path` was read and what came of reading it.
    void record(const std::filesystem::path& path, const void* unit, bool module = false)
    {
      read_units.push_back({ path, unit, module });
    }
    std::vector<ReadUnit> read_units;

  private:
    std::deque<SourceFile> all;
    Loc next_base = 0;
    std::map<std::filesystem::path, Parsed> parsed_units;
  };

} // namespace pl4g
