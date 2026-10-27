// The checker, part of check.py: what a loop can take its values from,
// handing an iterator on, generators, and a generator written into the loop
// that walks it (check.py lines 18053 to 20792).
#include <algorithm>
#include <format>
#include <memory>
#include <string>
#include <unordered_map>
#include <utility>

#include "diag/ids.hh"
#include "ir/printer.hh"
#include "sema/check.hh"
#include "sema/strings.hh"
#include "sema/tables.hh"
#include "target/statuses.hh"


namespace pl4g::sema {

  namespace {

    // The names of the language these parts spell out.
    constexpr std::string_view builtin_glyph = "\N{APL FUNCTIONAL SYMBOL QUAD}";
    constexpr std::string_view iter_glyph = "\N{RIGHTWARDS PAIRED ARROWS}";
    constexpr std::string_view wildcard_name = "_";
    constexpr std::string_view enumerate_name = "\N{APL FUNCTIONAL SYMBOL QUAD}enumerate";
    constexpr std::string_view start_name = "\N{APL FUNCTIONAL SYMBOL QUAD}start";
    constexpr std::string_view resume_name = "\N{APL FUNCTIONAL SYMBOL QUAD}resume";
    constexpr std::string_view heap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";

    // Python's `UndefConst(ty)`: a new one each time, as there, since a value's
    // identity is what several tables of the checker are keyed by.
    ir::Value* undef(ir::Module& module, const ir::Type* ty)
    {
      return const_cast<ir::UndefConst*>(module.constant<ir::UndefConst>(ty));
    }

    // Python's `a == b` for two types either of which may be nothing.
    bool same_or_none(const ir::Type* a, const ir::Type* b)
    {
      if (a == nullptr || b == nullptr)
        return a == b;
      return ir::same(a, b);
    }

    // The values of a state but the last, and the last: `s[:-1]`, `s[-1]`.
    std::span<ir::Value* const> but_last(std::span<ir::Value* const> state) { return state.first(state.size() - 1); }

    // What Python's `try`/`finally` puts back: the variables given, as they
    // were where this was made, once it goes out of scope.
    template<typename... T>
    class Kept {
    public:
      explicit Kept(T&... refs) : refs(refs...), saved(refs...) {}
      Kept(const Kept&) = delete;
      Kept& operator=(const Kept&) = delete;
      ~Kept() { refs = std::move(saved); }

    private:
      std::tuple<T&...> refs;
      std::tuple<T...> saved;
    };

    // What a `finally` does that is not putting something back.
    template<typename F>
    class Finally {
    public:
      explicit Finally(F f) : f(std::move(f)) {}
      Finally(const Finally&) = delete;
      Finally& operator=(const Finally&) = delete;
      ~Finally() { f(); }

    private:
      F f;
    };

    // How many nodes stand at and below `node`: Python's `_count(node, ast.Node)`.
    std::int64_t count_nodes(const ast::Node* node)
    {
      if (node == nullptr)
        return 0;
      std::int64_t found = 1;
      ast::for_each_child(node, [&](const ast::Node* one) { found += count_nodes(one); });
      return found;
    }

    // A name the checker writes, kept as long as the nodes are.
    const ast::NameRef* name_ref(ast::SyntaxArena& syntax, Span span, std::string_view name)
    {
      auto made = syntax.make<ast::NameRef>(span);
      made->name = syntax.text(name);
      return made;
    }

    // A block of the statements given, written as layout.
    const ast::Block* block_of(ast::SyntaxArena& syntax, Span span, const std::vector<const ast::Stmt*>& stmts)
    {
      auto made = syntax.make<ast::Block>(span);
      made->style = ast::BlockStyle::layout;
      made->statements = syntax.list(stmts);
      return made;
    }

    // `name` without the glyph it starts with: Python's `name[1:]`, one
    // character and not one byte.
    std::string_view without_first_character(std::string_view name)
    {
      if (name.empty())
        return name;
      std::size_t length = 1;
      auto lead = static_cast<unsigned char>(name[0]);
      if (lead >= 0xf0)
        length = 4;
      else if (lead >= 0xe0)
        length = 3;
      else if (lead >= 0xc0)
        length = 2;
      return name.substr(std::min(length, name.size()));
    }

    // The name a type of the program's own was defined with, or nothing.
    std::string defined_name(const ir::Type* ty)
    {
      if (auto record = ir::as<ir::ProductType>(ty))
        return record->name;
      if (auto sum = ir::as<ir::SumType>(ty))
        return sum->name;
      if (auto enumeration = ir::as<ir::EnumType>(ty))
        return enumeration->name;
      return {};
    }

    // Python's `isinstance(node, (IntLit, FloatLit, BoolLit, CharLit, StringLit))`.
    bool is_literal(const ast::Node* node)
    {
      return ast::is<ast::IntLit>(node) || ast::is<ast::FloatLit>(node) || ast::is<ast::BoolLit>(node)
             || ast::is<ast::CharLit>(node) || ast::is<ast::StringLit>(node);
    }

  } // namespace


  // -- what a loop can take its values from ----------------------------------

  ir::Value* Checker::written_out(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func,
                                  const ir::Type* expected, bool produces)
  {
    auto asked = fields_of_a_type(stmt->iterable);
    if (asked == ir::error_type()) [[unlikely]]
      return undef(module, ir::error_type());
    if (auto record = ir::as<ir::ProductType>(asked))
      return written_out_per_field(builder, stmt, func, record);
    auto value = lower_expr(builder, stmt->iterable, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type()) [[unlikely]]
      return undef(module, ir::error_type());
    auto tuple = ir::as<ir::TupleType>(ty);
    if (tuple == nullptr) [[unlikely]] {
      diags.emit(D::LANG_COMPTIME_NEEDS_A_TUPLE, stmt->iterable->span, { { "found", ty->written() } });
      return undef(module, ir::error_type());
    }
    if (produces || stmt->alternative != nullptr || stmt->label != nullptr) [[unlikely]] {
      diags.emit(D::LANG_COMPTIME_NEEDS_A_TUPLE, stmt->span, { { "found", ty->written() } });
      return undef(module, ir::error_type());
    }
    auto declared = stmt->type != nullptr ? resolve_type(stmt->type) : nullptr;
    // A member is one value to the program and may be several to the machine,
    // so which leaves belong to it is asked rather than assumed.
    for (std::size_t at = 0; at < tuple->members.size(); ++at) {
      auto one = tuple->members[at];
      if (declared != nullptr && declared != one) [[unlikely]] {
        report_mismatch(stmt->iterable->span, one, declared);
        return undef(module, ir::error_type());
      }
      auto turn = member(builder, value, tuple, std::int64_t(at), stmt->span);
      push_scope();
      bind_local(stmt->name, turn, stmt->name_span, false, invalid_span, false, &builder);
      report_inferred(std::vector{ std::tuple{ std::string(stmt->name), stmt->name_span } }, stmt->type);
      lower_block(builder, stmt->body, func, false);
      pop_scope(! builder.is_terminated() ? std::optional(stmt->body->span) : std::nullopt);
    }
    return undef(module, ir::void_type());
  }

  ir::Value* Checker::written_out_per_field(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func,
                                            const ir::ProductType* ty)
  {
    if (stmt->alternative != nullptr || stmt->label != nullptr) [[unlikely]] {
      diags.emit(D::LANG_COMPTIME_NEEDS_A_TUPLE, stmt->span, { { "found", ty->written() } });
      return undef(module, ir::error_type());
    }
    auto text = ir::parts_of(ir::str_type());
    auto holder = ir::tuple_type({ ir::str_type() });
    for (const auto& [name, kind] : ty->fields) {
      auto written = written_text(builder, name, stmt->span);
      std::vector<ir::Value*> pieces;
      for (std::size_t which = 0; which < text.size(); ++which)
        pieces.push_back(builder.extract(written, std::int64_t(which), text[which], stmt->span));
      auto turn = builder.whole(pieces, holder, stmt->span);
      push_scope();
      bind_local(stmt->name, turn, stmt->name_span, false, invalid_span, false, &builder);
      report_inferred(std::vector{ std::tuple{ std::string(stmt->name), stmt->name_span } }, stmt->type);
      if (auto local = find_local(stmt->name); local != nullptr)
        field_types[id_of(local)] = kind;
      lower_block(builder, stmt->body, func, false);
      pop_scope(! builder.is_terminated() ? std::optional(stmt->body->span) : std::nullopt);
    }
    return undef(module, ir::void_type());
  }

  const ir::Type* Checker::field_type_named(const ast::Expr* expr)
  {
    auto element = ast::as<ast::Element>(expr);
    if (element == nullptr || ! ast::is<ast::NameRef>(element->base) || element->indices.size() != 1)
      return nullptr;
    auto index = ast::as<ast::IntLit>(element->indices[0]);
    if (index == nullptr || index->value.too_large || index->value.value != 1)
      return nullptr;
    auto local = find_local(static_cast<const ast::NameRef*>(element->base)->name);
    if (local == nullptr)
      return nullptr;
    auto found = field_types.get(id_of(local));
    return found == nullptr ? nullptr : *found;
  }

  std::optional<Iteration> Checker::iteration_over(ir::Builder& builder, const ast::ForEach* stmt)
  {
    if (auto counted = enumerating(stmt->iterable))
      return enumerated(builder, stmt, *counted);
    return iteration_of(builder, stmt);
  }

  std::optional<std::tuple<const ast::Expr*, const ast::Expr*>> Checker::enumerating(const ast::Expr* written)
  {
    auto call = ast::as<ast::Call>(written);
    auto callee = call != nullptr ? ast::as<ast::NameRef>(call->callee) : nullptr;
    if (callee == nullptr || callee->name != enumerate_name)
      return std::nullopt;
    if (call->args.size() < 1 || call->args.size() > 2) [[unlikely]] {
      diags.emit(D::LANG_ENUMERATE_TAKES_ONE_OR_TWO, written->span, { { "found", std::to_string(call->args.size()) } });
      return std::nullopt;
    }
    return std::tuple{ call->args[0], call->args.size() == 2 ? call->args[1] : nullptr };
  }

  std::optional<Iteration> Checker::enumerated(ir::Builder& builder, const ast::ForEach* stmt,
                                               const std::tuple<const ast::Expr*, const ast::Expr*>& counted)
  {
    auto [walked, from_written] = counted;
    auto walking = syntax.copy(*stmt);
    walking->iterable = walked;
    walking->type = nullptr;
    auto found = iteration_of(builder, walking);
    if (! found)
      return std::nullopt;
    auto inner = std::make_shared<const Iteration>(std::move(*found));
    const ir::Type* index;
    ir::Value* first;
    if (from_written == nullptr) {
      index = ir::u64();
      first = builder.int_const(ir::u64(), 0);
    } else {
      auto literal = ast::as<ast::IntLit>(from_written);
      first = lower_expr(builder, from_written, literal != nullptr && ! literal->type_name ? ir::u64() : nullptr);
      index = value_type_of(first);
      if (index == ir::error_type()) [[unlikely]]
        return std::nullopt;
      if (! ir::is<ir::IntType>(index)) [[unlikely]] {
        diags.emit(D::LANG_ENUMERATE_COUNTS_IN_INTEGERS, from_written->span, { { "found", index->written() } });
        return std::nullopt;
      }
    }
    auto element = ir::tuple_type({ index, inner->element });
    auto one = builder.int_const(index, 1);
    auto span = stmt->span;

    // A turn that is itself several values -- a record, a dictionary's pair --
    // is made into the tuple by its leaves, as any tuple is.
    auto take = [this, inner, element, span](ir::Builder& b, std::span<ir::Value* const> state) {
      auto last = state.back();
      auto taken = inner->take(b, but_last(state));
      return made_tuple(b, std::vector{ last, taken }, element, span);
    };

    auto declared = stmt->type != nullptr ? resolve_type(stmt->type) : nullptr;
    if (declared != nullptr && declared != element) [[unlikely]] {
      report_mismatch(stmt->iterable->span, element, declared);
      return std::nullopt;
    }
    Iteration made;
    made.element = element;
    made.start = inner->start;
    made.start.push_back(first);
    made.more = [inner](ir::Builder& b, std::span<ir::Value* const> s) { return inner->more(b, but_last(s)); };
    made.take = take;
    made.step = [inner, one, span](ir::Builder& b, std::span<ir::Value* const> s) {
      auto moved = inner->step(b, but_last(s));
      moved.push_back(b.binary(ir::BinOp::add, s.back(), one, span));
      return moved;
    };
    if (inner->wanted)
      made.wanted = [inner](ir::Builder& b, std::span<ir::Value* const> s) { return inner->wanted(b, but_last(s)); };
    made.passed = [inner](ir::Builder& b, std::span<ir::Value* const> s) {
      auto moved = (inner->passed ? inner->passed : inner->step)(b, but_last(s));
      moved.push_back(s.back());
      return moved;
    };
    Ask taken_place;
    if (inner->place)
      taken_place = [inner](ir::Builder& b, std::span<ir::Value* const> s) { return inner->place(b, but_last(s)); };
    made.members = std::vector<std::pair<Ask, Ask>>{
      { [](ir::Builder&, std::span<ir::Value* const> s) { return s.back(); }, Ask{} },
      { [inner](ir::Builder& b, std::span<ir::Value* const> s) { return inner->take(b, but_last(s)); }, taken_place },
    };
    return made;
  }

  std::optional<Iteration> Checker::iteration_of(ir::Builder& builder, const ast::ForEach* stmt)
  {
    if (ast::is<ast::Range>(stmt->iterable))
      return over_a_range(builder, stmt);
    auto declared = stmt->type != nullptr ? resolve_type(stmt->type) : nullptr;
    ir::Value* value;
    Picked picked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = stmt->iterable;
      pick_walked = {};
      value = lower_expr(builder, stmt->iterable, holding(declared, stmt->iterable));
      picked = pick_walked;
    }
    auto ty = value_type_of(value);
    if (ty == ir::error_type()) [[unlikely]]
      return std::nullopt;
    std::optional<Iteration> found;
    if (auto walk = std::get_if<Iterable*>(&picked)) {
      found = walk_of(builder, *walk, stmt->iterable->span);
      if (auto name = ast::as<ast::NameRef>(stmt->iterable); found && name != nullptr)
        report_iterator(name->name,
                        std::format("'{}' is walked here, in place and from the start: nothing is built and no call is made",
                                    name->name),
                        stmt->iterable->span);
    } else if (auto pick = std::get_if<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(&picked)) {
      const auto& [base, aty, mask] = *pick;
      found = over_a_pick(builder, static_cast<const ast::Element*>(stmt->iterable), base, aty, mask);
    } else if (ir::is<ir::TupleType>(ty)) [[unlikely]] {
      diags.emit(D::LANG_FOREACH_TUPLE_NEEDS_COMPTIME, stmt->iterable->span);
      return std::nullopt;
    } else {
      found = over_a_value(builder, value, ty, stmt->iterable->span);
      if (found && ir::is<ir::IterType>(ty)) {
        auto name = ast::as<ast::NameRef>(stmt->iterable);
        report_iterator(name != nullptr ? std::string(name->name) : ty->written(),
                        std::format("a {} handed in is walked through a call per turn: what it walks is not seen here",
                                    ty->written()),
                        stmt->iterable->span);
      }
    }
    if (! found)
      return std::nullopt;
    if (declared != nullptr && declared != found->element) [[unlikely]] {
      report_mismatch(stmt->iterable->span, found->element, declared);
      return std::nullopt;
    }
    return found;
  }

  const ir::Type* Checker::holding(const ir::Type* declared, const ast::Expr* iterable)
  {
    if (declared == nullptr || declared == ir::error_type())
      return nullptr;
    switch (iterable->tag) {
    case ast::NodeKind::ListLit:
      return ir::list_type(declared);
    case ast::NodeKind::ArrayLit: {
      // A turn gives an element where the array has one dimension and a row
      // where it has more, so a row declared says every dimension but the
      // first and the writing says the first.
      auto array = ir::as<ir::ArrayType>(declared);
      std::vector<std::optional<std::uint64_t>> shape{
        std::optional<std::uint64_t>(static_cast<const ast::ArrayLit*>(iterable)->elements.size()) };
      if (array != nullptr)
        shape.insert(shape.end(), array->shape.begin(), array->shape.end());
      return ir::array_type(array != nullptr ? array->element : declared, std::move(shape));
    }
    case ast::NodeKind::SetLit:
      return ir::set_type(declared, true);
    default:
      return nullptr;
    }
  }

  std::optional<Iteration> Checker::over_a_range(ir::Builder& builder, const ast::ForEach* stmt)
  {
    auto found = range_of(builder, stmt);
    if (! found)
      return std::nullopt;
    auto [first, last, step, rising, element] = *found;
    if (! rising)
      return over_a_counted_range(builder, stmt, first, last, step, element);
    bool is_signed = element->is_signed;
    auto going = *rising ? (is_signed ? ir::CmpPred::slt : ir::CmpPred::ult)
                         : (is_signed ? ir::CmpPred::sgt : ir::CmpPred::ugt);
    // The step saturates rather than checking, so that a range whose last
    // turn would step past the end of its own type ends instead of faulting.
    auto moving = *rising ? ir::BinOp::sat_add : ir::BinOp::sat_sub;
    auto span = stmt->span;
    Iteration made;
    made.element = element;
    made.start = { first };
    made.more = [going, last, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(going, s[0], last, span);
    };
    made.take = [](ir::Builder&, std::span<ir::Value* const> s) { return s[0]; };
    made.step = [moving, step, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return LoopState{ b.binary(moving, s[0], step, span) };
    };
    return made;
  }

  Iteration Checker::over_an_array(ir::Builder& builder, ir::Value* value, const ir::ArrayType* ty, Span span)
  {
    auto [start, lengths] = shape_of(builder, value, ty, span);
    auto strides = strides_of(builder, value, ty, span);
    auto element = ty->rank() == 1 ? ty->element : row_type(ty, 1);

    Iteration made;
    made.element = element;
    made.start = { builder.int_const(ir::u64(), 0) };
    made.more = [lengths, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(ir::CmpPred::ult, s[0], lengths[0], span);
    };
    made.take = [this, start, ty, lengths, strides, span](ir::Builder& b, std::span<ir::Value* const> s) {
      auto offset = b.binary(ir::BinOp::wrap_mul, s[0], strides[0], span);
      if (ty->rank() == 1)
        return b.load(element_place(b, start, ty->element, offset, span), span);
      return row_at(b, start, ty, lengths, strides, offset, 1, span);
    };
    made.step = [span](ir::Builder& b, std::span<ir::Value* const> s) {
      return LoopState{ b.binary(ir::BinOp::wrap_add, s[0], b.int_const(ir::u64(), 1), span) };
    };
    // A row is a view of the elements rather than one of them, and nothing a
    // reference names.
    if (ty->rank() == 1)
      made.place = [this, start, ty, strides, span](ir::Builder& b, std::span<ir::Value* const> s) {
        auto offset = b.binary(ir::BinOp::wrap_mul, s[0], strides[0], span);
        return element_place(b, start, ty->element, offset, span);
      };
    return made;
  }

  std::optional<Iteration> Checker::over_a_pick(ir::Builder& builder, const ast::Element* written, ir::Value* base,
                                                const ir::ArrayType* ty, const Mask& mask, bool checked)
  {
    auto span = written->span;
    if (auto asked = std::get_if<std::function<ir::Value*(ir::Builder&, ir::Value*)>>(&mask)) {
      auto [start, lengths] = shape_of(builder, base, ty, span);
      auto strides = strides_of(builder, base, ty, span);
      auto element = ty->rank() == 1 ? ty->element : row_type(ty, 1);
      Iteration made;
      made.element = element;
      made.start = { builder.int_const(ir::u64(), 0) };
      made.more = [lengths, span](ir::Builder& b, std::span<ir::Value* const> s) {
        return b.compare(ir::CmpPred::ult, s[0], lengths[0], span);
      };
      made.take = [this, start, ty, lengths, strides, span](ir::Builder& b, std::span<ir::Value* const> s) {
        auto offset = b.binary(ir::BinOp::wrap_mul, s[0], strides[0], span);
        if (ty->rank() == 1)
          return b.load(element_place(b, start, ty->element, offset, span), span);
        return row_at(b, start, ty, lengths, strides, offset, 1, span);
      };
      made.step = [span](ir::Builder& b, std::span<ir::Value* const> s) {
        return LoopState{ b.binary(ir::BinOp::wrap_add, s[0], b.int_const(ir::u64(), 1), span) };
      };
      made.wanted = [asked = *asked](ir::Builder& b, std::span<ir::Value* const> s) { return asked(b, s[0]); };
      if (ty->rank() == 1)
        made.place = [this, start, ty, strides, span](ir::Builder& b, std::span<ir::Value* const> s) {
          auto offset = b.binary(ir::BinOp::wrap_mul, s[0], strides[0], span);
          return element_place(b, start, ty->element, offset, span);
        };
      return made;
    }
    auto marking = std::get<ir::Value*>(mask);
    auto held = ir::as<ir::ArrayType>(value_type_of(marking));
    contract_assert(held != nullptr);
    auto depth = held->rank();
    bool differs = depth > ty->rank();
    for (std::size_t at = 0; ! differs && at < std::min(held->shape.size(), ty->shape.size()); ++at)
      differs = held->shape[at] && ty->shape[at] && *held->shape[at] != *ty->shape[at];
    if (differs) [[unlikely]] {
      diags.emit(D::LANG_MASK_WRONG_SHAPE, written->indices[0]->span,
                 { { "found", held->written() }, { "wanted", ty->written() } });
      return std::nullopt;
    }
    auto [start, lengths] = shape_of(builder, base, ty, span);
    auto [marks, told] = shape_of(builder, marking, held, span);
    for (std::size_t at = 0; at < (checked ? depth : 0); ++at)
      if (! held->shape[at] || ! ty->shape[at]) {
        auto same_length = builder.compare(ir::CmpPred::eq, told[at], lengths[at], span);
        builder.check(same_length, "a mask whose shape is not the array's", target::statuses::OUT_OF_RANGE, span);
      }
    auto places = by_row(builder, builder.int_const(ir::u64(), 1), std::span(lengths).first(depth), span);
    auto element = depth == ty->rank() ? ty->element : row_type(ty, std::int64_t(depth));
    auto strides = strides_of(builder, base, ty, span);

    // A view of several dimensions is walked with an index along each, so
    // that where the walk is takes no division; one run, or one dimension, is
    // a multiplication of how far along it is.
    bool counted = depth > 1 && ! ty->fixed();
    std::vector<ir::Value*> mask_strides;
    if (counted)
      mask_strides = strides_of(builder, marking, held, span);

    auto offset_of = [this, counted, strides, lengths, depth, ty, span](ir::Builder& b, std::span<ir::Value* const> s) {
      if (counted)
        return offset_at(b, s.subspan(1), std::span(strides).first(depth), span);
      return flat_offset(b, lengths, strides, s[0], std::int64_t(depth), ty->fixed(), span);
    };

    Iteration made;
    made.element = element;
    auto zero = builder.int_const(ir::u64(), 0);
    made.start = { zero };
    if (counted)
      made.start.insert(made.start.end(), depth, zero);
    made.more = [places, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(ir::CmpPred::ult, s[0], places, span);
    };
    made.take = [this, offset_of, depth, ty, start, lengths, strides, span](ir::Builder& b,
                                                                          std::span<ir::Value* const> s) {
      auto offset = offset_of(b, s);
      if (depth == ty->rank())
        return b.load(element_place(b, start, ty->element, offset, span), span);
      return row_at(b, start, ty, lengths, strides, offset, std::int64_t(depth), span);
    };
    made.step = [this, counted, lengths, depth, span](ir::Builder& b, std::span<ir::Value* const> s) {
      auto flat = b.binary(ir::BinOp::wrap_add, s[0], b.int_const(ir::u64(), 1), span);
      LoopState moved{ flat };
      if (counted) {
        auto turned = odometer(b, s.subspan(1), std::span(lengths).first(depth), false, span);
        moved.insert(moved.end(), turned.begin(), turned.end());
      }
      return moved;
    };
    made.wanted = [this, counted, marking, held, marks, mask_strides, span](ir::Builder& b,
                                                                          std::span<ir::Value* const> s) {
      if (! counted)
        return b.load(flat_place(b, marking, held, s[0], span), span);
      auto offset = offset_at(b, s.subspan(1), mask_strides, span);
      return b.load(element_place(b, marks, held->element, offset, span), span);
    };
    return made;
  }

  bool Checker::pick_per_place(ir::Builder& builder, const ast::Element* expr, ir::Value* base,
                               const ir::ArrayType* ty)
  {
    auto span = expr->span;
    struct Walked {
      std::string name;
      ir::Value* value;
      const ir::ArrayType* held;
      std::int64_t levels;
    };
    std::vector<Walked> walked;
    std::vector<std::pair<std::string, ir::Value*>> fixed;

    auto hoist = [&](ir::Value* value, Span at) -> const ast::Expr* {
      auto name = each_name();
      fixed.emplace_back(name, value);
      return name_ref(syntax, at, name);
    };

    using Written = std::optional<std::pair<const ast::Expr*, const ast::Expr*>>;

    // The node written for one place, and for the whole, with everything but
    // the walking worked out.  `spreads` says whether an array here is walked,
    // which it is where an operator is given one and is not where a call is.
    auto walk = [&](this auto const& self, const ast::Expr* node, bool spreads) -> Written {
      if (! spreads && ! marked(node)) {
        if (is_literal(node))
          return std::pair{ node, node };
        auto value = lower_expr(builder, node, nullptr);
        if (value_type_of(value) == ir::error_type())
          return std::nullopt;
        auto named = hoist(value, node->span);
        return std::pair{ named, named };
      }
      if (auto binary = ast::as<ast::Binary>(node)) {
        auto left = self(binary->left, true);
        auto right = left ? self(binary->right, true) : std::nullopt;
        if (! left || ! right)
          return std::nullopt;
        auto one = syntax.copy(*binary);
        one->left = left->first;
        one->right = right->first;
        auto whole = syntax.copy(*binary);
        whole->left = left->second;
        whole->right = right->second;
        return std::pair{ one, whole };
      }
      if (auto unary = ast::as<ast::Unary>(node)) {
        auto inner = self(unary->operand, true);
        if (! inner)
          return std::nullopt;
        auto one = syntax.copy(*unary);
        one->operand = inner->first;
        auto whole = syntax.copy(*unary);
        whole->operand = inner->second;
        return std::pair{ one, whole };
      }
      if (auto call = ast::as<ast::Call>(node); call != nullptr && walks_per_place(call)) {
        std::vector<const ast::Expr*> one;
        std::vector<const ast::Expr*> whole;
        for (auto arg : call->args) {
          if (ast::is<ast::Spread>(arg))
            return std::nullopt;
          auto named = ast::as<ast::Named>(arg);
          auto got = self(named != nullptr ? named->value : arg, false);
          if (! got)
            return std::nullopt;
          if (named != nullptr) {
            auto first = syntax.copy(*named);
            first->value = got->first;
            one.push_back(first);
            auto second = syntax.copy(*named);
            second->value = got->second;
            whole.push_back(second);
          } else {
            one.push_back(got->first);
            whole.push_back(got->second);
          }
        }
        auto first = syntax.copy(*call);
        first->args = syntax.list(one);
        auto second = syntax.copy(*call);
        second->args = syntax.list(whole);
        return std::pair{ first, second };
      }
      if (auto found = marked(node)) {
        auto [written, levels, each] = *found;
        auto value = lower_expr(builder, written, nullptr);
        auto held = walkable(value, levels, written->span);
        if (held == nullptr)
          return std::nullopt;
        auto name = each_name();
        walked.push_back({ name, value, held, levels });
        const ast::Expr* at = name_ref(syntax, written->span, name);
        const ast::Expr* marks = at;
        for (std::int64_t level = 0; level < levels; ++level) {
          auto around = syntax.make<ast::Each>(written->span);
          around->operand = marks;
          marks = around;
        }
        auto first = each(syntax, at);
        return std::pair{ first, each(syntax, marks) };
      }
      if (is_literal(node))
        return std::pair{ node, node };
      auto value = lower_expr(builder, node, nullptr);
      auto held = value_type_of(value);
      if (held == ir::error_type())
        return std::nullopt;
      if (auto array = ir::as<ir::ArrayType>(held); spreads && array != nullptr) {
        auto name = each_name();
        walked.push_back({ name, value, array, 0 });
        const ast::Expr* at = name_ref(syntax, node->span, name);
        return std::pair{ at, at };
      }
      auto named = hoist(value, node->span);
      return std::pair{ named, named };
    };

    auto got = walk(expr->indices[0], true);
    if (! got) {
      pick_walked = {};
      return false;
    }
    auto [one, whole] = *got;
    // A mask a place at a time is one along the outermost dimension, which is
    // every element of an array of one and the rows of one of more.
    bool along = std::ranges::all_of(walked, [](const Walked& w) {
      return w.held->rank() == 1 && (w.levels == 0 || w.levels == 1);
    });
    if (walked.empty() || ! along) {
      push_scope();
      ir::Value* mask;
      {
        Finally popped([this] { pop_scope(); });
        for (const auto& [name, value] : fixed)
          bind_hidden(builder, name, value, span);
        for (const auto& w : walked)
          bind_hidden(builder, w.name, w.value, span);
        mask = on_its_own(builder, whole);
      }
      pick_walked = std::tuple{ base, ty, Mask{ mask } };
      return false;
    }
    auto lengths = std::get<1>(shape_of(builder, base, ty, span));
    struct Read {
      std::string name;
      ir::Value* start;
      const ir::ArrayType* held;
      ir::Value* apart;
    };
    std::vector<Read> reads;
    for (const auto& w : walked) {
      if (w.held->shape[0] && ty->shape[0]) {
        if (*w.held->shape[0] != *ty->shape[0]) [[unlikely]] {
          diags.emit(D::LANG_MASK_WRONG_SHAPE, expr->indices[0]->span,
                     { { "found", w.held->written() }, { "wanted", ty->written() } });
          pick_walked = {};
          return false;
        }
      } else {
        auto told = std::get<1>(shape_of(builder, w.value, w.held, span));
        auto same_length = builder.compare(ir::CmpPred::eq, told[0], lengths[0], span);
        builder.check(same_length, "a mask whose shape is not the array's", target::statuses::OUT_OF_RANGE, span);
      }
      auto start = std::get<0>(shape_of(builder, w.value, w.held, span));
      auto apart = strides_of(builder, w.value, w.held, span)[0];
      reads.push_back({ w.name, start, w.held, apart });
    }
    auto written = expr->indices[0];

    // Whether the mask picks what is at `place`.
    auto at_place = [this, fixed, reads, one, written, ty, span](ir::Builder& b, ir::Value* place) {
      push_scope();
      ir::Value* picked;
      {
        Finally popped([this] { pop_scope(); });
        for (const auto& [name, value] : fixed)
          bind_hidden(b, name, value, span);
        for (const auto& read : reads) {
          auto at = b.binary(ir::BinOp::wrap_mul, place, read.apart, span);
          bind_element(b, read.name, read.start, read.held->element, at, span);
        }
        picked = lower_expr(b, one, ir::bool_type());
      }
      auto found = value_type_of(picked);
      if (! ir::same(found, ir::bool_type()) && ! ir::same(found, ir::error_type())) [[unlikely]]
        diags.emit(D::LANG_MASK_WRONG_SHAPE, written->span, { { "found", found->written() }, { "wanted", ty->written() } });
      return picked;
    };

    pick_walked = std::tuple{ base, ty, Mask{ std::function<ir::Value*(ir::Builder&, ir::Value*)>(at_place) } };
    return true;
  }

  std::optional<Iteration> Checker::over_a_value(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    if (auto array = ir::as<ir::ArrayType>(ty))
      return over_an_array(builder, value, array, span);
    if (auto list = ir::as<ir::ListType>(ty))
      return over_a_list(builder, value, list, span);
    if (ty == ir::str_type())
      return over_a_string(builder, value, span);
    if (ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty))
      return over_a_table(builder, value, ty, span);
    if (auto iter = ir::as<ir::IterType>(ty); iter != nullptr && iter->element != nullptr)
      return over_an_iterator(builder, value, iter, span);
    auto ready = syntax.make<ast::Ready>(span);
    ready->value = value;
    auto call = each_call(builder, ready, ty, span);
    if (call.is_refused())
      return std::nullopt;
    if (auto written = call.get<ast::Call>()) {
      // A type of the program's own walked by its `each`: the walk is that
      // generator's call, handed the value.
      auto generator = generator_named(written->callee);
      contract_assert(generator != nullptr);
      auto element = generator->element == ir::error_type() ? nullptr : generator->element;
      auto walk = lower_expr(builder, written, ir::iter_type(element));
      auto walked = ir::as<ir::IterType>(value_type_of(walk));
      if (walked == nullptr || walked->element == nullptr)
        return std::nullopt;
      return over_an_iterator(builder, walk, walked, span);
    }
    auto said = diags.emit(D::LANG_LOOP_NOT_AN_ITERATOR, span, { { "found", ty->written() } });
    if (auto written = each_written(ty, span))
      said.note(D::LANG_EACH_NOT_A_GENERATOR, *written, { { "type", ty->written() } });
    return std::nullopt;
  }

  std::optional<Span> Checker::each_written(const ir::Type* ty, Span span)
  {
    auto pointer = ir::as<ir::PtrType>(ty);
    auto named = pointer != nullptr ? pointer->pointee : ty;
    auto name = defined_name(named);
    if (name.empty())
      return std::nullopt;
    auto at = syntax.make<ast::Member>(span);
    at->base = name_ref(syntax, span, name);
    at->name = syntax.text(sema::each_name);
    at->name_span = span;
    auto path = path_to_member(named, sema::each_name, at);
    auto found = path != nullptr ? member_named(path) : Held{};
    if (found.empty() || found.get<Generator>() != nullptr)
      return std::nullopt;
    if (auto generic = found.get<Generic>())
      return generic->node->name_span;
    if (auto func = found.get<ir::Function>())
      return func->name_span;
    return span;
  }

  const ast::Expr* Checker::walking_each(const ir::Type* ty, Span span)
  {
    if (auto pointer = ir::as<ir::PtrType>(ty))
      ty = pointer->pointee;
    auto name = defined_name(ty);
    if (name.empty())
      return nullptr;
    auto at = syntax.make<ast::Member>(span);
    at->base = name_ref(syntax, span, name);
    at->name = syntax.text(sema::each_name);
    at->name_span = span;
    auto path = path_to_member(ty, sema::each_name, at);
    if (path == nullptr || member_named(path).get<Generator>() == nullptr)
      return nullptr;
    return path;
  }

  const ir::Type* Checker::said_to_be(const ast::Expr* expr)
  {
    if (auto found = hint_of(expr))
      return found;
    if (auto member = ast::as<ast::Member>(expr)) {
      auto held = said_to_be(member->base);
      if (auto pointer = ir::as<ir::PtrType>(held))
        held = pointer->pointee;
      if (auto record = ir::as<ir::ProductType>(held)) {
        auto at = field_index(record, member->name);
        return at ? record->fields[*at].second : nullptr;
      }
    } else if (auto element = ast::as<ast::Element>(expr)) {
      if (std::ranges::none_of(element->indices, [](const ast::Expr* one) { return ast::is<ast::Range>(one); })) {
        auto base = said_to_be(element->base);
        auto held = ir::as<ir::ArrayType>(base);
        if (held != nullptr && element->indices.size() == held->rank())
          return held->element;
        if (auto list = ir::as<ir::ListType>(base); list != nullptr && element->indices.size() == 1)
          return list->element;
      }
    }
    return nullptr;
  }

  bool Checker::through_a_list(const ast::Expr* expr)
  {
    while (ast::is<ast::Member>(expr) || ast::is<ast::Element>(expr)) {
      if (auto element = ast::as<ast::Element>(expr)) {
        if (ir::is<ir::ListType>(said_to_be(element->base)))
          return true;
        expr = element->base;
      } else
        expr = ast::as<ast::Member>(expr)->base;
    }
    return false;
  }

  Held Checker::each_call(ir::Builder& builder, const ast::Expr* base, const ir::Type* ty, Span span)
  {
    if (walking_each(ty, span) == nullptr)
      return {};
    auto callee = syntax.make<ast::Member>(span);
    callee->base = base;
    callee->name = syntax.text(sema::each_name);
    callee->name_span = span;
    auto call = syntax.make<ast::Call>(span);
    call->callee = callee;
    auto through = through_a_value(builder, call);
    if (through.is_refused())
      return Held(Refused{});
    if (auto found = through.get<ast::Call>(); found != nullptr && generator_named(found->callee) != nullptr)
      return through;
    return {};
  }

  Iterable* Checker::each_walk(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    auto ready = syntax.make<ast::Ready>(span);
    ready->value = value;
    auto call = each_call(builder, ready, ty, span).get<ast::Call>();
    if (call == nullptr)
      return nullptr;
    Picked walked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = call;
      pick_walked = {};
      lower_expr(builder, call, nullptr);
      walked = pick_walked;
    }
    auto found = std::get_if<Iterable*>(&walked);
    return found != nullptr ? *found : nullptr;
  }

  const ast::Expr* Checker::called_each(ir::Builder& builder, const ast::Expr* written)
  {
    auto allocated = ast::as<ast::Allocated>(written);
    auto inner = allocated != nullptr ? allocated->value : written;
    if (ast::is<ast::Range>(inner) || ast::is<ast::ArrayLit>(inner) || ast::is<ast::ListLit>(inner))
      return written;
    auto said = said_to_be(inner);
    auto call = each_call(builder, inner, said, inner->span).get<ast::Call>();
    if (call == nullptr)
      return written;
    if (allocated != nullptr) {
      auto made = syntax.copy(*allocated);
      made->value = call;
      return made;
    }
    return call;
  }

  const ir::Type* Checker::picked_element(const ir::ArrayType* ty, const Mask& mask)
  {
    auto value = std::get_if<ir::Value*>(&mask);
    auto held = value != nullptr ? ir::as<ir::ArrayType>(value_type_of(*value)) : nullptr;
    std::size_t depth = held != nullptr ? held->rank() : 1;
    return depth == ty->rank() ? ty->element : row_type(ty, std::int64_t(depth));
  }

  void Checker::lower_iterator_definition(ir::Builder& builder, const ast::VarDef* node, const ir::IterType* declared)
  {
    ir::Value* value;
    Picked walked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = node->value;
      pick_walked = {};
      initializing = std::string(node->name);
      Finally reset([this] { initializing = std::nullopt; });
      value = lower_expr(builder, node->value, nullptr);
      walked = pick_walked;
    }
    if (std::holds_alternative<std::monostate>(walked)) {
      auto ty = value_type_of(value);
      if (ty == ir::error_type()) [[unlikely]] {
        bind_local(node->name, undef(module, ir::error_type()), node->name_span, false, node->span);
        return;
      }
      if (auto walk = each_walk(builder, value, ty, node->value->span)) {
        bind_iterator(builder, node, Picked{ walk }, declared);
        return;
      }
      auto found = ! ir::is<ir::TupleType>(ty) ? over_a_value(builder, value, ty, node->value->span) : std::nullopt;
      if (! found) [[unlikely]] {
        if (ir::is<ir::TupleType>(ty))
          diags.emit(D::LANG_LOOP_NOT_AN_ITERATOR, node->value->span, { { "found", ty->written() } });
        bind_local(node->name, undef(module, ir::error_type()), node->name_span, false, node->span);
        return;
      }
      auto made = make<Iterable>();
      made->element = found->element;
      made->written = node->value;
      made->base = value;
      made->ty = ty;
      walked = made;
    }
    bind_iterator(builder, node, walked, declared);
  }

  void Checker::lower_array_definition(ir::Builder& builder, const ast::VarDef* node, const ast::ArrayTypeRef* written)
  {
    ir::Value* value;
    Picked walked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = node->value;
      pick_walked = {};
      initializing = std::string(node->name);
      Finally reset([this] { initializing = std::nullopt; });
      value = lower_expr(builder, node->value, nullptr);
      walked = pick_walked;
    }
    if (! std::holds_alternative<std::monostate>(walked)) {
      Iterable* walk;
      if (auto pick = std::get_if<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(&walked)) {
        const auto& [base, ty, mask] = *pick;
        walk = make<Iterable>();
        walk->element = picked_element(ty, mask);
        walk->written = node->value;
        walk->base = base;
        walk->ty = ty;
        walk->mask = mask;
      } else
        walk = std::get<Iterable*>(walked);
      std::string_view name = node->name;
      if (auto ref = ast::as<ast::NameRef>(node->value))
        name = ref->name;
      else if (auto call = ast::as<ast::Call>(node->value); call != nullptr && ast::is<ast::NameRef>(call->callee))
        name = static_cast<const ast::NameRef*>(call->callee)->name;
      value = built_from(builder, name, walk, node->value->span, nullptr);
    }
    auto found = value_type_of(value);
    if (found == ir::error_type()) [[unlikely]] {
      bind_local(node->name, undef(module, ir::error_type()), node->name_span, node->mutable_, node->span);
      return;
    }
    std::vector<KnownNumber> asked;
    for (auto one : written->shape)
      asked.push_back(one == nullptr ? KnownNumber{} : constant_number(one));
    // Python compares `False` with a dimension as the nought it is.
    auto agrees = [](const KnownNumber& one, std::optional<std::uint64_t> along) {
      if (! one.refused && ! one.value)
        return true;
      if (! along)
        return false;
      return one.refused ? *along == 0 : *one.value == __int128(*along);
    };
    auto array = ir::as<ir::ArrayType>(found);
    bool fits = array != nullptr && ((written->shape.size() == 1 && written->shape[0] == nullptr)
                                     || (asked.size() == array->rank() && [&] {
                                           for (std::size_t at = 0; at < asked.size(); ++at)
                                             if (! agrees(asked[at], array->shape[at]))
                                               return false;
                                           return true;
                                         }()));
    if (! fits) [[unlikely]] {
      std::string expected = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}";
      for (std::size_t at = 0; at < asked.size(); ++at) {
        if (at > 0)
          expected += ",";
        if (asked[at].refused)
          expected += "False";
        else if (asked[at].value)
          expected += ir::decimal(*asked[at].value);
      }
      expected += "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}";
      diags.emit(D::LANG_TYPE_INITIALIZER_MISMATCH, node->value->span,
                 { { "name", node->name }, { "expected", expected }, { "found", found->written() } });
      bind_local(node->name, undef(module, ir::error_type()), node->name_span, node->mutable_, node->span);
      return;
    }
    value = owned_array(builder, node, value);
    kept_by_a_name(found, node->name_span);
    name_value(value, node->name, node->name_span);
    bind_local(node->name, value, node->name_span, node->mutable_, node->span, false, &builder);
  }

  ir::Value* Checker::owned_array(ir::Builder& builder, const ast::VarDef* node, ir::Value* value)
  {
    auto ty = ir::as<ir::ArrayType>(value_type_of(value));
    if (ty == nullptr
        || (! names_a_place(node->value) && ! ast::is<ast::If>(node->value) && ! ast::is<ast::Match>(node->value)))
      return value;
    bool writable = may_write(node->value);
    if (! ty->fixed()) {
      if (auto list = list_views.get(id_of(node->value)); node->mutable_ && list != nullptr) [[unlikely]] {
        diags.emit(D::LANG_LIST_VIEW_WRITTEN, node->value->span, { { "found", (*list)->written() } });
        return undef(module, ir::error_type());
      }
      if (node->mutable_ && ! writable) [[unlikely]] {
        if (! said_sealed(node->value, node->value->span))
          diags.emit(D::LANG_ARRAY_VIEW_NOT_WRITABLE, node->value->span, { { "name", node->name } });
        return undef(module, ir::error_type());
      }
      return value;
    }
    if (! node->mutable_ && ! writable)
      return value;
    if (auto why = not_copied_because(ty->element)) {
      if (node->mutable_) [[unlikely]] {
        diags.emit(D::LANG_ARRAY_NOT_COPIED, node->value->span, { { "type", ty->element->written() }, { "why", *why } });
        return undef(module, ir::error_type());
      }
      return value;
    }
    auto place = builder.frame(ty, node->span);
    auto room = builder.cast(ir::CastKind::bitcast, place, ty, node->span);
    copy_elements(builder, room, ty, value, ty, node->span);
    return room;
  }

  void Checker::bind_iterator(ir::Builder& builder, const ast::VarDef* node, const Picked& walked,
                              const ir::IterType* declared)
  {
    Iterable* found = make<Iterable>();
    if (auto walk = std::get_if<Iterable*>(&walked)) {
      // Another name for a walk already described, or a value walked as it
      // is: the same walk, lending what that one lends -- or, for a value
      // named here for the first time, what the value reads.
      *found = **walk;
      if (found->lent.empty())
        found->lent = names_read(node->value);
    } else {
      const auto& [base, ty, mask] = std::get<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(walked);
      found->element = picked_element(ty, mask);
      found->written = node->value;
      found->base = base;
      found->ty = ty;
      found->mask = mask;
      found->lent = names_read(node->value);
    }
    if (declared != nullptr && declared->element != nullptr && ! same_or_none(declared->element, found->element))
      [[unlikely]] {
      diags.emit(D::LANG_TYPE_INITIALIZER_MISMATCH, node->value->span,
                 { { "name", node->name },
                   { "expected", declared->written() },
                   { "found", ir::iter_type(found->element)->written() } });
      bind_local(node->name, undef(module, ir::error_type()), node->name_span, false, node->span);
      return;
    }
    auto ty = ir::iter_type(found->element);
    bind_local(node->name, undef(module, ty), node->name_span, false, node->span, false, &builder);
    auto held = scopes.back().get(std::string(node->name));
    if (held == nullptr)
      return;
    auto local = *held;
    local->iterable = found;
    auto depth = std::int64_t(scopes.size()) - 1;
    for (const auto& name : found->lent)
      iterator_loans.emplace_back(name, std::string(node->name), node->span, depth);
    report_iterator(node->name,
                    std::format("'{}' is {}: a walk described and not made -- nothing is built, and every walk of it "
                                "starts from the start",
                                node->name, ty->written()),
                    node->span);
    if (! found->lent.empty()) {
      std::string lent;
      for (const auto& one : found->lent) {
        if (! lent.empty())
          lent += ", ";
        lent += std::format("'{}'", one);
      }
      report_iterator(node->name,
                      std::format("'{}' borrows {} while it is in scope: writing {} meanwhile is refused, so that every "
                                  "walk reads what the first would have",
                                  node->name, lent, found->lent.size() == 1 ? "it" : "them"),
                      node->span);
    }
  }

  std::vector<std::string> Checker::names_read(const ast::Expr* expr)
  {
    std::vector<std::string> found;
    auto visit = [&](this auto const& self, const ast::Node* node) -> void {
      if (auto ref = ast::as<ast::NameRef>(node)) {
        auto local = find_local(ref->name);
        const ir::Type* held = local != nullptr ? held_by(local) : nullptr;
        if (held == nullptr) {
          auto top_held = provided(ref->name).get<ir::GlobalVar>();
          held = top_held != nullptr ? top_held->value_type : nullptr;
        }
        if ((ir::is<ir::ArrayType>(held) || ir::is<ir::ListType>(held) || ir::is<ir::SetType>(held)
             || ir::is<ir::DictType>(held))
            && std::ranges::find(found, ref->name) == found.end())
          found.emplace_back(ref->name);
        return;
      }
      ast::for_each_child(node, [&](const ast::Node* one) { self(one); });
    };
    visit(expr);
    return found;
  }

  void Checker::report_iterator(std::string_view name, std::string_view reason, Span span)
  {
    module.record("iterator", std::string(name), std::string(reason), span);
  }

  bool Checker::lent_to_an_iterator(const ast::Expr* expr, Span where)
  {
    auto root = expr;
    while (true)
      if (auto member = ast::as<ast::Member>(root))
        root = member->base;
      else if (auto element = ast::as<ast::Element>(root))
        root = element->base;
      else
        break;
    auto ref = ast::as<ast::NameRef>(root);
    if (ref == nullptr)
      return false;
    for (auto it = iterator_loans.rbegin(); it != iterator_loans.rend(); ++it) {
      const auto& [name, iterator, span, depth] = *it;
      if (name == ref->name) {
        diags.emit(D::LANG_ITERATOR_LENDS, where, { { "name", name }, { "iterator", iterator } })
          .note(D::LANG_ITERATOR_MADE_HERE, span, { { "iterator", iterator } });
        return true;
      }
    }
    return false;
  }

  ir::Value* Checker::iterator_named(ir::Builder& builder, const ast::NameRef* ref, Local* local,
                                     const ir::Type* expected)
  {
    local->read = true;
    auto found = local->iterable;
    contract_assert(found != nullptr);
    if (pick_in == ref) {
      pick_walked = found;
      return undef(module, ir::void_type());
    }
    if (auto wanted = ir::as<ir::IterType>(expected))
      return handed_on(builder, ref->name, found, wanted, ref->span);
    return built_from(builder, ref->name, found, ref->span, expected);
  }

  std::optional<Iteration> Checker::walk_of(ir::Builder& builder, Iterable* found, Span span)
  {
    if (found->generator != nullptr) {
      auto ty = ir::iter_type(found->element);
      auto value = generator_described(builder, found->generator->node->name, found, ty, span);
      if (value_type_of(value) == ir::error_type())
        return std::nullopt;
      return over_an_iterator(builder, value, ty, span);
    }
    if (! std::holds_alternative<std::monostate>(found->mask))
      return over_a_pick(builder, static_cast<const ast::Element*>(found->written), found->base,
                         ir::as<ir::ArrayType>(found->ty), found->mask);
    return over_a_value(builder, found->base, found->ty, span);
  }

  ir::Value* Checker::built_from(ir::Builder& builder, std::string_view name, Iterable* found, Span span,
                                 const ir::Type* expected)
  {
    if (std::holds_alternative<std::monostate>(found->mask)) {
      if (ir::is<ir::IterType>(found->ty) || found->generator != nullptr) [[unlikely]] {
        diags.emit(D::LANG_ITERATOR_NOT_AN_ARRAY, span, { { "name", name } });
        return undef(module, ir::error_type());
      }
      if (! accepts(expected, found->ty)) [[unlikely]] {
        report_mismatch(span, found->ty, expected);
        return undef(module, ir::error_type());
      }
      return found->base;
    }
    auto ty = ir::as<ir::ArrayType>(found->ty);
    auto written = ast::as<ast::Element>(found->written);
    contract_assert(ty != nullptr && written != nullptr);
    auto mask = mask_built(builder, found);
    if (mask == nullptr)
      return undef(module, ir::error_type());
    auto here = syntax.copy(*written);
    here->span = span;
    auto made = lower_picked(builder, here, found->base, ty, mask, expected);
    if (value_type_of(made) != ir::error_type())
      report_iterator(name,
                      std::format("'{}' is built into an array here, in this call's room: an array was asked for, and "
                                  "the walk gives one element at a time",
                                  name),
                      span);
    return made;
  }

  ir::Value* Checker::mask_built(ir::Builder& builder, Iterable* found)
  {
    if (auto value = std::get_if<ir::Value*>(&found->mask))
      return *value;
    auto ty = ir::as<ir::ArrayType>(found->ty);
    auto written = ast::as<ast::Element>(found->written);
    contract_assert(ty != nullptr && written != nullptr);
    auto along = ty->shape[0];
    if (! along) [[unlikely]] {
      diags.emit(D::LANG_MASK_NEEDS_A_STATED_SHAPE, written->base->span, { { "found", ty->written() } });
      return nullptr;
    }
    auto held = ir::array_type(ir::bool_type(), { *along });
    auto place = builder.frame(held, written->span);
    auto marks = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(ir::bool_type(), true), written->span);
    const auto& asked = std::get<std::function<ir::Value*(ir::Builder&, ir::Value*)>>(found->mask);
    for (std::uint64_t at = 0; at < *along; ++at) {
      auto where = element_place(builder, marks, ir::bool_type(), builder.int_const(ir::u64(), at), written->span);
      auto picked = asked(builder, builder.int_const(ir::u64(), at));
      builder.store(where, picked, written->span);
    }
    return builder.cast(ir::CastKind::bitcast, place, held, written->span);
  }


  // -- handing an iterator on --------------------------------------------------

  std::vector<ir::Value*> Checker::words_of(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr && array->fixed())
      return { builder.cast(ir::CastKind::bitcast, value, ir::ptr_type(array->element, true), span) };
    if (auto result = ir::as<ir::ResultType>(ty)) {
      // Taken apart by what a result is made of rather than by place: an
      // answer may be several words itself, and the truth value is after all
      // of them.
      std::vector<ir::Value*> found;
      found.push_back(builder.unwrap(value, result->ok, span));
      found.push_back(builder.failed(value, span));
      if (result->err != nullptr)
        found.push_back(builder.error(value, result->err, span));
      return found;
    }
    auto parts = ir::parts_of(ty);
    if (parts.size() == 1) {
      auto inner = only_member(ty);
      if (inner == nullptr)
        return { value };
      // A record of one field is that field's word, and not the record.
      return words_of(builder, builder.extract(value, 0, inner, span), inner, span);
    }
    std::vector<ir::Value*> found;
    for (std::size_t at = 0; at < parts.size(); ++at)
      found.push_back(builder.extract(value, std::int64_t(at), parts[at], span));
    return found;
  }

  std::vector<const ir::Type*> Checker::word_types(const ir::Type* ty)
  {
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr && array->fixed())
      return { ir::ptr_type(array->element, true) };
    return ir::parts_of(ty);
  }

  ir::Value* Checker::from_words(ir::Builder& builder, std::span<ir::Value* const> words, const ir::Type* ty, Span span)
  {
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr && array->fixed())
      return builder.cast(ir::CastKind::bitcast, words[0], ty, span);
    if (auto result = ir::as<ir::ResultType>(ty))
      return builder.wrap(words[0], words[1], ty, span, result->err != nullptr ? words[2] : nullptr);
    if (words.size() == 1) {
      auto inner = only_member(ty);
      if (inner == nullptr)
        return words[0];
      return builder.make_tuple(std::vector{ from_words(builder, words, inner, span) }, ty, span);
    }
    return builder.make_tuple(words, ty, span);
  }

  ir::Value* Checker::handed_on(ir::Builder& builder, std::string_view name, Iterable* found,
                                const ir::IterType* wanted, Span span)
  {
    auto ty = ir::iter_type(found->element);
    if (wanted->element != nullptr && ! same_or_none(wanted->element, found->element)) [[unlikely]] {
      report_mismatch(span, ty, wanted);
      return undef(module, ir::error_type());
    }
    if (found->generator != nullptr)
      return generator_described(builder, name, found, wanted, span);
    bool picks = ! std::holds_alternative<std::monostate>(found->mask);
    if (! picks && ir::is<ir::IterType>(found->ty))
      // Handed in, and handed on as it came.
      return found->base;
    auto mask = picks ? mask_built(builder, found) : nullptr;
    if (picks && mask == nullptr)
      return undef(module, ir::error_type());
    std::vector<std::pair<ir::Value*, const ir::Type*>> held{ { found->base, found->ty } };
    if (mask != nullptr)
      held.emplace_back(mask, value_type_of(mask));
    std::vector<const ir::Type*> types;
    for (const auto& [_, kind] : held)
      for (auto one : word_types(kind))
        types.push_back(one);
    auto record = ir::tuple_type(types);
    auto offsets = ir::member_offsets_of(record, layout());
    auto place = builder.frame(record, span);
    std::vector<ir::Value*> words;
    for (const auto& [value, kind] : held)
      for (auto one : words_of(builder, value, kind, span))
        words.push_back(one);
    for (std::size_t at = 0; at < std::min({ words.size(), types.size(), offsets.size() }); ++at) {
      auto where = inside(builder, place, std::int64_t(offsets[at]), types[at], span);
      builder.store(where, words[at], span);
    }
    auto walk = make<Iterable>();
    walk->element = found->element;
    walk->written = found->written;
    walk->base = found->base;
    walk->ty = found->ty;
    if (mask != nullptr)
      walk->mask = mask;
    auto start = walk_of(builder, walk, span);
    if (! start)
      return undef(module, ir::error_type());
    if (start->start.size() != 1 || value_type_of(start->start[0]) != ir::u64()) [[unlikely]] {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, span,
                 { { "feature", std::format("handing on a walk of '{}'", found->ty->written()) } });
      return undef(module, ir::error_type());
    }
    std::vector<const ir::Type*> kinds;
    for (const auto& [_, kind] : held)
      kinds.push_back(kind);
    auto step = next_function(found, kinds, span);
    report_iterator(name,
                    std::format("'{}' is handed on as {}: what it walks is described in this call's room, and the "
                                "callee takes each turn through '{}', a call per turn",
                                name, ty->written(), step->name),
                    span);
    return builder.make_tuple(std::vector{ builder.code_address(step, span),
                                           builder.cast(ir::CastKind::bitcast, place, environment_type(), span),
                                           start->start[0] },
                              ty, span);
  }

  ir::Value* Checker::as_an_iterator(ir::Builder& builder, const ast::Expr* expr, const ir::IterType* wanted, Span span)
  {
    ir::Value* value;
    Picked walked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = expr;
      pick_walked = {};
      value = lower_expr(builder, expr, nullptr);
      walked = pick_walked;
    }
    std::string_view name = "what is written here";
    if (auto ref = ast::as<ast::NameRef>(expr))
      name = ref->name;
    else if (auto call = ast::as<ast::Call>(expr)) {
      if (auto callee = ast::as<ast::NameRef>(call->callee))
        name = callee->name;
      else if (auto member = ast::as<ast::Member>(call->callee))
        name = member->name;
    }
    Iterable* walk = nullptr;
    if (std::holds_alternative<std::monostate>(walked)) {
      auto ty = value_type_of(value);
      if (ty == ir::error_type()) [[unlikely]]
        return undef(module, ir::error_type());
      if (auto iter = ir::as<ir::IterType>(ty)) {
        if (wanted->element != nullptr && ! same_or_none(iter->element, wanted->element)) [[unlikely]] {
          report_mismatch(span, ty, wanted);
          return undef(module, ir::error_type());
        }
        return value;
      }
      if (ir::is<ir::TupleType>(ty)) [[unlikely]] {
        report_mismatch(span, ty, wanted);
        return undef(module, ir::error_type());
      }
      walk = each_walk(builder, value, ty, span);
      if (walk != nullptr && walk->generator != nullptr)
        return handed_on(builder, walk->generator->node->name, walk, wanted, span);
      if (! ir::is<ir::ArrayType>(ty) && ! ir::is<ir::ListType>(ty) && ! ir::is<ir::SetType>(ty)
          && ! ir::is<ir::DictType>(ty) && ty != ir::str_type()) [[unlikely]] {
        report_mismatch(span, ty, wanted);
        return undef(module, ir::error_type());
      }
      auto found = over_a_value(builder, value, ty, span);
      if (! found)
        return undef(module, ir::error_type());
      walk = make<Iterable>();
      walk->element = found->element;
      walk->written = expr;
      walk->base = value;
      walk->ty = ty;
    } else if (auto pick = std::get_if<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(&walked)) {
      const auto& [base, ty, mask] = *pick;
      walk = make<Iterable>();
      walk->element = picked_element(ty, mask);
      walk->written = expr;
      walk->base = base;
      walk->ty = ty;
      walk->mask = mask;
    } else
      walk = std::get<Iterable*>(walked);
    return handed_on(builder, name, walk, wanted, span);
  }

  void Checker::words_into(ir::Builder& builder, ir::Value* place, ir::Value* value, const ir::Type* ty, Span span)
  {
    auto types = word_types(ty);
    auto record = ir::tuple_type(types);
    auto words = words_of(builder, value, ty, span);
    auto offsets = ir::member_offsets_of(record, layout());
    for (std::size_t at = 0; at < std::min({ words.size(), types.size(), offsets.size() }); ++at) {
      auto where = inside(builder, place, std::int64_t(offsets[at]), types[at], span);
      builder.store(where, words[at], span);
    }
  }

  ir::Value* Checker::words_from(ir::Builder& builder, ir::Value* place, const ir::Type* ty, Span span)
  {
    auto types = word_types(ty);
    auto record = ir::tuple_type(types);
    auto offsets = ir::member_offsets_of(record, layout());
    std::vector<ir::Value*> words;
    for (std::size_t at = 0; at < std::min(types.size(), offsets.size()); ++at)
      words.push_back(builder.load(inside(builder, place, std::int64_t(offsets[at]), types[at], span), span));
    return from_words(builder, words, ty, span);
  }


  // -- generators -------------------------------------------------------------

  namespace {

    // A function the checker writes to take one turn of a walk: handed where
    // the description is, where the walk's state is, and where to write the
    // turn, and answering whether there was one.
    ir::Function* turn_function(Checker& checker, const std::string& name, Span span)
    {
      auto slot = ir::ptr_type(ir::u64(), true);
      auto made = std::make_unique<ir::Function>(
        name, ir::func_type({ environment_type(), slot, environment_type() }, ir::bool_type()));
      made->attrs.impure = true;
      made->attrs.chained = true;
      made->linkage = ir::Linkage::internal;
      made->span = span;
      made->name_span = span;
      made->source_path = pure_path(checker.path).generic_string();
      made->param_names = { "", "", "" };
      auto func = checker.module.add_function(std::move(made), checker.key(name));
      checker.owned.emplace_back(static_cast<ir::Value*>(func));
      func->add_block();
      return func;
    }

  } // namespace

  std::vector<const ir::Type*> Checker::record_types(Generator* found)
  {
    std::vector<const ir::Type*> types = found->params;
    if (found->given != nullptr)
      types.push_back(found->given);
    if (found->finishes != nullptr)
      types.push_back(found->finishes);
    return types;
  }

  ir::Value* Checker::given_place(ir::Builder& builder, ir::Value* args, Generator* found, Span span)
  {
    contract_assert(found->given != nullptr);
    auto [record, first] = argument_record(record_types(found));
    auto at = first.size() - (found->finishes != nullptr ? 2 : 1);
    return inside(builder, args, std::int64_t(ir::member_offsets_of(record, layout())[first[at]]), ir::u8(), span);
  }

  ir::Value* Checker::finish_place(ir::Builder& builder, ir::Value* args, Generator* found, Span span)
  {
    contract_assert(found->finishes != nullptr);
    auto [record, first] = argument_record(record_types(found));
    return inside(builder, args, std::int64_t(ir::member_offsets_of(record, layout())[first.back()]), ir::u8(), span);
  }

  std::tuple<const ir::TupleType*, std::vector<std::int64_t>> Checker::argument_record(
    std::span<const ir::Type* const> params)
  {
    std::vector<const ir::Type*> words;
    std::vector<std::int64_t> first;
    for (auto ty : params) {
      first.push_back(std::int64_t(words.size()));
      for (auto one : word_types(ty))
        words.push_back(one);
    }
    return { ir::tuple_type(std::move(words)), std::move(first) };
  }

  std::vector<ir::Value*> Checker::arguments_from(ir::Builder& builder, ir::Value* place,
                                                  std::span<const ir::Type* const> params, Span span)
  {
    auto [record, first] = argument_record(params);
    auto offsets = ir::member_offsets_of(record, layout());
    std::vector<ir::Value*> found;
    for (std::size_t which = 0; which < std::min(params.size(), first.size()); ++which) {
      auto kinds = word_types(params[which]);
      std::vector<ir::Value*> words;
      for (std::size_t one = 0; one < kinds.size(); ++one)
        words.push_back(builder.load(
          inside(builder, place, std::int64_t(offsets[std::size_t(first[which]) + one]), kinds[one], span), span));
      found.push_back(from_words(builder, words, params[which], span));
    }
    return found;
  }

  void Checker::arguments_into(ir::Builder& builder, ir::Value* place, std::span<const ir::Type* const> params,
                               std::span<ir::Value* const> values, Span span)
  {
    auto [record, first] = argument_record(params);
    auto offsets = ir::member_offsets_of(record, layout());
    for (std::size_t which = 0; which < std::min({ params.size(), first.size(), values.size() }); ++which) {
      auto words = words_of(builder, values[which], params[which], span);
      auto kinds = word_types(params[which]);
      for (std::size_t one = 0; one < std::min(words.size(), kinds.size()); ++one) {
        auto where = inside(builder, place, std::int64_t(offsets[std::size_t(first[which]) + one]), kinds[one], span);
        builder.store(where, words[one], span);
      }
    }
  }

  void Checker::lower_yield(ir::Builder& builder, const ast::Yield* stmt, ir::Function* func)
  {
    auto found = generating;
    if (found == nullptr || generating_out == nullptr) [[unlikely]] {
      diags.emit(D::LANG_YIELD_OUTSIDE_A_GENERATOR, stmt->span);
      return;
    }
    if (stmt->delegates && ! members.empty()) {
      auto walked = syntax.copy(*stmt);
      walked->value = called_each(builder, stmt->value);
      stmt = walked;
    }
    if (stmt->delegates && delegated(builder, stmt))
      return;
    if (stmt->delegates) {
      auto name = each_name();
      auto yield = syntax.make<ast::Yield>(stmt->span);
      yield->value = name_ref(syntax, stmt->span, name);
      auto loop = syntax.make<ast::ForEach>(stmt->span);
      loop->name = syntax.text(name);
      loop->name_span = stmt->span;
      loop->iterable = stmt->value;
      loop->body = block_of(syntax, stmt->span, { yield });
      auto written = syntax.make<ast::ExprStmt>(stmt->span);
      written->value = loop;
      lower_stmt(builder, written, func, false);
      return;
    }
    auto value = lower_into(builder, stmt->value, found->element, stmt->span);
    if (value_type_of(value) == ir::error_type())
      return;
    words_into(builder, generating_out, value, found->element, stmt->span);
    stopped(builder, false, stmt->span);
  }

  ir::Value* Checker::lower_yield_value(ir::Builder& builder, const ast::YieldValue* expr, const ir::Type* expected)
  {
    auto found = generating;
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_YIELD_OUTSIDE_A_GENERATOR, expr->span);
      return undef(module, ir::error_type());
    }
    if (expr->delegates)
      return lower_delegated_value(builder, expr, expected);
    if (found->given == nullptr || generating_args == nullptr) [[unlikely]] {
      diags.emit(D::LANG_YIELD_TAKES_NOTHING, expr->span, { { "name", found->node->name } });
      return undef(module, ir::error_type());
    }
    auto func = std::get<1>(demanding);
    contract_assert(func != nullptr);
    auto yield = syntax.make<ast::Yield>(expr->span);
    yield->value = expr->value;
    lower_yield(builder, yield, func);
    if (builder.is_terminated())
      return undef(module, ir::error_type());
    auto place = given_place(builder, generating_args, found, expr->span);
    auto given = words_from(builder, place, found->given, expr->span);
    if (! accepts(expected, found->given)) [[unlikely]] {
      report_mismatch(expr->span, found->given, expected);
      return undef(module, ir::error_type());
    }
    return given;
  }

  ir::Value* Checker::lower_delegated_value(ir::Builder& builder, const ast::YieldValue* expr,
                                            const ir::Type* expected)
  {
    decltype(last_delegation) done;
    {
      Kept kept(last_delegation);
      last_delegation = std::nullopt;
      auto stmt = syntax.make<ast::Yield>(expr->span);
      stmt->value = expr->value;
      stmt->delegates = true;
      if (! delegated(builder, stmt)) [[unlikely]] {
        diags.emit(D::LANG_DELEGATED_VALUE_NOT_A_CALL, expr->value->span);
        return undef(module, ir::error_type());
      }
      done = last_delegation;
    }
    if (! done || builder.is_terminated())
      return undef(module, ir::error_type());
    auto [found, arguments] = *done;
    if (found->finishes == nullptr) [[unlikely]] {
      diags.emit(D::LANG_DELEGATE_ANSWERS_NOTHING, expr->value->span, { { "name", found->node->name } });
      return undef(module, ir::error_type());
    }
    auto place = finish_place(builder, arguments, found, expr->span);
    auto answer = words_from(builder, place, found->finishes, expr->span);
    if (! accepts(expected, found->finishes)) [[unlikely]] {
      report_mismatch(expr->span, found->finishes, expected);
      return undef(module, ir::error_type());
    }
    return answer;
  }

  void Checker::stopped(ir::Builder& builder, bool delegates, Span span)
  {
    contract_assert(generating_out != nullptr);
    auto resumed = builder.new_block("resumed");
    auto told = builder.new_block("told");
    auto finishing = builder.new_block("finishing");
    builder.yield(told, span, delegates);
    builder.position_at(told);
    builder.condbr(is_nowhere(builder, generating_out, span), finishing, resumed, {}, {}, span);
    builder.position_at(finishing);
    returning(builder, builder.int_const(ir::u8(), 0), span);
    builder.position_at(resumed);
  }

  const ir::TupleType* Checker::delegation()
  {
    auto word = environment_type();
    return ir::tuple_type({ word, word, word, word, word });
  }

  bool Checker::delegated(ir::Builder& builder, const ast::Yield* stmt)
  {
    auto target = stmt->value;
    auto allocated = ast::as<ast::Allocated>(target);
    auto inner = ast::as<ast::Call>(allocated != nullptr ? allocated->value : target);
    if (inner == nullptr || generator_named(inner->callee) == nullptr)
      return false;
    Picked picked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = target;
      pick_walked = {};
      lower_expr(builder, target, nullptr);
      picked = pick_walked;
    }
    auto walked = std::get_if<Iterable*>(&picked);
    if (walked == nullptr || (*walked)->generator == nullptr)
      return true;
    auto walk = *walked;
    auto found = walk->generator;
    auto current = generating;
    contract_assert(current != nullptr && generating_frame != nullptr);
    if (! same_or_none(found->element, current->element) || ! same_or_none(found->given, current->given)) [[unlikely]] {
      // What the delegate hands out and takes back is what this body does:
      // the driver hands it what the walk is resumed with.
      report_mismatch(stmt->value->span, ir::running_type(found->element, found->given, false),
                      ir::running_type(current->element, current->given, false));
      return true;
    }
    if (walk->arena == nullptr && walks_itself(found)) [[unlikely]] {
      walks_itself_said(found, stmt->value->span);
      return true;
    }
    if (found->impure)
      an_effect(D::LANG_PURE_CALLS_IMPURE, stmt->value->span, Held(static_cast<const ast::Node*>(found->node)),
                { { "name", found->node->name } });
    auto span = stmt->span;
    auto [place, offsets] = description_made(builder, walk, span);
    contract_assert(found->func != nullptr);
    auto frame = inside(builder, place, offsets[4], ir::u8(), span);
    auto counter = builder.cast(ir::CastKind::bitcast, frame, ir::ptr_type(ir::u64(), true), span);
    builder.store(counter, builder.int_const(ir::u64(), 0), span);
    auto record = builder.frame(delegation(), span);
    auto offsets_of_record = ir::member_offsets_of(delegation(), layout());
    auto arguments = inside(builder, place, offsets[3], ir::u8(), span);
    auto nowhere = builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), environment_type(), span);
    std::vector<ir::Value*> words;
    words.push_back(builder.code_address(found->func, span));
    words.push_back(builder.cast(ir::CastKind::bitcast, frame, environment_type(), span));
    words.push_back(arguments);
    words.push_back(nowhere);
    words.push_back(found->given == nullptr ? nowhere : given_place(builder, arguments, found, span));
    for (std::size_t at = 0; at < words.size(); ++at) {
      auto where = inside(builder, record, std::int64_t(offsets_of_record[at]), environment_type(), span);
      auto word = builder.cast(ir::CastKind::bitcast, words[at], environment_type(), span);
      builder.store(where, word, span);
    }
    auto handed = inside(builder, generating_frame, 8, environment_type(), span);
    auto described = builder.cast(ir::CastKind::bitcast, record, environment_type(), span);
    builder.store(handed, described, span);
    report_iterator(found->node->name,
                    std::format("'{}' is handed the walk in one step: its turns are taken straight from its body, not "
                                "through '{}'",
                                found->node->name, current->node->name),
                    span);
    last_delegation = std::tuple{ found, arguments };
    stopped(builder, true, span);
    return true;
  }

  Generator* Checker::generator_named(const ast::Expr* callee)
  {
    if (auto member = ast::as<ast::Member>(callee)) {
      auto held = module_at(member->base);
      auto base = ast::as<ast::NameRef>(member->base);
      if (held != nullptr && ! (base != nullptr && find_local(base->name) != nullptr)) {
        auto found = held->exports.get(std::string(member->name));
        return found != nullptr ? found->get<Generator>() : nullptr;
      }
      // A generator inside a type, `T.each` or `m.T.each`.
      return member_named(callee).get<Generator>();
    }
    auto ref = ast::as<ast::NameRef>(callee);
    if (ref == nullptr || find_local(ref->name) != nullptr)
      return nullptr;
    auto found = top->get(std::string(ref->name));
    return found != nullptr ? found->get<Generator>() : nullptr;
  }

  std::optional<std::tuple<Generator*, std::vector<ir::Value*>>> Checker::generator_arguments(ir::Builder& builder,
                                                                                          const ast::Call* expr,
                                                                                          Generator* found)
  {
    std::string_view name = found->node->name;
    auto params = found->node->params;
    if (expr->args.size() < params.size()
        && std::all_of(params.begin() + std::ptrdiff_t(expr->args.size()), params.end(),
                       [](const ast::Param* one) { return could_be_an_arena(one->type); })) {
      // An arena left out is the one in force, as in any call: the arena of
      // the `in` around the call -- `g(…) in a` hands `a` -- or `⎕heap`.
      std::vector<const ast::Expr*> args(expr->args.begin(), expr->args.end());
      for (auto missing = expr->args.size(); missing < params.size(); ++missing) {
        auto ready = syntax.make<ast::Ready>(expr->span);
        ready->value = out_of(builder, expr->span);
        args.push_back(ready);
      }
      auto completed = syntax.copy(*expr);
      completed->args = syntax.list(args);
      expr = completed;
    }
    if (expr->args.size() != params.size()) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", name }, { "expected", params.size() }, { "found", expr->args.size() } });
      return std::nullopt;
    }
    for (auto arg : expr->args)
      if (ast::is<ast::Named>(arg) || ast::is<ast::Spread>(arg)) [[unlikely]] {
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, arg->span,
                   { { "feature", "a named or spread argument of a generator" } });
        return std::nullopt;
      }
    if (found->parameters.empty()) {
      std::vector<ir::Value*> values;
      for (std::size_t at = 0; at < std::min(expr->args.size(), found->params.size()); ++at) {
        auto value = lower_into(builder, expr->args[at], found->params[at], expr->args[at]->span);
        if (value_type_of(value) == ir::error_type())
          return std::nullopt;
        values.push_back(value);
      }
      return std::tuple{ found, std::move(values) };
    }
    Dict<std::string, const ir::Type*> given;
    std::vector<ir::Value*> lowered;
    for (std::size_t at = 0; at < std::min(expr->args.size(), params.size()); ++at) {
      auto arg = expr->args[at];
      auto param = params[at];
      const ir::Type* wanted;
      {
        auto scope = in_scope_of(Held(found));
        wanted = worked_out(param->type, given);
      }
      ir::Value* value;
      if (wanted != nullptr)
        value = lower_into(builder, arg, wanted, arg->span);
      else if (ast::is<ast::IterTypeRef>(param->type))
        value = lower_into(builder, arg, ir::iter_type(nullptr), arg->span);
      else
        value = lower_expr(builder, arg, nullptr);
      auto ty = value_type_of(value);
      if (ty == ir::error_type())
        return std::nullopt;
      if (wanted == nullptr) {
        auto scope = in_scope_of(Held(found));
        if (! reading(param->type, ty, given, arg->span, param->name))
          return std::nullopt;
      }
      lowered.push_back(value);
    }
    auto made = generator_instance(found, given, expr->span);
    if (made == nullptr)
      return std::nullopt;
    std::vector<ir::Value*> values;
    for (std::size_t at = 0; at < std::min({ lowered.size(), made->params.size(), expr->args.size() }); ++at)
      values.push_back(as_wanted(builder, lowered[at], made->params[at], expr->args[at]->span));
    if (std::ranges::any_of(values, [this](ir::Value* one) { return value_type_of(one) == ir::error_type(); }))
      return std::nullopt;
    return std::tuple{ made, std::move(values) };
  }

  ir::Value* Checker::lower_generator_call(ir::Builder& builder, const ast::Call* expr, Generator* found,
                                           const ir::Type* expected)
  {
    auto given = generator_arguments(builder, expr, found);
    if (! given)
      return undef(module, ir::error_type());
    auto [made, values] = std::move(*given);
    auto ty = ir::iter_type(made->element);
    auto walk = make<Iterable>();
    walk->element = made->element;
    walk->written = expr;
    walk->base = undef(module, ty);
    walk->ty = ty;
    walk->generator = made;
    walk->args = std::move(values);
    if (pick_in == expr) {
      pick_walked = walk;
      return undef(module, ir::void_type());
    }
    if (auto wanted = ir::as<ir::IterType>(expected))
      return handed_on(builder, made->node->name, walk, wanted, expr->span);
    diags.emit(D::LANG_ITERATOR_NOT_AN_ARRAY, expr->span, { { "name", made->node->name } });
    return undef(module, ir::error_type());
  }

  bool Checker::generator_reaches_itself(Generator* found)
  {
    KeySet seen;
    std::vector<Generator*> waiting{ found };
    bool first = true;
    while (! waiting.empty()) {
      auto one = waiting.back();
      waiting.pop_back();
      if (one == found && ! first)
        return true;
      first = false;
      if (seen.contains(id_of(one)))
        continue;
      seen.insert(id_of(one));
      for (const auto& name : called_in(one->node->body)) {
        auto held = top->get(name);
        if (auto other = held != nullptr ? held->get<Generator>() : nullptr) {
          if (other == found)
            return true;
          waiting.push_back(other);
        }
      }
      if (auto walked = walks_into.get(id_of(one)))
        for (auto other : *walked) {
          if (other == found)
            return true;
          waiting.push_back(other);
        }
    }
    return false;
  }

  void Checker::walks_itself_said(Generator* found, Span span)
  {
    if (walks_itself_at.contains(span.start))
      return;
    walks_itself_at.insert(span.start);
    diags.emit(D::LANG_GENERATOR_RECURSION, span, { { "name", found->node->name } });
  }

  bool Checker::walks_itself(Generator* found)
  {
    auto current = generating;
    if (current != nullptr) {
      auto& walked = walks_into.setdefault(id_of(current), {});
      if (std::ranges::find(walked, found) == walked.end())
        walked.push_back(found);
      if (found == current)
        return true;
    }
    return generator_reaches_itself(found);
  }

  ir::Value* Checker::generator_in(ir::Builder& builder, const ast::Allocated* expr, ir::Value* place,
                                   const ir::Type* expected)
  {
    contract_assert(ast::is<ast::Call>(expr->value));
    Picked picked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = expr->value;
      pick_walked = {};
      // And the arena in force for the call, so that an arena it leaves out is
      // the one its frame is kept in.
      Kept arenas(out_of_arena, out_of_arenas);
      out_of_arena = place;
      out_of_arenas = arenas_in(expr->arena);
      lower_expr(builder, expr->value, nullptr);
      picked = pick_walked;
    }
    auto walked = std::get_if<Iterable*>(&picked);
    if (walked == nullptr || (*walked)->generator == nullptr)
      return undef(module, ir::error_type());
    auto walk = make<Iterable>(**walked);
    walk->written = expr;
    walk->arena = place;
    if (pick_in == expr) {
      pick_walked = walk;
      return undef(module, ir::void_type());
    }
    if (auto wanted = ir::as<ir::IterType>(expected))
      return handed_on(builder, walk->generator->node->name, walk, wanted, expr->span);
    diags.emit(D::LANG_ITERATOR_NOT_AN_ARRAY, expr->span, { { "name", walk->generator->node->name } });
    return undef(module, ir::error_type());
  }

  ir::Value* Checker::lower_start(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1 || ast::is<ast::Named>(expr->args[0]) || ast::is<ast::Spread>(expr->args[0]))
      [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", start_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return undef(module, ir::error_type());
    }
    auto written = expr->args[0];
    if (! members.empty())
      // `⎕start(v)` and `⎕start(v in a)` over a value of a type with an `each`
      // as the call it stands for.
      written = called_each(builder, written);
    ir::Value* value;
    Picked picked;
    {
      Kept kept(pick_in, pick_walked);
      pick_in = written;
      pick_walked = {};
      value = lower_expr(builder, written, nullptr);
      picked = pick_walked;
    }
    Iterable* walked = nullptr;
    if (auto walk = std::get_if<Iterable*>(&picked))
      walked = *walk;
    bool picks = std::holds_alternative<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(picked);
    if (walked == nullptr && ! picks)
      // A type of the program's own is started as the walk its `each`
      // describes, where what it is was known only once it was lowered.
      walked = each_walk(builder, value, value_type_of(value), written->span);
    auto span = expr->span;
    const ir::Type* given_type = nullptr;
    ir::Value* given_at = builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), environment_type(), span);
    // `⎕start(g(…) in ⎕heap)`: the description and the state in the heap,
    // and a walk that may go wherever a value may.
    bool lasting = walked != nullptr && walked->generator != nullptr && walked->arena != nullptr
                   && tables::is_heap(walked->arena);
    ir::Value* walk = nullptr;
    const ir::Type* element = nullptr;
    if (walked != nullptr && walked->generator != nullptr) {
      auto found = walked->generator;
      std::string_view name = found->node->name;
      if (lasting) {
        auto allocated = ast::as<ast::Allocated>(written);
        auto call = static_cast<const ast::Call*>(allocated != nullptr ? allocated->value : written);
        auto count = std::min({ found->node->params.size(), found->params.size(), call->args.size() });
        for (std::size_t at = 0; at < count; ++at)
          if (! kept_anywhere(found->params[at]) && ! plainly_lasting(call->args[at])) [[unlikely]] {
            diags.emit(D::LANG_LASTING_WALK_REACHES, written->span,
                       { { "name", name },
                         { "param", found->node->params[at]->name },
                         { "found", found->params[at]->written() } });
            return undef(module, ir::error_type());
          }
      }
      walk = generator_described(builder, name, walked, ir::iter_type(found->element), span, true);
      if (value_type_of(walk) == ir::error_type())
        return undef(module, ir::error_type());
      if (found->given != nullptr) {
        auto offsets = std::get<1>(description_of(found));
        auto described = builder.extract(walk, 1, environment_type(), span);
        auto arguments = inside(builder, described, offsets[3], ir::u8(), span);
        given_at = given_place(builder, arguments, found, span);
        given_type = found->given;
      }
      element = found->element;
    } else {
      if (walked == nullptr && ! picks) {
        auto ty = value_type_of(value);
        if (ty == ir::error_type())
          return undef(module, ir::error_type());
        if (auto iter = ir::as<ir::IterType>(ty)) {
          walk = value;
          element = iter->element;
        } else {
          auto found_walk = ! ir::is<ir::TupleType>(ty) ? over_a_value(builder, value, ty, written->span)
                                                        : std::nullopt;
          if (! found_walk)
            return undef(module, ir::error_type());
          walked = make<Iterable>();
          walked->element = found_walk->element;
          walked->written = written;
          walked->base = value;
          walked->ty = ty;
        }
      }
      if (picks) {
        const auto& [base, ty, mask] = std::get<std::tuple<ir::Value*, const ir::ArrayType*, Mask>>(picked);
        walked = make<Iterable>();
        walked->element = picked_element(ty, mask);
        walked->written = written;
        walked->base = base;
        walked->ty = ty;
        walked->mask = mask;
      }
      if (walked != nullptr) {
        element = walked->element;
        walk = handed_on(builder, start_name, walked, ir::iter_type(element), span);
      }
      if (value_type_of(walk) == ir::error_type())
        return undef(module, ir::error_type());
    }
    contract_assert(element != nullptr);
    ir::Value* slot;
    if (lasting) {
      auto room = tables::allocate(builder, walked->arena, builder.int_const(ir::u64(), 8));
      slot = builder.cast(ir::CastKind::bitcast, room, ir::ptr_type(ir::u64(), true), span);
    } else
      slot = builder.frame(ir::u64(), span);
    builder.store(slot, builder.extract(walk, 2, ir::u64(), span), span);
    auto ty = ir::running_type(element, given_type, lasting);
    auto code = builder.extract(walk, 0, environment_type(), span);
    auto described = builder.extract(walk, 1, environment_type(), span);
    auto made = builder.make_tuple(std::vector{ code, described, slot, given_at }, ty, span);
    report_iterator(start_name,
                    std::format("a walk under way, driven by '{}': its state is in {}", resume_name,
                                lasting ? heap_name : std::string_view("this call's room")),
                    span);
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(span, ty, expected);
      return undef(module, ir::error_type());
    }
    return made;
  }

  ir::Value* Checker::lower_resume(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.empty() || expr->args.size() > 2
        || std::ranges::any_of(expr->args, [](const ast::Expr* one) {
             return ast::is<ast::Named>(one) || ast::is<ast::Spread>(one);
           })) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", resume_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return undef(module, ir::error_type());
    }
    auto span = expr->span;
    auto first = expr->args[0];
    if (auto call = ast::as<ast::Call>(first); call != nullptr && generator_named(call->callee) != nullptr)
      [[unlikely]] {
      // A generator's call describes a walk and is not one under way.
      diags.emit(D::LANG_RESUME_NOT_RUNNING, first->span,
                 { { "found", std::format("a call of '{}'", written_as(call->callee)) } });
      return undef(module, ir::error_type());
    }
    auto cursor = lower_expr(builder, first, nullptr);
    auto found = value_type_of(cursor);
    if (found == ir::error_type())
      return undef(module, ir::error_type());
    auto ty = ir::as<ir::RunningType>(found);
    if (ty == nullptr) [[unlikely]] {
      diags.emit(D::LANG_RESUME_NOT_RUNNING, expr->args[0]->span, { { "found", found->written() } });
      return undef(module, ir::error_type());
    }
    if ((ty->given == nullptr) != (expr->args.size() == 1)) [[unlikely]] {
      diags.emit(D::LANG_RESUME_ARGUMENTS, span, { { "found", ty->written() } });
      return undef(module, ir::error_type());
    }
    if (ty->given != nullptr) {
      auto handed = lower_into(builder, expr->args[1], ty->given, expr->args[1]->span);
      if (value_type_of(handed) == ir::error_type())
        return undef(module, ir::error_type());
      auto place = builder.extract(cursor, 3, environment_type(), span);
      words_into(builder, place, handed, ty->given, span);
    }
    auto element = ty->element;
    auto room = builder.frame(ir::tuple_type(word_types(element)), span);
    auto out = builder.cast(ir::CastKind::bitcast, room, environment_type(), span);
    auto code = builder.extract(cursor, 0, environment_type(), span);
    auto described = builder.extract(cursor, 1, environment_type(), span);
    auto state = builder.extract(cursor, 2, ir::ptr_type(ir::u64(), true), span);
    auto took = builder.call(nullptr, std::vector{ code, described, state, out }, ir::bool_type(), span);
    auto answer = ir::result_type(element);
    auto taken = words_from(builder, out, element, span);
    auto none = builder.binary(ir::BinOp::xor_, took, builder.bool_const(true), span);
    auto made = builder.wrap(taken, none, answer, span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(span, answer, expected);
      return undef(module, ir::error_type());
    }
    return made;
  }

  ir::Value* Checker::generator_described(ir::Builder& builder, std::string_view name, Iterable* walk,
                                          const ir::IterType* wanted, Span span, bool driven)
  {
    auto found = walk->generator;
    contract_assert(found != nullptr);
    if (found->given != nullptr && ! driven) [[unlikely]] {
      // A loop hands nothing in, and a `⇉T` parameter is walked by one.
      diags.emit(D::LANG_COROUTINE_NOT_WALKED, span,
                 { { "name", found->node->name }, { "given", found->given->written() } });
      return undef(module, ir::error_type());
    }
    if (walk->arena == nullptr && walks_itself(found)) [[unlikely]] {
      walks_itself_said(found, span);
      return undef(module, ir::error_type());
    }
    if (found->impure)
      an_effect(D::LANG_PURE_CALLS_IMPURE, span, Held(static_cast<const ast::Node*>(found->node)),
                { { "name", found->node->name } });
    auto [place, offsets] = description_made(builder, walk, span);
    auto record = std::get<0>(description_of(found));
    auto step = generator_next(found, record, offsets, span);
    report_iterator(name,
                    std::format("'{}' is walked through calls to '{}': its frame is in {}, and each walk starts it "
                                "afresh",
                                name, step->name,
                                walk->arena != nullptr ? "the arena 'in' named" : "this call's room"),
                    span);
    auto code = builder.code_address(step, span);
    auto described = builder.cast(ir::CastKind::bitcast, place, environment_type(), span);
    return builder.make_tuple(std::vector{ code, described, builder.int_const(ir::u64(), 0) },
                              ir::iter_type(found->element), span);
  }

  std::tuple<const ir::TupleType*, std::vector<std::int64_t>> Checker::description_of(Generator* found)
  {
    auto arguments = std::get<0>(argument_record(record_types(found)));
    auto word = environment_type();
    auto head = ir::tuple_type({ ir::u64(), ir::u64(), word, arguments });
    auto offsets = ir::member_offsets_of(head, layout());
    auto end = offsets[3] + ir::size_of(arguments, layout());
    auto frame_at = (end + 15) / 16 * 16;
    std::vector<std::int64_t> at(offsets.begin(), offsets.end());
    at.push_back(std::int64_t(frame_at));
    return { ir::tuple_type({ ir::u64(), ir::u64(), word, arguments, found->frame }), std::move(at) };
  }

  std::tuple<ir::Value*, std::vector<std::int64_t>> Checker::description_made(ir::Builder& builder, Iterable* walk,
                                                                              Span span)
  {
    auto found = walk->generator;
    contract_assert(found != nullptr);
    auto [record, offsets] = description_of(found);
    ir::Value* place;
    if (walk->arena != nullptr) {
      // In the arena `in` named: how large is known once the generator's body
      // is split, which is after this, so the room is asked for in a number
      // the pass splitting it fills in.
      auto size = builder.size_of(record, span);
      auto room = tables::allocate(builder, walk->arena, size);
      place = builder.cast(ir::CastKind::bitcast, room, ir::ptr_type(record, true), span);
    } else
      place = builder.frame(record, span);
    for (std::size_t at : { 0, 1 }) {
      auto where = inside(builder, place, offsets[at], ir::u64(), span);
      builder.store(where, builder.int_const(ir::u64(), 0), span);
    }
    auto where = inside(builder, place, offsets[2], environment_type(), span);
    auto nowhere = builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), environment_type(), span);
    builder.store(where, nowhere, span);
    auto arguments = inside(builder, place, offsets[3], ir::u8(), span);
    arguments_into(builder, arguments, found->params, walk->args, span);
    return { place, offsets };
  }

  ir::Function* Checker::generator_next(Generator* found, const ir::TupleType* record,
                                        std::span<const std::int64_t> offsets, Span span)
  {
    if (found->step != nullptr)
      return found->step;
    auto slot = ir::ptr_type(ir::u64(), true);
    contract_assert(found->func != nullptr);
    auto name = std::format("{}next.{}", builtin_glyph, without_first_character(found->func->name));
    auto func = turn_function(*this, name, span);
    auto entry = func->entry();
    ir::Builder inner(module, *func);
    auto word = environment_type();
    auto described = entry->add_param(word, "");
    auto state = entry->add_param(slot, "");
    auto out = entry->add_param(word, "");
    auto record_offsets = ir::member_offsets_of(delegation(), layout());
    auto counter = inside(inner, described, offsets[0], ir::u64(), span);
    auto current = inside(inner, described, offsets[1], ir::u64(), span);
    auto top_word = inside(inner, described, offsets[2], word, span);
    auto arguments = inside(inner, described, offsets[3], ir::u8(), span);
    auto frame = inside(inner, described, offsets[4], ir::u8(), span);
    auto own = inner.cast(ir::CastKind::bitcast, frame, word, span);
    auto given = inner.cast(ir::CastKind::bitcast, arguments, word, span);
    auto nowhere = inner.cast(ir::CastKind::bitcast, inner.int_const(ir::u64(), 0), word, span);
    auto walking = inner.load(state, span);
    auto token = inner.memory();

    auto at = [&](ir::BasicBlock* block) {
      inner.position_at(block);
      inner.set_memory(token);
    };
    // One word of a delegation.
    auto part = [&](ir::Value* of, std::size_t which) {
      return inside(inner, of, std::int64_t(record_offsets[which]), word, span);
    };
    auto is_null = [&](ir::Value* value) {
      auto bits = inner.cast(ir::CastKind::bitcast, value, ir::u64(), span);
      return inner.compare(ir::CmpPred::eq, bits, inner.int_const(ir::u64(), 0), span);
    };
    // Resume the generator's own body, or the one `of` says -- handing a
    // delegate what the walk was resumed with, which `⎕resume` wrote where the
    // generator's own body reads it.
    auto body = [&](ir::Value* of, ir::Value* told) {
      if (of == nullptr)
        return inner.call(found->func, std::vector<ir::Value*>{ own, told, given }, ir::u8(), span);
      if (found->given != nullptr && told != nowhere) {
        auto handed = words_from(inner, given_place(inner, arguments, found, span), found->given, span);
        auto into = inner.load(part(of, 4), span);
        words_into(inner, into, handed, found->given, span);
      }
      auto code = inner.load(part(of, 0), span);
      auto its_frame = inner.load(part(of, 1), span);
      auto its_arguments = inner.load(part(of, 2), span);
      return inner.call(nullptr, std::vector{ code, its_frame, told, its_arguments }, ir::u8(), span);
    };

    auto start = inner.new_block("start");
    auto going = inner.new_block("going");
    auto drive = inner.new_block("drive");
    auto asked = inner.new_block("asked");
    auto stopping = inner.new_block("stopping");
    inner.condbr(is_nowhere(inner, out, span), stopping, asked, {}, {}, span);
    // Told to finish: every body the walk is in, innermost first, then the
    // generator's own -- where a walk is under way.
    at(stopping);
    auto finishing = inner.new_block("finishing");
    auto chain = inner.new_block("chain");
    auto mine = inner.new_block("mine");
    auto nothing = inner.new_block("nothing");
    auto started = inner.compare(ir::CmpPred::ne, walking, inner.int_const(ir::u64(), 0), span);
    auto now = inner.load(current, span);
    auto this_one = inner.compare(ir::CmpPred::eq, now, walking, span);
    auto under_way = inner.binary(ir::BinOp::and_, started, this_one, span);
    inner.condbr(under_way, finishing, nothing, {}, {}, span);
    at(finishing);
    auto here = inner.load(top_word, span);
    inner.condbr(is_null(here), mine, chain, {}, {}, span);
    at(chain);
    body(here, nowhere);
    inner.store(top_word, inner.load(part(here, 3), span), span);
    inner.br(finishing, {}, span);
    at(mine);
    body(nullptr, nowhere);
    inner.store(state, inner.int_const(ir::u64(), 0), span);
    inner.br(nothing, {}, span);
    at(nothing);
    inner.ret(inner.bool_const(false), span);
    // Asked for a turn.
    at(asked);
    inner.condbr(inner.compare(ir::CmpPred::eq, walking, inner.int_const(ir::u64(), 0), span), start, going, {}, {},
                 span);
    at(start);
    auto number = inner.binary(ir::BinOp::wrap_add, inner.load(counter, span), inner.int_const(ir::u64(), 1), span);
    inner.store(counter, number, span);
    inner.store(current, number, span);
    inner.store(state, number, span);
    inner.store(inner.cast(ir::CastKind::bitcast, frame, slot, span), inner.int_const(ir::u64(), 0), span);
    inner.store(top_word, nowhere, span);
    inner.br(drive, {}, span);
    at(going);
    auto still = inner.compare(ir::CmpPred::eq, inner.load(current, span), walking, span);
    inner.check(still, "a walk of a generator overtaken by another walk of it", target::statuses::OUT_OF_RANGE, span);
    inner.br(drive, {}, span);
    // The walk is in the generator's own body or in a delegation.
    at(drive);
    here = inner.load(top_word, span);
    auto own_turn = inner.new_block("own");
    auto other_turn = inner.new_block("other");
    auto yes = inner.new_block("yes");
    auto no = inner.new_block("no");
    inner.condbr(is_null(here), own_turn, other_turn, {}, {}, span);
    at(own_turn);
    auto answer = body(nullptr, out);
    auto handed = inner.new_block("handed");
    inner.switch_(answer, std::vector<std::pair<__int128, ir::BasicBlock*>>{ { 1, yes }, { 2, handed } }, no, span);
    at(handed);
    // The generator's own body handed the walk on.
    auto first = inner.load(inside(inner, frame, 8, word, span), span);
    inner.store(part(first, 3), nowhere, span);
    inner.store(top_word, first, span);
    inner.br(drive, {}, span);
    at(other_turn);
    answer = body(here, out);
    auto deeper = inner.new_block("deeper");
    auto back = inner.new_block("back");
    inner.switch_(answer, std::vector<std::pair<__int128, ir::BasicBlock*>>{ { 1, yes }, { 2, deeper } }, back, span);
    at(deeper);
    // Handed on again: the next delegation is in that body's frame.
    auto its_frame = inner.load(part(here, 1), span);
    auto further = inner.load(inside(inner, its_frame, 8, word, span), span);
    inner.store(part(further, 3), here, span);
    inner.store(top_word, further, span);
    inner.br(drive, {}, span);
    at(back);
    // That body has nothing more: back to the one that handed it the walk.
    inner.store(top_word, inner.load(part(here, 3), span), span);
    inner.br(drive, {}, span);
    at(yes);
    inner.ret(inner.bool_const(true), span);
    at(no);
    inner.ret(inner.bool_const(false), span);
    found->step = func;
    module.record("name-lambda", name,
                  std::format("code that takes one turn of a walk of '{}' through its frame", found->node->name), span);
    return func;
  }


  // -- a generator written into the loop that walks it ------------------------

  std::optional<std::tuple<Generator*, Held>> Checker::written_in(const ast::ForEach* stmt)
  {
    auto target = stmt->iterable;
    Generator* found = nullptr;
    Held given;
    if (auto call = ast::as<ast::Call>(target)) {
      found = generator_named(call->callee);
      given = Held(static_cast<const ast::Node*>(call));
    } else if (auto ref = ast::as<ast::NameRef>(target)) {
      auto local = find_local(ref->name);
      if (local != nullptr && local->iterable != nullptr && local->iterable->generator != nullptr) {
        found = local->iterable->generator;
        given = Held(local);
      }
    }
    if (found == nullptr || found->node->body == nullptr)
      return std::nullopt;
    if (auto why = not_written_in(found)) {
      report_iterator(found->node->name,
                      std::format("'{}' is not written into this loop, {}, so it is walked through calls",
                                  found->node->name, *why),
                      stmt->iterable->span);
      return std::nullopt;
    }
    return std::tuple{ found, given };
  }

  std::optional<std::string> Checker::not_written_in(Generator* found)
  {
    auto body = found->node->body;
    contract_assert(body != nullptr);
    if (found->given != nullptr)
      return "since it takes something back each time it is resumed";
    if (found->finishes != nullptr)
      return "since it answers something when it ends";
    if (takes_an_answer(body))
      return "since it takes what a generator it hands the walk to answers";
    if (found->scope != nullptr && found->scope != top)
      return "since it is written in another file, whose names its body means";
    if (std::ranges::find(writing_in, id_of(found)) != writing_in.end())
      return "since it would be written into itself";
    if (contains(body, ast::NodeKind::Try))
      return "since it hands a failure out with '?'";
    auto stops = count(body, ast::NodeKind::Yield);
    auto size = count_nodes(body);
    if (stops > 8 || size * std::max<std::int64_t>(stops, 1) > 400)
      return "being too large to copy at each of its stops";
    auto bound_here = names_bound_in(found->node);
    // ORDER: Python asks the names of a set, in an order its hashing of
    // strings makes up; where more than one would answer, this names the
    // first in sorted order.
    for (const auto& name : names_in(body))
      if (! bound_here.contains(name) && find_local(name) != nullptr)
        return std::format("since '{}' there would mean the loop's own '{}'", name, name);
    return std::nullopt;
  }

  ir::Value* Checker::lower_written_in(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func,
                                       const ir::Type* expected, bool produces, Generator* found, Held given)
  {
    auto node = found->node;
    contract_assert(node->body != nullptr);
    std::vector<ir::Value*> values;
    if (auto call = given.get<ast::Call>()) {
      auto made = generator_arguments(builder, call, found);
      if (! made)
        return undef(module, ir::error_type());
      std::tie(found, values) = std::move(*made);
    } else {
      auto local = given.get<Local>();
      contract_assert(local != nullptr && local->iterable != nullptr);
      local->read = true;
      values = local->iterable->args;
    }
    auto number = each_count + 1;
    each_count += 1;
    auto prefix = std::format("{}{}.", iter_glyph, number);
    auto bound_here = names_bound_in(node);
    Dict<std::string, std::string> renames;
    for (const auto& name : bound_here)
      renames[name] = prefix + name;
    Dict<std::string, std::string> labels;
    for (const auto& name : labels_in(node->body))
      labels[name] = prefix + name;
    // Its type parameters too, and bound to what this walk's call gave them,
    // so that they mean that inside it and nothing outside it.
    Dict<std::string, std::string> types;
    for (const auto& [name, _] : found->bound)
      types[name] = prefix + name;
    auto body = renamed(syntax, node->body, renames, labels, &types);
    auto element = node->ret_type != nullptr ? types_renamed(syntax, node->ret_type, types) : nullptr;
    const ast::Label* outer = stmt->label;
    if (outer == nullptr) {
      auto made = syntax.make<ast::Label>(stmt->span);
      made->name = syntax.text(prefix + "walk");
      outer = made;
    }
    auto span = stmt->span;
    auto one_turn = syntax.make<ast::Range>(span);
    {
      auto first = syntax.make<ast::IntLit>(span);
      first->value = ast::Integer{ 0 };
      first->type_name = "u8";
      auto last = syntax.make<ast::IntLit>(span);
      last->value = ast::Integer{ 1 };
      last->type_name = "u8";
      one_turn->start = first;
      one_turn->stop = last;
    }
    auto counter = std::make_shared<int>(0);

    // One `yield`, as the loop's body with its name bound.
    auto handed = [this, counter, prefix, stmt, outer, element, one_turn, span](const ast::Expr* value) -> const ast::Stmt* {
      *counter += 1;
      auto inner = syntax.make<ast::Label>(span);
      inner->name = syntax.text(std::format("{}turn{}", prefix, *counter));
      auto rest = continued(syntax, stmt->body, outer->name, inner->name);
      bool uses_inner = rest != stmt->body;
      const ast::Stmt* bind;
      if (stmt->name == wildcard_name && stmt->more.empty()) {
        auto assign = syntax.make<ast::AssignStmt>(value->span);
        assign->name = syntax.text(wildcard_name);
        assign->name_span = value->span;
        assign->value = value;
        bind = assign;
      } else {
        // What the walk gives is what the generator says it gives, and not
        // whatever one `yield` happened to write: a result's answer and its
        // failure are both a result.
        auto definition = syntax.make<ast::VarDef>(value->span);
        definition->name = stmt->name;
        definition->name_span = stmt->name_span;
        definition->type = stmt->type != nullptr || ! stmt->more.empty() ? stmt->type : element;
        definition->value = value;
        definition->more = stmt->more;
        bind = definition;
      }
      std::vector<const ast::Stmt*> stmts{ bind };
      for (auto one : rest->stmts())
        stmts.push_back(one);
      auto loop = syntax.make<ast::ForEach>(span);
      loop->name = syntax.text(wildcard_name);
      loop->name_span = span;
      loop->iterable = one_turn;
      loop->body = block_of(syntax, span, stmts);
      loop->label = uses_inner ? inner : nullptr;
      auto written = syntax.make<ast::ExprStmt>(span);
      written->value = loop;
      return written;
    };

    auto walk_name = [counter, prefix]() {
      *counter += 1;
      return std::format("{}each{}", prefix, *counter);
    };

    body = yields_replaced(syntax, body, handed, outer->name, walk_name);
    // The loop of one turn takes the name where something names it: a `break`
    // in the loop's body, or the generator returning.
    bool named = breaks(stmt->body, outer->name) || contains(node->body, ast::NodeKind::ReturnStmt);
    auto loop = syntax.make<ast::ForEach>(span);
    loop->name = syntax.text(wildcard_name);
    loop->name_span = span;
    loop->iterable = one_turn;
    loop->body = body;
    loop->keyword = stmt->keyword;
    loop->label = named ? outer : nullptr;
    loop->alternative = stmt->alternative;
    if (found->impure)
      an_effect(D::LANG_PURE_CALLS_IMPURE, stmt->iterable->span, Held(static_cast<const ast::Node*>(found->node)),
                { { "name", node->name } });
    report_iterator(node->name,
                    std::format("'{}' is written into this loop: each 'yield' is where the loop's body runs, and no "
                                "coroutine is made",
                                node->name),
                    stmt->iterable->span);
    push_scope();
    writing_in.push_back(id_of(found));
    auto outer_bound = bound;
    Finally restored([this, &outer_bound] {
      bound = outer_bound;
      writing_in.pop_back();
      pop_scope();
    });
    if (! found->bound.empty())
      for (const auto& [name, ty] : found->bound)
        bound[*types.get(name)] = ty;
    for (std::size_t at = 0; at < std::min(node->params.size(), values.size()); ++at) {
      auto param = node->params[at];
      const auto& name = *renames.get(std::string(param->name));
      bind_local(name, values[at], param->span, param->mutable_, invalid_span, false, &builder);
      if (auto local = scopes.back().get(name); local != nullptr)
        (*local)->read = true;
    }
    return lower_foreach(builder, loop, func, expected, produces);
  }

  ir::Function* Checker::next_function(Iterable* found, std::span<const ir::Type* const> kinds, Span span)
  {
    std::pair<std::vector<std::int64_t>, bool> key_of{ {}, ! std::holds_alternative<std::monostate>(found->mask) };
    for (auto one : kinds)
      key_of.first.push_back(id_of(one));
    if (auto made = next_functions.get(key_of))
      return *made;
    auto element = found->element;
    auto name = std::format("{}next{}", builtin_glyph, next_functions.size());
    auto func = turn_function(*this, name, span);
    auto entry = func->entry();
    ir::Builder inner(module, *func);
    auto word = environment_type();
    auto described = entry->add_param(word, "");
    auto state = entry->add_param(ir::ptr_type(ir::u64(), true), "");
    auto out = entry->add_param(word, "");
    // Asked to finish, there is nothing to finish: what a loop walks as it is
    // puts nothing off.
    auto walking = inner.new_block("walking");
    auto stopping = inner.new_block("stopping");
    inner.condbr(is_nowhere(inner, out, span), stopping, walking, {}, {}, span);
    inner.position_at(stopping);
    inner.ret(inner.bool_const(false), span);
    inner.position_at(walking);
    std::vector<const ir::Type*> types;
    for (auto kind : kinds)
      for (auto one : word_types(kind))
        types.push_back(one);
    auto record = ir::tuple_type(types);
    auto offsets = ir::member_offsets_of(record, layout());
    std::vector<ir::Value*> words;
    for (std::size_t at = 0; at < std::min(types.size(), offsets.size()); ++at)
      words.push_back(inner.load(inside(inner, described, std::int64_t(offsets[at]), types[at], span), span));
    std::vector<ir::Value*> values;
    std::size_t at = 0;
    for (auto kind : kinds) {
      auto count = word_types(kind).size();
      values.push_back(from_words(inner, std::span(words).subspan(at, count), kind, span));
      at += count;
    }
    auto base = values[0];
    auto mask = values.size() > 1 ? values[1] : nullptr;
    std::optional<Iteration> walk;
    if (mask != nullptr) {
      auto written = ast::as<ast::Element>(found->written);
      auto ty = ir::as<ir::ArrayType>(found->ty);
      contract_assert(written != nullptr && ty != nullptr);
      walk = over_a_pick(inner, written, base, ty, Mask{ mask }, false);
    } else
      walk = over_a_value(inner, base, found->ty, span);
    contract_assert(walk.has_value());
    auto first = inner.load(state, span);
    auto loop = inner.new_block("loop");
    ir::Value* here = loop->add_param(ir::u64(), "at");
    inner.br(loop, std::vector{ first }, span);
    inner.position_at(loop);
    ir::Value* const turn[] = { here };
    auto asks = walk->wanted ? inner.new_block("pick") : nullptr;
    auto give = inner.new_block("give");
    auto done = inner.new_block("done");
    inner.condbr(walk->more(inner, turn), asks != nullptr ? asks : give, done, {}, {}, span);
    if (asks != nullptr && walk->wanted) {
      auto skip = inner.new_block("skip");
      inner.position_at(asks);
      inner.condbr(walk->wanted(inner, turn), give, skip, {}, {}, span);
      inner.position_at(skip);
      inner.br(loop, (walk->passed ? walk->passed : walk->step)(inner, turn), span);
    }
    inner.position_at(give);
    auto taken = walk->take(inner, turn);
    auto moved = walk->step(inner, turn);
    contract_assert(moved.size() == 1);
    inner.store(state, moved[0], span);
    words_into(inner, out, taken, element, span);
    inner.ret(inner.bool_const(true), span);
    inner.position_at(done);
    inner.ret(inner.bool_const(false), span);
    next_functions[key_of] = func;
    module.record("name-lambda", name,
                  std::format("code that takes one turn of a walk giving {}, for a callee that is handed it",
                              element->written()),
                  span);
    return func;
  }

  Iteration Checker::over_an_iterator(ir::Builder& builder, ir::Value* value, const ir::IterType* ty, Span span)
  {
    auto element = ty->element;
    contract_assert(element != nullptr);
    auto word = environment_type();
    auto code = builder.extract(value, 0, word, span);
    auto described = builder.extract(value, 1, word, span);
    auto start = builder.extract(value, 2, ir::u64(), span);
    auto slot = builder.frame(ir::u64(), span);
    builder.store(slot, start, span);
    // Where each turn's element is written: as many words as it takes, so that
    // an element of any size comes back the same way.
    auto given = builder.frame(ir::tuple_type(word_types(element)), span);
    auto out = builder.cast(ir::CastKind::bitcast, given, word, span);

    Iteration made;
    made.element = element;
    made.start = { slot };
    made.more = [code, described, out, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.call(nullptr, std::vector{ code, described, s[0], out }, ir::bool_type(), span);
    };
    made.take = [this, out, element, span](ir::Builder& b, std::span<ir::Value* const>) {
      return words_from(b, out, element, span);
    };
    made.step = [](ir::Builder&, std::span<ir::Value* const> s) { return LoopState{ s[0] }; };
    // Asked with nowhere to write a turn to: finish, and run what was put off.
    // A walk that ran out has nothing to finish, and says so.
    made.finish = [code, described, slot, word, span](ir::Builder& b) {
      auto nowhere = b.cast(ir::CastKind::bitcast, b.int_const(ir::u64(), 0), word, span);
      b.call(nullptr, std::vector{ code, described, slot, nowhere }, ir::bool_type(), span);
    };
    return made;
  }

  Iteration Checker::over_a_list(ir::Builder& builder, ir::Value* value, const ir::ListType* ty, Span span)
  {
    auto elements = builder.extract(value, 0, ir::parts_of(ty)[0], span);
    auto length = builder.extract(value, 1, ir::u64(), span);
    Iteration made;
    made.element = ty->element;
    made.start = { builder.int_const(ir::u64(), 0) };
    made.more = [length, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(ir::CmpPred::ult, s[0], length, span);
    };
    made.take = [this, elements, ty, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.load(element_place(b, elements, ty->element, s[0], span), span);
    };
    made.step = [span](ir::Builder& b, std::span<ir::Value* const> s) {
      return LoopState{ b.binary(ir::BinOp::wrap_add, s[0], b.int_const(ir::u64(), 1), span) };
    };
    made.place = [this, elements, ty, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return element_place(b, elements, ty->element, s[0], span);
    };
    return made;
  }

  Iteration Checker::over_a_string(ir::Builder& builder, ir::Value* value, Span span)
  {
    auto bytes = builder.extract(value, 0, ir::parts_of(ir::str_type())[0], span);
    auto length = builder.extract(value, 1, ir::u64(), span);
    auto taker = strings::next_function(module);
    auto taken = strings::taken_type(module);
    // Taking the character and moving past it are one call: what it answered
    // is kept for whichever of the two is asked second.
    auto held = std::make_shared<std::unordered_map<const ir::Value*, ir::Value*>>();
    auto reach = [held, taker, bytes, taken, span](ir::Builder& b, std::span<ir::Value* const> s) {
      auto found = held->find(s[0]);
      if (found != held->end())
        return found->second;
      auto made = b.call(taker, std::vector{ bytes, s[0] }, taken, span);
      held->emplace(s[0], made);
      return made;
    };
    Iteration made;
    made.element = ir::char_type();
    made.start = { builder.int_const(ir::u64(), 0) };
    made.more = [length, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(ir::CmpPred::ult, s[0], length, span);
    };
    made.take = [reach, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.extract(reach(b, s), 0, ir::char_type(), span);
    };
    made.step = [reach, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return LoopState{ b.extract(reach(b, s), 1, ir::u64(), span) };
    };
    return made;
  }

  Iteration Checker::over_a_table(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    auto shape = table_shape(ty);
    tables::ensure_runtime(module, shape);
    auto table = builder.cast(ir::CastKind::bitcast, value, tables::table_type(module), span);
    auto finder = module.function(tables::next_symbol);
    // How many entries are written, read once: the walk is of the dense array,
    // in the order the keys were put in, and ends where it does.
    auto used_at = builder.binary(ir::BinOp::add, table, builder.int_const(ir::u64(), tables::used_field), span);
    auto written = builder.load(used_at, span);
    auto set = ir::as<ir::SetType>(ty);
    auto dict = ir::as<ir::DictType>(ty);
    auto key_ty = set != nullptr ? set->element : dict->key;
    const ir::Type* element = set != nullptr ? key_ty : ir::tuple_type({ dict->key, dict->value });

    // One word of an entry, read as what it holds.
    auto read = [span](ir::Builder& b, ir::Value* place, std::int64_t offset, const ir::Type* held) {
      auto at = b.binary(ir::BinOp::add, place, b.int_const(ir::u64(), offset), span);
      return b.load(b.cast(ir::CastKind::bitcast, at, ir::ptr_type(held, true), span), span);
    };

    Iteration made;
    made.element = element;
    made.start = { builder.call(finder, std::vector{ table, builder.int_const(ir::u64(), 0) }, ir::u64(), span) };
    made.more = [written, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(ir::CmpPred::ult, s[0], written, span);
    };
    made.take = [this, table, read, key_ty, set, dict, shape, element, span](ir::Builder& b,
                                                                            std::span<ir::Value* const> s) {
      auto place = tables::entry_at(b, table, s[0]);
      auto key = read(b, place, tables::key_at, key_ty);
      if (set != nullptr)
        return key;
      // A key or a value of several words is that many leaves of the pair.
      auto held = read(b, place, shape.value_at(), dict->value);
      return made_tuple(b, std::vector{ key, held }, static_cast<const ir::TupleType*>(element), span);
    };
    made.step = [finder, table, span](ir::Builder& b, std::span<ir::Value* const> s) {
      auto next = b.binary(ir::BinOp::wrap_add, s[0], b.int_const(ir::u64(), 1), span);
      return LoopState{ b.call(finder, std::vector{ table, next }, ir::u64(), span) };
    };
    return made;
  }

  Iteration Checker::over_a_counted_range(ir::Builder& builder, const ast::ForEach* stmt, ir::Value* first,
                                          ir::Value* last, ir::Value* step, const ir::IntType* element)
  {
    auto span = stmt->span;
    auto stepped = ir::as<ir::IntType>(value_type_of(step));
    contract_assert(stepped != nullptr);

    // `value` as a 64-bit unsigned number, its sign carried along.
    auto wide = [span](ir::Builder& b, ir::Value* value, const ir::IntType* ty) {
      if (ty == ir::u64())
        return value;
      return b.cast(ty->is_signed && ty->bits < 64 ? ir::CastKind::sext : ir::CastKind::zext, value, ir::u64(), span);
    };
    // All ones where `truth` holds, nought where it does not.
    auto mask = [span](ir::Builder& b, ir::Value* truth) {
      auto zero = b.int_const(ir::u64(), 0);
      auto one = b.cast(ir::CastKind::zext, truth, ir::u64(), span);
      return b.binary(ir::BinOp::wrap_sub, zero, one, span);
    };

    auto zero = builder.int_const(stepped, 0);
    auto falling = stepped->is_signed ? builder.compare(ir::CmpPred::slt, step, zero, span) : builder.bool_const(false);
    auto size = wide(builder, step, stepped);
    {
      auto when_falling = mask(builder, falling);
      auto negated = builder.binary(ir::BinOp::wrap_sub, builder.int_const(ir::u64(), 0), size, span);
      auto down = builder.binary(ir::BinOp::and_, when_falling, negated, span);
      auto when_rising = builder.binary(ir::BinOp::xor_, mask(builder, falling),
                                        builder.int_const(ir::u64(), (__int128(1) << 64) - 1), span);
      auto up = builder.binary(ir::BinOp::and_, when_rising, size, span);
      size = builder.binary(ir::BinOp::or_, down, up, span);
    }
    builder.check(builder.compare(ir::CmpPred::ne, size, builder.int_const(ir::u64(), 0), span),
                  "a range whose step came to nought", target::statuses::ZERO_STEP, span);
    bool is_signed = element->is_signed;
    auto below = builder.compare(is_signed ? ir::CmpPred::slt : ir::CmpPred::ult, first, last, span);
    auto above = builder.compare(is_signed ? ir::CmpPred::sgt : ir::CmpPred::ugt, first, last, span);
    auto rising = builder.binary(ir::BinOp::xor_, falling, builder.bool_const(true), span);
    auto rising_below = builder.binary(ir::BinOp::and_, rising, below, span);
    auto falling_above = builder.binary(ir::BinOp::and_, falling, above, span);
    auto ahead = builder.binary(ir::BinOp::or_, rising_below, falling_above, span);
    auto start = wide(builder, first, element);
    auto end = wide(builder, last, element);
    auto up = builder.binary(ir::BinOp::wrap_sub, end, start, span);
    auto down = builder.binary(ir::BinOp::wrap_sub, start, end, span);
    auto upward = builder.binary(ir::BinOp::and_, mask(builder, rising), up, span);
    auto downward = builder.binary(ir::BinOp::and_, mask(builder, falling), down, span);
    auto distance = builder.binary(ir::BinOp::or_, upward, downward, span);
    auto whole = builder.binary(ir::BinOp::udiv, distance, size, span);
    auto left_over = builder.binary(ir::BinOp::urem, distance, size, span);
    auto part = builder.compare(ir::CmpPred::ne, left_over, builder.int_const(ir::u64(), 0), span);
    auto when_ahead = mask(builder, ahead);
    auto counted = builder.binary(ir::BinOp::wrap_add, whole, builder.cast(ir::CastKind::zext, part, ir::u64(), span),
                                  span);
    auto turns = builder.binary(ir::BinOp::and_, when_ahead, counted, span);
    // The step in the ends' own type: a signed twin's bits are what adding it
    // with wrapping does to an unsigned number, in either direction.
    auto moving = ir::same(stepped, element) ? step : builder.cast(ir::CastKind::zext, step, element, span);

    Iteration made;
    made.element = element;
    made.start = { first, builder.int_const(ir::u64(), 0) };
    made.more = [turns, span](ir::Builder& b, std::span<ir::Value* const> s) {
      return b.compare(ir::CmpPred::ult, s[1], turns, span);
    };
    made.take = [](ir::Builder&, std::span<ir::Value* const> s) { return s[0]; };
    made.step = [moving, span](ir::Builder& b, std::span<ir::Value* const> s) {
      auto next = b.binary(ir::BinOp::wrap_add, s[0], moving, span);
      return LoopState{ next, b.binary(ir::BinOp::wrap_add, s[1], b.int_const(ir::u64(), 1), span) };
    };
    return made;
  }

  std::optional<std::tuple<ir::Value*, ir::Value*, ir::Value*, std::optional<bool>, const ir::IntType*>>
  Checker::range_of(ir::Builder& builder, const ast::ForEach* stmt)
  {
    auto written = ast::as<ast::Range>(stmt->iterable);
    if (written == nullptr) [[unlikely]] {
      auto value = lower_expr(builder, stmt->iterable, nullptr);
      auto found = value_type_of(value);
      if (found != ir::error_type())
        diags.emit(D::LANG_LOOP_NOT_AN_ITERATOR, stmt->iterable->span, { { "found", found->written() } });
      return std::nullopt;
    }
    auto declared = stmt->type != nullptr ? resolve_type(stmt->type) : nullptr;
    auto first = declared != nullptr ? lower_into(builder, written->start, declared, written->start->span)
                                     : lower_expr(builder, written->start, nullptr);
    auto found = value_type_of(first);
    if (found == ir::error_type())
      return std::nullopt;
    auto element = ir::as<ir::IntType>(found);
    if (element == nullptr) [[unlikely]] {
      diags.emit(D::LANG_RANGE_NOT_AN_INTEGER, written->start->span, { { "found", found->written() } });
      return std::nullopt;
    }
    auto last = lower_expr(builder, written->stop, element);
    auto other = value_type_of(last);
    if (other == ir::error_type())
      return std::nullopt;
    if (! ir::same(other, element)) [[unlikely]] {
      diags.emit(D::LANG_RANGE_ENDS_DIFFER, written->span,
                 { { "first", element->written() }, { "second", other->written() } });
      return std::nullopt;
    }
    if (written->step != nullptr && ! ast::is<ast::IntLit>(written->step)) {
      // Worked out while running: of the ends' type, or -- so that an
      // unsigned range may count down -- of the signed type of its width.
      auto twin = ir::int_type(element->bits, true, element->unit);
      // Lowered as what it is said to be where that is the twin, and as the
      // ends' type otherwise, which is what a literal in it takes.
      auto said = hint_of(written->step);
      const ir::Type* asked = element;
      if (said != nullptr)
        asked = ir::same(said, element) || ir::same(said, twin) ? said : nullptr;
      auto step = lower_expr(builder, written->step, asked);
      auto stepped = value_type_of(step);
      if (stepped == ir::error_type())
        return std::nullopt;
      if (! ir::same(stepped, element) && ! ir::same(stepped, twin)) [[unlikely]] {
        diags.emit(D::LANG_RANGE_STEP_TYPE, written->step->span,
                   { { "element", element->written() }, { "twin", twin->written() }, { "found", stepped->written() } });
        return std::nullopt;
      }
      return std::tuple{ first, last, step, std::optional<bool>{}, element };
    }
    auto [distance, rising] = range_step(written, element);
    if (! distance)
      return std::nullopt;
    return std::tuple{ first, last, builder.int_const(element, *distance), std::optional<bool>{ rising }, element };
  }

  std::tuple<std::optional<__int128>, bool> Checker::range_step(const ast::Range* written, const ir::IntType* element)
  {
    if (written->step == nullptr)
      return { 1, true };
    auto literal = ast::as<ast::IntLit>(written->step);
    contract_assert(literal != nullptr);
    auto given = literal->value.value;
    if (given == 0 && ! literal->value.too_large) [[unlikely]] {
      diags.emit(D::LANG_RANGE_STEP_IS_ZERO, written->step->span);
      return { std::nullopt, true };
    }
    auto distance = given < 0 ? -given : given;
    if (literal->value.too_large || ! element->holds(distance)) [[unlikely]] {
      diags.emit(D::LANG_SYNTAX_INTEGER_RANGE, written->step->span,
                 { { "literal", ir::decimal(distance) }, { "type", element->written() } });
      return { std::nullopt, true };
    }
    return { distance, given > 0 };
  }

  std::vector<Local*> Checker::loop_locals(const ast::Block* body, const ast::Block* alternative)
  {
    Dict<std::int64_t, Local*> found;
    auto names = assigned_in(body);
    if (alternative != nullptr)
      for (auto& name : assigned_in(alternative))
        names.push_back(std::move(name));
    for (const auto& name : names) {
      auto local = find_local(name);
      // A name with storage of its own stands for the same place at every
      // turn: what a turn changes is what is in the place, which is read where
      // it is read and carried nowhere.
      if (local != nullptr && ! local->placed)
        found.setdefault(id_of(local), local);
    }
    rooms_in(body, alternative, found);
    std::vector<Local*> locals;
    for (const auto& [_, local] : found)
      locals.push_back(local);
    return locals;
  }

} // namespace pl4g::sema
