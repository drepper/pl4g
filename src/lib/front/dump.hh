// A syntax tree written out as text, every field of every node, in the form
// src/test/parser_agrees.py writes pypl4g's trees in -- which is what lets the
// two parsers be compared on every program there is.
//
// A node is `(Name field=value ...)` with its fields in the order of their
// names; a sequence is `[...]`, nothing is `None`, a span `@start-end` in
// bytes, a string in quotes with every byte outside printable ASCII escaped,
// a floating-point number `f:` and its bits, an enumerator its name.
#pragma once

#include <string>

#include "front/ast.hh"


namespace pl4g::ast {

  // The text of the tree under `node`.
  std::string dump(const Node* node);

} // namespace pl4g::ast
