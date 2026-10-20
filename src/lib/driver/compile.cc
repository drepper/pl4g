// The stages of a compilation up to the IR.  A port of the front-end half of
// pypl4g's driver/main.py.
#include "driver/compile.hh"

#include "diag/ids.hh"
#include "front/expand.hh"
#include "front/lexer.hh"
#include "front/parser.hh"
#include "sema/check-helpers.hh"


namespace pl4g::driver {

  std::vector<const ast::SourceUnit*> Compilation::read_and_parse()
  {
    std::vector<const ast::SourceUnit*> units;
    for (const auto& path : options.inputs) {
      auto read = sources.read(path);
      if (! read) [[unlikely]] {
        if (read.error().kind == ReadFailure::Kind::not_utf8)
          diags.emit(D::LANG_SYNTAX_INVALID_UTF8, invalid_span, { { "offset", read.error().offset } });
        else
          diags.emit(D::IMPL_INPUT_UNREADABLE, invalid_span, { { "path", path.string() }, { "reason", read.error().reason } });
        continue;
      }
      const SourceFile& file = **read;
      auto tokens = tokenize(file, diags, false);
      auto unit = parse(arena, std::move(tokens), path.generic_string(), diags, false, &file);
      sources.record(path, unit);
      units.push_back(unit);
    }
    return units;
  }


  sema::ModuleRegistry Compilation::registry() const
  {
    sema::SearchPath search;
    search.given = options.module_path;
    return sema::ModuleRegistry(std::move(search));
  }


  const ast::SourceUnit* Compilation::imported_unit(const ast::ModuleImport* node)
  {
    auto found = registry();
    std::filesystem::path path;
    try {
      path = found.resolve(node->source, options.inputs.empty() ? std::filesystem::path() : options.inputs.front());
    } catch (const sema::ModuleNotFound&) {
      // Not reported here: the checker resolves imports too and says so with
      // the span and the places it looked, which is the message worth having.
      return nullptr;
    }
    if (auto earlier = sources.parsed(path))
      return static_cast<const ast::SourceUnit*>(earlier->unit);
    auto read = sources.read(path);
    if (! read)
      return nullptr;
    // What the expander wants of it is its macros; a function's body is
    // parsed only if something reads it.  Kept for the checker, which reads
    // the same file for the rest.
    auto held = std::make_shared<ast::SyntaxArena>();
    auto tokens = tokenize(**read, diags, true);
    auto unit = parse(*held, std::move(tokens), path.generic_string(), diags, true, *read);
    sources.remember(path, { unit, held });
    module_arenas.push_back(std::move(held));
    return unit;
  }


  std::vector<const ast::SourceUnit*> Compilation::expand(std::span<const ast::SourceUnit* const> units)
  {
    auto checked = [this](std::span<const ast::SourceUnit* const> macros) -> std::optional<MacroModule> {
      auto found = registry();
      auto made = sema::check_macros(macros, diags, &sources, &found,
                                     options.inputs.empty() ? std::nullopt : std::optional(options.inputs.front()));
      if (made.module == nullptr)
        return std::nullopt;
      return MacroModule{ std::move(made.module), std::move(made.quoted) };
    };
    return pl4g::expand(arena, units, diags, checked,
                        [this](const ast::ModuleImport* node) { return imported_unit(node); });
  }


  std::unique_ptr<ir::Module> Compilation::analyze(std::span<const ast::SourceUnit* const> units, sema::Notes* notes)
  {
    auto module = std::make_unique<ir::Module>();
    module->name = options.inputs.empty() ? "<none>" : options.inputs.front().filename().string();
    module->triple = options.triple;
    if (options.stack_size)
      module->stack_size = *options.stack_size;
    if (options.guard_size)
      module->guard_size = *options.guard_size;
    auto found = registry();
    sema::check(*module, units, diags, &found, &sources, notes, options.conditions, false, options.ignore_system);
    return module;
  }


  std::unique_ptr<ir::Module> Compilation::front_end(sema::Notes* notes)
  {
    auto units = read_and_parse();
    auto before = diags.error_count;
    auto expanded = expand(units);
    if (diags.error_count > before)
      // Nothing is checked after an expansion that failed: what the checker
      // would be handed is the program the macro could not write.
      return nullptr;
    return analyze(expanded, notes);
  }

} // namespace pl4g::driver
