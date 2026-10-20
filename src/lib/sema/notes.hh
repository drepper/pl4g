// What the checker learned about the names in a file, kept for an editor.
//
// A port of pypl4g's sema/notes.py.  Nothing is recorded unless something
// asks: a checker handed no `Notes` writes nothing, and what a build pays is a
// comparison with nullptr per name resolved.  The notes are about uses; where
// a definition is and what its comment says is in the syntax tree already.
#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "source/location.hh"


namespace pl4g::sema {

  // What a name turned out to be, in the words an editor shows.
  namespace note_kind {
    inline constexpr std::string_view variable = "variable";
    inline constexpr std::string_view parameter = "parameter";
    inline constexpr std::string_view function = "function";
    inline constexpr std::string_view type = "type";
    inline constexpr std::string_view module = "module";
  } // namespace note_kind


  // One name in the source, and what the checker made of it (`Note`).
  struct Note {
    // Where the name is written.
    Span span;
    // One of `note_kind`.
    std::string_view kind;
    // The name as the source wrote it.
    std::string name;
    // What it is, rendered the way the language writes a type.
    std::string detail;
    // Where it was defined, invalid where there is nowhere to point.
    Span defined = invalid_span;
    // The documentation comment of the definition, where it has one.
    std::optional<std::string> doc;
  };


  // Every note of one run, and the answer to "what is at this place" (`Notes`).
  class Notes {
  public:
    // Write one down.
    void add(Note note) { found.push_back(std::move(note)); }

    // The note over `loc`, the narrowest where several cover it.
    const Note* at(Loc loc) const noexcept;

    // How many there are.
    std::size_t size() const noexcept { return found.size(); }

  private:
    std::vector<Note> found;
  };

} // namespace pl4g::sema
