// Lexical analysis, including the layout rules.
//
// The lexer makes the INDENT, DEDENT and NEWLINE tokens the layout-based syntax
// needs, as Python's does.  Inside brackets line structure means nothing and
// none of them is made.
#pragma once

#include <string_view>
#include <vector>

#include "diag/engine.hh"
#include "front/token.hh"
#include "source/manager.hh"


namespace pl4g {

  // Tokenize `source`.  Where `lazy` says so, each top-level function's
  // indented body is left as one `body` token for `tokenize_body` to lex when
  // something reads it -- which is how a module's unused functions say nothing.
  std::vector<Token> tokenize(const SourceFile& source, DiagEngine& diags, bool lazy = false);

  // The tokens of a body `tokenize` left as text, as the lexer would have made
  // them there.
  std::vector<Token> tokenize_body(const SourceFile& source, Span span, DiagEngine& diags);

  // Whether one character may be an operator: a symbol by Unicode's
  // categories, or a glyph the language already uses as one.
  bool is_operator_glyph(char32_t cp) noexcept;

  // Whether one character may open, or close, a pair a program defines.
  bool is_operator_opener(char32_t cp) noexcept;
  bool is_operator_closer(char32_t cp) noexcept;

  // Whether what stands between the accents of `` `+` `` names an operator: one
  // glyph, or an opening and a closing bracket written together.
  bool is_operator_name(std::string_view written) noexcept;

  // Whether `text` is one name a program may write: not a keyword, and not a
  // name the compiler provides.
  bool is_a_name(std::string_view text) noexcept;

} // namespace pl4g
