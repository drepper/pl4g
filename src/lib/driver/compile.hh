// The stages of a compilation up to the IR: read, parse, expand the macros,
// check -- what pypl4g's driver does before its back end, for a front end of
// any kind to run: the command line, the REPL, the language server.
#pragma once

#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "diag/engine.hh"
#include "front/ast.hh"
#include "ir/ir.hh"
#include "sema/clauses.hh"
#include "sema/modules.hh"
#include "sema/notes.hh"
#include "sema/session.hh"
#include "source/manager.hh"


namespace pl4g::driver {

  // What a compilation is asked to do, as far as the front end goes.
  struct Options {
    std::vector<std::filesystem::path> inputs;
    std::vector<std::filesystem::path> module_path;
    sema::Conditions conditions = sema::Conditions::check;
    std::string triple = "x86_64-linux-none";
    std::optional<std::int64_t> stack_size;
    std::optional<std::int64_t> guard_size;
    bool ignore_system = false;
  };


  // One compilation's front end: the files it read, the trees made of them,
  // and what is said about them, said through `diags`.
  class Compilation {
  public:
    Compilation(Options options, DiagEngine& diags, SourceManager& sources)
      : options(std::move(options)), diags(diags), sources(sources) {}

    // Read every input and parse it.
    std::vector<const ast::SourceUnit*> read_and_parse();

    // The units with every macro invocation replaced by what it wrote.
    std::vector<const ast::SourceUnit*> expand(std::span<const ast::SourceUnit* const> units);

    // Check the units and lower them into a module; what was wrong with them
    // has been said.  `notes`, where given, is told what each name is.
    // `session`, where given, is the interactive session the units are an
    // input of.
    std::unique_ptr<ir::Module> analyze(std::span<const ast::SourceUnit* const> units, sema::Notes* notes = nullptr,
                                        sema::Session* session = nullptr);

    // All three, as `pypl4g` runs them: nothing is checked after an expansion
    // that failed.  Nothing where what was read cannot become a module.
    std::unique_ptr<ir::Module> front_end(sema::Notes* notes = nullptr);

    // Where modules are looked for, as every stage that looks has to agree.
    sema::ModuleRegistry registry() const;

    Options options;
    // The trees, and what the expander makes of them.
    ast::SyntaxArena arena;

  private:
    // The parsed file a module import names, for the expander to read macros of.
    const ast::SourceUnit* imported_unit(const ast::ModuleImport* node);

    DiagEngine& diags;
    SourceManager& sources;
    // The trees of modules the expander read, kept for the checker.
    std::vector<std::shared_ptr<ast::SyntaxArena>> module_arenas;
  };

} // namespace pl4g::driver
