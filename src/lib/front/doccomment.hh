// What a documentation comment says, beyond saying it.
//
// A port of pypl4g's front/doccomment.py.  A comment is prose, except a line
// written the way Doxygen writes one -- `\param`, `@return` and the few others
// -- which is about a particular part of what is documented.  This reads a
// comment into its parts; whether a `\param` names a parameter is asked where
// definitions are checked.
#pragma once

#include <string>
#include <string_view>
#include <vector>


namespace pl4g::doc {

  // One command of a documentation comment, and what it carries (`Part`).
  struct Part {
    // The word as written, without the sigil, and the sigil, `\` or `@`.
    std::string word;
    char sigil = '\\';
    // What the word means here, or empty where it means nothing.
    std::string_view command;
    // The name a `\param` names; empty for the rest.
    std::string subject;
    // The text that followed, its continuation lines joined.
    std::string text;
    // Where the command was written: a line of the comment counted from nought
    // and the column of the sigil within it, which is the length of the spaces
    // and tabs before it, so bytes and characters alike.
    int line = 0;
    int column = 0;

    // The command as the comment has it, for quoting back at a reader.
    std::string written() const { return std::string(1, sigil) + word; }
    // Whether it is a command this understands.
    bool known() const noexcept { return ! command.empty(); }
  };


  // A documentation comment, taken apart (`Doc`).
  struct Doc {
    // The prose before the first command, or what `\brief` said.
    std::string summary;
    // Every command, in the order written.
    std::vector<Part> parts;

    // The first line of the summary.
    std::string brief() const;
    // Every part with the given meaning, in order.
    std::vector<const Part*> of(std::string_view command) const;
    // The first part with that meaning, or nullptr.
    const Part* first(std::string_view command) const;
    // What it says about the parameters.
    std::vector<const Part*> params() const { return of("param"); }
  };


  // The meaning of each command a comment may be written with (`COMMANDS`):
  // what the rest of the compiler calls it, or empty for an unknown word.
  std::string_view command_named(std::string_view word) noexcept;

  // The order an editor shows the commands in (`ORDER`).
  extern const std::vector<std::string_view> order;

  // Read a documentation comment, its lines joined with newlines, into its
  // parts (`parse`).
  Doc parse(std::string_view text);

} // namespace pl4g::doc
