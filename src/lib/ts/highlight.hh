// The tree-sitter service: what the grammar in tree-sitter-pl4g says each
// piece of a text is, and where a reader may fold it.
//
// The compiler has a lexer of its own and could colour a line from it, but a
// lexer knows a name is a name and not that this one is a type and that one a
// parameter.  The grammar knows, and its queries are what an editor already
// uses, so a snippet a diagnostic shows is coloured the way the same line is
// where it was written: one description of the language and not two.  The
// grammar's parser and both queries are built into the library.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <vector>


namespace pl4g::ts {

  // One piece of a text and what the grammar calls it: tree-sitter's capture
  // name, `keyword` or `function.builtin` or `comment.documentation`.
  struct Run {
    std::size_t begin = 0;
    std::size_t end = 0;
    std::string name;
  };

  // A piece of a text a reader may fold away, by lines counted from nought.
  struct Fold {
    std::uint32_t first_line = 0;
    std::uint32_t last_line = 0;
  };

  // The grammar, loaded once, and what it says about a text.
  class Highlighter {
  public:
    Highlighter();
    Highlighter(const Highlighter&) = delete;
    Highlighter& operator=(const Highlighter&) = delete;
    ~Highlighter();

    // Every highlighted run of `text`, by bytes, earliest first; of two
    // beginning together the longer first, and of two alike the one the
    // query writes later -- the rule tree-sitter's own highlighter follows.
    // A text asked about again, the same object, is not parsed again.
    const std::vector<Run>& runs(std::string_view text);

    // What each piece of the line `text[start, start + length)` is, as
    // offsets within that line, a piece already inside another left out.
    std::vector<Run> of_line(std::string_view text, std::size_t start, std::size_t length);

    // Where `text` may be folded.
    std::vector<Fold> folds(std::string_view text);

  private:
    struct Grammar;
    std::unique_ptr<Grammar> grammar;
  };

} // namespace pl4g::ts
