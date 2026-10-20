// The syntax tree and the tokens as pypl4g's `--emit ast` and `--emit tokens`
// write them: Python's `repr` of its dataclasses, every field in the order it
// is declared, the offsets of every span counted in characters as Python
// counts them.
#pragma once

#include <span>
#include <string>

#include "front/ast.hh"
#include "front/token.hh"
#include "source/manager.hh"


namespace pl4g::ast {

  // `repr(unit)`, with the spans as pypl4g's coordinates of `sources`' files.
  std::string python_repr(const SourceUnit* unit, const SourceManager& sources);

  // What pypl4g calls a kind of token: `INT`, `KW_FN`, `OPERATOR`.
  std::string python_kind_name(TokKind kind);

  // One line per token of `tokens`, as `--emit tokens` writes them: where it
  // is, padded to 28 characters, its kind's name to 14, and `repr` of its text.
  std::string token_lines(std::span<const Token> tokens, const SourceManager& sources);

} // namespace pl4g::ast
