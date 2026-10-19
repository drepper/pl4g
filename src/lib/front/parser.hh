// The recursive-descent parser.
//
// The grammar is context-free and definitions need not come in order, so the
// parser makes one tree per file and leaves every question of meaning to the
// checker.
#pragma once

#include <string_view>
#include <vector>

#include "diag/engine.hh"
#include "front/ast.hh"
#include "front/token.hh"
#include "source/manager.hh"


namespace pl4g {

  // Parse the tokens of one file, which the arena keeps.
  //
  // In two steps: every definition's signature, with each function's body set
  // aside; then the bodies.  Where `defer` says so the second step is left out
  // and a body is parsed when something first reads it -- how a module is read,
  // so a function nothing names is never parsed.  `source` is the file, which a
  // body the lexer left as text needs.
  const ast::SourceUnit* parse(ast::SyntaxArena& arena, std::vector<Token> tokens, std::string_view path,
                               DiagEngine& diags, bool defer = false, const SourceFile* source = nullptr);

} // namespace pl4g
