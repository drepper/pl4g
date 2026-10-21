// The checker, check.py lines 6566 to 9481: collecting the definitions,
// their attributes, the types and units a program defines, what a macro is
// made of, the operators a program defines, what a signature requires and
// demands, and the conditions a body carries.
#include "sema/check.hh"

#include <algorithm>
#include <format>
#include <ranges>

#include "front/lexer.hh"
#include "ir/printer.hh"
#include "rt/names.hh"
#include "sema/owned.hh"
#include "sema/strings.hh"
#include "target/registry.hh"
#include "target/statuses.hh"


namespace pl4g::sema {

  namespace {

    // The names of front/token.py the part here needs.
    constexpr std::string_view heap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";
    constexpr std::string_view static_name = "\N{APL FUNCTIONAL SYMBOL QUAD}static";
    constexpr std::string_view empty_arena_name = "\N{APL FUNCTIONAL SYMBOL QUAD}arena";
    constexpr std::string_view answer_name = "\N{APL FUNCTIONAL SYMBOL QUAD}answer";
    constexpr std::string_view typeof_name = "\N{APL FUNCTIONAL SYMBOL QUAD}typeof";
    constexpr std::string_view iter_glyph = "\N{RIGHTWARDS PAIRED ARROWS}";
    constexpr std::string_view lifetime_glyph = "\N{WHITE HOURGLASS}";
    constexpr std::string_view arena_name = "arena";


    // Run `f` on the way out of a scope, however it is left: Python's `finally`.
    template<typename F>
    struct Finally {
      F f;
      ~Finally() { f(); }
    };
    template<typename F>
    Finally(F) -> Finally<F>;


    // What Python's `UndefConst(ERROR)` stands for: a value of no type.
    ir::Value* undef_error(ir::Module& module)
    {
      return const_cast<ir::UndefConst*>(module.undef(ir::error_type()));
    }

    // How many characters `text` has, which is what Python's `len` counts.
    std::size_t code_points(std::string_view text)
    {
      return std::ranges::count_if(text, [](char c) { return (static_cast<unsigned char>(c) & 0xc0) != 0x80; });
    }

    // Numbers as the IR's `Function` keeps them.
    std::vector<int> ints(const std::vector<std::int64_t>& numbers)
    {
      return { numbers.begin(), numbers.end() };
    }

    // The keys of a dict of borrows as `func_type` takes them: sorted.
    std::vector<std::pair<int, ir::Borrowed>> sorted_borrows(const Dict<std::int64_t, ir::Borrowed>& borrows)
    {
      std::vector<std::pair<int, ir::Borrowed>> found;
      for (const auto& [at, one] : borrows)
        found.emplace_back(int(at), one);
      std::ranges::sort(found, {}, &std::pair<int, ir::Borrowed>::first);
      return found;
    }

    // A function type with these flags, as pypl4g's `func_type` takes them.
    const ir::FuncType* function_type(std::vector<const ir::Type*> params, const ir::Type* ret, bool listable = false,
                                  bool hands_out = false, bool lasting = false, bool impure = false,
                                  bool can_ignore = false, std::vector<std::pair<int, ir::Borrowed>> borrows = {})
    {
      ir::FuncType shape;
      shape.listable = listable;
      shape.hands_out = hands_out;
      shape.lasting = lasting;
      shape.impure = impure;
      shape.can_ignore = can_ignore;
      shape.borrows = std::move(borrows);
      return ir::func_type(std::move(params), ret, shape);
    }

    // Put the names of another file in force while an object lives, and the
    // ones in force before back when it goes.
    struct NamesKept {
      Checker& checker;
      Dict<std::string, Held>* top;
      Dict<std::string, Bundle*>* bundles;

      NamesKept(Checker& checker, Dict<std::string, Held>* scope, Dict<std::string, Bundle*>* siblings)
        : checker(checker), top(checker.top), bundles(checker.bundles)
      {
        if (scope != nullptr && siblings != nullptr) {
          checker.top = scope;
          checker.bundles = siblings;
        }
      }
      NamesKept(const NamesKept&) = delete;
      NamesKept& operator=(const NamesKept&) = delete;
      ~NamesKept()
      {
        checker.top = top;
        checker.bundles = bundles;
      }
    };

  } // anonymous namespace


  // -- collection -------------------------------------------------------------

  void Checker::check_doc(const ast::Node* node)
  {
    ast::MaybeText text;
    std::span<const Span> lines;
    std::string_view owner;
    const ast::FuncDef* func = ast::as<ast::FuncDef>(node);
    ast::visit(*node, [&](const auto& n) {
      if constexpr (requires { n.doc; n.doc_lines; n.name; }) {
        text = n.doc;
        lines = n.doc_lines;
        owner = n.name;
      }
    });
    if (! text || text->empty())
      return;
    auto found = doc::parse(*text);
    if (found.parts.empty())
      return;
    NameSet takes;
    if (func != nullptr)
      for (auto param : func->params)
        takes.emplace(param->name);
    bool answers = func != nullptr && func->ret_type != nullptr;
    NameSet documented;
    for (const auto& part : found.parts) {
      Span where = doc_span(lines, part);
      std::string written = part.written();
      if (! part.known()) {
        diags.emit(D::LANG_DOC_UNKNOWN_COMMAND, where, { { "command", written } });
        continue;
      }
      if ((part.command == "param" || part.command == "return") && func == nullptr) {
        diags.emit(D::LANG_DOC_NOT_A_FUNCTION, where, { { "command", written }, { "owner", owner } });
        continue;
      }
      if (part.command == "param") {
        if (documented.contains(part.subject))
          diags.emit(D::LANG_DOC_PARAM_TWICE, where, { { "name", part.subject } });
        else if (! takes.contains(part.subject))
          diags.emit(D::LANG_DOC_PARAM_UNKNOWN, where,
                     { { "command", written }, { "name", part.subject }, { "owner", owner } });
        documented.insert(part.subject);
      } else if (part.command == "return" && ! answers)
        diags.emit(D::LANG_DOC_RETURN_OF_NOTHING, where, { { "command", written }, { "owner", owner } });
    }
  }


  std::vector<const ir::Type*> Checker::as_the_entry_hands_over(const ast::FuncDef* node,
                                                                std::span<const BoundAttr> attrs,
                                                                std::span<const ir::Type* const> params)
  {
    std::vector<const ir::Type*> found(params.begin(), params.end());
    if (params.empty() || std::get<0>(function_attrs(attrs)).special != ir::SpecialKind::startup)
      return found;
    auto record = ir::as<ir::ProductType>(params[0]);
    if (record == nullptr || record->name != startup_argument_type_name)
      return found;
    found[0] = ir::ptr_type(params[0], node->params[0]->mutable_);
    return found;
  }


  void Checker::check_several(const ast::FuncDef* node)
  {
    for (std::size_t at = 0; at < node->params.size(); ++at) {
      auto param = node->params[at];
      if (! param->several)
        continue;
      std::string_view why;
      if (! node->is_macro)
        why = "only a macro has one";
      else if (at != node->params.size() - 1)
        why = "nothing after it could be given anything";
      if (! why.empty())
        diags.emit(D::LANG_SEVERAL_NOT_A_MACRO_TAIL, param->span, { { "name", param->name }, { "why", why } });
    }
  }


  bool Checker::a_type_here(const ast::FuncDef* node)
  {
    const Held* found = top->get(std::string(*node->held));
    if (found != nullptr && found->get<NamedType>() != nullptr)
      return true;
    diags.emit(D::LANG_MEMBER_NOT_A_TYPE_HERE, node->held_span.value_or(node->name_span),
               { { "name", *node->held },
                 { "found", found == nullptr || found->empty() ? "nothing here" : "not a type this file defines" } });
    return false;
  }


  Collected* Checker::collect_function(const ast::FuncDef* node, std::string_view path)
  {
    node = kept_in_the_signature(node);
    check_doc(node);
    if (! declare(declared_as(node), node->name_span, path))
      return nullptr;
    std::string whole = written_name(node);
    if (node->held && ! a_type_here(node))
      return nullptr;
    check_several(node);
    auto attrs = bind_attributes(node->attrs, AttrTarget::function);
    std::vector<std::string> written;
    for (auto param : node->params)
      parameters_in(param->type, written);
    if (node->generates)
      return collect_generator(node, path, attrs, ! written.empty());
    if (! written.empty())
      return collect_generic(node, path, attrs, written);
    std::vector<std::string> answered;
    parameters_in(node->ret_type, answered);
    if (! answered.empty()) {
      diags.emit(D::LANG_GENERIC_NOT_DETERMINED, node->name_span, { { "name", answered[0] } });
      return nullptr;
    }
    // What a definition says it raises holds while its signature is checked
    // here and again while its body is checked in the second pass.
    auto pairs = expected_numbers(attrs);
    expected_pairs[std::string(node->name)] = pairs;
    Expectation* expectation = begin_expecting(pairs);
    auto outer_scopes = lifetime_scopes;
    lifetime_scopes.push_back(lifetime_scope(node->params, node));
    Finally restore{ [&] {
      lifetime_scopes = outer_scopes;
      if (expectation != nullptr)
        diags.release(*expectation);
    } };

    std::vector<const ir::Type*> params;
    for (auto p : node->params)
      params.push_back(resolve_type(p->type));
    for (std::size_t at = 0; at < params.size(); ++at)
      if (auto iter = ir::as<ir::IterType>(params[at]); iter != nullptr && iter->element == nullptr)
        diags.emit(D::LANG_ITERATOR_ELEMENT_LEFT_OUT, node->params[at]->type->span);
    for (auto& ty : params)
      if (auto iter = ir::as<ir::IterType>(ty); iter != nullptr && iter->element == nullptr)
        ty = ir::error_type();
    params = as_the_entry_hands_over(node, attrs, params);
    const ir::Type* ret = return_type(node->ret_type);
    std::vector<const ast::TypeExpr*> param_types;
    for (auto p : node->params)
      param_types.push_back(p->type);
    auto borrows = borrows_written(param_types, params, node->ret_type, ret,
                                   node->ret_type != nullptr ? node->ret_type->span : node->name_span);
    auto [made_in, param_made_in] = made_in_of(node, params, ret);
    auto hands_out = hands_out_of(node);
    if (hands_out)
      params.push_back(ir::ptr_type(ir::arena_type(), true));
    auto [func_attrs, linkage] = function_attrs(attrs);

    auto made = std::make_unique<ir::Function>(whole, function_type(params, ret, func_attrs.listable, false, false, false,
                                                                false, sorted_borrows(borrows)));
    made->hands_out = hands_out;
    made->attrs = func_attrs;
    made->linkage = linkage;
    for (auto p : node->params)
      made->param_names.emplace_back(p->name);
    made->writes_arrays = ints(writes_arrays(node, params));
    made->takes_owned = ints(takes_owned(node, params));
    made->defaults = defaults_of(node, params);
    if (auto all = borrows.get(-1); all != nullptr)
      made->borrows_from = all->params;
    for (const auto& [leaf, one] : borrows)
      if (leaf >= 0)
        made->leaf_borrows.emplace_back(int(leaf), one.params);
    for (auto [slot, kept] : kept_pairs(node))
      made->keeps.emplace_back(int(slot), int(kept));
    made->lifetime_key = lifetime_key(node);
    for (auto& [name, at] : carried_lifetimes(node, params))
      made->lifetimes.emplace_back(name, ints(at));
    made->made_in = made_in;
    made->param_made_in = param_made_in;
    made->exported = is_export(attrs);
    made->cconv = convention_of(func_attrs);
    made->span = node->span;
    made->name_span = node->name_span;
    made->source_path = std::string(path);
    made->doc = std::string(node->doc.value_or(""));
    ir::Function* func = made.get();
    node_of_func[id_of(func)] = node;
    if (func_attrs.listable && params.empty())
      diags.emit(D::LANG_LISTABLE_TAKES_NOTHING, node->name_span, { { "name", node->name } });
    check_external(node, func_attrs);
    check_host(node, func_attrs, params, ret);
    check_offered(node, func_attrs, linkage);
    // Wherever it stands and whoever wrote it: a record laid out for something
    // else to read travels as a reference or not at all.
    for (std::size_t at = 0; at < std::min(params.size(), node->params.size()); ++at) {
      not_by_value(params[at], node->params[at]->type->span);
      not_a_piece(params[at], node->params[at]->type->span, node->name);
    }
    not_a_piece(ret, node->ret_type != nullptr ? node->ret_type->span : node->name_span, node->name);
    if (node->ret_type != nullptr)
      not_by_value(ret, node->ret_type->span);
    module.add_function(std::move(made), key(whole));
    hand_over_text(func);
    if (! node->held)
      (*top)[std::string(node->name)] = Held(static_cast<ir::Value*>(func));
    else
      members[{ std::string(*node->held), std::string(node->name) }] = Held(static_cast<ir::Value*>(func));
    an_operator(node, Held(static_cast<ir::Value*>(func)));
    owned.push_back(Held(static_cast<ir::Value*>(func)));
    register_special(func, node);

    auto collected = make<Collected>();
    collected->node = node;
    collected->attrs = attrs;
    collected->func = func;
    collected->expectation = expectation;
    return collected;
  }


  Collected* Checker::collect_generator(const ast::FuncDef* node, std::string_view path,
                                        std::span<const BoundAttr> attrs, bool generic)
  {
    if (node->body == nullptr) {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, node->name_span, { { "feature", "a generator defined somewhere else" } });
      return nullptr;
    }
    auto func_attrs = std::get<0>(function_attrs(attrs));
    if (generic) {
      // Its types are what a call gives it, so nothing of it is compiled here.
      std::vector<std::string> written;
      for (auto param : node->params)
        parameters_in(param->type, written);
      auto made = make<Generator>();
      made->node = node;
      made->element = ir::error_type();
      made->impure = func_attrs.impure || func_attrs.io;
      made->path = std::string(path);
      made->exported = is_export(attrs);
      made->scope = top;
      made->siblings = bundles;
      made->parameters = written;
      made->attrs.assign(attrs.begin(), attrs.end());
      put_generator(node, made);
      return nullptr;
    }
    Generator* made = generator_function(node, path, func_attrs, {});
    if (made == nullptr)
      return nullptr;
    made->exported = is_export(attrs);
    made->scope = top;
    made->siblings = bundles;
    put_generator(node, made);
    auto collected = make<Collected>();
    collected->node = node;
    collected->attrs.assign(attrs.begin(), attrs.end());
    collected->func = made->func;
    return collected;
  }


  void Checker::put_generator(const ast::FuncDef* node, Generator* made)
  {
    if (node->held && node->name == sema::each_name
        && (node->params.size() != 2 || ! could_be_an_arena(node->params[1]->type)))
      diags.emit(D::LANG_EACH_SHAPE, node->name_span, { { "type", *node->held } });
    if (! node->held)
      (*top)[std::string(node->name)] = Held(made);
    else
      members[{ std::string(*node->held), std::string(node->name) }] = Held(made);
  }


  Generator* Checker::generator_function(const ast::FuncDef* node, std::string_view path,
                                         const ir::FuncAttrs& func_attrs,
                                         const Dict<std::string, const ir::Type*>& bound)
  {
    std::vector<const ir::Type*> params;
    for (auto p : node->params)
      params.push_back(resolve_type(p->type));
    const ir::Type* element = node->ret_type != nullptr ? resolve_type(node->ret_type) : ir::error_type();
    if (element == ir::error_type() || std::ranges::contains(params, static_cast<const ir::Type*>(ir::error_type())))
      return nullptr;
    const ir::Type* given = node->takes != nullptr ? resolve_type(node->takes) : nullptr;
    if (given == ir::error_type())
      return nullptr;
    const ir::Type* finishes = node->finishes != nullptr ? resolve_type(node->finishes) : nullptr;
    if (finishes == ir::error_type())
      return nullptr;
    if (auto short_ = short_lived(element); short_ != nullptr) {
      diags.emit(D::LANG_ITERATOR_DOES_NOT_LEAVE, node->ret_type != nullptr ? node->ret_type->span : node->name_span,
                 { { "found", short_->written() }, { "what", "handed out" } });
      return nullptr;
    }
    std::string whole = written_name(node);
    if (! bound.empty()) {
      std::string types;
      for (const auto& [name, ty] : bound) {
        if (! types.empty())
          types += ",";
        types += ty->mangled();
      }
      whole = std::format("{}\N{TOP LEFT CORNER}{}\N{TOP RIGHT CORNER}", whole, types);
    }
    ir::CoroutineFrame* frame = ir::new_frame(whole);
    std::string name = std::format("{}{}", iter_glyph, whole);
    auto environment = environment_type();
    ir::FuncAttrs attrs = func_attrs;
    attrs.impure = true;
    attrs.inline_ = ir::InlineHint::never;
    attrs.chained = true;
    auto made = std::make_unique<ir::Function>(name, function_type({ environment, environment, environment }, ir::u8()));
    made->attrs = attrs;
    made->coroutine = frame;
    made->linkage = ir::Linkage::internal;
    made->param_names = { "frame", "out", "args" };
    made->span = node->span;
    made->name_span = node->name_span;
    made->source_path = std::string(path);
    made->doc = std::string(node->doc.value_or(""));
    ir::Function* func = module.add_function(std::move(made), key(name));
    owned.push_back(Held(static_cast<ir::Value*>(func)));
    auto found = make<Generator>();
    found->node = node;
    found->func = func;
    found->params = params;
    found->element = element;
    found->impure = func_attrs.impure || func_attrs.io;
    found->frame = frame;
    found->path = std::string(path);
    found->bound = bound;
    found->given = given;
    found->finishes = finishes;
    generator_bodies[id_of(func)] = found;
    return found;
  }


  void Checker::lower_set_aside(Collected* entry)
  {
    // What the checker keeps about the body it is lowering, which a body
    // lowered in the middle of another sets aside and puts back.
    auto saved = std::tuple(std::move(scopes), std::move(addressed), answering, impure, std::move(carried),
                            std::move(loops), std::move(outside), std::move(initializing), std::move(assigning),
                            std::move(operand_of), std::move(handing_over), std::move(deferred), std::move(emptied),
                            std::move(answer_made_in), out_of_arena, std::move(out_of_arenas), std::move(made_from),
                            std::move(answer_kinds), thin_to, answer_arms, yield_answer, std::move(hands_out),
                            hands_out_local, std::move(receiving), unscanned, std::move(borrows), std::move(places),
                            std::move(iterator_loans), pick_in, std::move(pick_walked), demanding,
                            std::move(writing_in), std::move(block_spans));
    Finally restore{ [&] {
      std::tie(scopes, addressed, answering, impure, carried, loops, outside, initializing, assigning, operand_of,
               handing_over, deferred, emptied, answer_made_in, out_of_arena, out_of_arenas, made_from, answer_kinds,
               thin_to, answer_arms, yield_answer, hands_out, hands_out_local, receiving, unscanned, borrows, places,
               iterator_loans, pick_in, pick_walked, demanding, writing_in, block_spans)
        = std::move(saved);
    } };
    scopes.clear();
    addressed.clear();
    deferred.clear();
    emptied.clear();
    block_spans.clear();
    carried.clear();
    loops.clear();
    outside.clear();
    borrows.clear();
    places.clear();
    iterator_loans.clear();
    writing_in.clear();
    initializing.reset();
    assigning.reset();
    operand_of.reset();
    handing_over.reset();
    pick_in = nullptr;
    pick_walked = {};
    answer_arms = nullptr;
    yield_answer = nullptr;
    hands_out.reset();
    hands_out_local = nullptr;
    receiving.reset();
    out_of_arena = nullptr;
    out_of_arenas = KeySet{ heap_key };
    made_from.clear();
    lower_function(entry);
  }


  Generator* Checker::generator_instance(Generator* template_, const Dict<std::string, const ir::Type*>& given,
                                         Span span)
  {
    for (const auto& name : template_->parameters)
      if (! given.contains(name)) {
        diags.emit(D::LANG_GENERIC_NOT_DETERMINED, span, { { "name", name } });
        return nullptr;
      }
    Types key_;
    for (const auto& name : template_->parameters)
      key_.types.push_back(*given.get(name));
    if (auto found = template_->instances.get(key_); found != nullptr)
      return *found;
    auto outer = bound;
    bound = given;
    Finally restore{ [&] { bound = outer; } };
    auto scope = in_scope_of(Held(template_));
    auto attrs = std::get<0>(function_attrs(template_->attrs));
    Generator* found = generator_function(template_->node, template_->path, attrs, given);
    if (found == nullptr)
      return nullptr;
    found->scope = template_->scope;
    found->siblings = template_->siblings;
    found->exported = template_->exported;
    template_->instances[key_] = found;
    std::string types;
    for (auto one : key_.types) {
      if (! types.empty())
        types += ", ";
      types += one->written();
    }
    module.record("instantiate", std::string(template_->node->name),
                  std::format("compiled for {}, which is what a call gave it", types), span);
    auto entry = make<Collected>();
    entry->node = template_->node;
    entry->attrs = template_->attrs;
    entry->func = found->func;
    lower_set_aside(entry);
    return found;
  }


  void Checker::check_external(const ast::FuncDef* node, const ir::FuncAttrs& attrs)
  {
    if (! attrs.external) {
      if (node->body == nullptr && ! attrs.builtin && ! attrs.host)
        diags.emit(D::LANG_FUNCDEF_NO_BODY, node->name_span, { { "name", node->name } });
      return;
    }
    if (node->body != nullptr)
      diags.emit(D::LANG_FUNCDEF_EXTERNAL_HAS_A_BODY, node->name_span, { { "name", node->name } });
    if (! rt::defines(*attrs.external))
      diags.emit(D::LANG_FUNCDEF_EXTERNAL_UNKNOWN, node->name_span, { { "symbol", *attrs.external } });
  }


  std::string Checker::convention_of(const ir::FuncAttrs& attrs) const
  {
    if (attrs.abi)
      return *attrs.abi;
    // A constructor or a destructor of a shared object is called by the
    // loader, from the table of them the object carries, which calls the way
    // the system calls things: there is nothing in between to translate.
    if (module.output == ir::Output::shared
        && (attrs.special == ir::SpecialKind::constructor || attrs.special == ir::SpecialKind::destructor))
      return std::string(ir::system_cconv);
    return std::string(ir::default_cconv);
  }


  void Checker::check_host(const ast::FuncDef* node, const ir::FuncAttrs& attrs,
                           std::span<const ir::Type* const> params, const ir::Type* ret)
  {
    if (! attrs.host)
      return;
    // It has no body, as an external function has none; there has to be a
    // host, which an executable has not; and what crosses is what C has.
    if (node->body != nullptr)
      diags.emit(D::LANG_FUNCDEF_EXTERNAL_HAS_A_BODY, node->name_span, { { "name", node->name } });
    if (! ir::hosted(module.output))
      diags.emit(D::LANG_HOST_IN_AN_EXECUTABLE, node->name_span, { { "name", node->name } });
    for (std::size_t at = 0; at < std::min(params.size(), node->params.size()); ++at) {
      auto ty = params[at];
      if (auto record = ir::as<ir::ProductType>(ty); ty == ir::error_type() || (record != nullptr && record->abi))
        // Not known, or a record laid out for C, which 4596 is about.
        continue;
      if (ir::as<ir::IntType>(ty) == nullptr && ir::as<ir::FloatType>(ty) == nullptr
          && ir::as<ir::BoolType>(ty) == nullptr && ir::as<ir::CharType>(ty) == nullptr
          && ir::as<ir::PtrType>(ty) == nullptr && ir::as<ir::StrType>(ty) == nullptr)
        diags.emit(D::LANG_HOST_PARAMETER_TYPE, node->params[at]->type->span,
                   { { "type", ty->written() }, { "param", node->params[at]->name }, { "name", node->name } });
    }
    if (ret != ir::error_type() && ret != ir::void_type() && ir::as<ir::IntType>(ret) == nullptr
        && ir::as<ir::FloatType>(ret) == nullptr && ir::as<ir::BoolType>(ret) == nullptr
        && ir::as<ir::CharType>(ret) == nullptr)
      diags.emit(D::LANG_HOST_RESULT_TYPE, node->ret_type != nullptr ? node->ret_type->span : node->name_span,
                 { { "name", node->name }, { "type", ret->written() } });
  }


  void Checker::check_offered(const ast::FuncDef* node, const ir::FuncAttrs& attrs, ir::Linkage linkage)
  {
    // What calls it was compiled by something else, which calls the way the
    // system does; a function of this compiler's own convention would be
    // called in a way it does not expect.
    if (module.output == ir::Output::shared && linkage == ir::Linkage::visible && ! attrs.abi && node->body != nullptr)
      diags.emit(D::LANG_SHARED_VISIBLE_CONVENTION, node->name_span, { { "name", node->name } });
  }


  void Checker::hand_over_text(ir::Function* func)
  {
    if (! func->attrs.host || std::ranges::none_of(func->fty->params, [](const ir::Type* ty) {
          return ir::as<ir::StrType>(ty) != nullptr;
        }))
      return;
    // C has no `str`: what it calls text is the address of bytes ending in a
    // nul.  So the function the program calls is made a function of its own,
    // whose body makes that copy of each `str` in the heap, calls the host's
    // function -- a declaration taking an address in its place -- and gives the
    // copies back.  Made here, in the representation, the copy is made once for
    // every way the program is run: the interpreter's host call and the
    // compiled stub see only what C sees.
    auto bytes = strings::bytes_type(module);
    std::vector<const ir::Type*> handed;
    for (auto ty : func->fty->params)
      handed.push_back(ir::as<ir::StrType>(ty) != nullptr ? bytes : ty);
    auto made = std::make_unique<ir::Function>(func->name + ".host", ir::func_type(handed, func->fty->ret));
    made->attrs = func->attrs;
    made->linkage = ir::Linkage::internal;
    made->cconv = func->cconv;
    made->span = func->span;
    made->name_span = func->name_span;
    made->source_path = func->source_path;
    made->module = func->module;
    made->param_names = func->param_names;
    ir::Function* crossing = module.add_function(std::move(made));
    func->attrs.host.reset();
    func->attrs.abi.reset();
    func->cconv = std::string(ir::default_cconv);
    auto entry = func->add_block();
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    std::vector<ir::Value*> given;
    std::vector<std::pair<ir::Value*, ir::Value*>> copies;
    for (std::size_t at = 0; at < func->fty->params.size(); ++at) {
      auto ty = func->fty->params[at];
      auto param = entry->add_param(ty, func->param_names[at]);
      if (ir::as<ir::StrType>(ty) == nullptr) {
        given.push_back(param);
        continue;
      }
      auto text = builder.extract(param, 0, bytes);
      auto length = builder.extract(param, 1, ir::u64());
      auto copy = builder.call(strings::c_string_function(module), std::vector<ir::Value*>{ text, length }, bytes);
      copies.emplace_back(copy, builder.binary(ir::BinOp::wrap_add, length, builder.int_const(ir::u64(), 1)));
      given.push_back(copy);
    }
    auto answer = builder.call(crossing, given, func->fty->ret, func->span);
    for (auto [copy, size] : copies)
      builder.call(tables::heap_free_function(module), std::vector<ir::Value*>{ copy, size }, ir::void_type());
    builder.ret(func->fty->ret == ir::void_type() ? nullptr : answer);
  }


  void Checker::not_by_value(const ir::Type* ty, Span where)
  {
    if (auto record = ir::as<ir::ProductType>(ty); record != nullptr && record->abi)
      diags.emit(D::LANG_ABI_TYPE_BY_VALUE, where, { { "type", ty->written() } });
  }


  std::vector<const ir::Value*> Checker::defaults_of(const ast::FuncDef* node, std::span<const ir::Type* const> types)
  {
    std::vector<const ir::Value*> found;
    bool defaulted = false;
    for (std::size_t at = 0; at < std::min(node->params.size(), types.size()); ++at) {
      auto param = node->params[at];
      if (param->default_ == nullptr) {
        if (defaulted)
          diags.emit(D::LANG_PARAM_DEFAULT_ORDER, param->span, { { "name", param->name } });
        found.push_back(nullptr);
        continue;
      }
      defaulted = true;
      found.push_back(default_value(param, types[at]));
    }
    return found;
  }


  const ir::Value* Checker::default_value(const ast::Param* param, const ir::Type* ty)
  {
    const ast::Expr* written = param->default_;
    if (ty == ir::error_type())
      return nullptr;
    if (! ast::is<ast::IntLit>(written) && ! ast::is<ast::CharLit>(written) && ! ast::is<ast::BoolLit>(written)
        && ! ast::is<ast::FloatLit>(written) && ! ast::is<ast::Member>(written)) {
      diags.emit(D::LANG_PARAM_DEFAULT_NOT_SETTLED, written->span, { { "name", param->name } });
      return nullptr;
    }
    auto stood = syntax.make<ast::VarDef>(param->span);
    stood->name = param->name;
    stood->name_span = param->span;
    stood->type = param->type;
    stood->value = written;
    ir::Value* value = constant_value(stood, ty);
    return value != nullptr && value->is_const() ? value : nullptr;
  }


  std::pair<std::optional<std::vector<int>>, std::vector<std::optional<int>>>
  Checker::made_in_of(const ast::FuncDef* node, std::span<const ir::Type* const> params, const ir::Type* ret)
  {
    bool told = made_in_told.contains(id_of(node));
    made_in_told.insert(id_of(node));
    auto array = ir::as<ir::ArrayType>(ret);
    bool a_view = array != nullptr && ! array->fixed();

    auto arena = [&](std::string_view name, Span span, bool answer = false) -> std::optional<int> {
      std::optional<int> at;
      for (std::size_t i = 0; i < node->params.size(); ++i)
        if (node->params[i]->name == name) {
          at = int(i);
          break;
        }
      if (at && answer && a_view && ir::is<ir::ArrayType>(params[*at]))
        return at;
      auto ptr = at ? ir::as<ir::PtrType>(params[*at]) : nullptr;
      if (! at || ptr == nullptr || ptr->pointee != ir::arena_type()) {
        if (! told)
          diags.emit(D::LANG_MADE_IN_NOT_AN_ARENA, span, { { "name", name } });
        return std::nullopt;
      }
      return at;
    };
    auto pointless = [&](const ir::Type* ty, Span span) {
      if (! told && ty != ir::error_type() && ! owned::points_somewhere(ty))
        diags.emit(D::LANG_MADE_IN_POINTS_NOWHERE, span, { { "type", ty->written() } });
    };

    std::optional<std::vector<int>> made;
    if (! node->made_in.empty()) {
      pointless(ret, node->made_in[0].span);
      // `⎕heap` may be named too, and the image beside an arena or the heap.
      if (hands_out_of(node))
        made = std::vector<int>{ int(hands_at) };
      else {
        std::set<int> found;
        for (const auto& [name, span] : node->made_in) {
          std::optional<int> at = name == heap_name ? std::optional<int>(int(heap_at))
                                  : name == static_name ? std::optional<int>(int(static_at))
                                                        : arena(name, span, true);
          if (at)
            found.insert(*at);
        }
        made = std::vector<int>(found.begin(), found.end());
      }
    }
    std::vector<std::optional<int>> given;
    for (std::size_t at = 0; at < std::min(node->params.size(), params.size()); ++at) {
      auto param = node->params[at];
      if (! param->made_in) {
        given.push_back(std::nullopt);
        continue;
      }
      pointless(params[at], param->made_in->span);
      // `s: str in ⎕static`: what the caller hands over is in the image.
      given.push_back(param->made_in->name == static_name ? std::optional<int>(int(static_at))
                                                          : arena(param->made_in->name, param->made_in->span));
    }
    return { made, given };
  }


  void Checker::params_made_in(const ast::FuncDef* node, ir::Function* func)
  {
    Generator* const* generating_ = generator_bodies.get(id_of(func));
    std::vector<const ir::Type*> types = generating_ != nullptr ? (*generating_)->params : func->fty->params;

    auto local_of = [&](std::size_t at) { return find_local(node->params[at]->name); };
    for (std::size_t at = 0; at < node->params.size(); ++at) {
      Local* local = local_of(at);
      if (local == nullptr || is_arena(local) || ! owned::points_somewhere(types[at]))
        continue;
      std::optional<int> arena_at = at < func->param_made_in.size() ? func->param_made_in[at] : std::nullopt;
      if (arena_at == int(static_at)) {
        // In the image: made in nothing, lasting as long as the program.
        local->arenas.clear();
        continue;
      }
      Local* arena = arena_at ? local_of(std::size_t(*arena_at)) : nullptr;
      if (is_owning(types[at])) {
        if (arena != nullptr && is_arena(arena)) {
          local->holds_in = id_of(arena);
          local->arenas = KeySet{ id_of(arena) };
        } else {
          local->holds_in = heap_key;
          local->arenas = KeySet{ heap_key, container_key(local) };
        }
        continue;
      }
      if (arena != nullptr && is_arena(arena))
        local->arenas = KeySet{ id_of(arena) };
      else {
        local->arenas = KeySet{ id_of(local) };
        unknown_by_id[id_of(local)] = local;
      }
    }
    answer_kinds.clear();
    thin_to.reset();
    if (! func->made_in) {
      answer_made_in = std::tuple(KeySet{ heap_key }, std::string(heap_name));
      return;
    }
    const auto& made = *func->made_in;
    if (made == std::vector<int>{ int(hands_at) }) {
      // The arena is the body's own and does not exist yet.
      answer_made_in = std::tuple(KeySet{}, std::format("'{}'", func->hands_out.value_or("")));
      return;
    }
    if (made == std::vector<int>{ int(static_at) }) {
      // Nothing at all: what is in the image was made in nothing.
      answer_made_in = std::tuple(KeySet{}, std::string(static_name));
      return;
    }
    KeySet allowed;
    for (int at : made) {
      if (at == heap_at) {
        allowed.insert(heap_key);
        continue;
      }
      if (at == static_at)
        continue;
      Local* arena = local_of(std::size_t(at));
      if (arena != nullptr && is_arena(arena))
        allowed.insert(id_of(arena));
      else if (arena != nullptr && ir::is<ir::ArrayType>(types[std::size_t(at)])) {
        // Some of the elements handed over: what a view of them carries.
        auto frame = array_frame_of(arena);
        allowed.insert(frame.begin(), frame.end());
      }
    }
    std::vector<std::string> names;
    for (int at : made)
      names.push_back(at == heap_at     ? std::string(heap_name)
                      : at == static_at ? std::string(static_name)
                                        : std::string(node->params[std::size_t(at)]->name));
    answer_made_in = std::tuple(allowed, sema::alternatives(names));
    if (allowed == KeySet{ heap_key } && ! std::ranges::contains(made, int(static_at)) && thin_able(func->fty->ret))
      // `⎕heap` named: every answer is put in it, so the caller can add it to
      // what comes back.
      thin_to = heap_key;
  }


  std::optional<std::string> Checker::handing_callee(const ast::Expr* expr)
  {
    auto call = ast::as<ast::Call>(expr);
    auto callee = call != nullptr ? ast::as<ast::NameRef>(call->callee) : nullptr;
    if (callee == nullptr)
      return std::nullopt;
    if (Local* local = find_local(callee->name); local != nullptr) {
      auto held = ir::as<ir::FuncType>(held_by(local));
      return held != nullptr && held->hands_out ? std::optional<std::string>(callee->name) : std::nullopt;
    }
    const Held* found = top->get(std::string(callee->name));
    if (found == nullptr)
      return std::nullopt;
    if (auto func = found->get<ir::Function>(); func != nullptr && func->hands_out)
      return std::string(callee->name);
    if (auto generic = found->get<Generic>(); generic != nullptr && hands_out_of(generic->node))
      return std::string(callee->name);
    return std::nullopt;
  }


  void Checker::lower_receiving(ir::Builder& builder, const ast::VarDef* stmt)
  {
    auto [name, where] = *stmt->made_in;
    auto arena_def = syntax.make<ast::VarDef>(stmt->span);
    arena_def->name = name;
    arena_def->name_span = where;
    auto arena_type_ref = syntax.make<ast::TypeRef>(where);
    arena_type_ref->name = arena_name;
    arena_def->type = arena_type_ref;
    auto empty = syntax.make<ast::NameRef>(where);
    empty->name = empty_arena_name;
    arena_def->value = empty;
    arena_def->mutable_ = true;
    lower_local(builder, arena_def);
    Local* arena = find_local(name);
    if (arena == nullptr)
      return;
    // Read, so that one only received and given back is not "never read".
    arena->read = true;
    auto outer = std::pair(receiving, received);
    receiving = std::tuple(arena, stmt->value);
    received = nullptr;
    Local* got = nullptr;
    {
      Finally restore{ [&] { std::tie(receiving, received) = outer; } };
      auto plain = syntax.copy(*stmt);
      plain->made_in.reset();
      lower_local(builder, plain);
      made_in(stmt->name, stmt->value, stmt->span);
      got = received;
    }
    if (got == nullptr)
      diags.emit(D::LANG_BOUND_IN_NOT_HANDED_OUT, where, { { "name", name } });
  }


  ir::Value* Checker::arena_for(const ast::Call* expr, std::string_view name)
  {
    if (! receiving || std::get<1>(*receiving) != expr) {
      diags.emit(D::LANG_HANDED_OUT_UNBOUND, expr->span, { { "name", name } });
      return nullptr;
    }
    Local* local = std::get<0>(*receiving);
    receiving.reset();
    received = local;
    return local->value;
  }


  std::optional<KeySet> Checker::arenas_handed(const ast::Call* expr, const Dict<std::int64_t, const ast::Expr*>& written,
                                               std::int64_t at)
  {
    if (auto found = written.get(at); found != nullptr)
      return arenas_in(*found);
    if (auto force = arena_in_force.get(id_of(expr)); force != nullptr && force->contains(at))
      return out_of_arenas;
    return std::nullopt;
  }


  Dict<std::int64_t, const ast::Expr*> Checker::written_for(const ast::Call* expr, ir::Function* func)
  {
    Dict<std::int64_t, const ast::Expr*> found;
    std::int64_t place = 0;
    for (auto one : expr->args)
      if (auto named = ast::as<ast::Named>(one); named != nullptr) {
        auto it = std::ranges::find(func->param_names, named->name);
        if (it != func->param_names.end())
          found[it - func->param_names.begin()] = named->value;
      } else
        found[place++] = one;
    return found;
  }


  void Checker::where_made(const ast::Call* expr, ir::Function* func)
  {
    last_made_in.reset();
    auto written = written_for(expr, func);
    for (std::size_t at = 0; at < func->param_made_in.size(); ++at) {
      auto arena_at = func->param_made_in[at];
      const ast::Expr* const* given = written.get(std::int64_t(at));
      if (given == nullptr)
        continue;
      if (arena_at == int(static_at)) {
        NameSet stray;
        for (auto one : arenas_in(*given))
          stray.insert(reach_written(one));
        if (! stray.empty()) {
          // Nothing is copied into the image while the program runs.
          std::string found;
          for (const auto& one : stray) {
            if (! found.empty())
              found += ", ";
            found += one;
          }
          diags.emit(D::LANG_ARGUMENT_NOT_STATIC, (*given)->span,
                     { { "param", func->param_names[at] }, { "func", func->name }, { "found", found } });
        }
        continue;
      }
      const ir::Type* param_ty = func->fty->params[at];
      if (is_owning(param_ty)) {
        // Where its elements are kept matters only to a body that may
        // replace one, which only a `mut` parameter may.
        if (std::ranges::contains(func->writes_arrays, int(at)))
          container_handed(expr, func, written, std::int64_t(at),
                           arena_at ? std::optional<std::int64_t>(*arena_at) : std::nullopt);
        continue;
      }
      std::optional<KeySet> handed = arena_at ? arenas_handed(expr, written, *arena_at) : std::nullopt;
      if (! handed)
        continue;
      // ORDER: Python walks a set of ids here and stops at the first one it
      // reports; the ids sort the same way only by chance.
      for (auto one : arenas_in(*given)) {
        if (handed->contains(one))
          continue;
        Local* where = arena_by_id.get_or(one, nullptr);
        if (where == nullptr)
          where = unknown_by_id.get_or(one, nullptr);
        if (where == nullptr && one != heap_key)
          continue;
        diags.emit(D::LANG_MADE_IN_ARGUMENT_ELSEWHERE, (*given)->span,
                   { { "param", func->param_names[at] },
                     { "found", where != nullptr ? where->name : std::string(heap_name) },
                     { "func", func->name },
                     { "arena", func->param_names[std::size_t(*arena_at)] } });
        break;
      }
    }
    for (int at : func->writes_arrays) {
      const ast::Expr* const* given = written.get(at);
      if (given != nullptr && lent_to_an_iterator(*given, (*given)->span))
        continue;
      if (auto list = given != nullptr ? list_views.get(id_of(*given)) : nullptr; list != nullptr) [[unlikely]]
        diags.emit(D::LANG_LIST_VIEW_WRITTEN, (*given)->span, { { "found", (*list)->written() } });
      else if (given != nullptr && ! may_write(*given))
        diags.emit(D::LANG_ARRAY_HANDED_NOT_WRITABLE, (*given)->span,
                   { { "param", func->param_names[std::size_t(at)] }, { "func", func->name } });
      else if (given != nullptr && changed_here) {
        // Its elements are written by the callee: that is a change.
        const ast::Expr* root = *given;
        for (;;)
          if (auto m = ast::as<ast::Member>(root); m != nullptr)
            root = m->base;
          else if (auto e = ast::as<ast::Element>(root); e != nullptr)
            root = e->base;
          else if (auto i = ast::as<ast::Index>(root); i != nullptr)
            root = i->base;
          else
            break;
        if (auto name = ast::as<ast::NameRef>(root); name != nullptr)
          changed_here->emplace(name->name);
      }
    }
    if (! (owned::points(func->fty->ret) || holds_a_lambda(func->fty->ret)))
      return;
    if (func->attrs.external) {
      // What the outside answers is the outside's.
      made_from[id_of(expr)] = std::tuple(static_cast<const ast::Expr*>(expr), KeySet{});
      return;
    }
    if (! func->made_in) {
      last_made_in = KeySet{ heap_key };
      holds_from[id_of(expr)] = heap_key;
    } else {
      KeySet made;
      for (int at : *func->made_in)
        if (at == heap_at)
          made.insert(heap_key);
        else if (at == static_at) {
          // Perhaps in the image: lasting, and not an allocator's.
          if (func->made_in->size() > 1)
            made.insert(image_key);
        } else if (at == hands_at) {
          if (received != nullptr)
            made.insert(id_of(received));
        } else if (auto handed = arenas_handed(expr, written, at); handed)
          made.insert(handed->begin(), handed->end());
      last_made_in = made;
      if (auto found = owner_of(*last_made_in); found)
        holds_from[id_of(expr)] = *found;
    }
    made_from[id_of(expr)] = std::tuple(static_cast<const ast::Expr*>(expr), *last_made_in);
    // An answer whose allocator the callee fixed is in it, never text in the
    // image: where that is the heap, it is fresh.
    if ((func->answer_from || func->made_in == std::vector<int>{ int(heap_at) })
        && *last_made_in == KeySet{ heap_key })
      fresh_heap.insert(id_of(expr));
    callee_of[id_of(expr)] = func;
  }


  void Checker::container_handed(const ast::Call* expr, ir::Function* func,
                                 const Dict<std::int64_t, const ast::Expr*>& written, std::int64_t at,
                                 std::optional<std::int64_t> arena_at)
  {
    const ast::Expr* given = *written.get(at);
    auto name = ast::as<ast::NameRef>(given);
    Local* local = name != nullptr ? find_local(name->name) : nullptr;
    std::optional<std::int64_t> holds = local != nullptr ? local->holds_in : holder_of(given);
    std::optional<std::int64_t> wanted;
    if (! arena_at)
      wanted = heap_key;
    else if (auto arena = written.get(*arena_at); arena != nullptr)
      wanted = owner_of(arenas_in(*arena));
    if (! holds || holds != wanted) {
      std::string wanted_text = wanted == heap_key ? std::string(heap_name)
                                : arena_at ? std::format("what '{}' is given", func->param_names[std::size_t(*arena_at)])
                                           : std::string("?");
      diags.emit(D::LANG_CONTAINER_ALLOCATOR_MISMATCH, given->span,
                 { { "param", func->param_names[std::size_t(at)] },
                   { "func", func->name },
                   { "wanted", wanted_text },
                   { "found", holds ? owner_written(*holds) : std::string("an allocator nothing says") } });
      return;
    }
    if (local != nullptr && holds == heap_key) {
      elements_replaced(local->arenas, local->name, expr->span);
      // The call may have put the heap's in it.
      local->arenas.insert(heap_key);
    }
  }


  Dict<std::int64_t, ir::Borrowed> Checker::borrows_written(std::span<const ast::TypeExpr* const> params,
                                                            std::span<const ir::Type* const> types,
                                                            const ast::TypeExpr* ret_ref, const ir::Type* ret,
                                                            Span where, bool around)
  {
    Dict<std::int64_t, ir::Borrowed> found;
    auto ptr = ir::as<ir::PtrType>(ret);
    if (ptr == nullptr) {
      auto tuple = ir::as<ir::TupleType>(ret);
      auto tuple_ref = ast::as<ast::TupleTypeRef>(ret_ref);
      if (tuple != nullptr && tuple_ref != nullptr) {
        if (tuple_ref->members.size() != tuple->members.size())
          return found;
        auto within = ir::parts_within(ret);
        for (std::size_t at = 0; at < tuple->members.size(); ++at) {
          auto ref = tuple_ref->members[at];
          auto member = tuple->members[at];
          if (! holds_a_reference(member))
            continue;
          auto member_ptr = ir::as<ir::PtrType>(member);
          if (member_ptr == nullptr) {
            if (holds_a_short_reference(member))
              diags.emit(D::LANG_REF_ANSWERED, ref->span);
            continue;
          }
          if (auto borrowed = borrowed_by(params, types, ref, member_ptr, ref->span, around); borrowed)
            found[std::int64_t(within[at].first)] = *borrowed;
        }
        return found;
      }
      if (holds_a_short_reference(ret))
        // A reference inside something else that does not last as long as
        // the program, with nowhere a lifetime name could stand for it.
        diags.emit(D::LANG_REF_ANSWERED, where);
      return found;
    }
    if (auto borrowed = borrowed_by(params, types, ret_ref, ptr, where, around); borrowed)
      found[-1] = *borrowed;
    return found;
  }


  std::optional<ir::Borrowed> Checker::borrowed_by(std::span<const ast::TypeExpr* const> params,
                                                   std::span<const ir::Type* const> types, const ast::TypeExpr* ref,
                                                   const ir::PtrType* ret, Span where, bool around)
  {
    // The name on the reference itself and not on one it leads to.
    NameSet named;
    if (auto written = ast::as<ast::RefTypeRef>(ref); written != nullptr && written->lifetime)
      named.emplace(*written->lifetime);
    if (named.empty()) {
      if (! ret->lasting)
        diags.emit(D::LANG_BORROW_SAYS_NOTHING, where);
      return std::nullopt;
    }
    std::vector<std::pair<int, std::optional<std::vector<int>>>> carried_;
    for (std::size_t at = 0; at < std::min(params.size(), types.size()); ++at) {
      auto lifetimes = lifetimes_of(params[at]);
      if (std::ranges::any_of(named, [&](const std::string& one) { return lifetimes.contains(one); })) {
        auto leaves = leaves_named(params[at], types[at], named);
        carried_.emplace_back(int(at), leaves ? std::optional<std::vector<int>>(ints(*leaves)) : std::nullopt);
      }
    }
    NameSet own;
    for (const auto& [at, leaves] : carried_)
      own.merge(lifetimes_of(params[std::size_t(at)]));
    std::vector<std::pair<std::string, std::string>> outer;
    if (around)
      for (const auto& name : named) {
        if (own.contains(name))
          continue;
        for (const auto& [key_, names] : std::views::reverse(lifetime_scopes))
          if (names.contains(name)) {
            outer.emplace_back(key_, name);
            break;
          }
      }
    if (carried_.empty() && outer.empty()) {
      // A name nothing else carries says nothing.
      diags.emit(D::LANG_LIFETIME_NOT_DETERMINED, where,
                 { { "name", std::format("{}{}", lifetime_glyph, *named.begin()) } });
      return std::nullopt;
    }
    ir::Borrowed borrowed;
    for (const auto& [at, leaves] : carried_) {
      borrowed.params.push_back(at);
      borrowed.leaves.push_back(leaves);
    }
    borrowed.outer = outer;
    return borrowed;
  }


  std::tuple<std::string, NameSet> Checker::lifetime_scope(std::span<const ast::Param* const> params,
                                                           const ast::Node* node)
  {
    NameSet names;
    for (auto one : params)
      names.merge(lifetimes_of(one->type));
    return { lifetime_key(node), names };
  }


  std::string Checker::lifetime_key(const ast::Node* node)
  {
    // In characters, as pypl4g counts: the key is part of the name a lifetime
    // carries in a function type, and so of the symbols that type is in.
    return std::format("{}:{}", path.generic_string(), sources->character_offset(node->span.start));
  }


  Collected* Checker::collect_generic(const ast::FuncDef* node, std::string_view path,
                                      std::span<const BoundAttr> attrs, std::span<const std::string> written)
  {
    std::vector<std::string> answered;
    parameters_in(node->ret_type, answered);
    // A requirement's arrow settles what it names, which is how a type no
    // argument mentions is said.
    std::vector<std::string> settled(written.begin(), written.end());
    for (auto clause : node->clauses)
      if (clause->answers != nullptr)
        parameters_in(clause->answers, settled);
    for (const auto& one : answered)
      if (! std::ranges::contains(settled, one)) {
        diags.emit(D::LANG_GENERIC_NOT_DETERMINED, node->name_span, { { "name", one } });
        return nullptr;
      }
    if (auto kind = std::get<0>(function_attrs(attrs)).special; kind) {
      // The runtime's entry point, a constructor and a test are each one thing
      // the program has, and something written once per set of types is none.
      diags.emit(D::LANG_GENERIC_IS_SPECIAL, node->name_span,
                 { { "name", node->name }, { "attribute", enum_name(*kind) } });
      return nullptr;
    }
    check_clause_shapes(node->clauses);
    std::optional<std::vector<const ast::Param*>> handed;
    if (std::ranges::any_of(node->params, [](auto one) { return ast::is<ast::LiftedTypeRef>(one->type); })) {
      // A type handed as an argument settles a type parameter and is gone from
      // every function the definition becomes.
      handed = std::vector<const ast::Param*>(node->params.begin(), node->params.end());
      std::vector<const ast::Param*> kept;
      for (auto one : node->params)
        if (! ast::is<ast::LiftedTypeRef>(one->type))
          kept.push_back(one);
      auto copy = syntax.copy(*node);
      copy->params = syntax.list(kept);
      node = copy;
    }
    auto made = make<Generic>();
    made->node = node;
    made->path = std::string(path);
    made->attrs.assign(attrs.begin(), attrs.end());
    made->parameters.assign(written.begin(), written.end());
    made->exported = is_export(attrs);
    made->scope = top;
    made->siblings = bundles;
    made->written_params = handed;
    if (! node->held)
      (*top)[std::string(node->name)] = Held(made);
    else
      // Inside a type: reached through the type, as any function there is.
      members[{ std::string(*node->held), std::string(node->name) }] = Held(made);
    an_operator(node, Held(made));
    generics_here.push_back(made);
    return nullptr;
  }


  void Checker::register_special(ir::Function* func, const ast::FuncDef* node)
  {
    if (! func->attrs.special)
      return;
    switch (*func->attrs.special) {
    case ir::SpecialKind::startup:
      if (module.startup != nullptr) {
        auto previous = module.startup;
        diags.emit(D::LANG_FUNCDEF_SPECIAL_MULTIPLE_STARTUP, node->name_span)
          .note(D::LANG_FUNCDEF_SPECIAL_PREVIOUS_STARTUP, name_spans.get_or(previous->name, previous->span),
                { { "name", previous->name } });
        return;
      }
      check_startup_signature(func, node);
      module.startup = func;
      break;
    case ir::SpecialKind::build:
      if (module.build != nullptr) {
        auto previous = module.build;
        diags.emit(D::LANG_BUILD_MULTIPLE, node->name_span)
          .note(D::LANG_BUILD_PREVIOUS, name_spans.get_or(previous->name, previous->span), { { "name", previous->name } });
        return;
      }
      check_build_signature(func, node);
      module.build = func;
      break;
    case ir::SpecialKind::constructor:
      check_ctor_signature(func, node, "constructor");
      module.ctors.push_back(func);
      break;
    case ir::SpecialKind::destructor:
      check_ctor_signature(func, node, "destructor");
      module.dtors.push_back(func);
      break;
    case ir::SpecialKind::test_always:
    case ir::SpecialKind::test_build:
    case ir::SpecialKind::test_suite:
      check_test_signature(func, node);
      module.tests.push_back(func);
      break;
    }
  }


  void Checker::check_build_signature(ir::Function* func, const ast::FuncDef* node)
  {
    if (func->fty->ret != ir::void_type() || func->fty->params.size() != 1 || ! is_build_object(func->fty->params[0]))
      diags.emit(D::LANG_BUILD_BAD_SIGNATURE, node->name_span, { { "name", node->name } });
  }


  void Checker::check_startup_signature(ir::Function* func, const ast::FuncDef* node)
  {
    const ir::Type* expected = ir::builtin_type(startup_return_type_name);
    const auto& params = func->fty->params;
    if (func->fty->ret == ir::error_type() || std::ranges::contains(params, static_cast<const ir::Type*>(ir::error_type())))
      return;
    std::optional<std::string> problem;
    if (params.empty())
      problem = std::format("takes nothing, where it takes one '{}'", startup_argument_type_name);
    else if (params.size() > 1)
      problem = "takes more than one parameter";
    else if (! node->params.empty() && node->params[0]->mutable_)
      problem = std::format("takes a 'mut {}', where it takes the record as it is", startup_argument_type_name);
    else if (! is_startup_argument(params[0]))
      problem = std::format("takes a '{}'", params[0]->written());
    else if (! node->params.empty() && ast::is<ast::RefTypeRef>(node->params[0]->type))
      // The record and not a reference to it: there is one of it, so handing
      // it over is handing over the thing itself.
      problem = std::format("takes a reference to '{}' rather than the record itself", startup_argument_type_name);
    else if (! ir::same(func->fty->ret, expected))
      problem = std::format("returns '{}'", func->fty->ret->written());
    if (problem)
      diags.emit(D::LANG_FUNCDEF_SPECIAL_BAD_SIGNATURE, node->name_span,
                 { { "name", func->name }, { "type", startup_return_type_name }, { "problem", *problem } });
  }


  void Checker::check_ctor_signature(ir::Function* func, const ast::FuncDef* node, std::string_view kind)
  {
    const auto& params = func->fty->params;
    if (func->fty->ret == ir::error_type() || std::ranges::contains(params, static_cast<const ir::Type*>(ir::error_type())))
      return;
    std::optional<std::string> problem;
    if (! params.empty())
      problem = "it takes parameters";
    else if (func->fty->ret != ir::void_type())
      problem = std::format("it returns '{}'", func->fty->ret->written());
    if (problem)
      diags.emit(D::LANG_FUNCDEF_SPECIAL_BAD_CTOR_SIGNATURE, node->name_span,
                 { { "kind", kind }, { "name", func->name }, { "problem", *problem } });
  }


  void Checker::check_test_signature(ir::Function* func, const ast::FuncDef* node)
  {
    const auto& params = func->fty->params;
    if (func->fty->ret == ir::error_type() || std::ranges::contains(params, static_cast<const ir::Type*>(ir::error_type())))
      return;
    std::optional<std::string> problem;
    if (! params.empty())
      problem = "this one takes parameters";
    else if (func->fty->ret != ir::bool_type())
      problem = std::format("this one answers with '{}'", func->fty->ret->written());
    if (problem)
      diags.emit(D::LANG_FUNCDEF_SPECIAL_BAD_TEST_SIGNATURE, node->name_span,
                 { { "name", func->name }, { "problem", *problem } });
  }


  // -- attributes -------------------------------------------------------------

  std::vector<BoundAttr> Checker::bind_attributes(std::span<const ast::Attribute* const> nodes, AttrTarget target)
  {
    std::vector<BoundAttr> bound_;
    NameSet seen;
    Dict<std::string, std::string> groups;
    for (auto node : nodes) {
      const AttrSpec* spec = sema::lookup(node->name);
      if (spec == nullptr) {
        diags.emit(D::LANG_ATTR_UNKNOWN, node->name_span, { { "name", node->name } });
        continue;
      }
      if (seen.contains(node->name) && ! spec->repeatable) {
        diags.emit(D::LANG_ATTR_DUPLICATE, node->name_span, { { "name", node->name } });
        continue;
      }
      seen.emplace(node->name);
      if (! among(target, spec->targets)) {
        diags.emit(D::LANG_ATTR_NOT_APPLICABLE, node->name_span,
                   { { "name", node->name }, { "target", target_name(target) } });
        continue;
      }
      if (spec->group) {
        std::string group(*spec->group);
        if (auto other = groups.get(group); other != nullptr) {
          diags.emit(D::LANG_ATTR_CONFLICT, node->name_span, { { "name", node->name }, { "other", *other } });
          continue;
        }
        groups[group] = std::string(node->name);
      }
      if (auto values = bind_arguments(spec, node); values)
        bound_.push_back(BoundAttr{ spec, node, std::move(*values) });
    }
    return bound_;
  }


  std::optional<std::vector<std::pair<std::string_view, AttrValue>>>
  Checker::bind_arguments(const AttrSpec* spec, const ast::Attribute* node)
  {
    std::vector<std::pair<std::string_view, AttrValue>> values;
    for (const auto& p : spec->params)
      values.emplace_back(p.name, p.default_);
    std::set<std::string_view> supplied;
    std::size_t index = 0;
    bool seen_named = false;
    for (auto arg : node->args) {
      const AttrParam* param = nullptr;
      if (! arg->name) {
        if (seen_named) {
          diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, arg->span,
                     { { "name", spec->name }, { "problem", "has a positional argument after a named one" } });
          return std::nullopt;
        }
        if (index >= spec->params.size()) {
          diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, arg->span,
                     { { "name", spec->name },
                       { "problem", std::format("takes at most {} arguments; its signature is {}", spec->params.size(),
                                                spec->signature()) } });
          return std::nullopt;
        }
        param = &spec->params[index++];
      } else {
        seen_named = true;
        auto match = std::ranges::find(spec->params, *arg->name, &AttrParam::name);
        if (match == spec->params.end()) {
          diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, arg->span,
                     { { "name", spec->name },
                       { "problem", std::format("has no parameter named '{}'; its signature is {}", *arg->name,
                                                spec->signature()) } });
          return std::nullopt;
        }
        param = &*match;
      }
      if (supplied.contains(param->name)) {
        diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, arg->span,
                   { { "name", spec->name }, { "problem", std::format("got two values for '{}'", param->name) } });
        return std::nullopt;
      }
      supplied.insert(param->name);
      auto value = attr_value(spec, param->name, param->kind, param->choices, arg);
      if (! value)
        return std::nullopt;
      std::ranges::find(values, param->name, &std::pair<std::string_view, AttrValue>::first)->second = *value;
    }
    for (const auto& param : spec->params)
      if (param.required && ! supplied.contains(param.name)) {
        diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, node->span,
                   { { "name", spec->name },
                     { "problem", std::format("requires '{}'; its signature is {}", param.name, spec->signature()) } });
        return std::nullopt;
      }
    return values;
  }


  std::optional<AttrValue> Checker::attr_value(const AttrSpec* spec, std::string_view param_name, std::string_view kind,
                                               std::span<const std::string_view> choices, const ast::AttrArg* arg)
  {
    const ast::AttrValue* node = arg->value;
    AttrValue actual;
    if (auto i = ast::as<ast::AttrInt>(node); i != nullptr && kind == "integer")
      actual = i->value.value;
    else if (auto s = ast::as<ast::AttrString>(node); s != nullptr && kind == "string")
      actual = s->value;
    else if (auto b = ast::as<ast::AttrBool>(node); b != nullptr && kind == "boolean")
      actual = b->value;
    else if (auto n = ast::as<ast::AttrName>(node); n != nullptr && kind == "name")
      actual = n->value;
    else {
      diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, arg->span,
                 { { "name", spec->name }, { "problem", std::format("expects {} for '{}'", kind, param_name) } });
      return std::nullopt;
    }
    if (! choices.empty()) {
      auto text = std::get_if<std::string_view>(&actual);
      if (text == nullptr || ! std::ranges::contains(choices, *text)) {
        std::string listed;
        for (auto one : choices) {
          if (! listed.empty())
            listed += ", ";
          listed += one;
        }
        diags.emit(D::LANG_ATTR_BAD_ARGUMENTS, arg->span,
                   { { "name", spec->name },
                     { "problem", std::format("expects one of {} for '{}'", listed, param_name) } });
        return std::nullopt;
      }
    }
    return actual;
  }


  ir::Linkage Checker::linkage_of(std::span<const BoundAttr> bound_)
  {
    return std::ranges::any_of(bound_, [](const BoundAttr& attr) { return attr.name() == "visible"; })
             ? ir::Linkage::visible
             : ir::Linkage::internal;
  }


  bool Checker::is_export(std::span<const BoundAttr> bound_)
  {
    return std::ranges::any_of(bound_, [](const BoundAttr& attr) { return attr.name() == "export"; });
  }


  void Checker::known_convention(std::string_view name, const BoundAttr& attr)
  {
    if (every_target_knows().contains(name))
      return;
    auto known = target::conventions_of(module.triple);
    // Nothing where no backend answers to the triple: that is the driver's to
    // report.
    if (known == nullptr || std::ranges::contains(*known, name))
      return;
    std::vector<std::string> names(known->begin(), known->end());
    std::ranges::sort(names);
    diags.emit(D::LANG_ATTR_UNKNOWN_ABI, attr.node->span,
               { { "name", name }, { "target", module.triple }, { "known", and_then(names) } });
  }


  std::tuple<ir::FuncAttrs, ir::Linkage> Checker::function_attrs(std::span<const BoundAttr> bound_)
  {
    ir::FuncAttrs attrs;
    ir::Linkage linkage = linkage_of(bound_);
    for (const auto& attr : bound_) {
      auto name = attr.name();
      if (name == "startup")
        attrs.special = ir::SpecialKind::startup;
      else if (name == "build") {
        attrs.special = ir::SpecialKind::build;
        // Writing into what it was handed is the whole of what a build
        // function does.
        attrs.impure = true;
      } else if (name == "builtin")
        attrs.builtin = true;
      else if (name == "constructor") {
        attrs.special = ir::SpecialKind::constructor;
        attrs.priority = std::int64_t(attr.as_int("priority"));
      } else if (name == "destructor") {
        attrs.special = ir::SpecialKind::destructor;
        attrs.priority = std::int64_t(attr.as_int("priority"));
      } else if (name == "test")
        attrs.special = special_of_test_kind(attr.as_str("kind"));
      else if (name == "inline")
        attrs.inline_ = attr.as_str("mode") == "never" ? ir::InlineHint::never : ir::InlineHint::always;
      else if (name == "listable")
        attrs.listable = true;
      else if (name == "impure")
        attrs.impure = true;
      else if (name == "io") {
        // What it changes is a device: impure to everything below the checker.
        attrs.io = true;
        attrs.impure = true;
      } else if (name == "opaque")
        attrs.opaque = true;
      else if (name == "can_ignore")
        attrs.can_ignore = true;
      else if (name == "cdecl") {
        // The one this system uses, without the program having to know what
        // this architecture calls it.
        attrs.abi = std::string(ir::system_cconv);
        attrs.variadic = attr.as_bool("variadic");
      } else if (name == "abi") {
        auto written = attr.as_str("name");
        attrs.abi = std::string(written.empty() ? ir::system_cconv : written);
        known_convention(*attrs.abi, attr);
        attrs.variadic = attr.as_bool("variadic");
      } else if (name == "host") {
        // Defined by the program this one is loaded into, found there by name
        // at the first call, and called the way the system calls things --
        // which is the only way the host can be called -- and impure for the
        // reason an external function is.
        attrs.host = std::string(attr.as_str("symbol"));
        attrs.abi = std::string(ir::system_cconv);
        attrs.impure = true;
      } else if (name == "external") {
        // Defined somewhere else, called the way this system calls things, and
        // impure by being external: nothing here can see what it does.
        attrs.external = std::string(attr.as_str("symbol"));
        attrs.abi = std::string(ir::system_cconv);
        attrs.impure = true;
      } else if (name == "align")
        attrs.align = std::int64_t(attr.as_int("bytes"));
      else if (name == "section")
        attrs.section = std::string(attr.as_str("name"));
    }
    if (attrs.opaque)
      // Inlined, a call would be its body, which the optimizer may see
      // through: the box stays shut by being a call.
      attrs.inline_ = ir::InlineHint::never;
    return { attrs, linkage };
  }


  // -- types the program defines ----------------------------------------------

  void Checker::collect_type(const ast::Node* node, std::string_view path)
  {
    check_doc(node);
    auto type_def = ast::as<ast::TypeDef>(node);
    auto enum_def = ast::as<ast::EnumDef>(node);
    std::string_view name = type_def != nullptr ? type_def->name : enum_def->name;
    Span name_span = type_def != nullptr ? type_def->name_span : enum_def->name_span;
    auto written_attrs = type_def != nullptr ? type_def->attrs : enum_def->attrs;
    if (! declare(name, name_span, path))
      return;
    auto attrs = bind_attributes(written_attrs, AttrTarget::type);
    for (const auto& one : attrs)
      // What `@[abi]` says of a record is how it is laid out; a convention is
      // about how a function is called, so a name here says nothing.
      if (one.name() == "abi" && ! one.as_str("name").empty())
        diags.emit(D::LANG_ATTR_ABI_ON_A_TYPE_NAMES_ONE, one.node->span, { { "name", one.as_str("name") } });
    auto has = [&](std::string_view wanted) {
      return std::ranges::any_of(attrs, [&](const BoundAttr& one) { return one.name() == wanted; });
    };
    auto defined = make<NamedType>();
    defined->name = std::string(name);
    defined->node = node;
    defined->origin = std::string(path);
    defined->exported = is_export(attrs);
    defined->abi = has("abi");
    defined->unique = has("unique");
    defined->device = has("device");
    for (const auto& one : attrs)
      if (one.name() == "closed_by") {
        defined->closed_by = std::string(one.as_str("function"));
        break;
      }
    defined->scope = top;
    defined->siblings = bundles;
    if (type_def != nullptr && ! type_def->params.empty()) {
      NameSet seen;
      for (std::size_t at = 0; at < std::min(type_def->params.size(), type_def->param_spans.size()); ++at) {
        auto param = type_def->params[at];
        auto where = type_def->param_spans[at];
        if (! sema::is_generic(param)) {
          diags.emit(D::LANG_TYPE_PARAMETER_NOT_MARKED, where, { { "name", param }, { "type", type_def->name } });
          defined->refused = true;
        } else if (seen.contains(param)) {
          diags.emit(D::LANG_TYPE_PARAMETER_TWICE, where, { { "name", param }, { "type", type_def->name } });
          defined->refused = true;
        }
        seen.emplace(param);
      }
      check_clause_shapes(type_def->clauses);
    }
    (*top)[std::string(name)] = Held(defined);
    named_types.push_back(defined);
  }


  const ir::Type* Checker::resolved(NamedType* defined)
  {
    if (defined->ty != nullptr)
      return defined->ty;
    Span name_span = ast::is<ast::TypeDef>(defined->node) ? ast::as<ast::TypeDef>(defined->node)->name_span
                                                          : ast::as<ast::EnumDef>(defined->node)->name_span;
    if (defined->resolving) {
      if (behind_a_reference <= defined->opened_at || defined->shell == nullptr) {
        diags.emit(D::LANG_TYPEDEF_CONTAINS_ITSELF, name_span, { { "name", defined->name } });
        defined->ty = ir::error_type();
        return defined->ty;
      }
      // A reference, a list or a table stands between the type and itself,
      // which makes it finite: the object being built is handed back and
      // filled in later.  It has to have been opened since this resolving
      // began -- on the way round, and not merely around it.
      return defined->shell;
    }
    if (defined->takes_types())
      // Named without the types it takes, which the place that named it has
      // said; nothing is made of it.
      return ir::error_type();
    defined->resolving = true;
    defined->opened_at = behind_a_reference;
    defined->shell = const_cast<ir::Type*>(shell_for(defined));
    const ir::Type* made = nullptr;
    {
      auto kept_bound = bound;
      auto kept_top = top;
      auto kept_bundles = bundles;
      Finally restore{ [&] {
        defined->resolving = false;
        bound = kept_bound;
        top = kept_top;
        bundles = kept_bundles;
      } };
      if (defined->bound) {
        // An instance: its parameters stand for what it was given, and its
        // parts mean what they meant in the file that wrote them.
        bound = *defined->bound;
        if (defined->scope != nullptr && defined->siblings != nullptr) {
          top = defined->scope;
          bundles = defined->siblings;
        }
      }
      made = ast::is<ast::EnumDef>(defined->node) ? values_of(defined) : parts_of(defined);
    }
    defined->ty = filled_in(defined->shell, made);
    return defined->ty;
  }


  const ir::Type* Checker::values_of(NamedType* defined)
  {
    auto node = ast::as<ast::EnumDef>(defined->node);
    auto flag_attrs = bind_attributes(node->attrs, AttrTarget::type);
    bool flag = std::ranges::any_of(flag_attrs, [](const BoundAttr& attr) { return attr.name() == "flag"; });
    std::vector<std::string> names;
    std::vector<std::int64_t> numbers;
    NameSet where;
    Dict<std::int64_t, std::string> taken;
    bool spoiled = false;
    for (auto member : node->members) {
      if (where.contains(member->name)) {
        diags.emit(D::LANG_ENUMDEF_DUPLICATE_VALUE, member->name_span, { { "name", member->name } });
        spoiled = true;
        continue;
      }
      where.emplace(member->name);
      auto number = number_for(member, names, numbers, taken, flag);
      if (! number) {
        spoiled = true;
        continue;
      }
      names.emplace_back(member->name);
      numbers.push_back(*number);
    }
    if (spoiled || names.empty())
      return ir::error_type();
    auto holder = holder_for(node, numbers);
    if (holder == nullptr)
      return ir::error_type();
    return ir::enum_type(names, numbers, holder, flag, defined->name, defined->origin);
  }


  std::optional<std::int64_t> Checker::number_for(const ast::EnumMember* member, std::span<const std::string> names,
                                                  std::span<const std::int64_t> numbers,
                                                  Dict<std::int64_t, std::string>& taken, bool flag)
  {
    std::int64_t number = 0;
    if (auto name = ast::as<ast::NameRef>(member->value); name != nullptr) {
      auto found = std::ranges::find(names, name->name);
      if (found == names.end()) {
        diags.emit(D::LANG_ENUMDEF_UNKNOWN_EARLIER_VALUE, name->span, { { "name", name->name } });
        return std::nullopt;
      }
      return numbers[std::size_t(found - names.begin())];
    } else if (auto literal = ast::as<ast::IntLit>(member->value); literal != nullptr)
      number = std::int64_t(literal->value.value);
    else
      number = next_number(numbers, flag);
    if (auto held = taken.get(number); held != nullptr) {
      diags.emit(D::LANG_ENUMDEF_REPEATED_NUMBER, member->name_span,
                 { { "number", std::to_string(number) }, { "name", *held } });
      return std::nullopt;
    }
    taken[number] = std::string(member->name);
    return number;
  }


  std::int64_t Checker::next_number(std::span<const std::int64_t> numbers, bool flag)
  {
    if (! flag)
      return numbers.empty() ? 0 : numbers.back() + 1;
    std::int64_t highest = numbers.empty() ? 0 : std::ranges::max(numbers);
    std::int64_t bit = 1;
    while (bit <= highest)
      bit <<= 1;
    return bit;
  }


  const ir::IntType* Checker::holder_for(const ast::EnumDef* node, std::span<const std::int64_t> numbers)
  {
    if (node->holder == nullptr)
      return ir::holder_for(std::uint64_t(std::ranges::max(numbers) + 1));
    const ir::Type* named = resolve_type(node->holder);
    if (named == ir::error_type())
      return nullptr;
    auto integer = ir::as<ir::IntType>(named);
    if (integer == nullptr) {
      diags.emit(D::LANG_ENUMDEF_HOLDER_NOT_INTEGER, node->holder->span, { { "type", named->written() } });
      return nullptr;
    }
    for (auto n : numbers)
      if (! integer->holds(n)) {
        diags.emit(D::LANG_ENUMDEF_HOLDER_TOO_NARROW, node->holder->span,
                   { { "type", named->written() }, { "value", std::to_string(n) } });
        return nullptr;
      }
    return integer;
  }


  const ir::Type* Checker::parts_of(NamedType* defined)
  {
    auto node = ast::as<ast::TypeDef>(defined->node);
    bool sum = node->kind == ast::TypeKind::sum;
    std::vector<std::pair<std::string, const ir::Type*>> parts;
    NameSet seen;
    bool spoiled = false;
    for (auto field : node->fields) {
      if (seen.contains(field->name)) {
        diags.emit(D::LANG_TYPEDEF_DUPLICATE_PART, field->name_span, { { "name", field->name } });
        spoiled = true;
        continue;
      }
      if (field->mutable_ && sum) {
        diags.emit(D::LANG_TYPEDEF_MUT_VARIANT, field->span, { { "name", field->name } });
        spoiled = true;
      }
      seen.emplace(field->name);
      auto named = lifetimes_of(field->type);
      if (! named.empty()) {
        // A lifetime name is about the rest of a signature, and a type's
        // definition is none.
        diags.emit(D::LANG_LIFETIME_ON_A_FIELD, field->type->span,
                   { { "name", std::format("{}{}", lifetime_glyph, *named.begin()) } });
        spoiled = true;
        continue;
      }
      const ir::Type* ty = resolve_type(field->type);
      if (auto short_ = short_lived(ty); short_ != nullptr) {
        diags.emit(D::LANG_ITERATOR_DOES_NOT_LEAVE, field->type->span,
                   { { "found", short_->written() }, { "what", "a field" } });
        ty = ir::error_type();
      }
      if (ty == ir::void_type() && ! sum) {
        // A field that carries nothing leaves the product meaning what it
        // would have meant without it.
        diags.emit(D::LANG_TYPEDEF_FIELD_OF_NOTHING, field->name_span, { { "name", field->name } });
        spoiled = true;
        continue;
      }
      if (ty == ir::error_type()) {
        spoiled = true;
        continue;
      }
      if (sum) {
        // An arm of a `match` names the type of the alternative it takes, so
        // two alternatives of one type would be two no arm could choose
        // between.
        if (std::ranges::any_of(parts, [&](const auto& part) { return part.second == ty; })) {
          diags.emit(D::LANG_TYPEDEF_REPEATED_TYPE, field->name_span, { { "type", ty->written() } });
          spoiled = true;
          continue;
        }
      }
      parts.emplace_back(std::string(field->name), ty);
    }
    if (parts.empty() || spoiled)
      // Every part was wrong, and each was reported where it was written.
      return ir::error_type();
    if (sum) {
      auto made = ir::new_sum();
      made->variants = std::move(parts);
      made->name = defined->name;
      made->origin = defined->origin;
      return made;
    }
    auto made = ir::new_product();
    made->fields = std::move(parts);
    made->name = defined->name;
    made->origin = defined->origin;
    made->abi = defined->abi;
    made->unique = defined->unique;
    made->device = defined->device;
    made->closed_by = defined->closed_by;
    auto fields = mutable_fields(node);
    made->mutable_fields.assign(fields.begin(), fields.end());
    return made;
  }


  const ir::Type* Checker::defined_type(const ast::TypeRef* ref)
  {
    if (ref->module) {
      LoadedModule* held = module_called(*ref->module);
      if (held == nullptr) {
        diags.emit(D::LANG_IMPORT_NOT_A_MODULE, ref->span, { { "name", *ref->module } });
        return ir::error_type();
      }
      const Held* found = held->exports.get(std::string(ref->name));
      if (found == nullptr || found->empty()) {
        diags.emit(D::LANG_IMPORT_NOT_EXPORTED, ref->span, { { "name", ref->name }, { "module", *ref->module } });
        return ir::error_type();
      }
      if (auto named = found->get<NamedType>(); named != nullptr) {
        if (named->takes_types() || ! ref->args.empty())
          return named_with(named, ref);
        // Already worked out: the module was checked whole before this file
        // was allowed to name anything in it.
        return named->ty != nullptr ? named->ty : ir::error_type();
      }
      diags.emit(D::LANG_TYPE_UNKNOWN, ref->span, { { "name", ref->name } });
      return ir::error_type();
    }
    const Held* held = top->get(std::string(ref->name));
    NamedType* named = held != nullptr ? held->get<NamedType>() : nullptr;
    if (named != nullptr && (named->takes_types() || ! ref->args.empty()))
      return named_with(named, ref);
    return named != nullptr ? resolved(named) : nullptr;
  }


  const ir::Type* Checker::named_with(NamedType* defined, const ast::TypeRef* ref)
  {
    auto node = ast::as<ast::TypeDef>(defined->node);
    std::span<const ast::Text> params = node != nullptr ? std::span<const ast::Text>(node->params)
                                                        : std::span<const ast::Text>();
    if (params.empty()) {
      diags.emit(D::LANG_TYPE_TAKES_NO_ARGUMENTS, ref->span, { { "type", defined->name } });
      return ir::error_type();
    }
    if (ref->args.empty()) {
      diags.emit(D::LANG_TYPE_NEEDS_ARGUMENTS, ref->span, { { "type", defined->name }, { "count", params.size() } });
      return ir::error_type();
    }
    if (ref->args.size() != params.size()) {
      diags.emit(D::LANG_TYPE_WRONG_ARGUMENT_COUNT, ref->span,
                 { { "type", defined->name }, { "expected", params.size() }, { "found", ref->args.size() } });
      return ir::error_type();
    }
    std::vector<const ir::Type*> given;
    for (auto one : ref->args)
      given.push_back(resolve_type(one));
    if (std::ranges::contains(given, static_cast<const ir::Type*>(ir::error_type())))
      return ir::error_type();
    return instance_type(defined, given, ref->span);
  }


  const ir::Type* Checker::instance_type(NamedType* template_, std::span<const ir::Type* const> given, Span span)
  {
    auto node = ast::as<ast::TypeDef>(template_->node);
    if (template_->refused)
      return ir::error_type();
    Types key_{ { given.begin(), given.end() } };
    if (auto found = template_->instances.get(key_); found != nullptr)
      return resolved(*found);
    if (template_->unmet.contains(key_))
      // Its requirements were not met for these, which was said once.
      return ir::error_type();
    Dict<std::string, const ir::Type*> bound_;
    for (std::size_t at = 0; at < std::min(node->params.size(), given.size()); ++at)
      bound_[std::string(node->params[at])] = given[at];
    bool met_ = false;
    {
      NamesKept kept(*this, template_->scope, template_->siblings);
      std::string types;
      for (const auto& [one, ty] : bound_) {
        if (! types.empty())
          types += ", ";
        types += std::format("{} = {}", one, ty->written());
      }
      auto mark = diags.because(D::LANG_CLAUSE_ASKED_HERE, span, { { "function", template_->name }, { "types", types } });
      Finally no_longer{ [&] { diags.and_no_longer(mark); } };
      auto asked = bound_;
      met_ = check_clauses(node->clauses, asked, template_->name, span);
    }
    if (! met_) {
      template_->unmet.insert(key_);
      return ir::error_type();
    }
    std::string written;
    for (auto one : given) {
      if (! written.empty())
        written += ", ";
      written += one->written();
    }
    auto made = make<NamedType>();
    made->name = std::format("{}({})", template_->name, written);
    made->node = node;
    made->origin = template_->origin;
    made->exported = template_->exported;
    made->abi = template_->abi;
    made->unique = template_->unique;
    made->device = template_->device;
    made->closed_by = template_->closed_by;
    made->bound = bound_;
    made->template_ = template_;
    made->scope = template_->scope;
    made->siblings = template_->siblings;
    template_->instances[key_] = made;
    return resolved(made);
  }


  void Checker::check_generic_type(NamedType* template_)
  {
    auto node = ast::as<ast::TypeDef>(template_->node);
    if (template_->refused)
      return;
    unsigned told = diags.error_count;
    auto probe = make<NamedType>();
    probe->name = template_->name;
    probe->node = node;
    probe->origin = template_->origin;
    Dict<std::string, const ir::Type*> opaque;
    for (auto name : node->params)
      opaque[std::string(name)] = ir::new_opaque(std::string(name));
    probe->bound = opaque;
    probe->template_ = template_;
    probe->scope = template_->scope;
    probe->siblings = template_->siblings;
    bool quiet = diags.errors_only;
    diags.errors_only = true;
    {
      Finally restore{ [&] { diags.errors_only = quiet; } };
      resolved(probe);
    }
    if (diags.error_count > told)
      template_->refused = true;
  }


  const ir::Type* Checker::resolve_type(const ast::TypeExpr* ref)
  {
    auto array_ref = ast::as<ast::ArrayTypeRef>(ref);
    auto list_ref = ast::as<ast::ListTypeRef>(ref);
    if ((array_ref != nullptr && array_ref->result) || (list_ref != nullptr && list_ref->result)) {
      const ast::TypeExpr* plain = nullptr;
      ast::MaybeText error;
      if (array_ref != nullptr) {
        auto copy = syntax.copy(*array_ref);
        copy->result = false;
        copy->error.reset();
        error = array_ref->error;
        plain = copy;
      } else {
        auto copy = syntax.copy(*list_ref);
        copy->result = false;
        copy->error.reset();
        error = list_ref->error;
        plain = copy;
      }
      const ir::Type* array = resolve_type(plain);
      if (array == ir::error_type())
        return ir::error_type();
      if (! error)
        return ir::result_type(array);
      auto named = syntax.make<ast::TypeRef>(ref->span);
      named->name = *error;
      const ir::Type* carried_ = resolve_type(named);
      if (carried_ == ir::error_type())
        return ir::error_type();
      return ir::result_type(array, carried_);
    }
    if (auto collection = ast::as<ast::CollectionTypeRef>(ref); collection != nullptr)
      return collection_type(collection);
    if (array_ref != nullptr)
      return array_type(array_ref);
    if (list_ref != nullptr) {
      // A list is a few words whatever it holds, so a definition may reach
      // itself through one as it may through a reference.
      const ir::Type* element = behind_an_indirection(list_ref->element);
      if (element == ir::error_type())
        return ir::error_type();
      if (element == ir::void_type()) {
        diags.emit(D::LANG_ARRAY_ELEMENT_IS_NOTHING, ref->span);
        return ir::error_type();
      }
      return ir::list_type(element);
    }
    if (auto tuple = ast::as<ast::TupleTypeRef>(ref); tuple != nullptr) {
      std::vector<const ir::Type*> members_;
      for (auto m : tuple->members)
        members_.push_back(resolve_type(m));
      if (std::ranges::contains(members_, static_cast<const ir::Type*>(ir::error_type())))
        return ir::error_type();
      return ir::tuple_type(members_);
    }
    if (auto reference = ast::as<ast::RefTypeRef>(ref); reference != nullptr)
      return reference_type(reference);
    if (auto united = ast::as<ast::UnitTypeRef>(ref); united != nullptr)
      return united_type(united);
    if (auto iter = ast::as<ast::IterTypeRef>(ref); iter != nullptr) {
      if (iter->element == nullptr)
        return ir::iter_type(nullptr);
      const ir::Type* inner = resolve_type(iter->element);
      if (inner == ir::error_type())
        return ir::error_type();
      return ir::iter_type(inner);
    }
    if (ast::is<ast::LiftedTypeRef>(ref)) {
      // Only a generic function's parameter is handed a type, and that one is
      // taken out of the definition before anything resolves it.
      diags.emit(D::LANG_TYPE_ARGUMENT_NOT_GENERIC, ref->span);
      return ir::error_type();
    }
    if (auto running = ast::as<ast::RunningTypeRef>(ref); running != nullptr) {
      const ir::Type* element = resolve_type(running->element);
      const ir::Type* given = running->given != nullptr ? resolve_type(running->given) : nullptr;
      if (element == ir::error_type() || given == ir::error_type())
        return ir::error_type();
      if (running->kept_in && running->kept_in->name != heap_name) {
        diags.emit(D::LANG_KEPT_IN_NOT_HEAP, running->kept_in->span, { { "name", running->kept_in->name } });
        return ir::error_type();
      }
      return ir::running_type(element, given, running->kept_in.has_value());
    }
    if (auto func = ast::as<ast::FuncTypeRef>(ref); func != nullptr) {
      std::vector<const ir::Type*> params;
      for (auto one : func->params)
        params.push_back(resolve_type(one));
      const ir::Type* ret = return_type(func->ret, false);
      if (ret == ir::error_type() || std::ranges::contains(params, static_cast<const ir::Type*>(ir::error_type())))
        return ir::error_type();
      if (func->kept_in && func->kept_in->name != heap_name) {
        // A type says only that what was brought in lasts, and `⎕heap` is the
        // one place that says so.
        diags.emit(D::LANG_KEPT_IN_NOT_HEAP, func->kept_in->span, { { "name", func->kept_in->name } });
        return ir::error_type();
      }
      auto said_ = promised(func->attrs);
      // A value of it is called with nothing but the type in hand, so the
      // type says how long a reference it answers with lives.
      auto borrows_ = borrows_written(func->params, params, func->ret, ret,
                                      func->ret != nullptr ? func->ret->span : ref->span, true);
      return function_type(params, ret, said_.contains("listable"), func->made_in.has_value(),
                                   func->kept_in.has_value(), said_.contains("impure"), said_.contains("can_ignore"),
                                   sorted_borrows(borrows_));
    }
    return named_type(ast::as<ast::TypeRef>(ref));
  }


  // -- units ------------------------------------------------------------------

  const ir::Type* Checker::united_type(const ast::UnitTypeRef* ref)
  {
    const ir::Type* base = resolve_type(ref->base);
    if (base == ir::error_type())
      return ir::error_type();
    const ir::Unit* unit = unit_written(ref->unit);
    if (unit == nullptr)
      return ir::error_type();
    if (! ir::is<ir::IntType>(base) && ! ir::is<ir::FloatType>(base)) {
      diags.emit(D::LANG_UNIT_NOT_A_NUMBER, ref->span, { { "found", base->written() } });
      return ir::error_type();
    }
    return carrying(base, unit);
  }


  const ir::Unit* Checker::unit_written(const ast::UnitRef* ref)
  {
    const ir::Unit* made = ir::Unit::none();
    for (auto factor : ref->factors) {
      const ir::Unit* found = unit_named(factor->name, factor->span);
      if (found == nullptr)
        return nullptr;
      auto exponent = std::int64_t(factor->exponent.value);
      auto raised = found->raised(exponent < 0 ? -exponent : exponent);
      made = exponent > 0 ? made->times(*raised) : made->over(*raised);
    }
    return made;
  }


  const ir::Unit* Checker::unit_named(std::string_view name, Span span)
  {
    for (const auto& scope : std::views::reverse(unit_scopes))
      if (auto found = scope.get(std::string(name)); found != nullptr && *found != nullptr)
        return *found;
    if (auto builtin = builtin_units().get(std::string(name)); builtin != nullptr)
      return *builtin;
    diags.emit(D::LANG_UNIT_UNKNOWN, span, { { "name", name } });
    return nullptr;
  }


  void Checker::define_unit(const ast::UnitDef* node)
  {
    if (node->stands) {
      const ir::Unit* what = unit_written(node->stands->what);
      const ir::Unit* where = unit_written(node->stands->where);
      if (what != nullptr && where != nullptr)
        stands.emplace_back(what, where);
      return;
    }
    auto& scope = unit_scopes.back();
    std::string name(node->name);
    if (scope.contains(name) || (unit_scopes.size() == 1 && builtin_units().contains(name))) {
      diags.emit(D::LANG_UNIT_ALREADY_DEFINED, node->name_span, { { "name", node->name } });
      return;
    }
    if (node->measured == nullptr) {
      // A unit measured in nothing but itself, which is what every base unit
      // is and what a program counting apples wants.
      scope[name] = ir::Unit::make({ { name, 1 } }, ir::Rational{});
      return;
    }
    const ir::Unit* measured = unit_written(node->measured);
    if (measured == nullptr)
      return;
    auto [over, under] = *node->scale;
    if (under.value == 0) {
      // Nothing is so many of another divided by none of it.
      diags.emit(D::LANG_TYPE_DIVISION_BY_ZERO, node->span);
      return;
    }
    unit_scopes.back()[name] = ir::Unit::make(measured->powers, measured->scale * ir::Rational::of(over.value, under.value));
  }


  bool Checker::stands_for(const ir::Unit* found, const ir::Unit* wanted)
  {
    if (found == wanted)
      return true;
    std::set<const ir::Unit*> reached{ found };
    for (;;) {
      std::set<const ir::Unit*> more;
      for (const auto& [f, w] : stands)
        if (reached.contains(f) && ! reached.contains(w))
          more.insert(w);
      if (more.empty())
        return false;
      if (more.contains(wanted))
        return true;
      reached.insert(more.begin(), more.end());
    }
  }


  const ir::Type* Checker::reference_type(const ast::RefTypeRef* ref)
  {
    // What a reference occupies does not depend on what it names, so a
    // definition may reach itself through one.
    ++behind_a_reference;
    const ir::Type* pointee = nullptr;
    {
      Finally restore{ [&] { --behind_a_reference; } };
      pointee = resolve_type(ref->pointee);
    }
    if (pointee == ir::error_type())
      return ir::error_type();
    if (! can_be_referred_to(pointee)) {
      diags.emit(D::LANG_REF_TYPE_NOT_ALLOWED, ref->span, { { "found", pointee->written() } });
      return ir::error_type();
    }
    return ir::ptr_type(pointee, ref->mutable_, ref->lasting);
  }


  const ir::Type* Checker::behind_an_indirection(const ast::TypeExpr* ref)
  {
    ++behind_a_reference;
    Finally restore{ [&] { --behind_a_reference; } };
    return resolve_type(ref);
  }


  const ir::Type* Checker::array_type(const ast::ArrayTypeRef* ref)
  {
    if (ref->element == nullptr) {
      // Only a definition has a value to read the element off.
      if (defining_globally)
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, ref->span,
                   { { "feature", "an array of whatever it is given, at the top level" } });
      return ir::error_type();
    }
    const ir::Type* element = resolve_type(ref->element);
    if (element == ir::error_type())
      return ir::error_type();
    if (element == ir::void_type()) {
      diags.emit(D::LANG_ARRAY_ELEMENT_IS_NOTHING, ref->span);
      return ir::error_type();
    }
    std::vector<std::optional<std::uint64_t>> shape;
    for (auto written : ref->shape) {
      if (written == nullptr) {
        shape.push_back(std::nullopt);
        continue;
      }
      KnownNumber along = constant_number(written);
      if (along.refused)
        return ir::error_type();
      if (! along.value || *along.value < 0) {
        diags.emit(D::LANG_ARRAY_LENGTH_NOT_A_NUMBER, written->span);
        return ir::error_type();
      }
      shape.push_back(std::uint64_t(*along.value));
    }
    // A dimension the type states and one it does not may stand side by side.
    return ir::array_type(element, shape);
  }


  const ir::Type* Checker::collection_type(const ast::CollectionTypeRef* ref)
  {
    const ir::Type* element = resolve_type(ref->element);
    // A type parameter stands as a key where a generic function is written:
    // what it is, is settled where a call says.
    if (element != ir::error_type() && ! can_be_a_key(element) && ! ir::is<ir::OpaqueType>(element)) {
      diags.emit(D::LANG_COLLECTION_KEY_NOT_HASHABLE, ref->element->span, { { "found", element->written() } });
      element = ir::error_type();
    }
    if (ref->value == nullptr)
      return element != ir::error_type() ? static_cast<const ir::Type*>(ir::set_type(element, ref->mutable_))
                                         : ir::error_type();
    // A table holds its entries out of line, so its value is behind an
    // indirection as a list's element is; its key need not be.
    const ir::Type* value = behind_an_indirection(ref->value);
    if (element == ir::error_type() || value == ir::error_type())
      return ir::error_type();
    if (value == ir::void_type()) {
      diags.emit(D::LANG_COLLECTION_VALUE_IS_NOTHING, ref->value->span);
      return ir::error_type();
    }
    return ir::dict_type(element, value, ref->mutable_);
  }


  const ir::Type* Checker::named_type(const ast::TypeRef* ref)
  {
    const ir::Type* found = ! ref->module ? ir::builtin_type(ref->name) : nullptr;
    if (found == nullptr && ! ref->module && sema::is_generic(ref->name))
      // A type a call settled; outside an instantiation there is nothing for
      // it to be.
      found = bound.get_or(std::string(ref->name), nullptr);
    if (found == nullptr) {
      found = defined_type(ref);
      // Where the program defined it, there is somewhere to send a reader.
      NamedType* named = nullptr;
      if (notes != nullptr) {
        const Held* held = top->get(std::string(ref->name));
        if (ref->module) {
          LoadedModule* loaded = module_called(*ref->module);
          held = loaded != nullptr ? loaded->exports.get(std::string(ref->name)) : nullptr;
        }
        named = held != nullptr ? held->get<NamedType>() : nullptr;
      }
      if (found != nullptr && named != nullptr) {
        Span name_span;
        ast::MaybeText doc;
        if (auto t = ast::as<ast::TypeDef>(named->node); t != nullptr) {
          name_span = t->name_span;
          doc = t->doc;
        } else if (auto e = ast::as<ast::EnumDef>(named->node); e != nullptr) {
          name_span = e->name_span;
          doc = e->doc;
        }
        note(ref->span, note_kind::type, ref->name, found->written(), name_span,
             doc ? std::optional<std::string>(std::string(*doc)) : std::nullopt);
      }
    } else if (notes != nullptr)
      // A built-in: there is nowhere to send a reader, but what it is, is
      // still worth saying.
      note(ref->span, note_kind::type, ref->name, found->written());
    if (found == nullptr) {
      diags.emit(D::LANG_TYPE_UNKNOWN, ref->span, { { "name", ref->name } });
      return ir::error_type();
    }
    if (found == ir::error_type())
      return ir::error_type();
    if (ref->unit != nullptr) {
      const ir::Unit* written = unit_written(ref->unit);
      if (written == nullptr)
        return ir::error_type();
      if (! ir::is<ir::IntType>(found) && ! ir::is<ir::FloatType>(found)) {
        diags.emit(D::LANG_UNIT_NOT_A_NUMBER, ref->span, { { "found", found->written() } });
        return ir::error_type();
      }
      found = carrying(found, written);
    }
    if (! ref->result)
      return found;
    if (found == ir::void_type()) {
      diags.emit(D::LANG_TYPE_RESULT_OF_NOTHING, ref->span);
      return ir::error_type();
    }
    if (! ref->error)
      return ir::result_type(found);
    // What the error carries is a type like any other, named the way the
    // answer is: through the answer's module, except a type the language has.
    auto other = syntax.copy(*ref);
    other->name = *ref->error;
    other->result = false;
    other->error.reset();
    other->unit = nullptr;
    if (ir::builtin_type(*ref->error) != nullptr)
      other->module.reset();
    const ir::Type* carried_ = resolve_type(other);
    if (carried_ == ir::error_type())
      return ir::error_type();
    if (carried_ == ir::void_type()) {
      diags.emit(D::LANG_TYPE_RESULT_OF_NOTHING, ref->span);
      return ir::error_type();
    }
    return ir::result_type(found, carried_);
  }


  // -- what a macro is made of ------------------------------------------------

  ir::Value* Checker::lower_quote(ir::Builder& builder, const ast::Quote* expr, const ir::Type* expected)
  {
    if (! for_macros) {
      diags.emit(D::LANG_MACRO_QUOTE_OUTSIDE, expr->span);
      return undef_error(module);
    }
    auto at = quoted.size();
    quoted.push_back(expr);
    ir::Function* quote = syntax_builtin(quote_name, std::vector<const ir::Type*>{ ir::u64() });
    ir::Value* found = builder.call(quote, std::vector<ir::Value*>{ builder.int_const(ir::u64(), __int128(at)) },
                                    ir::syntax_type(), expr->span);
    std::vector<const ast::Hole*> holes;
    holes_of(expr, holes);
    for (std::size_t number = 0; number < holes.size(); ++number) {
      auto hole = holes[number];
      if (hole->value == nullptr) {
        diags.emit(D::LANG_MACRO_HOLE_OUTSIDE, hole->span);
        return undef_error(module);
      }
      ir::Value* given = lower_expr(builder, hole->value, nullptr);
      const ir::Type* ty = value_type_of(given);
      if (ty == ir::error_type())
        return undef_error(module);
      std::string_view name;
      const ir::Type* wanted = nullptr;
      if (ty == ir::syntax_type()) {
        name = fill_name;
        wanted = ir::syntax_type();
      } else if (auto integer = ir::as<ir::IntType>(ty); integer != nullptr) {
        // A number goes in as what a program would have written to mean it.
        name = fill_number_name;
        wanted = ir::i64();
        given = as_a_number(builder, given, integer, hole->span);
      } else if (ty == ir::str_type()) {
        // And text as the literal a program would have written.
        name = fill_text_name;
        wanted = ir::str_type();
      } else {
        diags.emit(D::LANG_MACRO_HOLE_NOT_A_PIECE, hole->span, { { "found", ty->written() } });
        return undef_error(module);
      }
      ir::Function* fill = syntax_builtin(name, std::vector<const ir::Type*>{ ir::syntax_type(), ir::u64(), wanted });
      found = builder.call(fill, std::vector<ir::Value*>{ found, builder.int_const(ir::u64(), __int128(number)), given },
                           ir::syntax_type(), expr->span);
    }
    if (! accepts(expected, ir::syntax_type())) {
      report_mismatch(expr->span, ir::syntax_type(), expected);
      return undef_error(module);
    }
    return found;
  }


  ir::Value* Checker::as_a_number(ir::Builder& builder, ir::Value* value, const ir::IntType* ty, Span span)
  {
    if (ty == ir::i64())
      return value;
    return builder.cast(ty->is_signed ? ir::CastKind::sext : ir::CastKind::zext, value, ir::i64(), span);
  }


  void Checker::not_a_piece(const ir::Type* ty, Span span, std::string_view name)
  {
    if (for_macros || ! holds_a_piece(ty))
      return;
    diags.emit(D::LANG_SYNTAX_AT_RUN_TIME, span, { { "name", name } });
  }


  ir::Value* Checker::lower_asked(ir::Builder& builder, const ast::Call* expr, std::string_view name,
                                  const ir::Type* expected)
  {
    if (! for_macros) {
      diags.emit(D::LANG_ASK_OUTSIDE_A_MACRO, expr->span, { { "name", name } });
      return undef_error(module);
    }
    const auto& wanted = *asks_about_a_piece().get(std::string(name));
    if (expr->args.size() != wanted.size()) {
      diags.emit(D::LANG_ASK_ARGUMENT_COUNT, expr->span,
                 { { "name", name }, { "expected", std::to_string(wanted.size()) },
                   { "found", std::to_string(expr->args.size()) } });
      return undef_error(module);
    }
    std::vector<ir::Value*> given;
    for (std::size_t at = 0; at < wanted.size(); ++at) {
      auto one = expr->args[at];
      ir::Value* value = lower_into(builder, one, wanted[at], one->span);
      if (value_type_of(value) == ir::error_type())
        return undef_error(module);
      given.push_back(value);
    }
    const ir::Type* answer = *syntax_answers().get(std::string(name));
    ir::Function* asked = syntax_builtin(name, wanted);
    ir::Value* found = builder.call(asked, given, answer, expr->span);
    if (! accepts(expected, answer)) {
      report_mismatch(expr->span, answer, expected);
      return undef_error(module);
    }
    return found;
  }


  ir::Value* Checker::pieces_alike(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right)
  {
    ir::Function* alike = syntax_builtin(alike_pieces_name,
                                         std::vector<const ir::Type*>{ ir::syntax_type(), ir::syntax_type() });
    ir::Value* found = builder.call(alike, std::vector<ir::Value*>{ left, right }, ir::bool_type(), expr->span);
    if (expr->op == ast::BinaryOp::not_equal)
      return builder.binary(ir::BinOp::xor_, found, builder.bool_const(true), expr->span);
    return found;
  }


  ir::Function* Checker::syntax_builtin(std::string_view name, std::span<const ir::Type* const> params)
  {
    if (ir::Function* found = module.function(name); found != nullptr)
      return found;
    auto made = std::make_unique<ir::Function>(
      std::string(name), function_type({ params.begin(), params.end() }, *syntax_answers().get(std::string(name))));
    made->linkage = ir::Linkage::internal;
    return module.add_function(std::move(made));
  }


  // -- operators a program defines --------------------------------------------

  void Checker::an_operator(const ast::FuncDef* node, Held what)
  {
    if (! is_operator_name(node->name))
      return;
    // A pair is written around what it is applied to, so its first parameter
    // is that and the rest are whatever stands inside; a glyph is written
    // before one operand or between two.
    bool paired_ = code_points(node->name) == 2;
    bool enough = paired_ ? node->params.size() >= 2 : (node->params.size() == 1 || node->params.size() == 2);
    if (! enough) {
      diags.emit(D::LANG_OPERATOR_ARITY, node->name_span,
                 { { "glyph", node->name }, { "count", std::to_string(node->params.size()) },
                   { "wanted", paired_ ? "two or more" : "one operand or two" } });
      return;
    }
    operators[{ std::string(node->name), std::int64_t(node->params.size()) }] = what;
  }


  ir::Value* Checker::paired(ir::Builder& builder, const ast::Expr* base, std::string_view pair,
                             std::span<const ast::Expr* const> inside, Span span, const ir::Type* expected)
  {
    if (operators.empty() || ! of_its_own(hint_of(base)))
      return nullptr;
    std::vector<const ast::Expr*> operands{ base };
    operands.insert(operands.end(), inside.begin(), inside.end());
    return operator_call(builder, pair, operands, span, expected);
  }


  bool Checker::operator_stands(const ast::Binary* expr, const ir::Type* context)
  {
    const ir::Type* found = hint_of(expr->left);
    if (found == nullptr)
      found = hint_of(expr->right);
    if (found == nullptr)
      found = context;
    if (found == nullptr || found == ir::error_type())
      return true;
    return operand_type_stands(expr->op, found);
  }


  bool Checker::operator_stands_alone(const ast::Unary* expr)
  {
    return ! of_its_own(hint_of(expr->operand));
  }


  const ir::Type* Checker::answers_written(const ast::Expr* expr)
  {
    auto unary = ast::as<ast::Unary>(expr);
    auto binary = ast::as<ast::Binary>(expr);
    std::string glyph(unary != nullptr ? describe(unary->op) : describe(binary->op));
    std::int64_t arity = unary != nullptr ? 1 : 2;
    if (operator_written(glyph, arity).empty())
      return nullptr;
    bool ours = unary != nullptr ? operator_stands_alone(unary) : operator_stands(binary, nullptr);
    if (ours)
      return nullptr;
    Held found = operator_written(glyph, arity);
    auto func = found.get<ir::Function>();
    if (func == nullptr)
      return nullptr;
    return func->fty->ret == ir::void_type() ? nullptr : func->fty->ret;
  }


  ir::Value* Checker::lower_fresh(ir::Builder& builder, const ast::Fresh* expr, const ir::Type* expected)
  {
    if (ir::Value* found = operator_call(builder, expr->glyph, expr->operands, expr->span, expected); found != nullptr)
      return found;
    diags.emit(D::LANG_OPERATOR_NOT_DEFINED, expr->span,
               { { "glyph", expr->glyph },
                 { "found", expr->operands.size() == 1 ? std::string("one operand")
                                                       : std::format("{} operands", expr->operands.size()) } });
    return undef_error(module);
  }


  Held Checker::operator_written(std::string_view glyph, std::int64_t arity)
  {
    return operators.get_or({ std::string(glyph), arity }, Held());
  }


  ir::Value* Checker::operator_call(ir::Builder& builder, std::string_view glyph,
                                    std::span<const ast::Expr* const> operands, Span span, const ir::Type* expected)
  {
    Held found = operator_written(glyph, std::int64_t(operands.size()));
    if (found.empty())
      return nullptr;
    auto call = syntax.make<ast::Call>(span);
    auto callee = syntax.make<ast::NameRef>(span);
    callee->name = syntax.text(glyph);
    call->callee = callee;
    call->args = syntax.list(std::vector<const ast::Expr*>(operands.begin(), operands.end()));
    if (auto generic = found.get<Generic>(); generic != nullptr)
      return lower_generic(builder, call, generic, expected);
    return through(builder, call, found.get<ir::Function>(), expected);
  }


  // -- what a signature requires and demands ----------------------------------

  void Checker::collect_bundle(const ast::BundleDef* node, std::string_view path)
  {
    for (std::size_t at = 0; at < std::min(node->params.size(), node->param_spans.size()); ++at)
      if (! sema::is_generic(node->params[at]))
        diags.emit(D::LANG_BUNDLE_PARAMETER_NOT_A_TYPE, node->param_spans[at],
                   { { "name", node->params[at] }, { "bundle", node->name } });
    std::string name(node->name);
    if (bundles->contains(name) || top->contains(name)) {
      auto earlier = bundles->get(name);
      auto spans = name_spans.get(name);
      std::optional<Span> previous;
      if (earlier != nullptr)
        previous = (*earlier)->node->name_span;
      else if (spans != nullptr)
        previous = *spans;
      auto told = diags.emit(D::LANG_FILESTRUCT_DUPLICATE_DEFINITION, node->name_span, { { "name", name } });
      if (previous)
        told.note(D::LANG_FILESTRUCT_PREVIOUS_DEFINITION, *previous, { { "name", name } });
      return;
    }
    auto bound_ = bind_attributes(node->attrs, AttrTarget::bundle);
    check_clause_shapes(node->clauses);
    for (auto clause : node->clauses)
      if (levels_of(clause->expr) == std::set<bool>{ false })
        // A bundle's parameters are types and it can name no value, so a line
        // over values has nothing to be about.
        diags.emit(D::LANG_BUNDLE_LINE_OVER_VALUES, clause->span, { { "bundle", node->name } });
    auto made = make<Bundle>();
    made->node = node;
    made->path = std::string(path);
    made->scope = top;
    made->siblings = bundles;
    made->exported = is_export(bound_);
    (*bundles)[name] = made;
  }


  std::string Checker::as_written(const ast::Clause* clause)
  {
    auto found = sources->snippet(clause->expr->span.to(clause->answers != nullptr ? clause->answers->span
                                                                                    : clause->expr->span));
    return found && ! found->empty() ? std::string(*found) : std::string("the requirement");
  }


  void Checker::check_bundle_cycles()
  {
    NameSet told;
    for (const auto& [name, bundle] : *bundles) {
      if (told.contains(name))
        continue;
      NameSet reached;
      if (reaches(bundle, name, reached)) {
        // Every bundle of the cycle is in one, and the first names it.
        told.merge(reached);
        told.insert(name);
        diags.emit(D::LANG_BUNDLE_CYCLE, bundle->node->name_span, { { "bundle", name } });
      }
    }
  }


  bool Checker::reaches(Bundle* bundle, std::string_view wanted, NameSet& seen)
  {
    NamesKept kept(*this, bundle->scope, bundle->siblings);
    return reaching(bundle, wanted, seen);
  }


  bool Checker::reaching(Bundle* bundle, std::string_view wanted, NameSet& seen)
  {
    for (auto clause : bundle->node->clauses) {
      Bundle* found = bundle_named(clause->expr);
      if (found == nullptr)
        continue;
      if (found->node->name == wanted)
        return true;
      if (seen.contains(found->node->name))
        continue;
      seen.emplace(found->node->name);
      if (reaches(found, wanted, seen))
        return true;
    }
    return false;
  }


  Bundle* Checker::bundle_named(const ast::Expr* expr)
  {
    auto call = ast::as<ast::Call>(expr);
    if (call == nullptr)
      return nullptr;
    if (auto name = ast::as<ast::NameRef>(call->callee); name != nullptr)
      return bundles->get_or(std::string(name->name), nullptr);
    if (auto member = ast::as<ast::Member>(call->callee); member != nullptr)
      if (LoadedModule* held = module_at(member->base); held != nullptr)
        return held->bundles.get_or(std::string(member->name), nullptr);
    return nullptr;
  }


  bool Checker::over_types(const ast::Expr* expr)
  {
    return levels_of(expr).contains(true);
  }


  std::set<bool> Checker::levels_of(const ast::Expr* expr)
  {
    std::set<bool> found;
    levels_in(expr, found);
    return found;
  }


  void Checker::levels_in(const ast::Node* expr, std::set<bool>& into)
  {
    if (expr == nullptr)
      return;
    if (auto name = ast::as<ast::NameRef>(expr); name != nullptr) {
      into.insert(sema::is_generic(name->name));
      return;
    }
    if (auto unary = ast::as<ast::Unary>(expr);
        unary != nullptr && (unary->op == ast::UnaryOp::max || unary->op == ast::UnaryOp::min)
        && ast::is<ast::Lifted>(unary->operand)) {
      into.insert(false);
      return;
    }
    auto call = ast::as<ast::Call>(expr);
    if (call != nullptr) {
      if (auto callee = ast::as<ast::NameRef>(call->callee); callee != nullptr && callee->name == typeof_name) {
        into.insert(false);
        return;
      }
    }
    if (auto binary = ast::as<ast::Binary>(expr);
        binary != nullptr && (binary->op == ast::BinaryOp::equal || binary->op == ast::BinaryOp::not_equal)
        && (lifts_a_parameter(binary->left) || lifts_a_parameter(binary->right)
            || type_stood_for(binary->left) != nullptr || type_stood_for(binary->right) != nullptr)) {
      // Two types compared, which the compiler answers with a truth value.
      into.insert(false);
      return;
    }
    if (ast::is<ast::Lifted>(expr)) {
      into.insert(true);
      return;
    }
    if (call != nullptr) {
      if (bundle_named(call) != nullptr)
        into.insert(true);
      for (auto one : call->args)
        levels_in(one, into);
      return;
    }
    ast::for_each_child(expr, [&](const ast::Node* one) { levels_in(one, into); });
  }


  std::optional<bool> Checker::reading_of(const ast::Clause* clause)
  {
    auto levels = levels_of(clause->expr);
    if (levels.size() > 1)
      return std::nullopt;
    std::vector<std::string> written;
    named_in(clause->expr, written);
    if (std::ranges::contains(written, answer_name) && clause->kind != ast::ClauseKind::post)
      return std::nullopt;
    if (levels.contains(true))
      return clause->kind != ast::ClauseKind::pre ? std::nullopt : std::optional<bool>(true);
    return clause->answers != nullptr ? std::nullopt : std::optional<bool>(false);
  }


  bool Checker::no_requirement_here(const ast::FuncDef* node)
  {
    bool refused = false;
    for (auto clause : node->clauses) {
      if (! levels_of(clause->expr).contains(true))
        // Over values: a condition, or -- with an arrow -- another mistake.
        continue;
      refused = true;
      diags.emit(D::LANG_REQUIREMENT_NOT_GENERIC, clause->span, { { "name", node->name } });
    }
    return refused;
  }


  bool Checker::check_clause_shapes(std::span<const ast::Clause* const> clauses)
  {
    if (clauses.empty() || shaped.contains(id_of(clauses.data())))
      return true;
    shaped.insert(id_of(clauses.data()));
    bool good = true;
    for (auto clause : clauses) {
      if (reading_of(clause))
        continue;
      good = false;
      auto levels = levels_of(clause->expr);
      std::vector<std::string> written;
      named_in(clause->expr, written);
      if (levels.size() > 1)
        diags.emit(D::LANG_CLAUSE_MIXES_LEVELS, clause->span);
      else if (std::ranges::contains(written, answer_name) && clause->kind != ast::ClauseKind::post)
        diags.emit(D::LANG_CLAUSE_ANSWER_OUTSIDE_POST, clause->span);
      else if (levels.contains(true))
        diags.emit(D::LANG_CLAUSE_POST_OVER_TYPES, clause->span);
      else
        // The arrow says what an unevaluated expression would answer.
        diags.emit(D::LANG_CLAUSE_ARROW_OVER_VALUES, clause->span);
    }
    return good;
  }


  bool Checker::check_clauses(std::span<const ast::Clause* const> clauses, Dict<std::string, const ir::Type*>& bound_,
                              std::string_view name, Span span)
  {
    for (auto clause : clauses) {
      if (reading_of(clause) != std::optional<bool>(true))
        continue;
      if (! met(clause, bound_, name, span))
        return false;
    }
    return true;
  }


  bool Checker::met(const ast::Clause* clause, Dict<std::string, const ir::Type*>& bound_, std::string_view name,
                    Span span)
  {
    const ir::Type* found = answered_by(clause, bound_, name, span);
    if (found == nullptr)
      return false;
    if (clause->answers == nullptr)
      return true;
    return settling(clause, found, bound_, name);
  }


  const ir::Type* Checker::answered_by(const ast::Clause* clause, Dict<std::string, const ir::Type*>& bound_,
                                       std::string_view name, Span span)
  {
    if (Bundle* bundle = bundle_named(clause->expr); bundle != nullptr) {
      if (clause->answers != nullptr) {
        diags.emit(D::LANG_BUNDLE_ANSWERS_NOTHING, clause->span, { { "bundle", bundle->node->name } });
        return nullptr;
      }
      return bundle_met(bundle, ast::as<ast::Call>(clause->expr), bound_, name, span) ? ir::void_type() : nullptr;
    }
    auto dummies = dummies_for(clause, bound_, name);
    if (! dummies)
      return nullptr;
    auto& [written, values] = *dummies;
    const ir::Type* found = written_over(values, written);
    if (found == nullptr && abstract != nullptr
        && std::ranges::any_of(values, [](const auto& one) { return holds_opaque(std::get<1>(one)); })) {
      // Asked of a type parameter of a body checked where it is written: what
      // that body's requirements say is all that is known of it.
      diags.emit(D::LANG_REQUIREMENT_NOT_PASSED_ON, clause->span,
                 { { "function", name }, { "written", as_written(clause) }, { "caller", abstract->name } });
      return nullptr;
    }
    if (found == nullptr) {
      std::vector<std::string> names = bound_.keys();
      std::ranges::sort(names);
      std::string types;
      for (const auto& one : names) {
        if (! types.empty())
          types += ", ";
        types += std::format("{} = {}", one, (*bound_.get(one))->written());
      }
      diags.emit(D::LANG_CLAUSE_NOT_MET, clause->span,
                 { { "function", name }, { "written", as_written(clause) }, { "types", types } });
      return nullptr;
    }
    return found;
  }


  bool Checker::bundle_met(Bundle* bundle, const ast::Call* expr, const Dict<std::string, const ir::Type*>& bound_,
                           std::string_view name, Span span)
  {
    const ast::BundleDef* written = bundle->node;
    if (auto member = ast::as<ast::Member>(expr->callee); member != nullptr && ! bundle->exported) {
      auto base = ast::as<ast::NameRef>(member->base);
      diags.emit(D::LANG_IMPORT_NOT_EXPORTED, member->name_span,
                 { { "name", written->name }, { "module", base != nullptr ? base->name : std::string_view() } });
      return false;
    }
    if (expr->args.size() != written->params.size()) {
      diags.emit(D::LANG_BUNDLE_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "bundle", written->name }, { "expected", written->params.size() }, { "found", expr->args.size() } });
      return false;
    }
    if (bundle->opening) {
      diags.emit(D::LANG_BUNDLE_CYCLE, expr->span, { { "bundle", written->name } });
      return false;
    }
    Dict<std::string, const ir::Type*> inner;
    for (std::size_t at = 0; at < expr->args.size(); ++at) {
      auto arg = expr->args[at];
      const ir::Type* given = type_operand(arg, bound_);
      if (given == nullptr) {
        diags.emit(D::LANG_BUNDLE_ARGUMENT_NOT_A_TYPE, arg->span, { { "bundle", written->name }, { "at", at + 1 } });
        return false;
      }
      inner[std::string(written->params[at])] = given;
    }
    bundle->opening = true;
    // Where the bundle was written, for as long as its lines are being read.
    NamesKept kept(*this, bundle->scope, bundle->siblings);
    Finally closed{ [&] { bundle->opening = false; } };
    for (auto one : written->clauses)
      if (! met(one, inner, written->name, span)) {
        diags.emit(D::LANG_BUNDLE_ASKED_HERE, expr->span, { { "bundle", written->name }, { "name", name } });
        return false;
      }
    return true;
  }


  const ir::Type* Checker::type_operand(const ast::Expr* expr, const Dict<std::string, const ir::Type*>& bound_)
  {
    if (auto name = ast::as<ast::NameRef>(expr); name != nullptr && sema::is_generic(name->name))
      return bound_.get_or(std::string(name->name), nullptr);
    if (auto lifted = ast::as<ast::Lifted>(expr); lifted != nullptr)
      return lifted_type(lifted);
    return nullptr;
  }


  std::optional<std::tuple<const ast::Expr*, std::vector<std::tuple<std::string, const ir::Type*>>>>
  Checker::dummies_for(const ast::Clause* clause, const Dict<std::string, const ir::Type*>& bound_,
                       std::string_view name)
  {
    std::vector<std::string> written_over_;
    type_names_in(clause->expr, written_over_);
    for (const auto& one : written_over_)
      if (! bound_.contains(one)) {
        diags.emit(D::LANG_CLAUSE_NOT_SETTLED_YET, clause->span, { { "name", one }, { "function", name } });
        return std::nullopt;
      }
    // Every type operand, in the order written, given a name: a type
    // parameter keeps its own, a lifted type gets one no program could write.
    // Walked from the top and not into a type operand, as Python's `walked`
    // is; the tree is rebuilt with the lifted ones replaced afterwards.
    Dict<std::string, const ir::Type*> values;
    std::unordered_map<const ast::Node*, const ast::Node*> instead;
    int made = 0;
    auto walk = [&](this auto& self, const ast::Node* node) -> void {
      if (node == nullptr)
        return;
      if (ast::is<ast::NameRef>(node) || ast::is<ast::Lifted>(node)) {
        auto expr = static_cast<const ast::Expr*>(node);
        if (const ir::Type* found = type_operand(expr, bound_); found != nullptr) {
          if (auto ref = ast::as<ast::NameRef>(node); ref != nullptr) {
            values[std::string(ref->name)] = found;
            return;
          }
          std::string called = std::format("{}{}", dummy_prefix, ++made);
          values[called] = found;
          auto replacement = syntax.make<ast::NameRef>(node->span);
          replacement->name = syntax.text(called);
          instead[node] = replacement;
          return;
        }
      }
      ast::for_each_child(node, [&](const ast::Node* one) { self(one); });
    };
    walk(clause->expr);
    const ast::Node* rewritten = instead.empty() ? clause->expr
                                                 : rebuilt(syntax, clause->expr, [&](const ast::Node* node) -> const ast::Node* {
                                                     auto found = instead.find(node);
                                                     return found == instead.end() ? nullptr : found->second;
                                                   });
    std::vector<std::tuple<std::string, const ir::Type*>> listed;
    for (const auto& [one, ty] : values)
      listed.emplace_back(one, ty);
    return std::tuple(static_cast<const ast::Expr*>(rewritten), listed);
  }


  const ir::Type* Checker::written_over(std::span<const std::tuple<std::string, const ir::Type*>> dummies,
                                        const ast::Expr* expr)
  {
    std::vector<const ir::Type*> types;
    for (const auto& [name, ty] : dummies)
      types.push_back(ty);
    auto func = make<ir::Function>(std::string(), function_type(types, ir::void_type()));
    ir::BasicBlock* block = func->add_block();
    ir::Builder builder(module, *func);
    bool told = false;
    auto diverted = diags.divert([&told](const Diagnostic&) { told = true; });
    auto kept = std::tuple(impure, answering, std::move(borrows), std::move(places), std::move(addressed));
    const ir::Type* answer = nullptr;
    {
      Finally restore{ [&] {
        diags.restore(std::move(diverted));
        std::tie(impure, answering, borrows, places, addressed) = std::move(kept);
      } };
      impure = true;
      answering = ir::void_type();
      borrows.clear();
      places.clear();
      addressed.clear();
      push_scope();
      for (const auto& [name, ty] : dummies)
        bind_local(name, block->add_param(ty, name), invalid_span, false, invalid_span, true, &builder);
      ir::Value* found = lower_expr(builder, expr, nullptr);
      answer = value_type_of(found);
      pop_scope();
    }
    return answer == ir::error_type() || told ? nullptr : answer;
  }


  bool Checker::settling(const ast::Clause* clause, const ir::Type* found, Dict<std::string, const ir::Type*>& bound_,
                         std::string_view name)
  {
    const ast::TypeExpr* written = clause->answers;
    if (auto ref = ast::as<ast::TypeRef>(written);
        ref != nullptr && ! ref->module && ! ref->result && sema::is_generic(ref->name)
        && ! bound_.contains(std::string(ref->name))) {
      bound_[std::string(ref->name)] = found;
      return true;
    }
    const ir::Type* wanted = nullptr;
    {
      auto outer = bound;
      bound = bound_;
      Finally restore{ [&] { bound = outer; } };
      wanted = resolve_type(written);
    }
    if (wanted == ir::error_type())
      return false;
    if (wanted != found) {
      diags.emit(D::LANG_CLAUSE_ANSWERS_OTHERWISE, clause->span,
                 { { "function", name }, { "expected", wanted->written() }, { "found", found->written() } });
      return false;
    }
    return true;
  }


  // -- the conditions a body carries ------------------------------------------

  void Checker::lower_conditions(ir::Builder& builder, const ast::FuncDef* node, ir::Function* func,
                                 bool while_running)
  {
    for (auto clause : node->clauses) {
      if (clause->kind != ast::ClauseKind::pre || reading_of(clause) != std::optional<bool>(false))
        continue;
      if (while_running && compares_types(clause->expr))
        continue;
      demanded(builder, clause, func, "a pre-condition that does not hold", target::statuses::PRE_CONDITION);
    }
  }


  ir::Value* Checker::lower_at_entry(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (! after) {
      diags.emit(D::LANG_ENTRY_OUTSIDE_POST, expr->span);
      return undef_error(module);
    }
    if (expr->args.size() != 1 || ! ast::is<ast::NameRef>(expr->args[0])) {
      diags.emit(D::LANG_ENTRY_TAKES_A_PARAMETER, expr->span);
      return undef_error(module);
    }
    auto written = ast::as<ast::NameRef>(expr->args[0]);
    ir::Value* found = at_entry.get_or(std::string(written->name), nullptr);
    if (found == nullptr) {
      auto node = std::get<0>(demanding);
      diags.emit(D::LANG_ENTRY_NOT_A_PARAMETER, written->span,
                 { { "name", written->name }, { "function", node != nullptr ? node->name : std::string_view() } });
      return undef_error(module);
    }
    const ir::Type* answer = value_type_of(found);
    if (! accepts(expected, answer)) {
      report_mismatch(expr->span, answer, expected);
      return undef_error(module);
    }
    return found;
  }


} // namespace pl4g::sema
