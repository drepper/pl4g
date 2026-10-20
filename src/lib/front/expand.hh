// Expanding macros, which happens between parsing and every check.
//
// A port of pypl4g's front/expand.py.  What the checker sees is a program with
// no macro left in it: an invocation is replaced by what its macro writes --
// by the first rule whose pattern it matches, or by running the macro, where
// it is written as a function, on the machine of front/interpret.hh.  Every
// piece keeps the position it was written at, and a name a template binds is
// renamed to one no source file can spell (hygiene).
//
// A piece of the program a running macro holds is a handle: an index into the
// expander's table of trees, each a `const ast::Node*`.  The machine holds the
// handle as the number it is; only the builtins below look inside.
#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <span>
#include <vector>

#include "diag/engine.hh"
#include "front/ast.hh"
#include "ir/ir.hh"


namespace pl4g {

  // The macros, checked and lowered: the module and the trees they quoted.
  // Python's `_Checked`, and the shape of what `sema::check_macros` answers.
  struct MacroModule {
    std::unique_ptr<ir::Module> module;
    std::vector<const ast::Quote*> quoted;
  };

  // How the macros written as functions are made runnable: handed units
  // holding them and what they may call, the module the checker lowered them
  // to, or nothing.
  using CheckMacros = std::function<std::optional<MacroModule>(std::span<const ast::SourceUnit* const>)>;

  // How a module an import names is found and read, before the checker has
  // resolved anything; nothing where it cannot be.
  using ImportedUnit = std::function<const ast::SourceUnit*(const ast::ModuleImport*)>;

  // How many rewrites one invocation may take before it is refused.
  inline constexpr int macro_rounds = 64;

  // What a renamed name carries: an operator glyph, which no name can hold.
  inline constexpr std::string_view hygiene_mark = "#";

  // Expand every macro in `units`, answering the units with none left.  The
  // new trees live in `arena`; a program with no macro is handed back as it
  // is.
  std::vector<const ast::SourceUnit*> expand(ast::SyntaxArena& arena, std::span<const ast::SourceUnit* const> units,
                                             DiagEngine& diags, CheckMacros checked = {},
                                             ImportedUnit imported = {});

  // Whether a top-level definition belongs to the macros and not to the
  // program: a macro, or a `comptime` function whose signature names a piece
  // of the program.  The checker asks it of a module's definitions too.
  bool only_for_macros(const ast::Node* item);

} // namespace pl4g
