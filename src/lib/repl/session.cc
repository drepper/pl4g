// The interactive session.
//
// Each input is checked as a program of its own, one file: the function the
// input runs in -- or, for a definition, the definition -- first, so that a
// diagnostic about what was typed names the lines it was typed on, and then
// `std`, the session's variables and the definitions kept so far.  A file's
// names are its own, so everything the input may name has to be in that one
// file.  Statements are checked twice where they bind something the session
// keeps or come to a value it shows: once to learn what those are, which is
// the check that is reported, and once more, quietly, with a variable of the
// session declared for each to be put in.  What the variables held is put
// back into the next program's before it starts; the programs that ran stay,
// since what a variable holds may point into what one of them placed.
#include "repl/repl.hh"

#include <algorithm>
#include <cstring>
#include <format>
#include <print>

#include "diag/render.hh"
#include "front/lexer.hh"
#include "front/parser.hh"
#include "opt/passes.hh"
#include "repl/lines.hh"
#include "repl/show.hh"
#include "sema/check-helpers.hh"
#include "util/fdio.hh"
#include "util/unicode.hh"


namespace pl4g::repl {

  namespace {

    // The function an input of statements runs in, and the names the session
    // gives what it keeps of an input.
    constexpr std::string_view function_name = "__pl4g_repl";
    constexpr std::string_view kept_prefix = "__pl4g_kept_";
    constexpr std::string_view answer_name = "__pl4g_answer";
    // How far the lines of an input of statements are indented, to be the
    // function's body, and the line above them that makes it one.
    constexpr unsigned body_indent = 4;
    constexpr std::string_view startup_line = "@[startup, impure] fn __pl4g_repl(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n";

    // A variable of the session: what it is called, its type as the program
    // writes it, and what it held when the input that changed it last ended.
    struct Variable {
      std::string name;
      std::string type;
      const ir::Type* ty = nullptr;
      bool is_mutable = false;
      std::vector<std::byte> bytes;
    };

    // A definition kept: its text, and the name it defines.
    struct Definition {
      std::string text;
      std::string name;
    };

    // What a check of an input is about: the text of the file, the part of it
    // that is what was typed, and how that part is shown.
    struct Layout {
      std::string text;
      std::size_t input_begin = 0;
      std::size_t input_end = 0;
      int line_shift = 0;
      unsigned indent = 0;
    };

    // Whether `item` is a definition the session keeps, and what it defines.
    std::optional<std::string> defined_by(const ast::Node* item)
    {
      switch (item->tag) {
      case ast::NodeKind::FuncDef: {
        auto f = static_cast<const ast::FuncDef*>(item);
        return f->held ? std::format("{}.{}", *f->held, f->name) : std::string(f->name);
      }
      case ast::NodeKind::TypeDef:
        return std::string(static_cast<const ast::TypeDef*>(item)->name);
      case ast::NodeKind::EnumDef:
        return std::string(static_cast<const ast::EnumDef*>(item)->name);
      case ast::NodeKind::UnitDef:
        return std::string(static_cast<const ast::UnitDef*>(item)->name);
      case ast::NodeKind::BundleDef:
        return std::string(static_cast<const ast::BundleDef*>(item)->name);
      case ast::NodeKind::MacroDef:
        return std::string(static_cast<const ast::MacroDef*>(item)->name);
      case ast::NodeKind::ModuleImport:
        return std::string(static_cast<const ast::ModuleImport*>(item)->name);
      default:
        return std::nullopt;
      }
    }

    // Where an item begins, its attributes being part of it.
    Loc start_of(const ast::Node* item)
    {
      Loc start = item->span.start;
      auto take = [&](std::span<const ast::Attribute* const> attrs) {
        for (auto one : attrs)
          start = std::min(start, one->span.start);
      };
      if (auto f = ast::as<ast::FuncDef>(item))
        take(f->attrs);
      else if (auto t = ast::as<ast::TypeDef>(item))
        take(t->attrs);
      else if (auto e = ast::as<ast::EnumDef>(item))
        take(e->attrs);
      else if (auto u = ast::as<ast::UnitDef>(item))
        take(u->attrs);
      return start;
    }

    // Whether what was typed so far is all of an input: no bracket is open,
    // and a line that opens a block has had the empty line that ends it.
    bool complete(std::string_view text)
    {
      int open = 0;
      bool in_text = false;
      for (std::size_t at = 0; at < text.size();) {
        auto d = unicode::decode(text, at);
        at += std::max(d.length, 1u);
        if (in_text) {
          if (d.cp == U'\\')
            at += 1;
          else if (d.cp == U'"')
            in_text = false;
          continue;
        }
        switch (d.cp) {
        case U'"': in_text = true; break;
        case U'(': case U'[': case U'{': case U'\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}':
        case U'\N{LEFT DOUBLE PARENTHESIS}': ++open; break;
        case U')': case U']': case U'}': case U'\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}':
        case U'\N{RIGHT DOUBLE PARENTHESIS}': --open; break;
        default: break;
        }
      }
      if (open > 0)
        return false;
      bool opens_a_block = false;
      std::size_t begin = 0;
      while (begin < text.size()) {
        auto end = text.find('\n', begin);
        auto line = text.substr(begin, end == std::string_view::npos ? std::string_view::npos : end - begin);
        auto last = line.find_last_not_of(" \t");
        if (last != std::string_view::npos && line[last] == ':')
          opens_a_block = true;
        if (end == std::string_view::npos)
          break;
        begin = end + 1;
      }
      return ! opens_a_block || text.ends_with("\n") || text.empty();
    }

    std::string_view trimmed(std::string_view text)
    {
      auto first = text.find_first_not_of(" \t\n");
      if (first == std::string_view::npos)
        return {};
      auto last = text.find_last_not_of(" \t\n");
      return text.substr(first, last - first + 1);
    }

  } // anonymous namespace


  struct Session::State {
    Settings settings;
    SourceManager sources;
    TextRenderer renderer{ sources, settings.devices.error };
    FdWriter out{ settings.devices.output };
    FdWriter err{ settings.devices.error };
    std::vector<Variable> variables;
    std::vector<Definition> definitions;
    unsigned inputs = 0;
    // What was made for each input that ran, kept because what a variable
    // holds may point into it.
    std::vector<std::unique_ptr<driver::Compilation>> compilations;
    std::vector<std::unique_ptr<ir::Module>> modules;
    std::vector<std::unique_ptr<interp::Program>> programs;

    explicit State(Settings given) : settings(std::move(given))
    {
      renderer.recolour(settings.colour, settings.devices.output);
      // A module the session imports may be one held in memory.
      for (const auto& [path, text] : settings.held)
        sources.hold(path, text);
    }

    // -- making the program ---------------------------------------------------

    // The rest of the file after what was typed: `std`, the variables, the
    // definitions, and the variables the session puts what it keeps in.
    std::string rest(std::span<const std::pair<std::string, Variable>> kept, const ir::Type* answer) const
    {
      std::string text = "\nlet std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n";
      auto declare = [&](std::string_view name, std::string_view type, bool is_mutable) {
        text += std::format("let {}: {}{} = 0\n", name, is_mutable ? "mut " : "", type);
      };
      for (const auto& one : variables)
        if (std::ranges::none_of(kept, [&](const auto& k) { return k.second.name == one.name; }))
          declare(one.name, one.type, one.is_mutable);
      for (const auto& [alias, one] : kept)
        declare(alias, one.type, true);
      if (answer != nullptr)
        declare(answer_name, answer->written(), true);
      for (const auto& one : definitions)
        text += one.text + "\n";
      return text;
    }

    // The file for an input of statements.
    Layout statements(std::string_view typed, std::span<const std::pair<std::string, Variable>> kept,
                      const ir::Type* answer) const
    {
      Layout made;
      made.text = startup_line;
      made.input_begin = made.text.size();
      std::size_t begin = 0;
      while (begin <= typed.size()) {
        auto end = typed.find('\n', begin);
        auto line = typed.substr(begin, end == std::string_view::npos ? std::string_view::npos : end - begin);
        made.text.append(body_indent, ' ');
        made.text += line;
        made.text += '\n';
        if (end == std::string_view::npos)
          break;
        begin = end + 1;
      }
      made.input_end = made.text.size();
      made.text.append(body_indent, ' ');
      made.text += "0u6\n";
      made.text += rest(kept, answer);
      made.line_shift = -1;
      made.indent = body_indent;
      return made;
    }

    // The file for an input of definitions: they come first.
    Layout definitions_first(std::string_view typed) const
    {
      Layout made;
      made.text = std::string(typed);
      made.input_end = made.text.size();
      made.text += "\n";
      made.text += startup_line;
      made.text.append(body_indent, ' ');
      made.text += "0u6\n";
      made.text += rest({}, nullptr);
      return made;
    }

    // -- checking it ----------------------------------------------------------

    struct Checked {
      std::unique_ptr<driver::Compilation> compilation;
      std::unique_ptr<ir::Module> module;
      const ast::SourceUnit* unit = nullptr;
      const SourceFile* file = nullptr;
      bool failed = true;
    };

    // Check the file `layout`, saying what is wrong with what was typed --
    // and every error, wherever it is -- unless `quiet`.
    Checked check(const Layout& layout, std::string_view shown, sema::Session* hooks, bool quiet,
                  const std::function<void(const ast::SourceUnit*, const SourceFile&, sema::Session&)>& prepare)
    {
      Checked made;
      const SourceFile& file = sources.add(std::string(shown), layout.text, layout.line_shift, layout.indent);
      made.file = &file;
      Loc begin = file.base + Loc(layout.input_begin);
      Loc end = file.base + Loc(layout.input_end);
      bool last_shown = false;
      DiagEngine diags(
        [&](const Diagnostic& d) {
          if (quiet)
            return;
          bool shows;
          if (d.info->severity == Severity::note)
            // Not where the session declared a variable: that is no line
            // anybody typed.
            shows = last_shown && (! d.span.is_valid() || d.span.start < file.base || d.span.start > file.end()
                                   || (d.span.start >= begin && d.span.start <= end));
          else
            shows = ! d.span.is_valid() || (d.span.start >= begin && d.span.start <= end)
                    || d.info->severity >= Severity::error;
          last_shown = shows;
          if (shows)
            renderer(d);
        },
        settings.warnings);
      renderer.use(diags);
      driver::Options options = settings.compile;
      options.inputs.clear();
      // What the session runs is run by the interpreter, in a program that
      // may define what a function the host defines names.
      options.output_kind = ir::Output::interpreted;
      made.compilation = std::make_unique<driver::Compilation>(options, diags, sources);
      auto& compilation = *made.compilation;
      auto tokens = tokenize(file, diags, false);
      made.unit = parse(compilation.arena, std::move(tokens), file.shown, diags, false, &file);
      if (diags.failed())
        return made;
      if (prepare && hooks != nullptr)
        prepare(made.unit, file, *hooks);
      std::vector<const ast::SourceUnit*> units{ made.unit };
      auto expanded = compilation.expand(units);
      if (diags.failed())
        return made;
      made.module = compilation.analyze(expanded, nullptr, hooks);
      made.failed = diags.failed();
      return made;
    }

    // The statements of the function an input runs in, as `unit` has them.
    static std::span<const ast::Stmt* const> body_of(const ast::SourceUnit* unit)
    {
      for (auto item : unit->items)
        if (auto f = ast::as<ast::FuncDef>(item); f != nullptr && f->name == function_name && f->body != nullptr)
          return f->body->stmts();
      return {};
    }

    // -- running it -----------------------------------------------------------

    // Run `module`, its variables of the session given what they held; the
    // program, kept, or nothing where it could not run.
    interp::Program* run(std::unique_ptr<ir::Module> module)
    {
      opt::run_pipeline(*module);
      interp::Options options;
      options.args.push_back("pl4g");
      options.args.insert(options.args.end(), settings.args.begin(), settings.args.end());
      options.environment = settings.environment;
      options.devices = settings.devices;
      options.sources = &sources;
      options.defined = &settings.defined;
      auto program = std::make_unique<interp::Program>(*module, std::move(options));
      for (const auto& one : variables)
        if (auto var = global_named(*module, one.name)) {
          auto at = program->place_of(var);
          if (at != nullptr && ir::size_of(var->value_type, layout()) == one.bytes.size())
            std::memcpy(at, one.bytes.data(), one.bytes.size());
        }
      auto outcome = program->start();
      // What the input changed of them is what they hold now.
      for (auto& one : variables)
        if (auto var = global_named(*module, one.name)) {
          auto at = program->place_of(var);
          if (at != nullptr && ir::size_of(var->value_type, layout()) == one.bytes.size())
            std::memcpy(one.bytes.data(), at, one.bytes.size());
        }
      if (outcome.signal != 0)
        err.println("stopped by signal {}", outcome.signal);
      else if (outcome.status != 0)
        err.println("exited with status {}", outcome.status);
      err.flush();
      modules.push_back(std::move(module));
      programs.push_back(std::move(program));
      return programs.back().get();
    }

    static ir::GlobalVar* global_named(const ir::Module& module, std::string_view name)
    {
      for (const auto& [_, var] : module.globals)
        if (var->name == name)
          return var.get();
      return nullptr;
    }

    const ir::DataLayout& layout() const
    {
      static const ir::DataLayout plain;
      return plain;
    }

    // -- the inputs -------------------------------------------------------------

    // Take an input of definitions: kept, a later one of a name taking the
    // place of the earlier one, where the program they make checks.
    void define(std::string_view typed, std::string label)
    {
      // What it defines, and where each definition's text is.
      CollectingEngine quiet;
      SourceManager scratch;
      const SourceFile& file = scratch.add(label, std::string(typed));
      ast::SyntaxArena arena;
      auto unit = parse(arena, tokenize(file, quiet.engine, false), file.shown, quiet.engine, false, &file);
      std::vector<Definition> made;
      for (std::size_t i = 0; i < unit->items.size(); ++i) {
        auto item = unit->items[i];
        auto begin = std::size_t(start_of(item) - file.base);
        auto end = i + 1 < unit->items.size() ? std::size_t(start_of(unit->items[i + 1]) - file.base) : typed.size();
        made.push_back({ std::string(trimmed(typed.substr(begin, end - begin))), *defined_by(item) });
      }
      auto kept = definitions;
      std::erase_if(definitions, [&](const Definition& old) {
        return std::ranges::any_of(made, [&](const Definition& one) { return one.name == old.name; });
      });
      sema::Session hooks;
      hooks.function = function_name;
      for (const auto& one : variables)
        hooks.variables.insert(one.name);
      auto checked = check(definitions_first(typed), label, &hooks, false, {});
      if (checked.failed) {
        definitions = std::move(kept);
        return;
      }
      definitions.insert(definitions.end(), made.begin(), made.end());
      compilations.push_back(std::move(checked.compilation));
    }

    // Take an input of statements: run them, keep what they bound at their
    // top, and show what the last of them came to.
    void execute(std::string_view typed, std::string label, bool only_the_type = false)
    {
      sema::Session hooks;
      hooks.function = function_name;
      for (const auto& one : variables)
        hooks.variables.insert(one.name);
      // What the input binds at its top, and its last statement.
      std::vector<std::tuple<std::string, bool, Span>> bound;
      auto prepare = [&](const ast::SourceUnit* unit, const SourceFile& file, sema::Session& into) {
        bound.clear();
        into.kept.clear();
        into.answer = nullptr;
        Loc input_end = file.base + Loc(file.text.find("\n" + std::string(body_indent, ' ') + "0u6\n"));
        for (auto stmt : body_of(unit)) {
          if (stmt->span.start >= input_end)
            break;
          if (auto def = ast::as<ast::VarDef>(stmt)) {
            into.kept.insert(def->name_span.start);
            bound.emplace_back(std::string(def->name), def->mutable_, def->name_span);
            for (const auto& more : def->more) {
              into.kept.insert(more.span.start);
              bound.emplace_back(std::string(more.name), def->mutable_, more.span);
            }
          }
          into.answer = nullptr;
          if (auto expr = ast::as<ast::ExprStmt>(stmt))
            if (! ast::is<ast::If>(expr->value) && ! ast::is<ast::While>(expr->value)
                && ! ast::is<ast::ForEach>(expr->value) && ! ast::is<ast::Match>(expr->value))
              into.answer = stmt;
        }
      };
      auto first = check(statements(typed, {}, nullptr), label, &hooks, false, prepare);
      if (first.failed)
        return;
      const ir::Type* answer = hooks.answer_type;
      if (answer == ir::void_type() || answer == ir::error_type())
        answer = nullptr;
      if (only_the_type) {
        if (answer != nullptr)
          out.println("{}", answer->written());
        else
          out.println("nothing");
        out.flush();
        return;
      }
      // What the session keeps: each name bound at the top whose value can
      // outlast the input.
      std::vector<std::pair<std::string, Variable>> kept;
      for (const auto& [name, is_mutable, span] : bound) {
        if (name == "_")
          continue;
        auto ty = hooks.types.get(name);
        if (ty == nullptr || *ty == nullptr || *ty == ir::error_type())
          continue;
        if (sema::holds_a_lambda(*ty) || sema::holds_a_short_reference(*ty) || sema::short_lived(*ty) != nullptr) {
          err.println("'{}' is not kept: a value of '{}' does not outlast the input that made it", name,
                      (*ty)->written());
          continue;
        }
        Variable one{ name, (*ty)->written(), *ty, is_mutable, {} };
        kept.emplace_back(std::format("{}{}", kept_prefix, kept.size()), std::move(one));
      }
      err.flush();
      if (kept.empty() && answer == nullptr) {
        compilations.push_back(std::move(first.compilation));
        run(std::move(first.module));
        return;
      }
      // Again, with somewhere to put all of it.
      sema::Session again;
      again.function = function_name;
      for (const auto& one : variables)
        again.variables.insert(one.name);
      for (const auto& [alias, one] : kept) {
        again.variables.insert(alias);
        again.stores[one.name] = alias;
      }
      if (answer != nullptr) {
        again.variables.insert(std::string(answer_name));
        again.answer_variable = answer_name;
      }
      auto second = check(statements(typed, kept, answer), label, &again, true, prepare);
      if (second.failed) {
        err.println("what the input bound could not be kept: its type cannot be written down");
        err.flush();
        compilations.push_back(std::move(first.compilation));
        run(std::move(first.module));
        return;
      }
      auto module = second.module.get();
      compilations.push_back(std::move(second.compilation));
      auto program = run(std::move(second.module));
      // What the input left in the session's variables.
      for (auto& [alias, one] : kept) {
        auto var = global_named(*module, alias);
        auto at = var != nullptr ? program->place_of(var) : nullptr;
        if (at == nullptr)
          continue;
        auto size = ir::size_of(var->value_type, layout());
        one.bytes.assign(at, at + size);
        std::erase_if(variables, [&](const Variable& old) { return old.name == one.name; });
        variables.push_back(std::move(one));
      }
      if (answer != nullptr)
        if (auto var = global_named(*module, answer_name))
          if (auto at = program->place_of(var)) {
            out.println("{} : {}", shown(var->value_type, at), answer->written());
            out.flush();
          }
    }

    std::string shown(const ir::Type* ty, const std::byte* at) const { return repl::shown(ty, at, layout()); }

    // Whether `typed` is definitions and nothing else.
    bool defines(std::string_view typed) const
    {
      CollectingEngine quiet;
      SourceManager scratch;
      const SourceFile& file = scratch.add("input", std::string(typed));
      ast::SyntaxArena arena;
      auto unit = parse(arena, tokenize(file, quiet.engine, false), file.shown, quiet.engine, false, &file);
      if (quiet.engine.failed() || unit->items.empty())
        return false;
      return std::ranges::all_of(unit->items, [](const ast::Node* item) { return defined_by(item).has_value(); });
    }

    // -- the session's own commands ---------------------------------------------

    // Answer `@command rest`; false where the session is to end.
    bool command(std::string_view typed)
    {
      auto end = typed.find_first_of(" \t\n");
      auto name = typed.substr(1, end == std::string_view::npos ? std::string_view::npos : end - 1);
      auto rest = trimmed(end == std::string_view::npos ? std::string_view() : typed.substr(end));
      if (name == "quit" || name == "exit")
        return false;
      if (name == "help") {
        out.print("{}", help_text);
      } else if (name == "type") {
        if (rest.empty())
          err.println("@type wants an expression");
        else
          execute(rest, next_name(), true);
      } else if (name == "load") {
        load(rest);
      } else if (name == "defs") {
        for (const auto& one : definitions)
          out.println("{}", one.text);
      } else if (name == "vars") {
        for (const auto& one : variables)
          out.println("{}{} : {} = {}", one.is_mutable ? "mut " : "", one.name, one.type,
                      one.ty != nullptr ? shown(one.ty, one.bytes.data()) : std::string("?"));
      } else if (name == "reset") {
        variables.clear();
        definitions.clear();
      } else {
        err.println("there is no command @{}; @help lists them", name);
      }
      out.flush();
      err.flush();
      return true;
    }

    // `@load PATH`: the definitions a file holds, as though they were typed.
    void load(std::string_view path)
    {
      if (path.empty()) {
        err.println("@load wants the name of a file");
        return;
      }
      auto read = sources.read(std::filesystem::path(path));
      if (! read) {
        err.println("cannot read {}: {}", path, read.error().reason);
        return;
      }
      std::string text = (*read)->text;
      if (! defines(text)) {
        err.println("{} holds something other than definitions; @load takes definitions only", path);
        return;
      }
      define(text, std::string(path));
    }

    std::string next_name() { return std::format("repl[{}]", ++inputs); }

    static constexpr std::string_view help_text =
      "Type a definition to keep it, a statement to run it, an expression to see what it comes to.\n"
      "A line ending in ':' opens a block, which an empty line closes.  The session's own commands:\n"
      "  @help          this\n"
      "  @type EXPR     the type of an expression, without running it\n"
      "  @load FILE     the definitions in a file\n"
      "  @defs          the definitions kept\n"
      "  @vars          the variables kept, and what they hold\n"
      "  @reset         forget every definition and variable\n"
      "  @quit          end the session (so does the end of the input)\n";

    // Take one input.
    bool take(std::string_view typed)
    {
      typed = trimmed(typed);
      if (typed.empty())
        return true;
      if (typed.starts_with('@') && ! typed.starts_with("@["))
        return command(typed);
      if (defines(typed))
        define(typed, next_name());
      else
        execute(typed, next_name());
      return true;
    }
  };


  Session::Session(Settings settings) : state(std::make_unique<State>(std::move(settings))) {}

  Session::~Session() = default;


  bool Session::feed(std::string_view text)
  {
    return state->take(text);
  }


  int Session::interact()
  {
    const auto& devices = state->settings.devices;
    LineReader reader(devices.input, devices.output, devices.error);
    std::string typed;
    while (true) {
      auto line = reader.read(typed.empty() ? "pl4g> " : "  ... ");
      if (! line) {
        if (! typed.empty())
          state->take(typed);
        break;
      }
      typed += *line;
      typed += '\n';
      if (! complete(std::string_view(typed).substr(0, typed.size() - (line->empty() ? 0 : 1))))
        continue;
      reader.remember(trimmed(typed));
      bool goes_on = state->take(typed);
      typed.clear();
      if (! goes_on)
        break;
    }
    return 0;
  }

} // namespace pl4g::repl
