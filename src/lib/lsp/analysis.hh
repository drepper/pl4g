// Running the compiler over what the editor is holding: a port of
// pypl4g/lsp/analysis.py.
//
// Nothing here compiles anything itself: it runs the same stages a command
// line runs, over a source manager that hands out the editor's text where it
// has any and reads the disk where it has not.  So a diagnostic in a buffer is
// the diagnostic the build will give.
#pragma once

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "diag/engine.hh"
#include "driver/compile.hh"
#include "front/ast.hh"
#include "sema/notes.hh"
#include "source/manager.hh"
#include "util/ordered.hh"


namespace pl4g::lsp {

  // The text of every document the editor has open, by its path.
  using Held = OrderedMap<std::filesystem::path, std::string, std::hash<std::filesystem::path>>;


  // What one run of the compiler over one file came to, and everything that
  // keeps it alive.
  struct Analysis {
    std::filesystem::path path;
    // The source manager that placed the diagnostics, which is what turns a
    // span into a line and a column.
    std::unique_ptr<SourceManager> sources = std::make_unique<SourceManager>();
    // What the checker made of every name it resolved.
    std::unique_ptr<sema::Notes> notes = std::make_unique<sema::Notes>();
    // Every diagnostic, in the order the compiler made them; the engine owns them.
    std::vector<const Diagnostic*> diagnostics;
    std::unique_ptr<DiagEngine> diags;
    // The syntax tree, where the file parsed at all.
    const ast::SourceUnit* unit = nullptr;
    // Whether this went as far as the whole compiler goes.
    bool whole = false;
    // What made the tree, and holds it.
    std::unique_ptr<driver::Compilation> compilation;
  };


  // Compile `path` out of `held`, as far as `whole` says: the front end --
  // reading, parsing and checking -- or, `whole`, everything the C++
  // implementation does, the macros expanded first.  Nothing raises: what a
  // caller gets back is an analysis, with whatever diagnostics there were.
  std::unique_ptr<Analysis> analyse(const std::filesystem::path& path, const Held& held, bool whole,
                                    std::vector<std::filesystem::path> module_path);

} // namespace pl4g::lsp
