// The checker, part one: where checking starts, the variables at the top
// level and what they are given, the modules a file reads, what a definition
// says it raises, and the scopes of a body.
//
// A port of pypl4g's sema/check.py, lines 4249 to 6565.
#include "sema/check.hh"

#include <algorithm>
#include <format>
#include <ranges>

#include "front/lexer.hh"
#include "front/names.hh"
#include "front/parser.hh"
#include "ir/printer.hh"
#include "sema/environ.hh"
#include "sema/lastuse.hh"
#include "util/unicode.hh"


namespace pl4g::sema {

  namespace {

    // What a turn of a loop is bound to in a name the compiler made up.
    constexpr std::string_view iter_glyph = "\N{RIGHTWARDS PAIRED ARROWS}";

    // The largest code point there is.
    constexpr __int128 max_code_point = 0x10FFFF;

    // A constant as a value an instruction may use: what the module made is
    // never changed, and pypl4g's values are not const either.
    ir::Value* mut(const ir::Value* value) noexcept
    {
      return const_cast<ir::Value*>(value);
    }

    // Python's `UndefConst(ERROR)`: a new one each time, as pypl4g makes it.
    ir::Value* undef_error(ir::Module& module)
    {
      return mut(module.constant<ir::UndefConst>(ir::error_type()));
    }

    // Python's `str.isidentifier`, near enough: what is not one is an
    // operator's glyph.
    bool is_identifier(std::string_view name)
    {
      if (name.empty())
        return false;
      for (std::size_t at = 0; at < name.size();) {
        auto [cp, length] = unicode::decode(name, at);
        if (cp == unicode::end_of_text) [[unlikely]]
          return false;
        if (cp != U'_' && ! (at == 0 ? unicode::is_alpha(cp) : unicode::is_alnum(cp)))
          return false;
        at += length;
      }
      return true;
    }

    // Whether a type as written names a piece of the program.
    bool names_syntax(const ast::Node* node)
    {
      if (auto ref = ast::as<ast::TypeRef>(node); ref != nullptr && ! ref->module && ref->name == "syntax")
        return true;
      bool found = false;
      ast::for_each_child(node, [&](const ast::Node* one) { found = found || names_syntax(one); });
      return found;
    }

    // Whether a top-level definition belongs to the macros and not to the
    // program: pypl4g's `front.expand.only_for_macros`.
    bool only_for_macros(const ast::Node* item)
    {
      if (ast::is<ast::MacroDef>(item))
        return true;
      auto func = ast::as<ast::FuncDef>(item);
      if (func == nullptr)
        return false;
      if (func->is_macro)
        return true;
      if (! func->at_compile_time)
        return false;
      for (auto param : func->params)
        if (names_syntax(param->type))
          return true;
      return func->ret_type != nullptr && names_syntax(func->ret_type);
    }

    // Whether the catalog has a diagnostic of this number.
    bool in_the_catalog(__int128 number)
    {
      auto all = catalog();
      return std::ranges::any_of(all, [&](const DiagInfo& info) { return __int128(info.number) == number; });
    }

    // A path as Python's `str(path)` writes it.
    std::string path_string(const std::filesystem::path& path)
    {
      return pure_path(path).string();
    }

    // The record of a table's block: what `__pl4g_table_new` makes.
    ir::ProductType* table_block(const ir::Type* arena)
    {
      auto block = ir::new_product();
      block->fields = { { "arena", arena },
                        { "mask", ir::u64() },
                        { "count", ir::u64() },
                        { "stride", ir::u64() },
                        { "entries", ir::ptr_type(ir::u8(), true) },
                        { "used", ir::u64() },
                        { "seed", ir::u64() },
                        { "index", ir::ptr_type(ir::u8(), true) } };
      block->name = "table";
      return block;
    }

  } // anonymous namespace


  // -- entry point ------------------------------------------------------------

  ir::Module& Checker::run(std::span<const ast::SourceUnit* const> given, bool whole_program)
  {
    std::vector<Collected*> collected;
    std::vector<const ast::SourceUnit*> kept(given.begin(), given.end());
    if (! for_macros)
      // What was written for the macros is not part of the program: nothing at
      // run time may hold a piece of the program.
      for (auto& unit : kept) {
        std::vector<const ast::Node*> items;
        for (auto item : unit->items)
          if (! only_for_macros(item))
            items.push_back(item);
        auto copy = syntax.copy(*unit);
        copy->items = syntax.list(items);
        unit = copy;
      }
    units = kept;
    for (auto unit : units)
      mark_system(unit);
    definitions.clear();
    top_written.clear();
    for (auto unit : units)
      for (auto item : unit->items) {
        if (auto func = ast::as<ast::FuncDef>(item))
          definitions[std::string(func->name)] = func;
        if (auto var = ast::as<ast::VarDef>(item))
          top_written[std::string(var->name)] = var;
      }
    // The imports first, since a type may be one another module defines; then
    // every unit and type name, then what the types are made of, and only then
    // the functions and the variables, whose signatures name types.
    for (auto unit : units) {
      module.source_paths.emplace_back(unit->path);
      for (auto item : unit->items)
        if (auto import = ast::as<ast::ModuleImport>(item))
          collect_import(import);
    }
    for (auto unit : units)
      for (auto item : unit->items)
        if (auto def = ast::as<ast::UnitDef>(item))
          define_unit(def);
    for (auto unit : units)
      for (auto item : unit->items)
        if (ast::is<ast::TypeDef>(item) || ast::is<ast::EnumDef>(item)) {
          collect_type(item, unit->path);
          field_slots(item);
        }
    // Bundles before the functions, since a clause of one may apply a bundle
    // written below it.
    for (auto unit : units)
      for (auto item : unit->items)
        if (auto bundle = ast::as<ast::BundleDef>(item))
          collect_bundle(bundle, unit->path);
    check_bundle_cycles();
    for (auto defined : std::vector(named_types))
      if (defined->takes_types())
        check_generic_type(defined);
      else
        resolved(defined);
    for (auto unit : units)
      for (auto item : unit->items)
        switch (item->tag) {
        case ast::NodeKind::FuncDef:
          if (auto gathered = collect_function(static_cast<const ast::FuncDef*>(item), unit->path); gathered != nullptr)
            collected.push_back(gathered);
          break;
        case ast::NodeKind::VarDef:
          collect_global(static_cast<const ast::VarDef*>(item));
          break;
        case ast::NodeKind::TypeDef:
          if (auto type = static_cast<const ast::TypeDef*>(item); ! type->singletons.empty())
            collect_singletons(type);
          break;
        case ast::NodeKind::ModuleImport:
        case ast::NodeKind::EnumDef:
        case ast::NodeKind::UnitDef:
        case ast::NodeKind::BundleDef:
        case ast::NodeKind::MacroDef:
          // A macro written as rules is no function and has nothing to lower.
          break;
        default:
          diags.internal("unknown kind of top-level definition");
        }
    check_closers();
    // Callees before their callers, as far as the call tree goes.
    if (whole_program || whole_modules || for_macros || notes != nullptr) {
      // Everything here is lowered, so everything it names in a module has
      // to be there first.
      NameSet wanted;
      mentioned(std::span<const ast::SourceUnit* const>(units), wanted);
      ask_the_modules(wanted);
      for (auto entry : callees_first(collected))
        lower_function(entry);
      for (auto one : std::vector(generics_here))
        check_generic(one);
      if ((whole_program || whole_modules) && diags.error_count == 0)
        // Only of a program that is otherwise right: a body an error stopped
        // part way has not said all it does.
        check_marks();
    } else {
      // A module read by something else is lowered as far as something names
      // it, and no further.  What runs whatever names it, and what the
      // variables here are given, is lowered now; everything else waits.
      waiting = collected;
      waiting_generics = generics_here;
      NameSet roots;
      for (const auto& [name, item] : top_written) {
        if (in_the_image(item->value))
          // Worked out while compiling: nothing of it is in the program.
          continue;
        mentioned(item->value, roots);
      }
      for (auto entry : collected)
        if (entry->func->attrs.special.has_value() || entry->func->linkage != ir::Linkage::internal)
          roots.insert(std::string(entry->node->name));
      want(roots);
    }
    build_the_tables();
    if (whole_program)
      check_program();
    return module;
  }


  void Checker::want(const NameSet& given)
  {
    NameSet names = given;
    std::vector<Collected*> chosen;
    std::vector<Generic*> generics;
    for (;;) {
      bool grew = false;
      for (auto entry : std::vector(waiting))
        if (names.contains(entry->node->name) || ! is_identifier(entry->node->name)) {
          std::erase(waiting, entry);
          chosen.push_back(entry);
          mentioned(entry->node, names);
          grew = true;
        }
      for (auto one : std::vector(waiting_generics))
        if (names.contains(one->node->name) || ! is_identifier(one->node->name)) {
          std::erase(waiting_generics, one);
          generics.push_back(one);
          mentioned(one->node, names);
          if (one->written_params)
            for (auto param : *one->written_params)
              mentioned(param, names);
          grew = true;
        }
      if (! grew)
        break;
    }
    if (chosen.empty() && generics.empty()) {
      for (const auto& name : exported_imports) {
        auto held = top->get(name);
        if (held == nullptr)
          continue;
        if (auto loaded = held->get<LoadedModule>(); loaded != nullptr && loaded->want)
          loaded->want(names);
      }
      return;
    }
    ask_the_modules(names);
    for (auto entry : callees_first(chosen))
      lower_function(entry);
    for (auto one : generics)
      check_generic(one);
  }


  LoadedModule* Checker::module_at(const ast::Expr* expr)
  {
    if (auto ref = ast::as<ast::NameRef>(expr)) {
      auto held = top->get(std::string(ref->name));
      return held == nullptr ? nullptr : held->get<LoadedModule>();
    }
    if (auto member = ast::as<ast::Member>(expr)) {
      auto outer = module_at(member->base);
      if (outer == nullptr)
        return nullptr;
      auto held = outer->exports.get(std::string(member->name));
      return held == nullptr ? nullptr : held->get<LoadedModule>();
    }
    return nullptr;
  }


  LoadedModule* Checker::module_called(std::string_view path)
  {
    auto parts = path | std::views::split('.');
    auto it = parts.begin();
    auto first = std::string(std::string_view(*it));
    auto found = top->get(first);
    Held held = found == nullptr ? Held() : *found;
    for (++it; it != parts.end(); ++it) {
      auto loaded = held.get<LoadedModule>();
      auto inner = loaded == nullptr ? nullptr : loaded->exports.get(std::string(std::string_view(*it)));
      held = inner == nullptr ? Held() : *inner;
    }
    return held.get<LoadedModule>();
  }


  void Checker::ask_the_modules(const NameSet& names)
  {
    std::set<const LoadedModule*> asked;
    std::vector<Held> all;
    for (const auto& [name, held] : *top)
      all.push_back(held);
    for (const auto& held : all)
      if (auto loaded = held.get<LoadedModule>(); loaded != nullptr && asked.insert(loaded).second)
        if (loaded->want)
          loaded->want(names);
  }


  void Checker::check_closers()
  {
    for (auto defined : named_types) {
      if (defined->closed_by.empty() || ! ir::is<ir::ProductType>(defined->ty))
        continue;
      auto found = members.get({ defined->name, defined->closed_by });
      std::optional<std::string> why;
      if (found == nullptr || found->empty())
        why = "is not defined in this file";
      else {
        auto value = found->get<ir::Value>();
        auto fty = value == nullptr ? nullptr : ir::as<ir::FuncType>(value->ty);
        if (fty == nullptr || fty->params.size() != 1 || fty->params[0] != defined->ty)
          why = std::format("does not take exactly one '{}' by value", defined->name);
      }
      if (why)
        diags.emit(D::LANG_CLOSED_BY_INVALID, defined->node->span,
                   { { "closer", defined->closed_by }, { "type", defined->name }, { "why", *why } });
    }
  }


  void Checker::build_the_tables()
  {
    if (built_globals.empty())
      return;
    auto func = std::make_unique<ir::Function>(unused_name(globals_symbol), ir::func_type({}, ir::void_type()));
    func->attrs.special = ir::SpecialKind::constructor;
    func->attrs.priority = 0;
    func->attrs.impure = true;
    func->linkage = ir::Linkage::internal;
    auto made = module.add_function(std::move(func));
    made->add_block();
    ir::Builder builder(module, *made);
    borrows.clear();
    places.clear();
    auto outer_answer = std::exchange(answering, ir::void_type());
    auto outer_impure = std::exchange(impure, true);
    push_scope();
    for (auto [var, node] : built_globals) {
      auto value = lower_into(builder, node->value, var->value_type, node->span);
      if (value_type_of(value) != ir::error_type())
        builder.store(var, value, node->span);
    }
    pop_scope();
    answering = outer_answer;
    impure = outer_impure;
    builder.ret();
    // In front of the program's own, and of another file's: what a
    // constructor may read is every table, whichever file it was written in.
    module.ctors.insert(module.ctors.begin(), made);
  }


  std::string Checker::unused_name(std::string_view wanted)
  {
    if (module.function(wanted) == nullptr)
      return std::string(wanted);
    std::int64_t at = 1;
    while (module.function(std::format("{}.{}", wanted, at)) != nullptr)
      ++at;
    return std::format("{}.{}", wanted, at);
  }


  // -- variables --------------------------------------------------------------

  Held Checker::provided(std::string_view name)
  {
    auto key = std::string(name);
    auto existing = top->get(key);
    Held found = existing == nullptr ? Held() : *existing;
    if (found.empty() && name == names::tolerance_name) {
      found = Held(static_cast<ir::Value*>(tolerance(module)));
      (*top)[key] = found;
    }
    if (found.empty() && name == names::heap_name) {
      found = Held(static_cast<ir::Value*>(heap(module)));
      (*top)[key] = found;
    }
    if (found.empty() && name == names::environ_name) {
      found = Held(static_cast<ir::Value*>(environ()));
      (*top)[key] = found;
    }
    return found;
  }


  ir::GlobalVar* Checker::environ()
  {
    auto heap = provided(names::heap_name).get<ir::GlobalVar>();
    contract_assert(heap != nullptr);
    auto made = sema::environ::variable(module);
    sema::environ::make_function(module, heap);
    module.environ = made;
    return made;
  }


  std::string Checker::key(std::string_view name)
  {
    if (path.filename().empty())
      return std::string(name);
    std::string filed = path_string(path);
    filed += '\0';
    filed += name;
    return filed;
  }


  bool Checker::declare(std::string_view name, Span span, std::string_view where)
  {
    if (name.starts_with(names::builtin_glyph)) {
      // The whole shape of name is the compiler's: a rule about the glyph,
      // not a clash with one definition.
      diags.emit(D::LANG_NAME_IS_THE_COMPILERS, span, { { "name", name } });
      return false;
    }
    auto key = std::string(name);
    if (auto previous = defined.get(key); previous != nullptr) {
      diags.emit(D::LANG_FILESTRUCT_DUPLICATE_DEFINITION, span, { { "name", name } })
        .note(D::LANG_FILESTRUCT_PREVIOUS_DEFINITION, std::get<0>(*previous), { { "name", name } });
      return false;
    }
    defined[key] = { span, std::string(where) };
    name_spans[key] = span;
    return true;
  }


  void Checker::collect_global(const ast::VarDef* node)
  {
    check_doc(node);
    if (node->name == names::wildcard_name) {
      diags.emit(D::LANG_WILDCARD_IS_NOT_DEFINED, node->span);
      return;
    }
    if (! declare(node->name, node->name_span))
      return;
    auto attrs = bind_attributes(node->attrs, AttrTarget::variable);
    if (session != nullptr && session->variables.contains(node->name)) {
      session_variable(node);
      return;
    }
    auto linkage = linkage_of(attrs);
    auto pairs = expected_numbers(attrs);
    auto expectation = begin_expecting(pairs);
    const ir::Type* ty = nullptr;
    const ir::Value* initializer = nullptr;
    bool is_static = false;
    {
      defining_globally = true;
      ty = variable_type(node);
      defining_globally = false;
      auto short_ = ty != nullptr ? short_lived(ty) : nullptr;
      if (short_ != nullptr) {
        diags.emit(D::LANG_ITERATOR_DOES_NOT_LEAVE, node->span,
                   { { "found", short_->written() }, { "what", "a variable at the top level" } });
        ty = ir::error_type();
      }
      if (ty != nullptr && holds_a_lambda(ty)) {
        diags.emit(D::LANG_LAMBDA_AT_TOP_LEVEL, node->span);
        ty = ir::error_type();
      } else if (ty != nullptr && holds_a_short_reference(ty)) {
        // A variable here lasts as long as the program, so what it names has
        // to as well.
        diags.emit(D::LANG_REF_AT_TOP_LEVEL, node->span);
        ty = ir::error_type();
      }
      // A collection is a table, built by running code unless it is `in
      // ⎕static`: what the variable holds until the constructor has run is
      // nought.
      is_static = in_the_image(node->value);
      if (ty != nullptr && ! image_definition(node, ty))
        ty = ir::error_type();
      // An empty one in the heap costs nothing until something is put in it.
      bool unbuilt = ty != nullptr && is_a_table(ty) && ! is_static && empty_in_the_heap(node->value);
      auto outer = std::exchange(for_the_image, is_static);
      if (unbuilt)
        initializer = empty_table_constant(ty);
      else if (ty == nullptr || (is_a_table(ty) && ! is_static))
        initializer = nullptr;
      else
        initializer = constant_value(node, ty);
      for_the_image = outer;
    }
    end_expecting(expectation);
    if (expectation != nullptr && expectation->saw_error) {
      // A variable whose definition could not be made sense of has no value
      // to put in the image; what it said it raises is settled now.
      settle_expecting(expectation, pairs);
      return;
    }
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr && ! array->fixed()) {
      diags.emit(D::LANG_ARRAY_NO_LENGTH_AT_TOP, node->span, { { "found", ty->written() } });
      ty = ir::error_type();
    }
    if (ty == nullptr)
      ty = ir::error_type();
    // A table's handle is written by the constructor whatever the program may
    // do with it, so the place it waits in is one that may be written.
    bool writable = node->mutable_ || (is_a_table(ty) && ! is_static) || has_mutable_field(ty);
    auto made = std::make_unique<ir::GlobalVar>(std::string(node->name), ty, ir::ptr_type(ty, writable));
    made->initializer = initializer;
    made->linkage = linkage;
    made->span = node->span;
    made->name_span = node->name_span;
    made->exported = is_export(attrs);
    made->system_layout = std::ranges::any_of(attrs, [](const BoundAttr& a) { return a.name() == "cdecl"; });
    auto var = module.add_global(std::move(made), key(node->name));
    (*top)[std::string(node->name)] = Held(static_cast<ir::Value*>(var));
    owned.push_back(Held(static_cast<ir::Value*>(var)));
    if (ty != ir::error_type())
      record_inferred(node->name, node->name_span, ty, node->type);
    // The expectation stays alive: whether anything reads this variable is not
    // known until every function has been checked.
    auto global = make<Global>();
    global->var = var;
    global->span = node->name_span;
    global->expectation = expectation;
    global->expected_pairs = pairs;
    top_level.push_back(global);
    if (is_a_table(ty) && ! is_static && initializer == nullptr)
      built_globals.emplace_back(var, node);
  }


  void Checker::session_variable(const ast::VarDef* node)
  {
    // What it holds is whatever the session put there, so what is written
    // after the `=` is not looked at.  Something this compilation cannot see
    // reads it and writes it: the session.
    defining_globally = true;
    const ir::Type* ty = node->type != nullptr ? resolve_type(node->type) : nullptr;
    defining_globally = false;
    if (ty == nullptr)
      ty = ir::error_type();
    auto made = std::make_unique<ir::GlobalVar>(std::string(node->name), ty, ir::ptr_type(ty, node->mutable_));
    made->linkage = ir::Linkage::visible;
    made->span = node->span;
    made->name_span = node->name_span;
    auto var = module.add_global(std::move(made), key(node->name));
    (*top)[std::string(node->name)] = Held(static_cast<ir::Value*>(var));
    owned.push_back(Held(static_cast<ir::Value*>(var)));
  }


  void Checker::mark_system(const ast::SourceUnit* unit)
  {
    if (unit->attrs.empty())
      return;
    auto bound = bind_attributes(unit->attrs, AttrTarget::file);
    if (std::ranges::any_of(bound, [](const BoundAttr& attr) { return attr.name() == "system"; }) && ! ignore_system) {
      auto found = unit->span.is_valid() ? sources->file_of(unit->span.start) : nullptr;
      if (found != nullptr) {
        diags.mark_system(found->base, found->end());
        module.system_files.emplace_back(found->base, found->end());
      }
    }
  }


  void Checker::report_inferred(std::span<const std::tuple<std::string, Span>> names, const ast::TypeExpr* written)
  {
    for (const auto& [name, span] : names) {
      if (name == names::wildcard_name)
        continue;
      auto local = find_local(name);
      if (local == nullptr)
        continue;
      auto held = held_by(local);
      if (session != nullptr && held != nullptr)
        session->types[name] = held;
      if (held != nullptr && held != ir::error_type())
        record_inferred(name, span, held, written);
    }
  }


  void Checker::record_inferred(std::string_view name, Span span, const ir::Type* held, const ast::TypeExpr* written)
  {
    std::string reason;
    if (written == nullptr)
      reason = std::format("its type was not written, and is '{}', worked out from what it was given", held->written());
    else {
      auto array = ast::as<ast::ArrayTypeRef>(written);
      auto said = array != nullptr && array->element == nullptr ? nullptr : quietly_resolved(written);
      if (said != nullptr && said->written() == held->written())
        return;
      auto snippet = sources->snippet(written->span);
      reason = std::format("'{}' was written and the type is '{}', the rest worked out from what it was given",
                           snippet && ! snippet->empty() ? *snippet : std::string_view("?"), held->written());
    }
    module.record("type-inferred", std::string(name), reason, span);
  }


  const ir::Type* Checker::quietly_resolved(const ast::TypeExpr* written)
  {
    auto kept_notes = std::exchange(notes, nullptr);
    const ir::Type* found = nullptr;
    try {
      found = resolve_type(written);
    } catch (...) {
      notes = kept_notes;
      throw;
    }
    notes = kept_notes;
    return found == ir::error_type() ? nullptr : found;
  }


  void Checker::collect_singletons(const ast::TypeDef* node)
  {
    auto defined_at = top->get(std::string(node->name));
    Held defined = defined_at == nullptr ? Held() : *defined_at;
    for (auto one : node->singletons) {
      if (node->kind == ast::TypeKind::sum) {
        diags.emit(D::LANG_SINGLETON_IN_A_SUM, one->name_span, { { "name", one->name }, { "type", node->name } });
        continue;
      }
      auto filed = std::format("{}.{}", node->name, one->name);
      auto named = defined.get<NamedType>();
      bool exported = named != nullptr && named->exported;
      ir::GlobalVar* var = nullptr;
      if (one->value == nullptr) {
        var = filled_singleton(node, one, filed, exported);
        if (var == nullptr)
          continue;
      } else {
        auto value = one->value;
        if (! in_the_image(value) && a_container_written(value)) {
          // In the image by what it is: `in ⎕static` is not written.
          auto where = syntax.make<ast::NameRef>(value->span);
          where->name = names::static_name;
          if (auto set = ast::as<ast::SetLit>(value)) {
            auto copy = syntax.copy(*set);
            copy->arena = where;
            value = copy;
          } else if (auto dict = ast::as<ast::DictLit>(value)) {
            auto copy = syntax.copy(*dict);
            copy->arena = where;
            value = copy;
          } else {
            auto allocated = syntax.make<ast::Allocated>(value->span);
            allocated->value = value;
            allocated->arena = where;
            value = allocated;
          }
        }
        std::vector<const ast::Attribute*> attrs;
        if (exported) {
          auto attr = syntax.make<ast::Attribute>(one->span);
          attr->name = "export";
          attr->name_span = one->name_span;
          attrs.push_back(attr);
        }
        auto def = syntax.make<ast::VarDef>(one->span);
        def->name = syntax.text(filed);
        def->name_span = one->name_span;
        def->type = one->type;
        def->value = value;
        def->mutable_ = false;
        def->attrs = syntax.list(attrs);
        collect_global(def);
        auto found = top->get(filed);
        var = found == nullptr ? nullptr : found->get<ir::GlobalVar>();
        if (var == nullptr)
          continue;
      }
      if (has_mutable_field(var->value_type)) {
        diags.emit(D::LANG_SINGLETON_CHANGES, one->name_span,
                   { { "name", one->name }, { "type", var->value_type->written() } });
        continue;
      }
      members[{ std::string(node->name), std::string(one->name) }] = Held(static_cast<ir::Value*>(var));
    }
  }


  ir::GlobalVar* Checker::filled_singleton(const ast::TypeDef* node, const ast::Field* one, std::string_view filed,
                                           bool exported)
  {
    bool here = false;
    if (node->name == filled_singleton_type && one->name == filled_singleton_name)
      for (const auto& where : SearchPath::system_modules()) {
        auto expected = where / filled_singleton_module;
        expected.replace_extension(module_suffix);
        if (pure_path(path) == pure_path(expected))
          here = true;
      }
    if (! here) {
      diags.emit(D::LANG_SINGLETON_WITHOUT_VALUE, one->name_span, { { "name", one->name } });
      return nullptr;
    }
    auto ty = ir::as<ir::ProductType>(resolve_type(one->type));
    if (ty == nullptr || ty->fields.size() != 2 || ty->fields[0].first != "fd" || ty->fields[1].first != "ring") {
      diags.internal("the directory the compiler fills in is not 'fd ; ring'");
      return nullptr;
    }
    if (! declare(filed, one->name_span))
      return nullptr;
    // The record the entry point lays out is no variable of the module, but
    // the module says which it is, for whatever lays it out.
    if (module.startup_record == nullptr)
      module.startup_record = std::make_unique<ir::GlobalVar>(startup_record()->name, ir::u8(),
                                                              ir::ptr_type(ir::u8(), true));
    auto ring = module.constant<ir::AddressConst>(ty->fields[1].second, module.startup_record.get(), 0);
    module.ring_addresses.push_back(ring);
    auto made = std::make_unique<ir::GlobalVar>(std::string(filed), ty, ir::ptr_type(ty, false));
    made->initializer = module.record_const(ty, { module.int_const(ty->fields[0].second, at_fdcwd), ring });
    made->span = one->span;
    made->name_span = one->name_span;
    made->exported = exported;
    auto var = module.add_global(std::move(made), key(filed));
    (*top)[std::string(filed)] = Held(static_cast<ir::Value*>(var));
    owned.push_back(Held(static_cast<ir::Value*>(var)));
    return var;
  }


  // -- modules ----------------------------------------------------------------

  void Checker::collect_import(const ast::ModuleImport* node)
  {
    if (! declare(node->name, node->name_span))
      return;
    auto bound = bind_attributes(node->attrs, AttrTarget::module);
    if (std::ranges::any_of(bound, [](const BoundAttr& attr) { return attr.name() == "export"; }))
      exported_imports.insert(std::string(node->name));
    std::filesystem::path found_at;
    try {
      found_at = registry->resolve(node->source, path);
    } catch (const ModuleNotFound& exc) {
      diags.emit(D::LANG_IMPORT_NOT_FOUND, node->source_span,
                 { { "name", exc.name }, { "looked", std::to_string(exc.looked.size()) } });
      return;
    }
    if (auto ring = registry->cycle_through(found_at)) {
      std::string chain;
      for (const auto& one : *ring) {
        if (! chain.empty())
          chain += " -> ";
        chain += one.filename().string();
      }
      diags.emit(D::LANG_IMPORT_CYCLE, node->source_span, { { "name", node->source }, { "chain", chain } });
      return;
    }
    auto found = registry->known(found_at);
    if (found == nullptr) {
      found = read_module(found_at, node);
      if (found == nullptr)
        return;
    }
    // Every route to a module is a name it could go by; which one it ends up
    // with is settled once every route is known.
    found->add_candidate(prefix, base_name(found_at));
    (*top)[std::string(node->name)] = Held(found);
    import_operators(found, node);
  }


  void Checker::import_operators(LoadedModule* found, const ast::ModuleImport* node)
  {
    for (const auto& [key, what] : found->operators) {
      if (operators.contains(key)) {
        auto from = operators_from.get(key);
        diags.emit(D::LANG_OPERATOR_TWICE_IMPORTED, node->source_span,
                   { { "glyph", key.first },
                     { "count", std::to_string(key.second) },
                     { "module", node->source },
                     { "other", from != nullptr && ! from->empty() ? *from : std::string("this file") } });
        continue;
      }
      operators[key] = what;
      operators_from[key] = std::string(node->source);
    }
  }


  LoadedModule* Checker::read_module(const std::filesystem::path& found_at, const ast::ModuleImport* node)
  {
    LoadedModule* loaded = nullptr;
    try {
      loaded = &registry->begin(found_at);
    } catch (const ImportCycle& exc) {
      std::string chain;
      for (const auto& one : exc.chain) {
        if (! chain.empty())
          chain += " -> ";
        chain += one.filename().string();
      }
      diags.emit(D::LANG_IMPORT_CYCLE, node->source_span, { { "name", node->source }, { "chain", chain } });
      return nullptr;
    }
    struct Finish {
      ModuleRegistry* registry;
      const std::filesystem::path& path;
      ~Finish() { registry->finish(path); }
    } finish{ registry, found_at };
    auto unit = read_unit(found_at, node);
    if (unit == nullptr)
      return nullptr;
    auto inner_prefix = found_name(prefix, base_name(found_at));
    // Kept for as long as the module is: what asks it to lower more is its own
    // `want`, which needs the checker that collected it.
    auto inner = std::make_shared<Checker>(module, diags, registry, found_at, inner_prefix, sources, &top_level,
                                           nullptr, conditions, for_macros, &quoted, whole_modules, ignore_system);
    std::vector<const ast::SourceUnit*> just{ unit };
    inner->run(just, false);
    // What it owns goes on growing as more of it is lowered, and Python hands
    // the list itself on: so it is copied again each time it is asked.
    auto settle_owned = [loaded, raw = inner.get()] {
      loaded->owned.clear();
      for (const auto& one : raw->owned)
        if (auto value = one.get<ir::Value>())
          loaded->owned.push_back(value);
    };
    loaded->want = [inner, settle_owned](const NameSet& names) {
      inner->want(names);
      settle_owned();
    };
    loaded->exports.clear();
    for (const auto& [name, what] : *inner->top)
      if (is_exported(what) || (inner->exported_imports.contains(name) && what.get<LoadedModule>() != nullptr))
        loaded->exports[name] = what;
    // Every bundle and not only the exported ones: "there is no such bundle"
    // and "it is not yours to apply" are two different things to be told.
    loaded->bundles.clear();
    for (const auto& [name, bundle] : *inner->bundles)
      loaded->bundles[name] = bundle;
    // Only the exported operators and members: neither has a name an
    // importing file could be told it may not use.
    loaded->operators.clear();
    for (const auto& [key, what] : inner->operators)
      if (is_exported(what))
        loaded->operators[key] = what;
    loaded->members.clear();
    for (const auto& [key, what] : inner->members)
      if (is_exported(what))
        loaded->members[key] = what;
    settle_owned();
    return loaded;
  }


  const ast::SourceUnit* Checker::read_unit(const std::filesystem::path& found_at, const ast::ModuleImport* node)
  {
    if (auto earlier = sources->parsed(found_at)) {
      // Read already, for its macros: one file is read once.
      sources->record(found_at, earlier->unit, true);
      return static_cast<const ast::SourceUnit*>(earlier->unit);
    }
    auto source = sources->read(found_at);
    if (! source) [[unlikely]] {
      auto reason = source.error().kind == ReadFailure::Kind::not_utf8 ? found_at.generic_string() : source.error().reason;
      diags.emit(D::LANG_IMPORT_UNREADABLE, node->source_span, { { "name", node->source }, { "reason", reason } });
      return nullptr;
    }
    // A body is lexed and parsed when something asks for it: a module is
    // lowered as far as the program names it.
    auto arena = std::make_shared<ast::SyntaxArena>();
    auto tokens = tokenize(**source, diags, true);
    auto found = parse(*arena, std::move(tokens), found_at.generic_string(), diags, true, *source);
    sources->remember(found_at, { found, arena });
    // A module read here is a source of the program like any other.
    sources->record(found_at, found, true);
    return found;
  }


  ir::Value* Checker::lower_member(ir::Builder& builder, const ast::Member* expr, const ir::Type* expected)
  {
    auto base = expr->base;
    auto single = member_named(expr);
    if (auto var = single.get<ir::GlobalVar>())
      return read_global(builder, var, expr->span);
    if (auto member = ast::as<ast::Member>(base)) {
      // A type another module exports, and a value of it.
      if (auto through = ir::as<ir::EnumType>(type_exported(member)))
        return value_of_enum(through, expr);
    }
    auto inner = ast::is<ast::Member>(base) ? module_at(base) : nullptr;
    auto ref = ast::as<ast::NameRef>(base);
    if (inner == nullptr && (ref == nullptr || holds_a_record(ref->name)))
      // A field of a record: what stands on the left is a value.
      return field_of(builder, expr, expected);
    // Python reads `base.name`, which a path through a module has too.
    std::string_view called = ref != nullptr ? ref->name : ast::as<ast::Member>(base)->name;
    if (auto provided = ir::as<ir::EnumType>(ir::builtin_type(called)))
      // An enumeration the compiler provides.
      return value_of_enum(provided, expr);
    Held held;
    if (inner != nullptr)
      held = Held(inner);
    else if (auto found = top->get(path_text(base)))
      held = *found;
    if (auto named = held.get<NamedType>())
      return enum_value(named, expr);
    auto loaded = held.get<LoadedModule>();
    if (loaded == nullptr) {
      diags.emit(D::LANG_IMPORT_NOT_A_MODULE, base->span, { { "name", path_text(base) } });
      return undef_error(module);
    }
    auto exported = loaded->exports.get(std::string(expr->name));
    Held found = exported == nullptr ? Held() : *exported;
    if (notes != nullptr) {
      // The module the name is reached through, and then the name itself.
      auto where = defined.get(path_text(base));
      note(base->span, note_kind::module, path_text(base), loaded->path.generic_string(),
           where != nullptr ? std::get<0>(*where) : invalid_span);
      if (auto func = found.get<ir::Function>())
        note(expr->name_span, note_kind::function, expr->name, func->ty->written(), func->name_span,
             func->doc.empty() ? std::nullopt : std::optional<std::string>(func->doc));
    }
    if (found.empty()) {
      diags.emit(D::LANG_IMPORT_NOT_EXPORTED, expr->name_span,
                 { { "name", expr->name }, { "module", path_text(base) } });
      return undef_error(module);
    }
    if (auto var = found.get<ir::GlobalVar>())
      return read_global(builder, var, expr->span);
    // A function is not a value, so naming one outside a call is naming
    // something there is nothing to do with.
    diags.emit(D::LANG_CALL_NOT_A_FUNCTION, expr->span, { { "name", expr->name } });
    return undef_error(module);
  }


  bool Checker::holds_a_record(std::string_view name)
  {
    if (auto local = find_local(name)) {
      auto held = held_by(local);
      if (ir::is<ir::ProductType>(held) || ir::is<ir::ArrayType>(held) || held == ir::error_type())
        return true;
      auto ptr = ir::as<ir::PtrType>(held);
      return ptr != nullptr && ir::is<ir::ProductType>(ptr->pointee);
    }
    auto var = provided(name).get<ir::GlobalVar>();
    return var != nullptr && (ir::is<ir::ProductType>(var->value_type) || ir::is<ir::ArrayType>(var->value_type));
  }


  ir::Value* Checker::field_of(ir::Builder& builder, const ast::Member* expr, const ir::Type* expected)
  {
    std::vector<std::string> below{ std::string(expr->name) };
    if (auto somewhere = record_at(builder, expr->base, false, below)) {
      // The record is somewhere, so only the field is read.
      auto [where, ty] = *somewhere;
      return field_of_place(builder, where, ty, expr, expected);
    }
    auto value = lower_expr(builder, expr->base, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type())
      return undef_error(module);
    if (ir::is<ir::ArrayType>(ty)) {
      diags.emit(D::LANG_FIELD_OF_AN_ARRAY, expr->name_span, { { "found", ty->written() }, { "name", expr->name } });
      return undef_error(module);
    }
    if (auto ptr = ir::as<ir::PtrType>(ty); ptr != nullptr && ir::is<ir::ProductType>(ptr->pointee)) {
      bool named = ast::is<ast::NameRef>(expr->base) || ast::is<ast::Member>(expr->base);
      diags.emit(D::LANG_FIELD_THROUGH_A_REFERENCE, expr->span,
                 { { "name", named ? path_text(expr->base) : std::string("it") },
                   { "type", ptr->pointee->written() },
                   { "field", expr->name } });
      return undef_error(module);
    }
    auto record = ir::as<ir::ProductType>(ty);
    if (record == nullptr) {
      diags.emit(D::LANG_IMPORT_NOT_A_MODULE, expr->base->span, { { "name", "an expression" } });
      return undef_error(module);
    }
    if (! field_index(record, expr->name)) {
      diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, expr->name_span, { { "type", ty->written() }, { "name", expr->name } });
      return undef_error(module);
    }
    // A record that arrived as a value and not as a place is put somewhere
    // first, so that one rule reads a field however the record got here.
    ir::Value* place;
    if (ir::held_in_memory(ty))
      place = builder.cast(ir::CastKind::bitcast, value, ir::ptr_type(ty, true), expr->span);
    else {
      place = builder.frame(ty, expr->span);
      record_into(builder, place, value, record, expr->span);
    }
    return field_of_place(builder, place, record, expr, expected);
  }


  ir::Value* Checker::array_in_a_field(ir::Builder& builder, const ast::Expr* expr, bool writing)
  {
    auto member = ast::as<ast::Member>(expr);
    if (member == nullptr)
      return nullptr;
    std::vector<std::string> below{ std::string(member->name) };
    auto somewhere = record_at(builder, member->base, writing, below);
    if (! somewhere)
      return nullptr;
    auto [where, ty] = *somewhere;
    auto at = field_index(ty, member->name);
    if (! at)
      return nullptr;
    auto held = ir::as<ir::ArrayType>(ty->fields[*at].second);
    if (held == nullptr || ! held->fixed())
      return nullptr;
    auto place = field_place(builder, where, ty, *at, member->span);
    return builder.cast(ir::CastKind::bitcast, place, held, member->span);
  }


  ir::Value* Checker::field_of_place(ir::Builder& builder, ir::Value* where, const ir::ProductType* ty,
                                     const ast::Member* expr, const ir::Type* expected)
  {
    auto at = field_index(ty, expr->name);
    if (! at) {
      diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, expr->name_span, { { "type", ty->written() }, { "name", expr->name } });
      return undef_error(module);
    }
    auto place = field_place(builder, where, ty, *at, expr->span);
    auto held = ty->fields[*at].second;
    auto record = ir::as<ir::ProductType>(held);
    auto found = record != nullptr ? record_from(builder, place, record, expr->span) : builder.load(place, expr->span);
    if (! accepts(expected, found->ty))
      report_mismatch(expr->span, found->ty, expected);
    return found;
  }


  std::optional<std::tuple<ir::Value*, const ir::ProductType*>>
  Checker::record_at(ir::Builder& builder, const ast::Expr* expr, bool writing, std::span<const std::string> below)
  {
    switch (expr->tag) {
    case ast::NodeKind::NameRef: {
      auto ref = static_cast<const ast::NameRef*>(expr);
      auto local = find_local(ref->name);
      if (local != nullptr && local->placed && ir::is<ir::ProductType>(local->held)) {
        if (! local->closer.empty() && local->ended_at) {
          told_ended(local, ref->span);
          return std::nullopt;
        }
        if (! (taking_a_reference && ref->name == *taking_a_reference)
            && lent_out(ref->name, ref->span, writing, below))
          return std::nullopt;
        local->read = true;
        if (notes != nullptr)
          // A record reached as a place is still the name being read.
          note(ref->span, local->is_parameter ? note_kind::parameter : note_kind::variable, ref->name,
               local->held->written(), local->span);
        return std::tuple{ local->value, ir::as<ir::ProductType>(local->held) };
      }
      if (local != nullptr)
        return std::nullopt;
      // A variable at the top level is a place for as long as the program is.
      auto found = provided(ref->name).get<ir::GlobalVar>();
      if (auto record = found != nullptr ? ir::as<ir::ProductType>(found->value_type) : nullptr)
        return std::tuple{ builder.address(found, ref->span), record };
      return std::nullopt;
    }
    case ast::NodeKind::Deref: {
      auto deref = static_cast<const ast::Deref*>(expr);
      auto value = lower_expr(builder, deref->operand, nullptr);
      auto ptr = ir::as<ir::PtrType>(value_type_of(value));
      if (auto record = ptr != nullptr ? ir::as<ir::ProductType>(ptr->pointee) : nullptr)
        return std::tuple{ value, record };
      return std::nullopt;
    }
    case ast::NodeKind::Element: {
      // An array's elements are where the array is, and so are a list's, for
      // a field only read.
      auto place = place_of_an_element(builder, static_cast<const ast::Element*>(expr), ! writing);
      if (! place)
        return std::nullopt;
      auto record = ir::as<ir::ProductType>(place->held);
      if (record == nullptr)
        return std::nullopt;
      return std::tuple{ place->address, record };
    }
    case ast::NodeKind::Member: {
      auto member = static_cast<const ast::Member*>(expr);
      // A variable another module exports is a place exactly as one of this
      // file's is.
      auto shared = exported_variable(member);
      if (auto record = shared != nullptr ? ir::as<ir::ProductType>(shared->value_type) : nullptr)
        return std::tuple{ builder.address(shared, member->span), record };
      std::vector<std::string> deeper{ std::string(member->name) };
      deeper.insert(deeper.end(), below.begin(), below.end());
      auto found = record_at(builder, member->base, writing, deeper);
      if (! found)
        return std::nullopt;
      auto [where, ty] = *found;
      auto at = field_index(ty, member->name);
      if (! at)
        return std::nullopt;
      auto held = ir::as<ir::ProductType>(ty->fields[*at].second);
      if (held == nullptr)
        return std::nullopt;
      return std::tuple{ field_place(builder, where, ty, *at, member->span), held };
    }
    default:
      return std::nullopt;
    }
  }


  ir::Value* Checker::enum_value(NamedType* defined, const ast::Member* expr)
  {
    return value_of_enum(resolved(defined), expr);
  }


  ir::Value* Checker::value_of_enum(const ir::Type* ty, const ast::Member* expr)
  {
    if (ty == ir::error_type())
      return undef_error(module);
    auto enumeration = ir::as<ir::EnumType>(ty);
    auto index = enumeration != nullptr ? enumeration->index_of(expr->name) : std::nullopt;
    if (! index) {
      diags.emit(D::LANG_ENUM_UNKNOWN_VALUE, expr->name_span, { { "name", expr->name }, { "type", ty->written() } });
      return undef_error(module);
    }
    return mut(module.enum_const(ty, *index));
  }


  const ir::Type* Checker::variable_type(const ast::VarDef* node)
  {
    if (auto array = ast::as<ast::ArrayTypeRef>(node->type); array != nullptr && array->element == nullptr)
      return array_of_its_value(node, array);
    if (node->type != nullptr)
      return resolve_type(node->type);
    return type_of(node->value);
  }


  const ir::Type* Checker::array_of_its_value(const ast::VarDef* node, const ast::ArrayTypeRef* written)
  {
    auto found = type_of(node->value);
    if (found == nullptr)
      return nullptr;
    // What each length written asked for: nothing, Python's `False`, or a
    // number.
    std::vector<std::optional<KnownNumber>> asked;
    for (auto one : written->shape)
      asked.push_back(one == nullptr ? std::nullopt : std::optional(constant_number(one)));
    auto array = ir::as<ir::ArrayType>(found);
    bool fits = array != nullptr;
    if (fits && ! (written->shape.size() == 1 && written->shape[0] == nullptr)) {
      fits = asked.size() == array->rank();
      for (std::size_t i = 0; fits && i < asked.size(); ++i) {
        if (! asked[i] || (! asked[i]->refused && ! asked[i]->value))
          continue;
        auto along = array->shape[i];
        // `False == 0` in Python.
        __int128 number = asked[i]->refused ? 0 : *asked[i]->value;
        fits = along.has_value() && __int128(*along) == number;
      }
    }
    if (! fits) {
      std::string expected = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}";
      for (std::size_t i = 0; i < asked.size(); ++i) {
        if (i > 0)
          expected += ',';
        if (! asked[i])
          continue;
        if (asked[i]->refused)
          expected += "False";
        else if (asked[i]->value)
          expected += ir::decimal(*asked[i]->value);
      }
      expected += "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
      diags.emit(D::LANG_TYPE_INITIALIZER_MISMATCH, node->value->span,
                 { { "name", node->name }, { "expected", expected }, { "found", found->written() } });
      return ir::error_type();
    }
    return found;
  }


  const ir::Type* Checker::array_written_type(const ast::ArrayLit* expr)
  {
    auto [shape, inner] = shape_written(expr);
    if (inner.empty()) {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                 { { "feature", "a value in the image whose type nothing around it says" } });
      return nullptr;
    }
    auto element = said_by(inner);
    if (element == nullptr)
      element = type_of(inner[0]);
    if (element == nullptr || element == ir::error_type())
      return nullptr;
    return ir::array_type(element, shape);
  }


  const ir::Type* Checker::type_of(const ast::Expr* expr)
  {
    // A name at the top level standing for a type this file defines.
    auto named_type = [&](const ast::Expr* name) -> NamedType* {
      auto ref = ast::as<ast::NameRef>(name);
      if (ref == nullptr)
        return nullptr;
      auto held = top->get(std::string(ref->name));
      return held == nullptr ? nullptr : held->get<NamedType>();
    };
    switch (expr->tag) {
    case ast::NodeKind::Allocated:
      // Where it is kept says nothing about what it is.
      return type_of(static_cast<const ast::Allocated*>(expr)->value);
    case ast::NodeKind::ArrayLit:
      return array_written_type(static_cast<const ast::ArrayLit*>(expr));
    case ast::NodeKind::Call:
      // A record or a sum written out says its type by its name.
      if (auto named = named_type(static_cast<const ast::Call*>(expr)->callee);
          named != nullptr && ! named->takes_types())
        return resolved(named);
      break;
    case ast::NodeKind::Member:
      // So does a value of an enumeration, `Colour.red`.
      if (auto named = named_type(static_cast<const ast::Member*>(expr)->base);
          named != nullptr && ir::is<ir::EnumType>(resolved(named)))
        return resolved(named);
      break;
    case ast::NodeKind::IntLit: {
      auto lit = static_cast<const ast::IntLit*>(expr);
      if (! lit->type_name) {
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                   { { "feature", "an integer literal with neither a type suffix nor a context that gives it a type" } });
        return nullptr;
      }
      return ir::builtin_type(*lit->type_name);
    }
    case ast::NodeKind::BoolLit:
      return ir::bool_type();
    case ast::NodeKind::CharLit:
      return ir::char_type();
    case ast::NodeKind::StringLit:
      return ir::str_type();
    case ast::NodeKind::Unary:
      if (static_cast<const ast::Unary*>(expr)->op == ast::UnaryOp::length)
        return size_type();
      break;
    case ast::NodeKind::FloatLit: {
      auto lit = static_cast<const ast::FloatLit*>(expr);
      return lit->type_name && ! lit->type_name->empty() ? ir::builtin_type(*lit->type_name) : nullptr;
    }
    case ast::NodeKind::NameRef: {
      auto ref = static_cast<const ast::NameRef*>(expr);
      auto placed = find_local(ref->name);
      if (placed != nullptr && placed->placed) {
        placed->read = true;
        return placed->held;
      }
      auto resolved_value = lookup(ref);
      return resolved_value == nullptr ? nullptr : value_type_of(resolved_value);
    }
    default:
      break;
    }
    diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
               { { "feature", "deriving the type of a variable from this kind of value" } });
    return nullptr;
  }


  const ir::Type* Checker::value_type_of(ir::Value* value)
  {
    if (value->vkind == ir::ValueKind::global)
      return static_cast<ir::GlobalVar*>(value)->value_type;
    return value->ty;
  }


  ir::Value* Checker::constant_value(const ast::VarDef* node, const ir::Type* ty)
  {
    // The definition with another value, as Python's `replace(node, value=…)`.
    auto with_value = [&](const ast::Expr* value) {
      auto copy = syntax.copy(*node);
      copy->value = value;
      return copy;
    };
    if (ty == ir::arena_type()) {
      // An arena of one's own: three zero words, an arena that has asked the
      // system for nothing yet.  There is one thing to write here.
      auto ref = ast::as<ast::NameRef>(node->value);
      if (! (ref != nullptr && ref->name == names::empty_arena_name))
        diags.emit(D::LANG_ARENA_STARTS_EMPTY, node->value->span, { { "name", names::empty_arena_name } });
      return nullptr;
    }
    if (ty == ir::error_type())
      // The type was already reported.
      return nullptr;
    if (auto result = ir::as<ir::ResultType>(ty)) {
      // The only constant of a result type a program can write is the
      // successful one.
      auto answer = constant_value(node, result->ok);
      if (answer == nullptr || ! answer->is_const())
        return nullptr;
      return mut(module.result_const(ty, answer));
    }
    if (auto integer = ir::as<ir::IntType>(ty); integer != nullptr && ! ast::is<ast::IntLit>(node->value)) {
      // Worked out while compiling: arithmetic over constants, and calls to
      // pure functions.
      auto folded = constant(node->value);
      if (is_refused(folded))
        return nullptr;
      NameSet seen;
      if (! folded && reaches_itself(node->name, node->value, seen)) {
        diags.emit(D::LANG_CONSTANT_DEFINED_BY_ITSELF, node->value->span, { { "name", node->name } });
        return nullptr;
      }
      if (folded && ! folded->failed) {
        if (folded->ty != nullptr && ! ir::same(folded->ty, ty)) {
          wrong_initializer(node, ty, folded->ty->written());
          return nullptr;
        }
        if (folded->too_large || ! integer->holds(folded->value)) {
          diags.emit(D::LANG_TYPE_ANSWER_DOES_NOT_FIT, node->value->span,
                     { { "value", ir::decimal(folded->value) }, { "type", ty->written() } });
          return nullptr;
        }
        return mut(module.int_const(ty, folded->value));
      }
    }
    auto value = node->value;
    switch (value->tag) {
    case ast::NodeKind::IntLit: {
      auto lit = static_cast<const ast::IntLit*>(value);
      if (ty == ir::char_type() && ! lit->type_name) {
        // A number written where a code point is wanted is that code point.
        if (lit->value.too_large || ! (0 <= lit->value.value && lit->value.value <= max_code_point)) {
          diags.emit(D::LANG_CHAR_OUTSIDE_UNICODE, value->span,
                     { { "value", ir::decimal(lit->value.value) }, { "last", "10FFFF" } });
          return nullptr;
        }
        if (is_surrogate(lit->value.value)) [[unlikely]] {
          diags.emit(D::LANG_CHAR_IS_A_SURROGATE, value->span,
                     { { "value", std::format("{:04X}", std::uint32_t(lit->value.value)) } });
          return nullptr;
        }
        return mut(module.char_const(std::uint32_t(lit->value.value)));
      }
      auto integer = ir::as<ir::IntType>(ty);
      if (integer == nullptr) {
        // A suffix names the literal's type outright; without one it is a
        // number of no particular width.
        auto named = ir::builtin_type(lit->type_name.value_or(""));
        wrong_initializer(node, ty, named != nullptr ? named->written() : std::string("integer"));
        return nullptr;
      }
      if (! literal_matches(node, ty))
        return nullptr;
      if (lit->value.too_large || ! integer->holds(lit->value.value)) {
        // ORDER: a literal beyond 127 bits is written as what was read of it.
        diags.emit(D::LANG_SYNTAX_INTEGER_RANGE, value->span,
                   { { "literal", ir::decimal(lit->value.value) }, { "type", ty->written() } });
        return nullptr;
      }
      return mut(module.int_const(ty, lit->value.value));
    }
    case ast::NodeKind::CharLit:
      if (ty != ir::char_type()) {
        wrong_initializer(node, ty, ir::char_type()->written());
        return nullptr;
      }
      return mut(module.char_const(std::uint32_t(static_cast<const ast::CharLit*>(value)->value.value)));
    case ast::NodeKind::BoolLit:
      if (ty != ir::bool_type()) {
        wrong_initializer(node, ty, ir::bool_type()->written());
        return nullptr;
      }
      return mut(module.bool_const(static_cast<const ast::BoolLit*>(value)->value));
    case ast::NodeKind::FloatLit: {
      auto lit = static_cast<const ast::FloatLit*>(value);
      if (! ir::is<ir::FloatType>(ty)) {
        wrong_initializer(node, ty, ir::f64()->written());
        return nullptr;
      }
      auto named = lit->type_name && ! lit->type_name->empty() ? ir::builtin_type(*lit->type_name) : nullptr;
      if (named != nullptr && named != ty) {
        wrong_initializer(node, ty, named->written());
        return nullptr;
      }
      return mut(module.float_const(ty, lit->value));
    }
    case ast::NodeKind::Allocated:
      if (in_the_image(value)) {
        // Said outright, which changes nothing about what it comes to -- but
        // that it is for the image, which a call asks.
        auto outer = std::exchange(for_the_image, true);
        ir::Value* found = nullptr;
        try {
          found = constant_value(with_value(static_cast<const ast::Allocated*>(value)->value), ty);
        } catch (...) {
          for_the_image = outer;
          throw;
        }
        for_the_image = outer;
        return found;
      }
      break;
    case ast::NodeKind::Known:
      return mut(static_cast<const ast::Known*>(value)->const_);
    case ast::NodeKind::Spread:
      diags.emit(D::LANG_SPREAD_NOT_HERE, value->span);
      return nullptr;
    default:
      break;
    }
    if (auto call = ast::as<ast::Call>(value); call != nullptr && for_the_image
        && (ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty) || ir::is<ir::ListType>(ty) || ir::is<ir::StrType>(ty))
        && ast::is<ast::NameRef>(call->callee)
        && find_local(static_cast<const ast::NameRef*>(call->callee)->name) == nullptr) {
      auto literal = worked_out_for_the_image(call, ty);
      if (literal == nullptr)
        return nullptr;
      return constant_value(with_value(literal), ty);
    }
    if (auto text = ast::as<ast::StringLit>(value); text != nullptr && ir::is<ir::StrType>(ty))
      return mut(text_constant(text->value));
    if (auto list = ast::as<ast::ListLit>(value)) {
      if (auto list_ty = ir::as<ir::ListType>(ty)) {
        std::vector<const ir::Value*> held;
        for (auto one : list->elements) {
          auto found = constant_value(with_value(one), list_ty->element);
          if (found == nullptr || ! found->is_const())
            return nullptr;
          held.push_back(found);
        }
        auto pieces = ir::parts_of(ty);
        const ir::Value* run;
        if (held.empty())
          run = module.constant<ir::AddressConst>(pieces[0], nullptr, 0);
        else {
          auto run_ty = ir::array_type(list_ty->element, { held.size() });
          auto contents = module.array_const(ir::array_type(list_ty->element, { held.size() }), held);
          run = module.constant<ir::AddressConst>(pieces[0], image_variable(run_ty, contents), 0);
        }
        auto count = module.int_const(ir::u64(), held.size());
        auto none = module.constant<ir::AddressConst>(pieces[2], nullptr, 0);
        return mut(module.constant<ir::PartsConst>(ty, std::vector<const ir::Value*>{ run, count, none }));
      }
    }
    if (auto set = ast::as<ast::SetLit>(value)) {
      if (ir::is<ir::DictType>(ty) && set->spread) {
        auto paired = spread_paired(set);
        if (paired == nullptr)
          return nullptr;
        return constant_value(with_value(paired), ty);
      }
      if (ir::is<ir::SetType>(ty) && (set->arena == nullptr || in_the_image(set))) {
        std::vector<std::tuple<const ast::Expr*, const ast::Expr*>> written;
        for (auto one : set->elements)
          written.emplace_back(one, nullptr);
        return mut(table_constant(node, ty, written));
      }
    }
    if (auto dict = ast::as<ast::DictLit>(value);
        dict != nullptr && ir::is<ir::DictType>(ty) && (dict->arena == nullptr || in_the_image(dict))) {
      std::vector<std::tuple<const ast::Expr*, const ast::Expr*>> written;
      for (const auto& entry : dict->entries)
        written.emplace_back(entry.key, entry.value);
      return mut(table_constant(node, ty, written));
    }
    if (auto array = ast::as<ast::ArrayLit>(value)) {
      if (auto array_ty = ir::as<ir::ArrayType>(ty)) {
        // Every element has to be one the compiler knows, gathered into one
        // run however many dimensions the shape has.
        auto held = constant_elements(node, array_ty, array, array_ty->shape);
        return held ? mut(module.array_const(ty, *held)) : nullptr;
      }
    }
    if (ast::is<ast::Call>(value)) {
      if (auto record = ir::as<ir::ProductType>(ty))
        // A record written out, every field of which the compiler knows.
        return mut(constant_record(node, record));
      if (auto sum = ir::as<ir::SumType>(ty))
        // A value of a sum written out: which part, and what it carries.
        return mut(constant_sum(node, sum));
    }
    if (auto member = ast::as<ast::Member>(value)) {
      // A value of an enumeration is known while compiling.
      auto found = enum_value_of(member);
      if (found == nullptr)
        return nullptr;
      if (found->ty != ty) {
        wrong_initializer(node, ty, found->ty->written());
        return nullptr;
      }
      return mut(found);
    }
    if (auto address = ast::as<ast::AddressOf>(value)) {
      if (auto ptr = ir::as<ir::PtrType>(ty))
        return mut(address_in_the_image(address, ptr));
    }
    if (ast::is<ast::StringLit>(value)) {
      wrong_initializer(node, ty, ir::str_type()->written());
      return nullptr;
    }
    if ((ast::is<ast::ListLit>(value) || ast::is<ast::SetLit>(value) || ast::is<ast::DictLit>(value)
         || ast::is<ast::ArrayLit>(value))
        && a_container_written(value)
        && known_while_compiling(value, [this](const ast::Member* m) { return names_an_enum(m); })) {
      // A container of what the compiler knows, of another type than the one
      // wanted -- or a table `in` an arena, which is built while running.
      diags.emit(D::LANG_STATIC_WRONG_CONTAINER, value->span, { { "type", ty->written() } });
      return nullptr;
    }
    if (for_the_image) {
      diags.emit(D::LANG_STATIC_NOT_KNOWN, value->span);
      return nullptr;
    }
    diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, value->span,
               { { "feature", "a top-level variable whose value is not a literal" } });
    return nullptr;
  }


  const ast::DictLit* Checker::spread_paired(const ast::SetLit* written)
  {
    auto pieces = written->elements;
    if (pieces.size() % 2) {
      diags.emit(D::LANG_SPREAD_NOT_PAIRED, written->span, { { "count", pieces.size() } });
      return nullptr;
    }
    std::vector<ast::ExprPair> entries;
    for (std::size_t at = 0; at < pieces.size(); at += 2)
      entries.push_back({ pieces[at], pieces[at + 1] });
    auto made = syntax.make<ast::DictLit>(written->span);
    made->arena = written->arena;
    made->entries = syntax.list(entries);
    return made;
  }


  const ast::Expr* Checker::worked_out_for_the_image(const ast::Call* call, const ir::Type* ty)
  {
    ReflectHere reflect(*this);
    comptime::Evaluator evaluator(units, {}, {}, &reflect);
    auto callee = static_cast<const ast::NameRef*>(call->callee);
    auto func = evaluator.function(callee->name);
    if (func == nullptr) {
      diags.emit(D::LANG_STATIC_NOT_KNOWN, call->span);
      return nullptr;
    }
    if (std::ranges::any_of(func->attrs, [](const ast::Attribute* a) { return a->name == "impure" || a->name == "io"; })) {
      diags.emit(D::LANG_CONSTANT_CALL_IMPURE, call->callee->span, { { "name", func->name } });
      return nullptr;
    }
    std::vector<std::string> generic;
    for (auto param : func->params)
      parameters_in(param->type, generic);
    comptime::Value answer;
    try {
      if (! generic.empty())
        throw comptime::CannotEvaluate(call->span, "a call to a generic function");
      std::vector<comptime::Value> arguments;
      for (auto one : call->args)
        arguments.push_back(evaluator.evaluate(one));
      answer = evaluator.call(*func, arguments);
    } catch (const comptime::CannotEvaluate& exc) {
      if (exc.ran_out)
        diags.emit(D::LANG_COMPTIME_ENDLESS, exc.span, { { "detail", exc.detail } });
      else
        diags.emit(D::LANG_COMPTIME_CANNOT, exc.span, { { "construct", exc.detail } });
      return nullptr;
    }
    auto literal = as_literal(answer, call->span, ty);
    if (literal == nullptr)
      diags.emit(D::LANG_COMPTIME_CANNOT, call->span,
                 { { "construct", std::format("what '{}' answers, in the image", func->name) } });
    return literal;
  }


  const ast::Expr* Checker::as_literal(const comptime::Value& value, Span span, const ir::Type* ty)
  {
    if (auto truth = value.get<bool>()) {
      auto made = syntax.make<ast::BoolLit>(span);
      made->value = *truth;
      return made;
    }
    if (auto number = value.get<__int128>()) {
      auto made = syntax.make<ast::IntLit>(span);
      made->value = { *number, false };
      return made;
    }
    if (auto text = value.get<std::string>()) {
      auto made = syntax.make<ast::StringLit>(span);
      made->value = syntax.text(*text);
      return made;
    }
    if (auto constant = value.get<const ir::Value*>(); constant != nullptr && (*constant)->vkind == ir::ValueKind::enum_const) {
      auto made = syntax.make<ast::Known>(span);
      made->const_ = *constant;
      return made;
    }
    if (auto table_at = value.get<std::shared_ptr<comptime::Table>>(); table_at != nullptr && ! (*table_at)->plain) {
      const auto& table = **table_at;
      if (table.is_set.value_or(false) || (! table.is_set && ir::is<ir::SetType>(ty))) {
        std::vector<const ast::Expr*> elements;
        bool missing = false;
        for (const auto& [key, _] : table.entries) {
          auto one = as_literal(key, span);
          missing = missing || one == nullptr;
          elements.push_back(one);
        }
        if (missing)
          return nullptr;
        auto made = syntax.make<ast::SetLit>(span);
        made->elements = syntax.list(elements);
        return made;
      }
      std::vector<ast::ExprPair> entries;
      bool missing = false;
      for (const auto& [key, one] : table.entries) {
        auto k = as_literal(key, span);
        auto v = as_literal(one, span);
        missing = missing || k == nullptr || v == nullptr;
        entries.push_back({ k, v });
      }
      if (missing)
        return nullptr;
      auto made = syntax.make<ast::DictLit>(span);
      made->entries = syntax.list(entries);
      return made;
    }
    if (auto run = value.get<std::shared_ptr<comptime::Run>>()) {
      std::vector<const ast::Expr*> elements;
      bool missing = false;
      for (const auto& one : **run) {
        auto found = as_literal(one, span);
        missing = missing || found == nullptr;
        elements.push_back(found);
      }
      if (missing)
        return nullptr;
      auto made = syntax.make<ast::ListLit>(span);
      made->elements = syntax.list(elements);
      return made;
    }
    return nullptr;
  }


  bool Checker::image_definition(const ast::VarDef* node, const ir::Type* declared)
  {
    if (in_the_image(node->value)) {
      if (node->mutable_) {
        diags.emit(D::LANG_STATIC_CHANGES, node->name_span, { { "name", node->name } });
        return false;
      }
      return true;
    }
    bool writable_table = false;
    if (auto set = ir::as<ir::SetType>(declared))
      writable_table = set->is_mutable;
    else if (auto dict = ir::as<ir::DictType>(declared))
      writable_table = dict->is_mutable;
    if (node->mutable_ || ! a_container_written(node->value) || ast::is<ast::Allocated>(node->value) || writable_table)
      return true;
    if (! known_while_compiling(node->value, [this](const ast::Member* m) { return names_an_enum(m); }))
      return true;
    auto derived = static_type(node->value);
    if (derived.is_mixed() || (declared == nullptr && node->type == nullptr && derived.type() == nullptr))
      // Parts that disagree, or nothing that says the type: a mistake the
      // ordinary path has the words for.
      return true;
    diags.emit(D::LANG_CONSTANT_NOT_STATIC, node->value->span, { { "name", node->name } });
    return false;
  }


  Held Checker::static_type(const ast::Expr* expr)
  {
    switch (expr->tag) {
    case ast::NodeKind::IntLit: {
      auto lit = static_cast<const ast::IntLit*>(expr);
      return lit->type_name && ! lit->type_name->empty() ? Held(ir::builtin_type(*lit->type_name)) : Held();
    }
    case ast::NodeKind::FloatLit: {
      auto lit = static_cast<const ast::FloatLit*>(expr);
      return lit->type_name && ! lit->type_name->empty() ? Held(ir::builtin_type(*lit->type_name)) : Held();
    }
    case ast::NodeKind::BoolLit:
      return Held(static_cast<const ir::Type*>(ir::bool_type()));
    case ast::NodeKind::CharLit:
      return Held(static_cast<const ir::Type*>(ir::char_type()));
    case ast::NodeKind::StringLit:
      return Held(static_cast<const ir::Type*>(ir::str_type()));
    case ast::NodeKind::Member: {
      auto member = static_cast<const ast::Member*>(expr);
      if (! names_an_enum(member))
        return {};
      auto held = top->get(std::string(static_cast<const ast::NameRef*>(member->base)->name));
      return Held(resolved(held->get<NamedType>()));
    }
    case ast::NodeKind::Allocated:
      if (in_the_image(expr))
        return static_type(static_cast<const ast::Allocated*>(expr)->value);
      return {};
    case ast::NodeKind::ArrayLit: {
      // Any element anywhere in it says the type of all of them; the writing
      // says the shape.
      auto array = static_cast<const ast::ArrayLit*>(expr);
      auto element = merged(static_leaves(array));
      auto shape = shape_of_literal(array);
      if (element.type() == nullptr || ! shape)
        return element.is_mixed() ? element : Held();
      std::vector<std::optional<std::uint64_t>> lengths;
      for (auto one : *shape)
        lengths.push_back(std::uint64_t(one));
      return Held(static_cast<const ir::Type*>(ir::array_type(element.type(), lengths)));
    }
    case ast::NodeKind::ListLit:
    case ast::NodeKind::SetLit: {
      auto elements = expr->tag == ast::NodeKind::ListLit ? static_cast<const ast::ListLit*>(expr)->elements
                                                           : static_cast<const ast::SetLit*>(expr)->elements;
      std::vector<Held> found;
      for (auto one : elements)
        found.push_back(static_type(one));
      auto element = merged(found);
      if (element.type() == nullptr)
        return element;
      if (expr->tag == ast::NodeKind::ListLit)
        return Held(static_cast<const ir::Type*>(ir::list_type(element.type())));
      return Held(static_cast<const ir::Type*>(ir::set_type(element.type())));
    }
    case ast::NodeKind::DictLit: {
      auto dict = static_cast<const ast::DictLit*>(expr);
      std::vector<Held> keys;
      for (const auto& entry : dict->entries)
        keys.push_back(static_type(entry.key));
      auto key = merged(keys);
      std::vector<Held> values;
      for (const auto& entry : dict->entries)
        values.push_back(static_type(entry.value));
      auto value = merged(values);
      if (key.is_mixed() || value.is_mixed())
        return Held(Mixed{});
      if (key.type() == nullptr || value.type() == nullptr)
        return {};
      return Held(static_cast<const ir::Type*>(ir::dict_type(key.type(), value.type())));
    }
    default:
      return {};
    }
  }


  std::vector<Held> Checker::static_leaves(const ast::ArrayLit* expr)
  {
    std::vector<Held> found;
    for (auto one : expr->elements)
      if (auto inner = ast::as<ast::ArrayLit>(one)) {
        auto deeper = static_leaves(inner);
        found.insert(found.end(), deeper.begin(), deeper.end());
      } else
        found.push_back(static_type(one));
    return found;
  }


  const ir::Value* Checker::address_in_the_image(const ast::AddressOf* expr, const ir::PtrType* ty)
  {
    auto found = place_in_the_image(expr->operand);
    if (! found) {
      diags.emit(D::LANG_ADDRESS_NOT_IN_THE_IMAGE, expr->operand->span);
      return nullptr;
    }
    auto [var, held, offset, writable, what] = *found;
    if (! can_be_referred_to(held)) {
      diags.emit(D::LANG_REF_TYPE_NOT_ALLOWED, expr->span, { { "found", held->written() } });
      return nullptr;
    }
    if ((expr->mutable_ || ty->is_mutable) && ! writable) {
      diags.emit(D::LANG_REF_PLACE_NOT_MUTABLE, expr->span, { { "name", what } });
      return nullptr;
    }
    if (held != ty->pointee) {
      report_mismatch(expr->span, ir::ptr_type(held, ty->is_mutable, true), ty);
      return nullptr;
    }
    return module.constant<ir::AddressConst>(ty, var, offset);
  }


  std::optional<std::tuple<ir::GlobalVar*, const ir::Type*, std::int64_t, bool, std::string>>
  Checker::place_in_the_image(const ast::Expr* expr)
  {
    if (auto ref = ast::as<ast::NameRef>(expr)) {
      auto var = provided(ref->name).get<ir::GlobalVar>();
      if (var == nullptr || var->value_type == ir::error_type())
        return std::nullopt;
      return std::tuple{ var, var->value_type, std::int64_t(0), var->is_mutable(), std::string(ref->name) };
    }
    if (auto member = ast::as<ast::Member>(expr)) {
      auto base = place_in_the_image(member->base);
      if (! base)
        return std::nullopt;
      auto [var, held, offset, writable, _] = *base;
      auto record = ir::as<ir::ProductType>(held);
      if (record == nullptr)
        return std::nullopt;
      auto at = field_index(record, member->name);
      if (! at)
        return std::nullopt;
      ir::DataLayout laid{ .pointer_size = 8, .system = var->system_layout };
      return std::tuple{ var, record->fields[*at].second, offset + std::int64_t(ir::offsets_of(record, laid)[*at]),
                         writable, std::format("{}.{}", written_as(member->base), member->name) };
    }
    if (auto element = ast::as<ast::Element>(expr)) {
      auto base = place_in_the_image(element->base);
      if (! base)
        return std::nullopt;
      auto [var, held, offset, writable, _] = *base;
      auto array = ir::as<ir::ArrayType>(held);
      if (array == nullptr || ! array->fixed() || element->indices.size() != array->rank())
        return std::nullopt;
      __int128 flat = 0;
      for (std::size_t i = 0; i < element->indices.size(); ++i) {
        auto index = ast::as<ast::IntLit>(element->indices[i]);
        __int128 length = array->shape[i].value_or(0);
        if (index == nullptr || index->value.too_large || ! (0 <= index->value.value && index->value.value < length))
          return std::nullopt;
        flat = flat * length + index->value.value;
      }
      ir::DataLayout laid{ .pointer_size = 8, .system = var->system_layout };
      return std::tuple{ var, array->element,
                         offset + std::int64_t(flat) * std::int64_t(ir::stride_of(array->element, laid)), writable,
                         std::string("an element") };
    }
    return std::nullopt;
  }


  bool Checker::names_an_enum(const ast::Member* expr)
  {
    auto ref = ast::as<ast::NameRef>(expr->base);
    auto held = ref != nullptr ? top->get(std::string(ref->name)) : nullptr;
    auto named = held != nullptr ? held->get<NamedType>() : nullptr;
    return named != nullptr && ir::is<ir::EnumType>(resolved(named));
  }


  const ir::PartsConst* Checker::text_constant(std::string_view text)
  {
    auto pieces = ir::parts_of(ir::str_type());
    auto start = module.constant<ir::AddressConst>(pieces[0], text_variable(text), 0);
    auto length = module.int_const(ir::u64(), text.size());
    auto none = module.constant<ir::AddressConst>(pieces[2], nullptr, 0);
    return module.constant<ir::PartsConst>(ir::str_type(), std::vector<const ir::Value*>{ start, length, none });
  }


  ir::GlobalVar* Checker::image_variable(const ir::Type* ty, const ir::Value* value, bool mutable_)
  {
    auto name = std::format("{}{}", static_symbol, module.globals.size());
    auto made = std::make_unique<ir::GlobalVar>(name, ty, ir::ptr_type(ty, mutable_));
    made->initializer = value;
    made->linkage = ir::Linkage::internal;
    return module.add_global(std::move(made));
  }


  const ir::Value* Checker::table_constant(const ast::VarDef* node, const ir::Type* ty,
                                           std::span<const std::tuple<const ast::Expr*, const ast::Expr*>> written)
  {
    tables::Shape shape;
    if (auto set = ir::as<ir::SetType>(ty))
      shape.key = set->element;
    else {
      auto dict = ir::as<ir::DictType>(ty);
      shape.key = dict->key;
      shape.value = dict->value;
    }
    auto held = tables::key_ir_type(shape);
    auto with_value = [&](const ast::Expr* value) {
      auto copy = syntax.copy(*node);
      copy->value = value;
      return copy;
    };
    std::vector<std::tuple<Held, const ir::Value*, const ir::Value*>> keys;
    for (auto [key, value] : written) {
      auto found = constant_value(with_value(key), shape.key);
      if (found == nullptr || ! found->is_const())
        return nullptr;
      const ir::Value* given = nullptr;
      if (value != nullptr && shape.value != nullptr) {
        given = constant_value(with_value(value), shape.value);
        if (given == nullptr || ! given->is_const())
          return nullptr;
      }
      auto bits = key_bits(found);
      if (bits.empty()) {
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, key->span, { { "feature", "a key of this type in the image" } });
        return nullptr;
      }
      const ir::Value* stored = found;
      if (held == ir::u64() && bits.number() != nullptr)
        stored = module.int_const(ir::u64(), *bits.number());
      keys.emplace_back(bits, stored, given);
    }
    auto entry = ir::new_product();
    entry->fields = { { "state", ir::u64() }, { "key", held } };
    if (shape.value != nullptr)
      entry->fields.emplace_back("value", shape.value);
    entry->name = "entry";
    if (std::int64_t(ir::size_of(entry, layout())) != shape.stride()
        || (shape.value != nullptr && std::int64_t(ir::offsets_of(entry, layout())[2]) != shape.value_at())) {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, node->value->span, { { "feature", "a table of this shape in the image" } });
      return nullptr;
    }
    auto hashed = held == ir::u64()
      ? std::function<std::uint64_t(const Held&)>([](const Held& b) { return word_hash(b); })
      : std::function<std::uint64_t(const Held&)>([](const Held& b) { return text_hash(b); });
    auto [capacity, words, dense] = table_laid_out(keys, hashed, shape.stride());
    auto live = module.int_const(ir::u64(), tables::live);
    // The index and the entries are one variable, the entries straight after
    // the index, and after the last entry one more, empty, which is what a
    // lookup of a key that is not there reads.
    auto index_ty = ir::array_type(ir::u64(), { std::uint64_t(capacity) });
    auto dense_ty = ir::array_type(entry, { std::uint64_t(dense.size() + 1) });
    auto run_ty = ir::new_product();
    run_ty->fields = { { "index", index_ty }, { "entries", dense_ty } };
    run_ty->name = "run";
    std::vector<const ir::Value*> index_words;
    for (auto one : words)
      index_words.push_back(module.int_const(ir::u64(), __int128(one)));
    auto index = module.array_const(index_ty, index_words);
    std::vector<const ir::Value*> laid;
    for (const auto& [key, value] : dense) {
      std::vector<const ir::Value*> fields{ live, key };
      if (value != nullptr)
        fields.push_back(value);
      laid.push_back(module.record_const(entry, fields));
    }
    laid.push_back(module.constant<ir::UndefConst>(entry));
    auto entries = module.array_const(dense_ty, laid);
    auto run = image_variable(run_ty, module.record_const(run_ty, { index, entries }));
    auto block = table_block(ir::ptr_type(ir::arena_type(), true));
    auto made = image_variable(block, module.record_const(block, {
      module.constant<ir::AddressConst>(block->fields[0].second, nullptr, 0),
      module.int_const(ir::u64(), capacity - 1),
      module.int_const(ir::u64(), std::int64_t(dense.size())),
      module.int_const(ir::u64(), shape.stride()),
      module.constant<ir::AddressConst>(block->fields[4].second, run, capacity * tables::word),
      module.int_const(ir::u64(), std::int64_t(dense.size())),
      module.int_const(ir::u64(), __int128(tables::image_seed)),
      module.constant<ir::AddressConst>(block->fields[7].second, run, 0),
    }));
    return module.constant<ir::AddressConst>(ty, made, 0);
  }


  const ir::Value* Checker::empty_table_constant(const ir::Type* ty)
  {
    tables::Shape shape;
    if (auto set = ir::as<ir::SetType>(ty))
      shape.key = set->element;
    else {
      auto dict = ir::as<ir::DictType>(ty);
      shape.key = dict->key;
      shape.value = dict->value;
    }
    auto capacity = tables::first_capacity;
    auto size = std::uint64_t(capacity * tables::word + tables::room_for(capacity) * shape.stride());
    auto run_ty = ir::array_type(ir::u8(), { size });
    std::vector<const ir::Value*> zeroes(size, module.int_const(ir::u8(), 0));
    auto run = image_variable(run_ty, module.array_const(run_ty, zeroes), true);
    auto arena = ir::ptr_type(ir::arena_type(), true);
    auto block = table_block(arena);
    // No seed yet: the first key put in is what the table takes one for.
    auto made = image_variable(block, module.record_const(block, {
      module.constant<ir::AddressConst>(arena, tables::heap_global(module), 0),
      module.int_const(ir::u64(), capacity - 1),
      module.int_const(ir::u64(), 0),
      module.int_const(ir::u64(), shape.stride()),
      module.constant<ir::AddressConst>(block->fields[4].second, run, capacity * tables::word),
      module.int_const(ir::u64(), 0),
      module.int_const(ir::u64(), 0),
      module.constant<ir::AddressConst>(block->fields[7].second, run, 0),
    }), true);
    return module.constant<ir::AddressConst>(ty, made, 0);
  }


  Held Checker::key_bits(const ir::Value* key)
  {
    auto low = [](__int128 value, unsigned bits) {
      return bits >= 128 ? value : value & ((__int128(1) << bits) - 1);
    };
    switch (key->vkind) {
    case ir::ValueKind::int_const:
      return Held(low(static_cast<const ir::IntConst*>(key)->value, ir::as<ir::IntType>(key->ty)->bits));
    case ir::ValueKind::bool_const:
      return Held(__int128(static_cast<const ir::BoolConst*>(key)->value ? 1 : 0));
    case ir::ValueKind::char_const:
      return Held(__int128(static_cast<const ir::CharConst*>(key)->value));
    case ir::ValueKind::enum_const: {
      auto holder = ir::as<ir::EnumType>(key->ty)->holder;
      return Held(low(static_cast<const ir::EnumConst*>(key)->number(), holder->bits));
    }
    case ir::ValueKind::parts_const: {
      if (key->ty != ir::str_type())
        return {};
      auto text = static_cast<const ir::AddressConst*>(static_cast<const ir::PartsConst*>(key)->parts[0]);
      auto var = static_cast<const ir::GlobalVar*>(text->target);
      auto run = static_cast<const ir::ArrayConst*>(var->initializer);
      std::string bytes;
      for (auto one : run->elements)
        if (one->vkind == ir::ValueKind::int_const)
          bytes += char(static_cast<const ir::IntConst*>(one)->value);
      return Held(std::move(bytes));
    }
    default:
      return {};
    }
  }


  const ir::Value* Checker::constant_record(const ast::VarDef* node, const ir::ProductType* ty)
  {
    auto written = static_cast<const ast::Call*>(node->value);
    if (! is_record_literal(written, ty)) {
      not_this_type(node, ty);
      return nullptr;
    }
    auto given = fields_given(written, ty);
    if (! given)
      return nullptr;
    std::vector<const ir::Value*> held;
    for (const auto& [name, field_ty] : ty->fields) {
      auto copy = syntax.copy(*node);
      copy->value = (*given->get(name))->value;
      auto found = constant_value(copy, field_ty);
      if (found == nullptr || ! found->is_const())
        return nullptr;
      held.push_back(found);
    }
    return module.record_const(ty, held);
  }


  void Checker::not_this_type(const ast::VarDef* node, const ir::Type* ty)
  {
    auto written = static_cast<const ast::Call*>(node->value);
    NamedType* named = nullptr;
    if (auto ref = ast::as<ast::NameRef>(written->callee))
      if (auto held = top->get(std::string(ref->name)))
        named = held->get<NamedType>();
    if (named != nullptr && ! named->takes_types()) {
      wrong_initializer(node, ty, resolved(named)->written());
      return;
    }
    diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, written->span,
               { { "feature", "a top-level variable whose value is not a literal" } });
  }


  const ir::Value* Checker::constant_sum(const ast::VarDef* node, const ir::SumType* ty)
  {
    auto written = static_cast<const ast::Call*>(node->value);
    NamedType* named = nullptr;
    if (auto ref = ast::as<ast::NameRef>(written->callee))
      if (auto held = top->get(std::string(ref->name)))
        named = held->get<NamedType>();
    if (named == nullptr || resolved(named) != ty) {
      not_this_type(node, ty);
      return nullptr;
    }
    if (written->args.size() != 1) {
      diags.emit(D::LANG_SUM_ONE_PART, written->span,
                 { { "type", ty->written() },
                   { "found", written->args.empty() ? std::string("none") : std::to_string(written->args.size()) } });
      return nullptr;
    }
    auto given = ast::as<ast::Named>(written->args[0]);
    if (given == nullptr) {
      diags.emit(D::LANG_PRODUCT_FIELD_NOT_NAMED, written->args[0]->span, { { "type", ty->written() } });
      return nullptr;
    }
    std::optional<std::size_t> at;
    for (std::size_t index = 0; index < ty->variants.size(); ++index)
      if (ty->variants[index].first == given->name) {
        at = index;
        break;
      }
    if (! at) {
      diags.emit(D::LANG_SUM_NO_SUCH_PART, given->span, { { "type", ty->written() }, { "name", given->name } });
      return nullptr;
    }
    auto held = ty->variants[*at].second;
    if (held == ir::void_type()) {
      auto truth = ast::as<ast::BoolLit>(given->value);
      if (truth == nullptr || ! truth->value) {
        diags.emit(D::LANG_SUM_PART_CARRIES_NOTHING, given->span, { { "type", ty->written() }, { "name", given->name } });
        return nullptr;
      }
      return module.constant<ir::SumConst>(ty, *at, nullptr);
    }
    auto copy = syntax.copy(*node);
    copy->value = given->value;
    auto found = constant_value(copy, held);
    if (found == nullptr || ! found->is_const())
      return nullptr;
    return module.constant<ir::SumConst>(ty, *at, found);
  }


  ir::Value* Checker::read_global(ir::Builder& builder, ir::GlobalVar* var, Span span)
  {
    auto held = var->value_type;
    if (! ir::held_in_memory(held))
      return builder.load(var, span);
    auto where = builder.address(var, span);
    if (ir::is<ir::SumType>(held) && var->is_mutable()) {
      // Copied into the frame, so that what was read goes on holding what it
      // held when the variable is given another value.
      auto room = builder.frame(held, span);
      copy_bytes(builder, room, where, std::int64_t(ir::size_of(held, layout())), span);
      where = room;
    }
    return builder.cast(ir::CastKind::bitcast, where, held, span);
  }


  std::optional<std::vector<const ir::Value*>>
  Checker::constant_elements(const ast::VarDef* node, const ir::ArrayType* ty, const ast::ArrayLit* written,
                             std::span<const std::optional<std::uint64_t>> shape)
  {
    if (! shape[0] || written->elements.size() != *shape[0]) {
      diags.emit(D::LANG_ARRAY_WRONG_LENGTH, written->span,
                 { { "given", written->elements.size() },
                   { "wanted", shape[0] ? std::to_string(*shape[0]) : std::string("None") } });
      return std::nullopt;
    }
    std::vector<const ir::Value*> held;
    for (auto one : written->elements) {
      if (shape.size() == 1) {
        auto copy = syntax.copy(*node);
        copy->value = one;
        auto found = constant_value(copy, ty->element);
        if (found == nullptr || ! found->is_const())
          return std::nullopt;
        held.push_back(found);
        continue;
      }
      auto inner = ast::as<ast::ArrayLit>(one);
      if (inner == nullptr) {
        diags.emit(D::LANG_ARRAY_NOT_WRITTEN_DEEP_ENOUGH, one->span);
        return std::nullopt;
      }
      auto deeper = constant_elements(node, ty, inner, shape.subspan(1));
      if (! deeper)
        return std::nullopt;
      held.insert(held.end(), deeper->begin(), deeper->end());
    }
    return held;
  }


  const ir::EnumConst* Checker::enum_value_of(const ast::Member* expr)
  {
    auto ref = ast::as<ast::NameRef>(expr->base);
    auto held = ref != nullptr ? top->get(std::string(ref->name)) : nullptr;
    auto named = held != nullptr ? held->get<NamedType>() : nullptr;
    if (named == nullptr) {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                 { { "feature", "a top-level variable whose value is not a literal" } });
      return nullptr;
    }
    auto found = enum_value(named, expr);
    return found->vkind == ir::ValueKind::enum_const ? static_cast<const ir::EnumConst*>(found) : nullptr;
  }


  void Checker::wrong_initializer(const ast::VarDef* node, const ir::Type* ty, std::string_view found)
  {
    diags.emit(D::LANG_TYPE_INITIALIZER_MISMATCH, node->value->span,
               { { "name", node->name }, { "expected", ty->written() }, { "found", found } });
  }


  bool Checker::literal_matches(const ast::VarDef* node, const ir::Type* ty)
  {
    auto literal = ast::as<ast::IntLit>(node->value);
    if (literal == nullptr || ! literal->type_name)
      return true;
    auto named = ir::builtin_type(*literal->type_name);
    if (named != nullptr && named != ty) {
      diags.emit(D::LANG_TYPE_INITIALIZER_MISMATCH, literal->span,
                 { { "name", node->name }, { "expected", ty->written() }, { "found", named->written() } });
      return false;
    }
    return true;
  }


  // -- expectations -----------------------------------------------------------

  std::vector<Expected> Checker::expected_numbers(std::span<const BoundAttr> attrs)
  {
    std::vector<Expected> found;
    for (const auto& attr : attrs) {
      if (attr.name() != "expect" && attr.name() != "ignore")
        continue;
      auto number = attr.as_int("number");
      if (! in_the_catalog(number)) {
        diags.emit(D::LANG_ATTR_EXPECT_UNKNOWN_NUMBER, attr.node->span, { { "number", ir::decimal(number) } });
        continue;
      }
      found.push_back({ .number = std::int64_t(number), .span = attr.node->span, .asserted = attr.name() == "expect" });
    }
    return found;
  }


  Expectation* Checker::begin_expecting(std::span<const Expected> pairs)
  {
    if (pairs.empty())
      return nullptr;
    std::vector<DiagId> numbers;
    std::vector<DiagId> required;
    for (const auto& item : pairs) {
      auto id = DiagId(item.number);
      if (std::ranges::find(numbers, id) == numbers.end())
        numbers.push_back(id);
      if (item.asserted && std::ranges::find(required, id) == required.end())
        required.push_back(id);
    }
    return &diags.expect(std::move(numbers), std::move(required));
  }


  void Checker::end_expecting(Expectation* expectation)
  {
    if (expectation != nullptr)
      diags.release(*expectation);
  }


  bool Checker::settle_expecting(Expectation* expectation, std::span<const Expected> pairs)
  {
    if (expectation == nullptr)
      return false;
    for (auto number : expectation->unmet()) {
      auto item = std::ranges::find_if(pairs, [&](const Expected& one) { return one.number == std::int64_t(number); });
      diags.emit(D::LANG_ATTR_EXPECT_NOT_RAISED, item->span, { { "number", std::int64_t(number) } });
    }
    return expectation->saw_error;
  }


  // -- scopes -----------------------------------------------------------------

  void Checker::push_scope()
  {
    scopes.emplace_back();
    unit_scopes.emplace_back();
  }


  void Checker::pop_scope(std::optional<Span> left_at)
  {
    if (left_at)
      all_closed(std::span(scopes).last(1), *left_at);
    // A reference kept by a name in this scope is gone with the name: kept at
    // the scope's index, everything at it or deeper goes.
    auto depth = std::int64_t(scopes.size()) - 1;
    std::erase_if(borrows, [&](const Borrow* b) { return b->depth >= depth; });
    std::erase_if(iterator_loans, [&](const auto& one) { return std::get<3>(one) >= depth; });
    unit_scopes.pop_back();
    auto leaving = std::move(scopes.back());
    scopes.pop_back();
    for (const auto& [name, local] : leaving) {
      report_unused(local);
      report_needless_mut(local);
      settle_local(local);
    }
  }


  void Checker::report_unused(Local* local)
  {
    if (local->read || local->is_parameter || local->value->ty == ir::error_type())
      return;
    if (session != nullptr && session->kept.contains(local->span.start))
      return;
    if (local->placed && local->written)
      // A place written and not read is the reason the place is there.
      return;
    if (local->expectation != nullptr)
      diags.resume(*local->expectation);
    diags.emit(D::LANG_VARDEF_VALUE_UNUSED, local->value_span, { { "name", local->name } });
    end_expecting(local->expectation);
  }


  void Checker::report_needless_mut(Local* local)
  {
    if (! local->is_mutable || ! changed_here || changed_here->contains(local->name)
        || local->value->ty == ir::error_type())
      return;
    if (session != nullptr && session->kept.contains(local->span.start))
      return;
    auto held = held_by(local);
    if ((whole_here.contains(local->name) && (ir::is<ir::SetType>(held) || ir::is<ir::DictType>(held)))
        || held == ir::arena_type() || local->name == names::wildcard_name || local->name.contains('#')
        || local->name.starts_with(iter_glyph) || local->name.starts_with(names::builtin_glyph)
        || mut_told.contains(local->span.start) || ! local->span.is_valid())
      return;
    mut_told.insert(local->span.start);
    if (local->expectation != nullptr)
      diags.resume(*local->expectation);
    diags.emit(D::LANG_MUT_NEVER_CHANGED, local->span,
               { { "name", local->name }, { "what", local->is_parameter ? "parameter" : "variable" } });
    end_expecting(local->expectation);
  }


  void Checker::settle_local(Local* local)
  {
    if (settle_expecting(local->expectation, local->expected_pairs))
      discard_function = true;
    local->expectation = nullptr;
  }


  void Checker::bind_local(std::string_view name, ir::Value* value, Span span, bool mutable_, Span value_span,
                           bool is_parameter, ir::Builder* builder, const ir::Type* placed_as)
  {
    auto& scope = scopes.back();
    auto key = std::string(name);
    if (auto previous = scope.get(key); previous != nullptr) {
      diags.emit(D::LANG_FILESTRUCT_DUPLICATE_DEFINITION, span, { { "name", name } })
        .note(D::LANG_FILESTRUCT_PREVIOUS_DEFINITION, (*previous)->span, { { "name", name } });
      return;
    }
    auto local = make<Local>();
    local->name = key;
    local->span = span;
    local->is_mutable = mutable_;
    local->value_span = value_span.is_valid() ? value_span : span;
    local->is_parameter = is_parameter;
    local->depth = std::int64_t(scopes.size());
    if (placed_as != nullptr) {
      // A name that already stands for a place: what was handed over is the
      // address, which is what a lambda's capture by reference brings in.
      local->value = value;
      local->placed = true;
      local->held = placed_as;
      scope[key] = local;
      places[id_of(value)] = local;
      lifetime_begins(local, placed_as);
      return;
    }
    auto held = value_type_of(value);
    // A record is given storage of its own whether or not anything takes its
    // address; and so is a name that may change holding a value held in
    // memory, a new value being copied into its own storage.
    bool changing = mutable_ && ir::held_in_memory(held) && ! ir::is<ir::ArrayType>(held);
    bool placed = builder != nullptr
      && (addressed.contains(name) || ir::is<ir::ProductType>(held) || changing)
      && (can_be_referred_to(held) || ir::is<ir::ListType>(held) || changing);
    if (placed) {
      // The program did not ask for that, so the log says so and which of the
      // reasons it was.
      std::string_view reason =
        addressed.contains(name)
          ? "something takes its address, so it is kept in storage of its own rather than in a register"
        : ir::is<ir::ProductType>(held)
          ? "it is a record, and a field is read at an offset, so it is kept in storage of its own for there to be an "
            "offset from"
          : "it may change and its value is bytes in memory, so it is kept in storage of its own and a new value is "
            "copied in";
      module.record("place-local", key, std::string(reason), span);
      auto place = builder->frame(held, span);
      if (auto record = ir::as<ir::ProductType>(held))
        record_into(*builder, place, value, record, span);
      else
        builder->store(place, value, span);
      value = place;
    }
    local->value = value;
    local->placed = placed;
    local->held = placed ? held : nullptr;
    scope[key] = local;
    if (placed)
      // Where the place is, so that a reference worked out from it can be
      // walked back to how long it lasts.
      places[id_of(value)] = local;
    lifetime_begins(local, held);
  }


  void Checker::lifetime_begins(Local* local, const ir::Type* held)
  {
    auto closer = closer_of(held);
    if (closer.empty())
      return;
    local->closer = closer;
    auto func = std::get<1>(demanding);
    auto record = ir::as<ir::ProductType>(held);
    if (local->is_parameter && func != nullptr && record != nullptr
        && func->name == std::format("{}.{}", record->name, closer)) {
      module.record("lifetime-ended", local->name,
                    std::format("closed: this is '{}', and what it is handed it closes", func->name), local->span);
      return;
    }
    local->owes = true;
  }


  bool Checker::moved(Local* local, Span span, std::string_view how)
  {
    if (local->ended_at) {
      told_ended(local, span);
      return false;
    }
    local->owes = false;
    local->ended_at = span;
    local->ended_how = std::string(how);
    // The move is the one thing said about the variable's end.
    moved_from.insert(span.start);
    if (in_deferred == "again")
      return true;
    std::string said(how);
    if (in_deferred && deferred_at) {
      // Put off: the move is made where the block is left, and the entries go
      // on the line after the block.
      auto [written, block] = *deferred_at;
      auto found = sources->position(written->span.start);
      said = std::format("{}, by what is put off on line {}", how,
                         found ? std::to_string(found->line) : std::string("?"));
      span = line_after(*sources, block.end);
    }
    module.record("move", local->name, said, span);
    return true;
  }


  void Checker::told_ended(Local* local, Span span)
  {
    if (! told_after_end.insert({ id_of(local), span.start, span.end }).second)
      return;
    diags.emit(D::LANG_CLOSABLE_AFTER_ITS_END, span, { { "name", local->name } })
      .note(D::LANG_CLOSABLE_ENDED_HERE, local->ended_at.value_or(span),
            { { "name", local->name }, { "how", std::format("its lifetime ended here: {}", local->ended_how) } });
  }


  void Checker::all_closed(std::span<const Dict<std::string, Local*>> left, Span span)
  {
    for (const auto& scope : left)
      for (const auto& [name, local] : scope) {
        if (local->closer.empty() || ! local->owes)
          continue;
        if (! told_unclosed.insert(id_of(local)).second)
          // Said once: every further way out would say it again.
          continue;
        diags.emit(D::LANG_CLOSABLE_NOT_CLOSED, span,
                   { { "name", local->name }, { "type", held_by(local)->written() }, { "closer", local->closer } })
          .note(D::LANG_CLOSABLE_ENDED_HERE, local->span, { { "name", local->name }, { "how", "its lifetime began here" } });
      }
  }


  ir::Value* Checker::not_moved_out(const ast::Expr* expr, ir::Value* value)
  {
    auto held = value_type_of(value);
    if (held == ir::error_type())
      return value;
    auto found = closable_in(held);
    if (found == nullptr)
      return value;
    if (auto stored = stored_into.get(id_of(expr)); stored != nullptr && ! made_where_written(expr, held)) {
      diags.emit(D::LANG_CLOSABLE_STORED, expr->span, { { "type", found->written() }, { "what", *stored } });
      return undef_error(module);
    }
    std::optional<std::string> what;
    switch (expr->tag) {
    case ast::NodeKind::Member:
      what = module_at(static_cast<const ast::Member*>(expr)->base) != nullptr ? "a variable at the top level" : "a field";
      break;
    case ast::NodeKind::Element:
    case ast::NodeKind::Index:
      what = "an element";
      break;
    case ast::NodeKind::Deref:
      what = "what a reference names";
      break;
    default:
      break;
    }
    if (what) {
      diags.emit(D::LANG_CLOSABLE_MOVED_OUT_OF_PLACE, expr->span, { { "type", found->written() }, { "what", *what } });
      return undef_error(module);
    }
    return value;
  }


  void Checker::report_undefined(std::string_view name, Span span)
  {
    auto key = std::string(name);
    if (std::ranges::any_of(outside, [&](const Dict<std::string, Local*>* scope) { return scope->contains(key); })) {
      diags.emit(D::LANG_CAPTURE_NOT_LISTED, span, { { "name", name } });
      return;
    }
    diags.emit(D::LANG_FILESTRUCT_UNDEFINED_NAME, span, { { "name", name } });
  }


  const ir::Type* Checker::held_by(Local* local)
  {
    if (local->placed && local->held != nullptr)
      return local->held;
    return value_type_of(local->value);
  }


  Local* Checker::find_local(std::string_view name)
  {
    auto key = std::string(name);
    for (auto& scope : std::views::reverse(scopes))
      if (auto found = scope.get(key))
        return *found;
    return nullptr;
  }


  void Checker::note(Span span, std::string_view kind, std::string_view name, std::string_view detail, Span defined_at,
                     std::optional<std::string> doc)
  {
    if (notes == nullptr)
      return;
    notes->add({ .span = span,
                 .kind = kind,
                 .name = std::string(name),
                 .detail = std::string(detail),
                 .defined = defined_at,
                 .doc = std::move(doc) });
  }


  ir::Value* Checker::lookup(const ast::NameRef* ref)
  {
    if (ref->name == names::wildcard_name) {
      // Where a value goes to be dropped: nothing there to read back.
      diags.emit(D::LANG_WILDCARD_IS_NOT_READ, ref->span);
      return nullptr;
    }
    if (auto found = find_local(ref->name)) {
      found->read = true;
      if (notes != nullptr) {
        auto held = found->placed && found->held != nullptr ? found->held : found->value->ty;
        note(ref->span, found->is_parameter ? note_kind::parameter : note_kind::variable, ref->name, held->written(),
             found->span);
      }
      return found->value;
    }
    auto found_global = provided(ref->name);
    if (auto var = found_global.get<ir::GlobalVar>()) {
      if (notes != nullptr)
        note(ref->span, note_kind::variable, ref->name, var->value_type->written(), var->span);
      return var;
    }
    if (found_global.get<LoadedModule>() != nullptr) {
      diags.emit(D::LANG_IMPORT_MODULE_AS_VALUE, ref->span, { { "name", ref->name } });
      return nullptr;
    }
    report_undefined(ref->name, ref->span);
    return nullptr;
  }

} // namespace pl4g::sema
