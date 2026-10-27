// The checker, part: the rest of arrays, numbers worked out while compiling,
// writes into places, calls through a value, generic functions checked where
// they are written and made for their types, lambdas, and references.
//
// A port of pypl4g/sema/check.py lines 12196 to 15127, method for method.
#include <algorithm>
#include <format>
#include <ranges>

#include "front/names.hh"
#include "ir/layout.hh"
#include "ir/printer.hh"
#include "sema/check.hh"
#include "sema/lastuse.hh"
#include "sema/liveness.hh"
#include "sema/owned.hh"
#include "sema/tables.hh"
#include "target/statuses.hh"


namespace pl4g::sema {

  namespace {

    namespace statuses = target::statuses;

    // A value that could not be worked out: Python's `UndefConst(ERROR)`.
    ir::Value* error_value(ir::Module& module)
    {
      return const_cast<ir::UndefConst*>(module.undef(ir::error_type()));
    }

    // A value of `ty` that is no particular one: Python's `UndefConst(ty)`.
    ir::Value* undef_of(ir::Module& module, const ir::Type* ty)
    {
      return const_cast<ir::UndefConst*>(module.undef(ty));
    }

    bool is_error(const ir::Type* ty) { return ty == ir::error_type(); }

    // How many characters UTF-8 text holds, as Python's `len` of a `str`
    // counts them.
    std::size_t characters(std::string_view text)
    {
      return static_cast<std::size_t>(std::ranges::count_if(text, [](char c) { return (c & 0xc0) != 0x80; }));
    }

    // What a function type says each leaf of its answer borrows from, as a
    // table: Python's `FuncType.borrowed()`.
    Dict<std::int64_t, ir::Borrowed> borrowed_of(const ir::FuncType* ty)
    {
      Dict<std::int64_t, ir::Borrowed> table;
      for (const auto& [leaf, one] : ty->borrows)
        table[leaf] = one;
      return table;
    }

    // The borrows of a signature, sorted, as a function type carries them.
    std::vector<std::pair<int, ir::Borrowed>> sorted_borrows(const Dict<std::int64_t, ir::Borrowed>& borrows)
    {
      std::vector<std::pair<int, ir::Borrowed>> sorted;
      for (const auto& [leaf, one] : borrows)
        sorted.emplace_back(static_cast<int>(leaf), one);
      std::ranges::sort(sorted);
      return sorted;
    }

    // The flags a function type carries beside its parameters and answer.
    struct FuncFlags {
      bool listable = false;
      bool hands_out = false;
      bool lasting = false;
      bool impure = false;
      bool can_ignore = false;
      std::vector<std::pair<int, ir::Borrowed>> borrows;
      std::string_view cconv = ir::default_cconv;
    };

    // Python's `module.types.func_type(params, ret, listable, hands_out, …)`.
    const ir::FuncType* function_type(std::vector<const ir::Type*> params, const ir::Type* ret, FuncFlags flags = {})
    {
      ir::FuncType shape;
      shape.listable = flags.listable;
      shape.hands_out = flags.hands_out;
      shape.lasting = flags.lasting;
      shape.impure = flags.impure;
      shape.can_ignore = flags.can_ignore;
      shape.borrows = std::move(flags.borrows);
      shape.cconv = flags.cconv;
      return ir::func_type(std::move(params), ret, shape);
    }

    // Python's `", ".join(…)`.
    template<typename R, typename F>
    std::string comma_joined(const R& items, F&& each, std::string_view between = ", ")
    {
      std::string text;
      bool first = true;
      for (const auto& one : items) {
        if (! first)
          text += between;
        first = false;
        text += each(one);
      }
      return text;
    }

    // The types a generic was given, as the messages say them: `T = u8, …`.
    std::string types_given(const std::vector<std::string>& parameters, const Dict<std::string, const ir::Type*>& bound)
    {
      std::string text;
      for (const auto& one : parameters) {
        auto found = bound.get(one);
        if (found == nullptr)
          continue;
        if (! text.empty())
          text += ", ";
        text += one + " = " + (*found)->written();
      }
      return text;
    }

    // Python's `a + b` and friends over unbounded integers, here where they
    // fit 128 bits; nothing where they do not.
    std::optional<__int128> added(__int128 a, __int128 b)
    {
      __int128 r;
      if (__builtin_add_overflow(a, b, &r)) [[unlikely]]
        return std::nullopt;
      return r;
    }

    std::optional<__int128> subtracted(__int128 a, __int128 b)
    {
      __int128 r;
      if (__builtin_sub_overflow(a, b, &r)) [[unlikely]]
        return std::nullopt;
      return r;
    }

    std::optional<__int128> multiplied(__int128 a, __int128 b)
    {
      __int128 r;
      if (__builtin_mul_overflow(a, b, &r)) [[unlikely]]
        return std::nullopt;
      return r;
    }

    __int128 magnitude(__int128 a) { return a < 0 ? -a : a; }

    // The root of a place written: through fields, elements and indices.
    const ast::Expr* root_of(const ast::Expr* root)
    {
      while (true)
        if (auto m = ast::as<ast::Member>(root))
          root = m->base;
        else if (auto e = ast::as<ast::Element>(root))
          root = e->base;
        else if (auto i = ast::as<ast::Index>(root))
          root = i->base;
        else
          return root;
    }

    // Why a borrow stopped counting, as the report log says it, by what
    // `Liveness` wrote down: Python's `_ENDED_BECAUSE`, `{keeper}` and
    // `{name}` put in.
    std::string ended_because(std::string_view why, std::string_view keeper, std::string_view name)
    {
      std::string k(keeper);
      std::string n(name);
      if (why == "unused")
        return "'" + k + "' is not used again on this way through";
      if (why == "given")
        return "'" + k + "' is given another value here, and what it held before is not used again";
      if (why == "given-after")
        return "the statement above gives '" + k + "' another value, after using the one it held";
      if (why == "carried")
        return "the statement above gives '" + k + "' another value made from the one it held, which keeps '" + n
               + "' lent from here on";
      if (why == "return")
        return "the function returns here";
      if (why == "break")
        return "the loop is left here";
      if (why == "continue")
        return "the turn ends here";
      return "the scope of '" + k + "' ends here";
    }

    // Run something as the scope it is made in is left, however it is left:
    // what Python's `finally` does.
    template<typename F>
    struct Finally {
      F run;
      Finally(F run) : run(std::move(run)) {}
      Finally(const Finally&) = delete;
      Finally& operator=(const Finally&) = delete;
      ~Finally() { run(); }
    };

  } // anonymous namespace


  // -- the rest of arrays, and what is worked out while compiling -------------

  bool Checker::reaches_itself(std::string_view name, const ast::Node* written, NameSet& seen)
  {
    if (auto ref = ast::as<ast::NameRef>(written)) {
      if (ref->name == name)
        return true;
      auto other = top_written.get(std::string(ref->name));
      if (other == nullptr || seen.contains(ref->name) || find_local(ref->name) != nullptr)
        return false;
      seen.emplace(ref->name);
      return reaches_itself(name, (*other)->value, seen);
    }
    if (written == nullptr)
      return false;
    bool found = false;
    ast::for_each_child(written, [&](const ast::Node* one) {
      if (! found && reaches_itself(name, one, seen))
        found = true;
    });
    return found;
  }


  std::optional<Folded> Checker::constant_not_yet_defined(std::string_view name)
  {
    auto written = top_written.get(std::string(name));
    if (written == nullptr || (*written)->mutable_ || (*written)->type == nullptr || constants_asked.contains(name))
      return std::nullopt;
    auto ty = ir::as<ir::IntType>(resolve_type((*written)->type));
    if (ty == nullptr)
      return std::nullopt;
    constants_asked.emplace(name);
    auto outer = constant_quiet;
    constant_quiet = true;
    std::optional<Folded> found;
    try {
      found = constant((*written)->value);
    } catch (...) {
      constant_quiet = outer;
      constants_asked.erase(std::string(name));
      throw;
    }
    constant_quiet = outer;
    constants_asked.erase(std::string(name));
    if (! found || found->failed || ! ty->holds(found->value) || (found->ty != nullptr && ! ir::same(found->ty, ty)))
      return std::nullopt;
    return Folded{ .value = found->value, .ty = ty };
  }


  std::optional<Folded> Checker::refuse(DiagId ident, Span span, DiagArgs args)
  {
    if (constant_quiet)
      return std::nullopt;
    diags.emit(ident, span, std::move(args));
    return refused_fold();
  }


  std::optional<Folded> Checker::fits(const ast::Expr* expr, __int128 value, const ir::IntType* ty)
  {
    if (ty != nullptr && ! ty->holds(value))
      return refuse(D::LANG_TYPE_ANSWER_DOES_NOT_FIT, expr->span,
                    { { "value", ir::decimal(value) }, { "type", ty->written() } });
    return Folded{ .value = value, .ty = ty };
  }


  std::optional<Folded> Checker::folded_length(const ast::Unary* expr)
  {
    if (auto text = ast::as<ast::StringLit>(expr->operand))
      // Python's `len` of a `str` counts characters, not bytes.
      return Folded{ .value = static_cast<__int128>(characters(text->value)), .ty = ir::u64() };
    auto found = hint_of(expr->operand);
    if (auto array = ir::as<ir::ArrayType>(found); array != nullptr && array->shape[0].has_value())
      return Folded{ .value = static_cast<__int128>(*array->shape[0]), .ty = ir::u64() };
    if (auto tuple = ir::as<ir::TupleType>(found))
      return Folded{ .value = static_cast<__int128>(tuple->members.size()), .ty = ir::u64() };
    return std::nullopt;
  }


  std::optional<Folded> Checker::folded_binary(const ast::Binary* expr)
  {
    auto op = expr->op;
    auto left = constant(expr->left);
    if (is_refused(left))
      return refused_fold();
    if (op == ast::BinaryOp::or_else) {
      if (! left)
        return std::nullopt;
      return left->failed ? constant(expr->right) : left;
    }
    auto right = constant(expr->right);
    if (is_refused(right))
      return refused_fold();
    if (! left || ! right || left->failed || right->failed)
      return std::nullopt;
    if (left->ty != nullptr && right->ty != nullptr && ! ir::same(left->ty, right->ty))
      // Two types meet, which is the program's mistake to report where it
      // reports every other: not a constant, so lowered as written.
      return std::nullopt;
    auto ty = left->ty != nullptr ? left->ty : right->ty;
    auto a = left->value;
    auto b = right->value;
    // A number beyond 128 bits, which Python would carry on with: no constant
    // here, and the program says what it says of it where it is lowered.
    auto fitting = [&](std::optional<__int128> answer) -> std::optional<Folded> {
      if (! answer) [[unlikely]]
        return std::nullopt;
      return fits(expr, *answer, ty);
    };
    switch (op) {
    case ast::BinaryOp::add:
      return fitting(added(a, b));
    case ast::BinaryOp::subtract:
      return fitting(subtracted(a, b));
    case ast::BinaryOp::multiply:
      return fitting(multiplied(a, b));
    case ast::BinaryOp::sat_add:
    case ast::BinaryOp::sat_sub:
    case ast::BinaryOp::sat_mul: {
      if (ty == nullptr)
        return std::nullopt;
      auto answer = op == ast::BinaryOp::sat_add   ? added(a, b)
                    : op == ast::BinaryOp::sat_sub ? subtracted(a, b)
                                                   : multiplied(a, b);
      if (! answer) [[unlikely]]
        return std::nullopt;
      return Folded{ .value = std::max(ty->low(), std::min(ty->high(), *answer)), .ty = ty };
    }
    case ast::BinaryOp::divide:
    case ast::BinaryOp::remainder: {
      if (b == 0 || (ty != nullptr && ty->is_signed && a == ty->low() && b == -1))
        return Folded{ .value = 0, .ty = ty, .failed = true };
      __int128 quotient = magnitude(a) / magnitude(b) * ((a < 0) == (b < 0) ? 1 : -1);
      return Folded{ .value = op == ast::BinaryOp::divide ? quotient : a - quotient * b, .ty = ty };
    }
    case ast::BinaryOp::max:
      return Folded{ .value = std::max(a, b), .ty = ty };
    case ast::BinaryOp::min:
      return Folded{ .value = std::min(a, b), .ty = ty };
    case ast::BinaryOp::power:
      return folded_power(expr, a, b, ty);
    case ast::BinaryOp::bit_and:
    case ast::BinaryOp::bit_or:
    case ast::BinaryOp::bit_xor: {
      auto answer = op == ast::BinaryOp::bit_and ? a & b : op == ast::BinaryOp::bit_or ? a | b : a ^ b;
      return Folded{ .value = ty == nullptr ? answer : within(answer, ty), .ty = ty };
    }
    case ast::BinaryOp::shift_left:
    case ast::BinaryOp::shift_right:
    case ast::BinaryOp::rotate_left:
    case ast::BinaryOp::rotate_right:
      return folded_shift(expr, a, b, ty);
    default:
      return std::nullopt;
    }
  }


  std::optional<Folded> Checker::folded_power(const ast::Expr* expr, __int128 base, __int128 exponent,
                                              const ir::IntType* ty)
  {
    try {
      auto answer = comptime::power(base, exponent);
      if (answer.too_large) [[unlikely]]
        return std::nullopt;
      return fits(expr, answer.value, ty);
    } catch (const comptime::DivisionByNought&) {
      return Folded{ .value = 0, .ty = ty, .failed = true };
    }
  }


  std::optional<Folded> Checker::folded_shift(const ast::Binary* expr, __int128 a, __int128 b, const ir::IntType* ty)
  {
    if (ty == nullptr)
      // The bits of a number of no width are not a thing there is an answer
      // about: how far a shift is too far is the width.
      return std::nullopt;
    if (! (0 <= b && b < static_cast<__int128>(ty->bits)))
      return refuse(D::LANG_CONSTANT_SHIFT_TOO_FAR, expr->span,
                    { { "type", ty->written() }, { "distance", ir::decimal(b) } });
    auto width = static_cast<int>(ty->bits);
    auto mask = width >= 127 ? ~static_cast<unsigned __int128>(0) : (static_cast<unsigned __int128>(1) << width) - 1;
    auto bits = static_cast<unsigned __int128>(a) & mask;
    auto distance = static_cast<int>(b);
    __int128 answer;
    switch (expr->op) {
    case ast::BinaryOp::shift_left:
      answer = static_cast<__int128>(bits << distance);
      break;
    case ast::BinaryOp::shift_right:
      answer = a >> distance;
      break;
    default: {
      if (ty->is_signed)
        return std::nullopt;
      auto turn = expr->op == ast::BinaryOp::rotate_left ? distance : width - distance;
      auto high = turn >= 128 ? 0 : bits << turn;
      auto low = width - turn >= 128 ? 0 : bits >> (width - turn);
      answer = static_cast<__int128>(high | low);
      break;
    }
    }
    return Folded{ .value = within(answer, ty), .ty = ty };
  }


  std::optional<Folded> Checker::folded_call(const ast::Call* expr)
  {
    auto callee = ast::as<ast::NameRef>(expr->callee);
    if (callee == nullptr || find_local(callee->name) != nullptr)
      return std::nullopt;
    auto found_func = definitions.get(std::string(callee->name));
    if (found_func == nullptr)
      return std::nullopt;
    auto func = *found_func;
    if (func->body == nullptr || func->ret_type == nullptr)
      return std::nullopt;
    std::vector<std::string> generic;
    for (auto param : func->params)
      parameters_in(param->type, generic);
    if (! generic.empty() || func->params.size() != expr->args.size())
      return std::nullopt;
    if (std::ranges::any_of(func->attrs, [](auto attr) { return attr->name == "impure" || attr->name == "io"; }))
      return refuse(D::LANG_CONSTANT_CALL_IMPURE, expr->callee->span, { { "name", func->name } });
    std::vector<comptime::Value> arguments;
    for (auto one : expr->args) {
      auto found = constant(one);
      if (is_refused(found))
        return refused_fold();
      if (! found || found->failed)
        return std::nullopt;
      arguments.emplace_back(found->value);
    }
    auto ret = ir::as<ir::IntType>(resolve_type(func->ret_type));
    if (ret == nullptr)
      return std::nullopt;
    comptime::Value answer;
    try {
      comptime::Evaluator evaluator(units);
      answer = evaluator.call(*func, arguments);
    } catch (const comptime::CannotEvaluate& exc) {
      if (exc.ran_out)
        return refuse(D::LANG_COMPTIME_ENDLESS, exc.span, { { "detail", exc.detail } });
      return refuse(D::LANG_COMPTIME_CANNOT, exc.span, { { "construct", exc.detail } });
    }
    if (! answer.is_whole())
      return std::nullopt;
    return fits(expr, *answer.get<__int128>(), ret);
  }


  const ast::IntLit* Checker::index_written_down(const ast::Expr* expr)
  {
    auto folded = welcome_constant(expr);
    if (! folded || folded->ty != nullptr || folded->failed)
      return nullptr;
    auto made = syntax.make<ast::IntLit>(expr->span);
    made->value = ast::Integer{ .value = folded->value };
    return made;
  }


  std::optional<Folded> Checker::welcome_constant(const ast::Expr* expr)
  {
    auto outer = constant_quiet;
    constant_quiet = true;
    try {
      auto found = constant(expr);
      constant_quiet = outer;
      return found;
    } catch (...) {
      constant_quiet = outer;
      throw;
    }
  }


  KnownNumber Checker::constant_number(const ast::Expr* expr)
  {
    auto found = constant(expr);
    if (is_refused(found))
    {
      KnownNumber refused_number;
      refused_number.refused = true;
      return refused_number;
    }
    if (! found || found->failed)
      return KnownNumber{};
    return KnownNumber{ .value = found->value };
  }


  ir::Value* Checker::lower_slice(ir::Builder& builder, const ast::Element* expr, ir::Value* base,
                                  const ir::ArrayType* ty, const ir::Type* expected)
  {
    auto span = expr->span;
    if (expr->indices.size() > ty->rank()) {
      diags.emit(D::LANG_ARRAY_WRONG_RANK, span, { { "given", expr->indices.size() }, { "wanted", ty->rank() } });
      return error_value(module);
    }
    auto [start, lengths] = shape_of(builder, base, ty, span);
    auto strides = strides_of(builder, base, ty, span);
    ir::Value* offset = builder.int_const(ir::u64(), 0);
    std::vector<ir::Value*> kept_lengths;
    std::vector<ir::Value*> kept_strides;
    std::vector<std::optional<std::uint64_t>> shape;
    for (std::size_t at = 0; at < expr->indices.size(); ++at) {
      auto written = expr->indices[at];
      auto along = ty->shape[at].has_value() ? std::optional<std::int64_t>(*ty->shape[at]) : std::nullopt;
      auto range = ast::as<ast::Range>(written);
      if (range == nullptr) {
        auto index = checked_index(builder, written, along, lengths[at], span);
        if (index == nullptr)
          return error_value(module);
        auto step = builder.binary(ir::BinOp::wrap_mul, index, strides[at], span);
        offset = builder.binary(ir::BinOp::wrap_add, offset, step, span);
        continue;
      }
      auto run = run_along(builder, range, along, lengths[at], span);
      if (! run)
        return error_value(module);
      auto [first, count, step] = *run;
      auto moved = builder.binary(ir::BinOp::wrap_mul, first, strides[at], span);
      offset = builder.binary(ir::BinOp::wrap_add, offset, moved, span);
      kept_lengths.push_back(count);
      kept_strides.push_back(builder.binary(ir::BinOp::wrap_mul, strides[at], step, span));
      shape.push_back(std::nullopt);
    }
    for (std::size_t at = expr->indices.size(); at < ty->rank(); ++at) {
      kept_lengths.push_back(lengths[at]);
      kept_strides.push_back(strides[at]);
      shape.push_back(ty->shape[at]);
    }
    auto answer = ir::array_type(ty->element, std::move(shape));
    auto place = element_place(builder, start, ty->element, offset, span);
    auto made = view(builder, place, kept_lengths, kept_strides, answer, span);
    if (! accepts(expected, answer))
      report_mismatch(span, answer, expected);
    return made;
  }


  std::optional<std::tuple<ir::Value*, ir::Value*, ir::Value*>>
  Checker::run_along(ir::Builder& builder, const ast::Range* written, std::optional<std::int64_t> along,
                     ir::Value* length, Span span)
  {
    auto first = checked_index(builder, written->start, along, length, span, true);
    if (first == nullptr)
      return std::nullopt;
    // The end may be the length itself, which is one past the last element
    // and so not an index; it is checked against the length and not below it.
    auto last = on_its_own(builder, written->stop);
    auto reaching = value_type_of(last);
    if (is_error(reaching))
      return std::nullopt;
    auto reaching_int = ir::as<ir::IntType>(reaching);
    if (reaching_int == nullptr) {
      diags.emit(D::LANG_ARRAY_INDEX_NOT_A_NUMBER, written->stop->span, { { "found", reaching->written() } });
      return std::nullopt;
    }
    auto end = as_count(builder, last, reaching_int, span);
    builder.check(builder.compare(ir::CmpPred::ule, end, length, span), "a slice reaching past the end of its array",
                  statuses::OUT_OF_RANGE, span);
    builder.check(builder.compare(ir::CmpPred::ule, first, end, span), "a slice that ends before it begins",
                  statuses::OUT_OF_RANGE, span);
    auto between = builder.binary(ir::BinOp::wrap_sub, end, first, span);
    if (written->step == nullptr)
      return std::tuple{ first, between, builder.int_const(ir::u64(), 1) };
    auto literal = ast::as<ast::IntLit>(written->step);
    if (literal != nullptr && ! literal->value.too_large && literal->value.value < 1) {
      diags.emit(D::LANG_ARRAY_SLICE_HAS_A_STEP, written->step->span);
      return std::nullopt;
    }
    auto told = on_its_own(builder, written->step);
    auto stepping = value_type_of(told);
    if (is_error(stepping))
      return std::nullopt;
    auto stepping_int = ir::as<ir::IntType>(stepping);
    if (stepping_int == nullptr) {
      diags.emit(D::LANG_ARRAY_INDEX_NOT_A_NUMBER, written->step->span, { { "found", stepping->written() } });
      return std::nullopt;
    }
    auto step = as_count(builder, told, stepping_int, span);
    if (literal == nullptr) {
      builder.check(builder.compare(ir::CmpPred::ne, step, builder.int_const(ir::u64(), 0), span),
                    "a slice whose step is not at least one", statuses::OUT_OF_RANGE, span);
      if (stepping_int->is_signed)
        builder.check(builder.compare(ir::CmpPred::sgt, told, builder.int_const(stepping_int, 0), span),
                      "a slice whose step is not at least one", statuses::OUT_OF_RANGE, span);
    }
    // As many as there are of first, first + step, ... below the end: the
    // places between, divided by the step and rounded up.
    auto less = builder.binary(ir::BinOp::wrap_sub, step, builder.int_const(ir::u64(), 1), span);
    auto rounded = builder.binary(ir::BinOp::wrap_add, between, less, span);
    auto count = builder.binary(ir::BinOp::udiv, rounded, step, span);
    return std::tuple{ first, count, step };
  }


  void Checker::lower_element_assign(ir::Builder& builder, const ast::ElementAssign* stmt)
  {
    auto base = array_in_a_field(builder, stmt->base, true);
    if (base == nullptr)
      base = lower_expr(builder, stmt->base, nullptr);
    auto found = value_type_of(base);
    if (is_error(found))
      return;
    if (ir::is<ir::TupleType>(found)) {
      // A tuple is a value and not a place: its members are registers, not
      // room in memory.
      diags.emit(D::LANG_TUPLE_ELEMENT_NOT_A_PLACE, stmt->span);
      return;
    }
    if (ir::is<ir::ListType>(found)) {
      lower_list_element_assign(builder, stmt);
      return;
    }
    auto ty = ir::as<ir::ArrayType>(found);
    if (ty == nullptr) {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, stmt->base->span, { { "found", found->written() } });
      return;
    }
    if (! record_may_change(stmt->base, stmt->span))
      // An element is written only through a name that says it may be: `mut`,
      // as a field is.
      return;
    // The statement as the element it writes, which is what the questions
    // below are asked of.
    auto element = syntax.make<ast::Element>(stmt->span);
    element->base = stmt->base;
    element->indices = stmt->indices;
    auto lent = borrowed_name(element);
    if (lent && lent_out(std::get<0>(*lent), stmt->span, true, std::get<1>(*lent)))
      // A reference to one of its elements is out, and whoever holds it was
      // promised nothing else changes the place.
      return;
    if (std::ranges::any_of(stmt->indices, [](auto one) { return ast::is<ast::Range>(one); })) {
      // A run, a column, a block: the view of those elements, and what is
      // given copied into them as into any array.
      auto slice = syntax.make<ast::Element>(stmt->span);
      slice->base = stmt->base;
      slice->indices = stmt->indices;
      auto picked = lower_slice(builder, slice, base, ty, nullptr);
      if (auto held = ir::as<ir::ArrayType>(value_type_of(picked)))
        array_into(builder, stmt->value, picked, held, stmt->span);
      return;
    }
    // Python hands the statement itself, which has the `base` and `indices`
    // an element has.
    if (auto mask = mask_written(builder, element); mask != nullptr) {
      assign_picked(builder, stmt, base, ty, mask);
      return;
    }
    auto [start, lengths] = shape_of(builder, base, ty, stmt->span);
    auto strides = strides_of(builder, base, ty, stmt->span);
    if (stmt->indices.size() < ty->rank()) {
      // Fewer indices than dimensions name a row, and what is given to a row
      // is copied into its elements, as what is given to an array is.
      auto offset = offset_of(builder, stmt->indices, ty, lengths, strides, stmt->span);
      if (offset == nullptr)
        return;
      auto row = row_at(builder, start, ty, lengths, strides, offset, static_cast<std::int64_t>(stmt->indices.size()),
                        stmt->span);
      auto row_ty = ir::as<ir::ArrayType>(value_type_of(row));
      contract_assert(row_ty != nullptr);
      array_into(builder, stmt->value, row, row_ty, stmt->span);
      return;
    }
    if (! made_here(start))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    auto offset = offset_of(builder, stmt->indices, ty, lengths, strides, stmt->span);
    if (offset == nullptr)
      return;
    auto value = lower_into(builder, stmt->value, ty->element, stmt->span);
    if (is_error(value_type_of(value)))
      return;
    auto place = element_place(builder, start, ty->element, offset, stmt->span);
    if (! owned::points(ty->element)) {
      if (holds_a_lambda(ty->element)) {
        auto kept = into_a_place(builder, start, stmt->base, stmt->value, value, ty->element, stmt->span);
        if (kept == nullptr)
          return;
      }
      builder.store(place, value, stmt->span);
      return;
    }
    // An element that points somewhere is kept as the array's allocator
    // says: as it is where that is safe, copied in otherwise.
    auto root = stmt->base;
    auto root_name = ast::as<ast::NameRef>(root);
    auto container = root_name != nullptr ? find_local(root_name->name) : nullptr;
    auto owner = container != nullptr ? container->holds_in : std::nullopt;
    if (container == nullptr || ! owner) {
      diags.emit(D::LANG_CONTAINER_OWNER_UNKNOWN, stmt->base->span, { { "type", ty->written() } });
      return;
    }
    auto [kept_value, made, spare] = stored(builder, stmt->value, value, ty->element, *owner, container->depth,
                                            ! container->is_parameter, stmt->span);
    value = kept_value;
    if (is_error(value_type_of(value)))
      return;
    if (spare != nullptr)
      owned::give_back(builder, spare, ty->element,
                       freshly_heap(stmt->value) ? std::optional<std::string_view>("heap") : std::nullopt);
    container->arenas.insert(made.begin(), made.end());
    if (*owner != heap_key) {
      // A pool gives back only all at once: what is replaced stays where it is
      // until then, and nothing is called for it.
      builder.store(place, value, stmt->span);
      return;
    }
    // The heap gives back what is replaced, asking the element which allocator
    // it came from -- and what was read out of the array before may be what
    // went.
    auto replaced_value = builder.load(place, stmt->span);
    builder.store(place, value, stmt->span);
    owned::give_back(builder, replaced_value, ty->element, std::nullopt);
    elements_replaced(container->arenas, root_name->name, stmt->span);
  }


  void Checker::lower_member_assign(ir::Builder& builder, const ast::MemberAssign* stmt)
  {
    auto whole = syntax.make<ast::Member>(stmt->span);
    whole->base = stmt->base;
    whole->name = stmt->name;
    whole->name_span = stmt->name_span;
    auto record = said_to_be(stmt->base);
    if (auto ptr = ir::as<ir::PtrType>(record))
      record = ptr->pointee;
    if (auto product = ir::as<ir::ProductType>(record))
      flow(slot_key("field", product->name, stmt->name), stmt->value);
    if (member_named(whole).get<ir::GlobalVar>() != nullptr) {
      // A singleton, `T.name ← …`: one object in the image, never written.
      diags.emit(D::LANG_VARDEF_NOT_MUTABLE, stmt->span, { { "name", path_text(whole) } });
      return;
    }
    if (through_a_list(stmt->base)) {
      list_field_assign(builder, stmt);
      return;
    }
    auto found = record_at(builder, stmt->base, true);
    if (! found) {
      diags.emit(D::LANG_ASSIGN_NOT_A_RECORD, stmt->base->span);
      return;
    }
    auto [where, ty] = *found;
    auto at = field_index(ty, stmt->name);
    if (! at) {
      diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, stmt->name_span, { { "type", ty->written() }, { "name", stmt->name } });
      return;
    }
    if (! lifted(stmt->base, stmt->name, ty) && ! record_may_change(stmt->base, stmt->name_span))
      return;
    auto held = ty->fields[*at].second;
    if (read_only_table(held)) {
      // A field holding a collection the type says nothing may be put in is
      // not a place to put a different collection either.
      diags.emit(D::LANG_PLACE_NOT_WRITABLE, stmt->name_span, { { "name", stmt->name }, { "found", held->written() } });
      return;
    }
    if (! made_here(where))
      // The record may be the caller's, so writing a field of it is a change
      // that outlives the call unless the storage is this call's own.
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    auto place = field_place(builder, where, ty, *at, stmt->span);
    if (auto inner = ir::as<ir::ProductType>(held);
        inner != nullptr && ! holds_a_reference(held) && ! kept_by_the_place(where, stmt->base, held)
        && is_record_literal(stmt->value, inner)) {
      // A record written out goes straight into the place that will hold it,
      // for the reason a definition's does.
      build_record(builder, place, stmt->value, inner, stmt->span);
      return;
    }
    auto value = lower_into(builder, stmt->value, held, stmt->span);
    if (is_error(value_type_of(value)))
      return;
    owning_field(stmt->value, ty, stmt->name, held);
    auto kept = into_a_place(builder, where, stmt->base, stmt->value, value, held, stmt->span);
    if (kept == nullptr)
      return;
    value = kept;
    if (holds_a_reference(held) && ! long_enough_for(where, value, stmt))
      return;
    if (auto inner = ir::as<ir::ProductType>(held)) {
      record_into(builder, place, value, inner, stmt->span);
      return;
    }
    builder.store(place, value, stmt->span);
  }


  void Checker::list_field_assign(ir::Builder& builder, const ast::MemberAssign* stmt)
  {
    written_allocator = nullptr;
    auto found = record_at(builder, stmt->base, true);
    if (! found) [[unlikely]] {
      diags.emit(D::LANG_ASSIGN_NOT_A_RECORD, stmt->base->span);
      return;
    }
    auto [where, ty] = *found;
    auto at = field_index(ty, stmt->name);
    if (! at) [[unlikely]] {
      diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, stmt->name_span, { { "type", ty->written() }, { "name", stmt->name } });
      return;
    }
    auto held = ty->fields[*at].second;
    if (read_only_table(held)) [[unlikely]] {
      diags.emit(D::LANG_PLACE_NOT_WRITABLE, stmt->name_span, { { "name", stmt->name }, { "found", held->written() } });
      return;
    }
    if (! made_here(where))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    auto allocator = written_allocator;
    auto place = field_place(builder, where, ty, *at, stmt->span);
    auto value = lower_into(builder, stmt->value, held, stmt->span);
    if (is_error(value_type_of(value)))
      return;
    if (owned::points(held) && owned::can_own(held) && allocator != nullptr) {
      ir::Value* replaced = builder.load(place, stmt->span);
      value = builder.call(owned::own_function(module, held), std::vector<ir::Value*>{ allocator, value }, held,
                           stmt->span);
      builder.store(place, value, stmt->span);
      owned::give_back(builder, replaced, held, std::nullopt);
      return;
    }
    if (auto inner = ir::as<ir::ProductType>(held)) {
      record_into(builder, place, value, inner, stmt->span);
      return;
    }
    builder.store(place, value, stmt->span);
  }


  std::optional<std::tuple<ir::Value*, const ir::Type*>> Checker::list_element_written(ir::Builder& builder,
                                                                                      const ast::Element* expr)
  {
    if (expr->indices.size() != 1 || ast::is<ast::Range>(expr->indices[0])) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, expr->base->span, { { "found", "a list" } });
      return std::nullopt;
    }
    auto found = list_place(builder, expr->base, expr->base->span, "a field written");
    if (! found)
      return std::nullopt;
    auto [place, ty] = *found;
    ir::Value* at = index_into(builder, expr->indices[0], expr->span);
    if (at == nullptr)
      return std::nullopt;
    made_writable(builder, place, ty, expr->span, expr->base);
    auto [elements, count, held] = list_at(builder, place, ty, expr->span);
    builder.check(builder.compare(ir::CmpPred::ult, at, count, expr->span), "an index outside its list",
                  target::statuses::OUT_OF_RANGE, expr->span);
    written_allocator = builder.extract(held, 2, ir::parts_of(ty)[2], expr->span);
    return std::tuple{ element_place(builder, elements, ty->element, at, expr->span), ty->element };
  }


  bool Checker::kept_by_the_place(ir::Value* where, const ast::Expr* base, const ir::Type* ty)
  {
    if (! (owned::points(ty) || holds_a_lambda(ty)))
      return false;
    return ast::is<ast::Deref>(root_of(base)) || ! made_here(where);
  }


  ir::Value* Checker::into_a_place(ir::Builder& builder, ir::Value* where, const ast::Expr* base,
                                   const ast::Expr* written, ir::Value* value, const ir::Type* ty, Span span,
                                   bool through)
  {
    if (! (owned::points(ty) || holds_a_lambda(ty)))
      return value;
    auto func = std::get<1>(demanding);
    if (! made_here(where)) {
      if (holds_a_lambda(ty)) {
        if (lambdas_last(ty))
          return value;
        return lambda_may_leave(written, KeySet{ heap_key }, func != nullptr ? std::string_view(func->name) : "", span)
                 ? value
                 : nullptr;
      }
      auto made = arenas_in(written);
      // A KeySet is sorted already, as Python's `sorted` makes it.
      std::vector<std::int64_t> stays;
      for (auto one : made)
        if (one != heap_key && one != image_key)
          stays.push_back(one);
      if (stays.empty() && ! (made.contains(heap_key) && shared_heap(written) && owned::can_own(ty)))
        return value;
      if (! owned::can_own(ty)) {
        NameSet named;
        for (auto one : stays)
          named.insert(reach_written(one));
        diags.emit(D::LANG_WRITTEN_BEYOND_THE_CALL, span, { { "found", comma_joined(named, [](const auto& n) { return n; }) } });
        return nullptr;
      }
      if (func != nullptr)
        module.record("copy-into-allocator", func->name,
                      std::string("what is written into a place from outside the call is copied into ")
                        + std::string(names::heap_name) + ", which lasts as long as the caller can read it",
                      span);
      auto own = owned::own_function(module, ty);
      auto owner = owner_place(builder, heap_key, span);
      return builder.call(own, std::vector<ir::Value*>{ owner, value }, ty, span);
    }
    if (! (through || ast::is<ast::Deref>(root_of(base))))
      // Written by name, which `_made_in_part` holds to the name's scope.
      return value;
    auto named = named_place_of(where);
    if (named == nullptr)
      return value;
    auto made = arenas_in(written);
    // ORDER: Python walks a frozenset of ids, whose order is the ids'; the
    // first arena found that is too young is what is said.  Sorted here.
    for (auto one : made) {
      auto arena = arena_by_id.get(one);
      if (arena != nullptr && ! (*arena)->is_parameter && (*arena)->depth > named->depth) {
        outlives_arena(named->name, *arena, span);
        return nullptr;
      }
    }
    named->arenas.insert(made.begin(), made.end());
    return value;
  }


  bool Checker::long_enough_for(ir::Value* where, ir::Value* value, const ast::MemberAssign* stmt)
  {
    auto named = named_place_of(where);
    if (named == nullptr) {
      // Nothing here made the place, so it came in from outside and how long
      // it lasts is not this call's to know.
      if (lasting(value))
        return true;
      diags.emit(D::LANG_REF_WRITTEN_INTO_A_PLACE_FROM_OUTSIDE, stmt->span);
      return false;
    }
    return ! outlives_it(value, named->depth, written_as(stmt->base) + "." + std::string(stmt->name), stmt->span);
  }


  bool Checker::record_may_change(const ast::Expr* expr, Span where)
  {
    if (auto member = ast::as<ast::Member>(expr)) {
      // A path that names a variable at the top level itself -- another
      // module's, or a singleton -- is as mutable as that variable.
      if (auto named = exported_variable(member); named != nullptr) {
        if (sealed(named)) [[unlikely]] {
          refuse_sealed(named, path_text(expr), where);
          return false;
        }
        if (named->is_mutable())
          return true;
        diags.emit(D::LANG_VARDEF_NOT_MUTABLE, where, { { "name", path_text(expr) } });
        return false;
      }
      return record_may_change(member->base, where);
    }
    if (auto element = ast::as<ast::Element>(expr))
      return record_may_change(element->base, where);
    if (auto deref = ast::as<ast::Deref>(expr)) {
      auto name = ast::as<ast::NameRef>(deref->operand);
      if (name == nullptr)
        // A reference worked out rather than named.
        return true;
      auto held = find_local(name->name);
      if (held == nullptr)
        return true;
      auto ty = ir::as<ir::PtrType>(held_by(held));
      if (ty != nullptr && ! ty->is_mutable) {
        diags.emit(D::LANG_REF_NOT_WRITABLE, where, { { "found", ty->written() } });
        return false;
      }
      return true;
    }
    if (auto name = ast::as<ast::NameRef>(expr)) {
      if (lent_to_an_iterator(expr, where))
        return false;
      if (auto local = find_local(name->name); local != nullptr) {
        if (local->is_mutable)
          return true;
        diags.emit(D::LANG_VARDEF_NOT_MUTABLE, where, { { "name", name->name } })
          .note(D::LANG_VARDEF_DEFINED_HERE, local->span, { { "name", name->name } });
        return false;
      }
      auto found = provided(name->name).get<ir::GlobalVar>();
      if (sealed(found)) [[unlikely]] {
        refuse_sealed(found, name->name, where);
        return false;
      }
      if (found != nullptr && ! found->is_mutable()) {
        diags.emit(D::LANG_VARDEF_NOT_MUTABLE, where, { { "name", name->name } });
        return false;
      }
      if (found != nullptr && read_only_table(found->value_type)) {
        // A name at the top level standing for a table nothing may put
        // anything in is not a place to put a different table.
        diags.emit(D::LANG_PLACE_NOT_WRITABLE, where,
                   { { "name", name->name }, { "found", found->value_type->written() } });
        return false;
      }
      return true;
    }
    return true;
  }


  Local* Checker::callee_value(const ast::Expr* expr)
  {
    auto name = ast::as<ast::NameRef>(expr);
    if (name == nullptr)
      return nullptr;
    auto local = find_local(name->name);
    if (local == nullptr || ! ir::is<ir::FuncType>(held_by(local)))
      return nullptr;
    return local;
  }


  ir::Value* Checker::lower_indirect(ir::Builder& builder, const ast::Call* expr, Local* local,
                                     const ir::Type* expected)
  {
    local->read = true;
    if (local->gone_at) {
      // What it brought in went with an arena, or with a container that
      // replaced what it was read out of: calling it would read that.
      told_gone(local, local->name, expr->callee->span);
      return error_value(module);
    }
    auto ty = ir::as<ir::FuncType>(held_by(local));
    contract_assert(ty != nullptr);
    auto held = read_capture(builder, local, expr->span);
    auto key = slot_of_local.get(id_of(local));
    return call_through(builder, expr, held, ty, local->name, key == nullptr ? Held() : Held(*key), expected);
  }


  ir::GlobalVar* Checker::global_callee(const ast::Expr* expr)
  {
    auto name = ast::as<ast::NameRef>(expr);
    if (name == nullptr || find_local(name->name) != nullptr)
      return nullptr;
    auto found = top->get(std::string(name->name));
    if (found == nullptr)
      return nullptr;
    auto var = found->get<ir::GlobalVar>();
    if (var == nullptr || ! ir::is<ir::FuncType>(var->value_type))
      return nullptr;
    return var;
  }


  ir::Value* Checker::lower_through_global(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto name = ast::as<ast::NameRef>(expr->callee);
    contract_assert(name != nullptr);
    auto held = lower_expr(builder, expr->callee, nullptr);
    auto ty = value_type_of(held);
    if (is_error(ty))
      return error_value(module);
    auto fty = ir::as<ir::FuncType>(ty);
    contract_assert(fty != nullptr);
    return call_through(builder, expr, held, fty, name->name, Held(), expected);
  }


  ir::Value* Checker::call_through(ir::Builder& builder, const ast::Call* expr, ir::Value* held,
                                   const ir::FuncType* ty, std::string_view name, Held key,
                                   const ir::Type* expected)
  {
    auto outer_listing = listing;
    listing = ty->listable;
    std::optional<std::vector<ir::Value*>> found_args;
    try {
      found_args = one_by_one(builder, expr->args, std::span<const ir::Type* const>(ty->params), std::string(name));
    } catch (...) {
      listing = outer_listing;
      throw;
    }
    listing = outer_listing;
    if (! found_args)
      return error_value(module);
    auto& args = *found_args;
    if (args.size() != ty->params.size()) {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", name }, { "expected", ty->params.size() }, { "found", args.size() } });
      return error_value(module);
    }
    if (std::ranges::any_of(args, [](auto one) { return is_error(one->ty); }))
      return error_value(module);
    if (ty->impure)
      // What its type promises is what is known of it.  Whether it is needed
      // is the slot's question, where the name has one: Python's
      // `("slot", key)`, its parts joined as a slot key's are.
      an_effect(D::LANG_PURE_CALLS_IMPURE, expr->span, key, { { "name", name } });
    tables_handed(ty->params, args, name, expr->span);
    // Both halves are read once, before any call is made: a walk makes one
    // call per element and they all go to the same code with the same
    // environment.
    auto code = builder.extract(held, 0, environment_type(), expr->span);
    if (ty->system()) {
      // The address and nothing else, called as C calls it: there is no
      // environment to hand over, and no arena a type of this convention could
      // say it hands out.
      auto direct = [&builder, code, ty](std::span<ir::Value* const> given, Span span) {
        std::vector<ir::Value*> all{ code };
        all.insert(all.end(), given.begin(), given.end());
        return builder.call_through(all, ty->ret, ty->cconv, span);
      };
      return answered_through(builder, ty, name, direct, args, expr, expected);
    }
    auto environment = builder.extract(held, 1, environment_type(), expr->span);

    std::vector<ir::Value*> handing;
    if (ty->hands_out) {
      auto place = arena_for(expr, name);
      if (place == nullptr)
        return error_value(module);
      handing.push_back(place);
    }

    auto make = [&builder, code, environment, handing, ty](std::span<ir::Value* const> given, Span span) {
      std::vector<ir::Value*> all{ code, environment };
      all.insert(all.end(), given.begin(), given.end());
      all.insert(all.end(), handing.begin(), handing.end());
      return builder.call(nullptr, all, ty->ret, span);
    };
    return answered_through(builder, ty, name, make, args, expr, expected);
  }


  ir::Value* Checker::answered_through(ir::Builder& builder, const ir::FuncType* ty, std::string_view name,
                                       std::function<ir::Value*(std::span<ir::Value* const>, Span)> make,
                                       std::span<ir::Value* const> args, const ast::Call* expr,
                                       const ir::Type* expected)
  {
    bool walks = false;
    for (std::size_t i = 0; i < args.size(); ++i)
      if (args[i]->ty != ty->params[i])
        walks = true;
    if (ty->listable && walks)
      return walked(builder, ty, make, args, expr, expected);
    auto called = make(args, expr->span);
    auto answer = lasting_as_given(builder, name, {}, borrowed_of(ty), args, called, expr->span);
    if (ty->ret == ir::void_type() && expected != nullptr) {
      diags.emit(D::LANG_CALL_HAS_NO_VALUE, expr->span, { { "name", name } });
      return error_value(module);
    }
    if (! accepts(expected, answer->ty)) {
      report_mismatch(expr->span, answer->ty, expected);
      return error_value(module);
    }
    return answer;
  }


  // -- a generic function checked where it is written -------------------------

  void Checker::check_generic(Generic* written)
  {
    auto node = written->node;
    if (node->body == nullptr)
      return;
    Dict<std::string, const ir::Type*> bound_here;
    for (const auto& name : written->parameters)
      bound_here[name] = ir::new_opaque(name);
    Shapes shapes;
    requirement_shapes(node->clauses, bound_here, shapes, node->name);
    auto told_before = diags.error_count;
    auto logged = module.reports.size();
    // What is put back, whatever the check made of the module.
    auto saved_bound = bound;
    auto saved_abstract = abstract;
    auto saved_discard = discard_function;
    auto saved_lambdas = lambdas;
    auto saved_owned = owned;
    std::set<std::string> kept_functions;
    for (const auto& [key, _] : module.functions)
      kept_functions.insert(key);
    std::set<std::string> kept_globals;
    for (const auto& [key, _] : module.globals)
      kept_globals.insert(key);
    std::set<const ir::GlobalVar*> kept_texts(module.texts.begin(), module.texts.end());
    bound = bound_here;
    abstract = make<Abstract>();
    abstract->name = node->name;
    abstract->shapes = shapes;
    discard_function = false;
    auto quiet = diags.errors_only;
    diags.errors_only = true;
    std::vector<Span> deferred_here;
    bool finished = false;
    auto put_back = [&] {
      bound = std::move(saved_bound);
      abstract = saved_abstract;
      discard_function = saved_discard;
      lambdas = saved_lambdas;
      diags.errors_only = quiet;
      // What it chose is nothing the program will be: only what it said stays
      // in the log.
      auto& entries = module.reports;
      std::vector<ir::Report> after(entries.begin() + static_cast<std::ptrdiff_t>(logged), entries.end());
      entries.resize(logged);
      for (auto& one : after)
        if (said().contains(one.kind))
          entries.push_back(std::move(one));
      // What the check made -- a lambda's function, a string's bytes -- is no
      // part of the program.
      owned = std::move(saved_owned);
      // A variable the compiler provides and keeps a name for -- `⎕heap`, made
      // on its first mention -- is the program's whoever mentioned it.
      std::set<const void*> provided_here;
      for (const auto& [_, held] : *top)
        if (auto value = held.get<ir::Value>())
          provided_here.insert(value);
      for (auto it = module.functions.begin(); it != module.functions.end();)
        if (! kept_functions.contains(it->first) && ! provided_here.contains(it->second.get())) {
          // Kept alive: nothing of the program names it, but what the check
          // made may.
          kept.push_back(std::shared_ptr<ir::Function>(it->second.release()));
          it = module.functions.erase(it);
        } else
          ++it;
      for (auto it = module.globals.begin(); it != module.globals.end();)
        if (! kept_globals.contains(it->first) && ! provided_here.contains(it->second.get())) {
          module.retired_globals.push_back(std::move(it->second));
          it = module.globals.erase(it);
        } else
          ++it;
      for (auto it = module.texts.begin(); it != module.texts.end();)
        if (! kept_texts.contains(*it) && ! provided_here.contains(*it)) {
          std::erase_if(module.text_of, [gone = *it](const auto& one) { return one.second == gone; });
          it = module.texts.erase(it);
        } else
          ++it;
    };
    try {
      std::vector<const ir::Type*> params;
      for (auto one : node->params)
        params.push_back(resolve_type(one->type));
      auto answer = return_type(node->ret_type);
      if (! is_error(answer) && std::ranges::none_of(params, is_error)) {
        auto [attrs, _] = function_attrs(written->attrs);
        auto func = make<ir::Function>(std::string(node->name), function_type(params, answer, { .listable = attrs.listable, .borrows = {} }));
        func->attrs = attrs;
        func->span = node->span;
        func->name_span = node->name_span;
        func->source_path = written->path;
        for (auto one : node->params)
          func->param_names.emplace_back(one->name);
        auto made_in = std::get<0>(made_in_of(node, params, answer));
        if (made_in)
          func->made_in = std::vector<int>(made_in->begin(), made_in->end());
        auto outer_demanding = demanding;
        demanding = { node, func };
        try {
          lower_instance(func, node, node->span);
        } catch (...) {
          demanding = outer_demanding;
          throw;
        }
        demanding = outer_demanding;
        deferred_here = abstract->deferred;
        finished = true;
      }
    } catch (...) {
      put_back();
      throw;
    }
    put_back();
    if (! finished)
      return;
    if (diags.error_count > told_before) {
      written->refused = true;
      return;
    }
    if (! deferred_here.empty())
      module.record("generic-deferred", std::string(node->name),
                    "checked against its requirements where it is written, except " + std::to_string(deferred_here.size())
                      + " 'comptime' construct" + (deferred_here.size() == 1 ? "" : "s")
                      + " asking what the types are, checked where a call says",
                    node->name_span);
    else
      module.record("generic-checked", std::string(node->name),
                    "checked against its requirements where it is written, whether or not anything calls it",
                    node->name_span);
  }


  void Checker::requirement_shapes(std::span<const ast::Clause* const> clauses,
                                   Dict<std::string, const ir::Type*>& bound_now, Shapes& shapes,
                                   std::string_view name)
  {
    for (auto clause : clauses) {
      if (reading_of(clause) != std::optional<bool>(true))
        continue;
      if (auto bundle = bundle_named(clause->expr); bundle != nullptr) {
        auto call = ast::as<ast::Call>(clause->expr);
        contract_assert(call != nullptr);
        Dict<std::string, const ir::Type*> inner;
        for (std::size_t i = 0; i < std::min(call->args.size(), bundle->node->params.size()); ++i)
          if (auto given = type_operand(call->args[i], bound_now); given != nullptr)
            inner[std::string(bundle->node->params[i])] = given;
        if (bundle->opening || inner.size() != bundle->node->params.size())
          continue;
        bundle->opening = true;
        auto kept_top = top;
        auto kept_bundles = bundles;
        if (bundle->scope != nullptr && bundle->siblings != nullptr) {
          top = bundle->scope;
          bundles = bundle->siblings;
        }
        try {
          requirement_shapes(bundle->node->clauses, inner, shapes, name);
        } catch (...) {
          top = kept_top;
          bundles = kept_bundles;
          bundle->opening = false;
          throw;
        }
        top = kept_top;
        bundles = kept_bundles;
        bundle->opening = false;
        continue;
      }
      const ir::Type* answers = nullptr;
      std::vector<std::string> over;
      type_names_in(clause->expr, over);
      if (over.empty()) {
        // Over types written down: the answer is known here, and what the
        // arrow names is that -- `pre(⌜u6⌝ + ⌜u6⌝ → E')` is `u6`.
        met(clause, bound_now, name, clause->span);
        continue;
      }
      if (clause->answers != nullptr) {
        auto written = clause->answers;
        if (auto ref = ast::as<ast::TypeRef>(written); ref != nullptr && ! ref->module && ! ref->result
                                                         && is_generic(ref->name)
                                                         && ! bound_now.contains(std::string(ref->name)))
          bound_now[std::string(ref->name)] = ir::new_opaque(std::string(ref->name));
        auto outer = bound;
        bound = bound_now;
        try {
          answers = resolve_type(written);
        } catch (...) {
          bound = std::move(outer);
          throw;
        }
        bound = std::move(outer);
        if (is_error(answers))
          continue;
      }
      requirement_shape(clause->expr, bound_now, shapes, answers);
    }
  }


  const ir::Type* Checker::requirement_shape(const ast::Expr* expr, const Dict<std::string, const ir::Type*>& bound_now,
                                             Shapes& shapes, const ir::Type* answers)
  {
    if (auto found = type_operand(expr, bound_now); found != nullptr)
      return found;
    if (auto address = ast::as<ast::AddressOf>(expr)) {
      auto inner = requirement_shape(address->operand, bound_now, shapes);
      return inner == nullptr ? nullptr : ir::ptr_type(inner, address->mutable_);
    }
    auto found = applied(expr);
    if (! found)
      return nullptr;
    auto& [kind, what, operands] = *found;
    std::vector<const ir::Type*> types;
    for (auto one : operands)
      types.push_back(requirement_shape(one, bound_now, shapes));
    if (std::ranges::any_of(types, [](auto one) { return one == nullptr; }))
      return nullptr;
    auto key = std::tuple{ kind, what, Types{ types } };
    const ir::Type* result = answers;
    if (result == nullptr) {
      auto earlier = shapes.get(key);
      result = earlier != nullptr && *earlier != nullptr ? *earlier : ir::new_opaque("what " + what + " answers");
    }
    shapes[key] = result;
    return result;
  }


  Held Checker::abstractly(ir::Builder& builder, const ast::Expr* expr)
  {
    contract_assert(abstract != nullptr);
    if (auto call = ast::as<ast::Call>(expr); call != nullptr && ! calls_named(call))
      return Held(expr);
    if (ast::is<ast::Binary>(expr) || ast::is<ast::Unary>(expr) || ast::is<ast::Fresh>(expr)) {
      std::string glyph;
      std::int64_t arity;
      if (auto fresh = ast::as<ast::Fresh>(expr)) {
        glyph = fresh->glyph;
        arity = static_cast<std::int64_t>(fresh->operands.size());
      } else if (auto unary = ast::as<ast::Unary>(expr)) {
        glyph = describe(unary->op);
        arity = 1;
      } else {
        glyph = describe(ast::as<ast::Binary>(expr)->op);
        arity = 2;
      }
      if (operator_written(glyph, arity).get<Generic>() != nullptr)
        // A generic operator is a generic call, held to its own.
        return Held(expr);
    }
    auto found = applied(expr);
    if (! found)
      return Held(expr);
    auto& [kind, what, operands] = *found;
    std::vector<const ir::Type*> hints;
    for (auto one : operands)
      hints.push_back(hint_of(one));
    bool any_opaque = std::ranges::any_of(hints, [](auto one) { return ir::is<ir::OpaqueType>(one); });
    bool all_known = true;
    for (std::size_t i = 0; i < operands.size(); ++i)
      if (hints[i] == nullptr && lowered_first(operands[i]))
        all_known = false;
    if (! any_opaque && all_known)
      return Held(expr);
    std::vector<const ast::Expr*> lowered;
    std::vector<const ir::Type*> types;
    for (auto one : operands) {
      if (lowered_first(one)) {
        auto value = lower_expr(builder, one, nullptr);
        if (is_error(value_type_of(value)))
          // Reported where it went wrong.
          return Held(error_value(module));
        auto ready = syntax.make<ast::Ready>(one->span);
        ready->value = value;
        lowered.push_back(ready);
        types.push_back(value_type_of(value));
      } else {
        // A literal with no suffix, a lambda: it takes its type from where it
        // stands, which beside a type parameter is nowhere.
        lowered.push_back(one);
        types.push_back(hint_of(one));
      }
    }
    if (std::ranges::none_of(types, [](auto one) { return ir::is<ir::OpaqueType>(one); }))
      return Held(static_cast<const ast::Node*>(with_operands(syntax, expr, lowered)));
    bool all_typed = std::ranges::none_of(types, [](auto one) { return one == nullptr; });
    if (all_typed) {
      if (auto shape = abstract->shapes.get(std::tuple{ kind, what, Types{ types } }); shape != nullptr && *shape != nullptr)
        return Held(undef_of(module, *shape));
    }
    auto opaque = *std::ranges::find_if(types, [](auto one) { return ir::is<ir::OpaqueType>(one); });
    auto told = diags.emit(D::LANG_GENERIC_NOT_REQUIRED, expr->span,
                           { { "what", what }, { "type", opaque->written() }, { "function", abstract->name } });
    if (all_typed)
      // What to write: the operation over the types it was applied to, which
      // is the one thing a requirement over other types is not.
      told.note(D::LANG_GENERIC_REQUIREMENT_WANTED, expr->span,
                { { "written", requirement_written(kind, what, types) } });
    return Held(error_value(module));
  }


  bool Checker::calls_named(const ast::Call* expr)
  {
    if (auto callee = ast::as<ast::NameRef>(expr->callee)) {
      if (callee->name.starts_with(names::builtin_glyph) || find_local(callee->name) != nullptr)
        return false;
      auto held = top->get(std::string(callee->name));
      return held != nullptr && held->get<ir::Function>() != nullptr;
    }
    return false;
  }


  ir::Function* Checker::make_instance(Generic* written, const Dict<std::string, const ir::Type*>& bound_now,
                                       std::span<const ir::Type* const> key, Span span)
  {
    auto node = written->node;
    if (written->refused)
      return nullptr;
    auto outer_bound = std::move(bound);
    bound = bound_now;
    auto outer_discard = discard_function;
    discard_function = false;
    Finally restore([&] {
      bound = std::move(outer_bound);
      discard_function = outer_discard;
    });
    std::vector<const ir::Type*> params;
    const ir::Type* answer;
    {
      auto outer_scopes = lifetime_scopes;
      lifetime_scopes.push_back(lifetime_scope(node->params, node));
      Finally put_back([&] { lifetime_scopes = std::move(outer_scopes); });
      for (auto one : node->params)
        params.push_back(resolve_type(one->type));
      answer = return_type(node->ret_type);
    }
    if (is_error(answer) || std::ranges::any_of(params, is_error))
      return nullptr;
    auto [attrs, linkage] = function_attrs(written->attrs);
    auto [made_in, param_made_in] = made_in_of(node, params, answer);
    std::vector<const ast::TypeExpr*> param_types;
    for (auto one : node->params)
      param_types.push_back(one->type);
    auto borrows = borrows_written(param_types, params, node->ret_type, answer,
                                   node->ret_type != nullptr ? node->ret_type->span : node->name_span);
    auto hands_out_name = hands_out_of(node);
    if (hands_out_name)
      params.push_back(ir::ptr_type(ir::arena_type(), true));
    auto fty = function_type(params, answer, { .listable = attrs.listable, .borrows = sorted_borrows(borrows) });
    auto made = std::make_unique<ir::Function>(std::string(node->name), fty);
    auto func = made.get();
    func->hands_out = hands_out_name;
    func->attrs = attrs;
    func->linkage = linkage;
    func->exported = false;
    func->cconv = attrs.abi ? *attrs.abi : std::string(ir::default_cconv);
    func->span = node->span;
    func->name_span = node->name_span;
    func->source_path = written->path;
    for (auto one : node->params)
      func->param_names.emplace_back(one->name);
    for (auto one : writes_arrays(node, params))
      func->writes_arrays.push_back(static_cast<int>(one));
    for (auto one : takes_owned(node, params))
      func->takes_owned.push_back(static_cast<int>(one));
    func->defaults = defaults_of(node, params);
    if (auto whole = borrows.get(-1))
      func->borrows_from = whole->params;
    for (const auto& [leaf, one] : borrows)
      if (leaf >= 0)
        func->leaf_borrows.emplace_back(static_cast<int>(leaf), one.params);
    for (const auto& [a, b] : kept_pairs(node))
      func->keeps.emplace_back(static_cast<int>(a), static_cast<int>(b));
    func->lifetime_key = lifetime_key(node);
    for (const auto& [name, at] : carried_lifetimes(node, params))
      func->lifetimes.emplace_back(name, std::vector<int>(at.begin(), at.end()));
    if (made_in)
      func->made_in = std::vector<int>(made_in->begin(), made_in->end());
    for (auto one : param_made_in)
      func->param_made_in.push_back(one ? std::optional<int>(static_cast<int>(*one)) : std::nullopt);
    // An operator may be defined for one operand and for two, over one type.
    auto filed = std::string(node->name) + "\N{TOP LEFT CORNER}"
                 + comma_joined(key, [](auto one) { return one->mangled(); }, ",") + "\N{TOP RIGHT CORNER}" + "/"
                 + std::to_string(node->params.size());
    module.add_function(std::move(made), this->key(filed));
    node_of_func[id_of(func)] = node;
    owned.push_back(Held(static_cast<ir::Value*>(func)));
    written->made[Types{ std::vector<const ir::Type*>(key.begin(), key.end()) }] = func;
    auto given = comma_joined(key, [](auto one) { return one->written(); });
    module.record("instantiate", std::string(node->name), "compiled for " + given + ", which is what a call gave it",
                  span);
    auto mark = diags.because(D::LANG_GENERIC_ASKED_HERE, span, { { "name", node->name }, { "types", given } });
    Finally no_longer([&] { diags.and_no_longer(mark); });
    lower_instance(func, node, span);
    return func;
  }


  void Checker::lower_instance(ir::Function* func, const ast::FuncDef* node, Span span)
  {
    if (node->body == nullptr || node->body->failed())
      // Said where its body could not be read -- or where the parser lost it on
      // the way, past something it could not read: there is nothing to check.
      return;
    auto block = func->add_block();
    ir::Builder inner(module, *func);
    // Everything about the function being checked is put aside, and put back
    // however this is left.
    auto outer_scopes = std::move(scopes);
    auto outer_addressed = std::move(addressed);
    auto outer_answering = answering;
    auto outer_impure = impure;
    auto outer_carried = std::move(carried);
    auto outer_loops = std::move(loops);
    auto outer_outside = std::move(outside);
    auto outer_initializing = std::move(initializing);
    auto outer_assigning = std::move(assigning);
    auto outer_operand_of = std::move(operand_of);
    auto outer_handing_over = std::move(handing_over);
    auto outer_filling = std::move(filling);
    auto outer_deferred = std::move(deferred);
    auto outer_emptied = std::move(emptied);
    auto outer_answer_made_in = answer_made_in;
    auto outer_out_of_arena = out_of_arena;
    auto outer_out_of_arenas = std::move(out_of_arenas);
    auto outer_made_from = std::move(made_from);
    auto outer_answer_kinds = answer_kinds;
    auto outer_thin_to = thin_to;
    auto outer_answer_arms = answer_arms;
    auto outer_yield_answer = yield_answer;
    auto outer_hands_out = std::move(hands_out);
    auto outer_hands_out_local = hands_out_local;
    auto outer_receiving = std::move(receiving);
    auto outer_unscanned = unscanned;
    auto outer_rooms = std::move(rooms);
    scopes.clear();
    addressed.clear();
    rooms.clear();
    answer_arms = yield_answer = nullptr;
    hands_out.reset();
    hands_out_local = nullptr;
    receiving.reset();
    // What was worked out about where an expression was made belongs to the
    // body it was lowered in: this one lowers the same nodes for other types.
    made_from.clear();
    // Where `in` said room comes from is the expression's around the call, and
    // this body is a function of its own.
    out_of_arena = nullptr;
    out_of_arenas = KeySet{ heap_key };
    // What the body this call stands in put off is that body's.
    deferred.clear();
    emptied.clear();
    answering = func->fty->ret;
    impure = func->attrs.impure;
    carried.clear();
    loops.clear();
    outside.clear();
    initializing.reset();
    assigning.reset();
    operand_of.reset();
    handing_over.reset();
    filling.reset();
    // This body's own conditions, at its entry and at its every `return`.
    auto outer_demanding = demanding;
    demanding = { node, func };
    auto outer_marking = marking;
    marking = marks_for(node, func, nullptr);
    auto outer_unsettled = std::move(unsettled_tables);
    unsettled_tables.clear();
    auto outer_entry = std::move(at_entry);
    at_entry.clear();
    // What the caller has lent out is the caller's.
    auto outer_borrows = std::move(borrows);
    borrows.clear();
    auto outer_changed = std::move(changed_here);
    auto outer_whole = std::move(whole_here);
    Finally restore([&] {
      scopes = std::move(outer_scopes);
      addressed = std::move(outer_addressed);
      answering = outer_answering;
      impure = outer_impure;
      carried = std::move(outer_carried);
      loops = std::move(outer_loops);
      outside = std::move(outer_outside);
      initializing = std::move(outer_initializing);
      assigning = std::move(outer_assigning);
      operand_of = std::move(outer_operand_of);
      handing_over = std::move(outer_handing_over);
      filling = std::move(outer_filling);
      deferred = std::move(outer_deferred);
      emptied = std::move(outer_emptied);
      answer_made_in = std::move(outer_answer_made_in);
      out_of_arena = outer_out_of_arena;
      out_of_arenas = std::move(outer_out_of_arenas);
      made_from = std::move(outer_made_from);
      answer_kinds = std::move(outer_answer_kinds);
      thin_to = outer_thin_to;
      answer_arms = outer_answer_arms;
      yield_answer = outer_yield_answer;
      hands_out = std::move(outer_hands_out);
      hands_out_local = outer_hands_out_local;
      receiving = std::move(outer_receiving);
      unscanned = outer_unscanned;
      rooms = std::move(outer_rooms);
      demanding = outer_demanding;
      at_entry = std::move(outer_entry);
      marking = outer_marking;
      unsettled_tables = std::move(outer_unsettled);
      borrows = std::move(outer_borrows);
      changed_here = std::move(outer_changed);
      whole_here = std::move(outer_whole);
    });
    push_scope();
    contract_assert(node->body != nullptr);
    addressed_in(node->body, addressed);
    read_through(node->body, read_through_ids);
    live_for(node);
    unscanned = node->body;
    changed_here = changed_names(node->body);
    whole_here = used_whole(node->body);
    std::vector<ir::Value*> arriving;
    for (std::size_t i = 0; i < std::min(func->fty->params.size(), node->params.size()); ++i)
      arriving.push_back(block->add_param(func->fty->params[i], std::string(node->params[i]->name)));
    for (std::size_t index = 0; index < arriving.size(); ++index) {
      auto param = node->params[index];
      auto value = arriving[index];
      at_entry[std::string(param->name)] = value;
      if (std::ranges::find(func->takes_owned, static_cast<int>(index)) == func->takes_owned.end())
        value = own_when_changed(inner, param, value, node->body);
      bind_local(param->name, value, param->span, param->mutable_, invalid_span, true, &inner);
    }
    parameter_slots(node, func);
    if (func->hands_out)
      hands_out = std::tuple{ *func->hands_out, static_cast<ir::Value*>(block->add_param(func->fty->params.back(),
                                                                                        *func->hands_out + ".out")),
                              static_cast<std::int64_t>(scopes.size()) };
    params_made_in(node, func);
    lower_conditions(inner, node, func, true);
    rooms_pending = std::pair(node->body, owned_params(node, func));
    lower_block(inner, node->body, func);
    if (! inner.is_terminated())
      all_closed(scopes, node->span);
    if (! inner.is_terminated()) {
      if (func->fty->ret == ir::void_type())
        inner.ret();
      else {
        diags.emit(D::LANG_FUNCDEF_RETURN_MISSING, node->name_span,
                   { { "name", func->name }, { "type", func->fty->ret->written() } });
        inner.unreachable(node->span);
      }
    }
    settle_answer(func, node);
    handed_out_made(func, node);
    pop_scope();
    settle_tables(func);
  }


  ir::Value* Checker::lower_generic(ir::Builder& builder, const ast::Call* expr, Generic* written,
                                    const ir::Type* expected)
  {
    std::vector<const ast::Param*> params;
    if (written->written_params)
      params = *written->written_params;
    else
      params.assign(written->node->params.begin(), written->node->params.end());
    auto handed = generic_arguments(expr, written, params);
    if (! handed)
      return error_value(module);
    Dict<std::string, const ir::Type*> bound_now;
    std::vector<ir::Value*> args;
    for (std::size_t at = 0; at < std::min(handed->size(), params.size()); ++at) {
      auto one = (*handed)[at];
      auto param = params[at];
      if (one == nullptr) {
        // An arena the call names none for: the one it stands in.
        const ir::Type* wanted_arena;
        {
          auto scope = in_scope_of(Held(written));
          wanted_arena = worked_out(param->type, bound_now);
        }
        if (wanted_arena == nullptr || ! is_an_arena_handed(wanted_arena)) {
          diags.emit(D::LANG_CALL_PARAMETER_MISSING, expr->span, { { "name", param->name } });
          return error_value(module);
        }
        args.push_back(out_of(builder, expr->span));
        arena_in_force[id_of(expr)].insert(static_cast<std::int64_t>(at));
        continue;
      }
      if (auto defaulted = ast::as<ast::Defaulted>(one)) {
        // What the definition says it is given where nothing is, read where
        // the definition is written.
        ir::Value* value;
        {
          auto scope = in_scope_of(Held(written));
          auto wanted_default = worked_out(param->type, bound_now);
          value = wanted_default != nullptr ? lower_into(builder, defaulted->value, wanted_default, expr->span)
                                            : lower_expr(builder, defaulted->value, nullptr);
        }
        if (is_error(value_type_of(value)))
          return error_value(module);
        args.push_back(value);
        continue;
      }
      if (auto lifted_type = ast::as<ast::LiftedTypeRef>(param->type)) {
        // A type, read where the call is and settling what the definition
        // names: nothing is handed over.
        auto lifted_value = ast::as<ast::Lifted>(one);
        if (lifted_value == nullptr || lifted_value->written == nullptr) {
          diags.emit(D::LANG_TYPE_ARGUMENT_NOT_A_TYPE, one->span, { { "name", param->name } });
          return error_value(module);
        }
        auto given = resolve_type(lifted_value->written);
        if (is_error(given))
          return error_value(module);
        auto scope = in_scope_of(Held(written));
        if (! reading(lifted_type->inner, given, bound_now, one->span, param->name))
          return error_value(module);
        continue;
      }
      // A parameter's type is read where the function is written, and the
      // argument where the call is.
      const ir::Type* wanted;
      {
        auto scope = in_scope_of(Held(written));
        wanted = worked_out(param->type, bound_now);
      }
      auto outer = handing_over;
      handing_over = std::tuple{ std::string(written->node->name), static_cast<std::int64_t>(at + 1) };
      const ir::Type* wanted_now;
      if (wanted == nullptr && ast::is<ast::IterTypeRef>(param->type))
        // What it walks is not settled, but that it walks is: the argument is
        // made an iterator, and what it gives is read off.
        wanted_now = ir::iter_type(nullptr);
      else
        wanted_now = wanted;
      ir::Value* value;
      {
        Finally put_back([&] { handing_over = outer; });
        value = wanted_now != nullptr ? lower_into(builder, one, wanted_now, one->span)
                                      : lower_expr(builder, one, nullptr);
      }
      auto found = value_type_of(value);
      if (is_error(found))
        return error_value(module);
      if (wanted == nullptr) {
        bool read;
        {
          auto scope = in_scope_of(Held(written));
          read = reading(param->type, found, bound_now, one->span, param->name);
        }
        if (! read)
          return error_value(module);
        const ir::Type* settled_now;
        {
          auto scope = in_scope_of(Held(written));
          settled_now = worked_out(param->type, bound_now);
        }
        auto tabled = [](const ir::Type* ty) { return ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty); };
        if (tabled(settled_now) && settled_now != found && tabled(found) && table_read_only_of(found) == settled_now)
          // A table that may be written handed where one that may only be read
          // is wanted: the same handle, nothing generated for it.
          value = builder.cast(ir::CastKind::bitcast, value, settled_now, one->span);
      }
      args.push_back(value);
    }
    // The requirements come between the arguments and the instance: they are
    // what the types must satisfy, and an arrow in one of them may settle a
    // parameter no argument could.
    auto mark = diags.because(D::LANG_CLAUSE_ASKED_HERE, expr->span,
                              { { "function", written->node->name },
                                { "types", types_given(written->parameters, bound_now) } });
    // From here on it is the definition's file that is being read, not this
    // one.
    auto kept_top = top;
    auto kept_bundles = bundles;
    auto kept_stands = stands;
    auto kept_unit_scopes = unit_scopes;
    if (written->scope != nullptr && written->siblings != nullptr) {
      top = written->scope;
      bundles = written->siblings;
    }
    if (written->stands) {
      stands = *written->stands;
      unit_scopes = { written->units };
    }
    ir::Function* func;
    {
      Finally put_back([&] {
        top = kept_top;
        bundles = kept_bundles;
        stands = std::move(kept_stands);
        unit_scopes = std::move(kept_unit_scopes);
      });
      bool met_all;
      {
        Finally no_longer([&] { diags.and_no_longer(mark); });
        met_all = check_clauses(written->node->clauses, bound_now, written->node->name, expr->span);
      }
      if (! met_all)
        return error_value(module);
      if (! conditions_can_hold(written, bound_now, expr->span))
        return error_value(module);
      for (const auto& name : written->parameters)
        if (! bound_now.contains(name)) {
          diags.emit(D::LANG_GENERIC_NOT_DETERMINED, expr->span, { { "name", name } });
          return error_value(module);
        }
      if (abstract != nullptr
          && std::ranges::any_of(bound_now, [](const auto& one) { return holds_opaque(one.second); })) {
        // Called from a body checked where it is written, with its types: its
        // requirements followed from that body's, which is all there is to
        // ask -- what it does with them is its own check.
        auto outer = std::move(bound);
        bound = bound_now;
        const ir::Type* answer;
        {
          Finally put_back_bound([&] { bound = std::move(outer); });
          answer = return_type(written->node->ret_type);
        }
        return undef_of(module, answer);
      }
      func = instance_of(written, bound_now, expr->span);
    }
    if (func == nullptr)
      return error_value(module);
    return made_call(builder, expr, func, args, expected);
  }


  std::optional<std::vector<const ast::Expr*>> Checker::generic_arguments(const ast::Call* expr, Generic* written,
                                                                          std::span<const ast::Param* const> params)
  {
    std::vector<const ast::Expr*> handed(params.size(), nullptr);
    std::vector<bool> filled(params.size(), false);
    std::size_t place = 0;
    bool named = false;
    auto index_of = [&](std::string_view name) -> std::optional<std::size_t> {
      for (std::size_t i = 0; i < params.size(); ++i)
        if (params[i]->name == name)
          return i;
      return std::nullopt;
    };
    for (auto one : expr->args) {
      if (auto by_name = ast::as<ast::Named>(one)) {
        named = true;
        auto at = index_of(by_name->name);
        if (! at) {
          diags.emit(D::LANG_CALL_UNKNOWN_PARAMETER, by_name->name_span,
                     { { "name", by_name->name }, { "func", written->node->name } });
          return std::nullopt;
        }
        if (filled[*at]) {
          diags.emit(D::LANG_CALL_PARAMETER_TWICE, by_name->name_span, { { "name", by_name->name } });
          return std::nullopt;
        }
        handed[*at] = by_name->value;
        filled[*at] = true;
        continue;
      }
      if (named) {
        diags.emit(D::LANG_CALL_POSITION_AFTER_NAME, one->span);
        return std::nullopt;
      }
      if (place >= params.size()) {
        diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                   { { "name", written->node->name }, { "expected", params.size() }, { "found", expr->args.size() } });
        return std::nullopt;
      }
      handed[place] = one;
      filled[place] = true;
      ++place;
    }
    for (std::size_t at = 0; at < params.size(); ++at) {
      if (filled[at])
        continue;
      auto param = params[at];
      if (param->default_ != nullptr) {
        auto defaulted = syntax.make<ast::Defaulted>(param->default_->span);
        defaulted->value = param->default_;
        handed[at] = defaulted;
      } else if (! could_be_an_arena(param->type)) {
        if (! named && std::ranges::none_of(params, [](auto one) { return one->default_ != nullptr; }))
          diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                     { { "name", written->node->name }, { "expected", params.size() }, { "found", expr->args.size() } });
        else
          diags.emit(D::LANG_CALL_PARAMETER_MISSING, expr->span, { { "name", param->name } });
        return std::nullopt;
      }
    }
    return handed;
  }


  bool Checker::conditions_can_hold(Generic* written, const Dict<std::string, const ir::Type*>& bound_now, Span span)
  {
    auto outer = std::move(bound);
    bound = bound_now;
    Finally put_back([&] { bound = std::move(outer); });
    for (auto clause : written->node->clauses) {
      if (clause->kind != ast::ClauseKind::pre || reading_of(clause) != std::optional<bool>(false)
          || settled(clause->expr) != std::optional<bool>(false))
        continue;
      diags.emit(D::LANG_CLAUSE_CANNOT_HOLD, clause->span, { { "function", written->node->name } })
        .note(D::LANG_GENERIC_ASKED_HERE, span,
              { { "name", written->node->name }, { "types", types_given(written->parameters, bound_now) } });
      return false;
    }
    return true;
  }


  const ir::Type* Checker::worked_out(const ast::TypeExpr* param, const Dict<std::string, const ir::Type*>& bound_now)
  {
    std::vector<std::string> named;
    parameters_in(param, named);
    if (std::ranges::any_of(named, [&](const auto& one) { return ! bound_now.contains(one); }))
      return nullptr;
    auto outer = std::move(bound);
    bound = bound_now;
    const ir::Type* found;
    {
      Finally put_back([&] { bound = std::move(outer); });
      found = resolve_type(param);
    }
    return is_error(found) ? nullptr : found;
  }


  bool Checker::reading(const ast::TypeExpr* param, const ir::Type* found, Dict<std::string, const ir::Type*>& bound_now,
                        Span span, std::string_view name)
  {
    if (auto ref = ast::as<ast::TypeRef>(param); ref != nullptr && ! ref->module && is_generic(ref->name) && ! ref->result) {
      auto earlier = bound_now.get(std::string(ref->name));
      if (earlier != nullptr && *earlier != found) {
        diags.emit(D::LANG_GENERIC_TWO_WAYS, span,
                   { { "name", ref->name }, { "first", (*earlier)->written() }, { "second", found->written() } });
        return false;
      }
      bound_now[std::string(ref->name)] = found;
      return true;
    }
    if (auto array = ast::as<ast::ArrayTypeRef>(param); array != nullptr && ir::is<ir::ArrayType>(found))
      return reading(array->element, ir::as<ir::ArrayType>(found)->element, bound_now, span, name);
    if (auto list = ast::as<ast::ListTypeRef>(param); list != nullptr && ir::is<ir::ListType>(found))
      return reading(list->element, ir::as<ir::ListType>(found)->element, bound_now, span, name);
    if (auto iter = ast::as<ast::IterTypeRef>(param)) {
      auto walked = ir::as<ir::IterType>(found);
      if (walked != nullptr && iter->element != nullptr && walked->element != nullptr)
        return reading(iter->element, walked->element, bound_now, span, name);
    }
    if (auto ref = ast::as<ast::RefTypeRef>(param); ref != nullptr && ir::is<ir::PtrType>(found))
      return reading(ref->pointee, ir::as<ir::PtrType>(found)->pointee, bound_now, span, name);
    if (auto collection = ast::as<ast::CollectionTypeRef>(param)) {
      if (collection->value == nullptr && ir::is<ir::SetType>(found))
        return reading(collection->element, ir::as<ir::SetType>(found)->element, bound_now, span, name);
      if (collection->value != nullptr && ir::is<ir::DictType>(found)) {
        auto dict = ir::as<ir::DictType>(found);
        return reading(collection->element, dict->key, bound_now, span, name)
               && reading(collection->value, dict->value, bound_now, span, name);
      }
    }
    if (auto tuple = ast::as<ast::TupleTypeRef>(param)) {
      auto members = ir::as<ir::TupleType>(found);
      if (members != nullptr && tuple->members.size() == members->members.size()) {
        for (std::size_t i = 0; i < tuple->members.size(); ++i)
          if (! reading(tuple->members[i], members->members[i], bound_now, span, name))
            return false;
        return true;
      }
    }
    if (auto ref = ast::as<ast::TypeRef>(param); ref != nullptr && ! ref->args.empty()) {
      const void* family = nullptr;
      const std::vector<const ir::Type*>* args = nullptr;
      if (auto product = ir::as<ir::ProductType>(found)) {
        family = product->family;
        args = &product->args;
      } else if (auto sum = ir::as<ir::SumType>(found)) {
        family = sum->family;
        args = &sum->args;
      }
      if (family != nullptr && family == template_of(ref) && ref->args.size() == args->size()) {
        // A type with type parameters, given types: what it was given is read
        // as the arguments are.
        for (std::size_t i = 0; i < ref->args.size(); ++i)
          if (! reading(ref->args[i], (*args)[i], bound_now, span, name))
            return false;
        return true;
      }
    }
    if (auto fn = ast::as<ast::FuncTypeRef>(param)) {
      auto fty = ir::as<ir::FuncType>(found);
      if (fty != nullptr && fn->params.size() == fty->params.size()) {
        for (std::size_t i = 0; i < fn->params.size(); ++i)
          if (! reading(fn->params[i], fty->params[i], bound_now, span, name))
            return false;
        if (fn->ret == nullptr)
          return fty->ret == ir::void_type();
        return reading(fn->ret, fty->ret, bound_now, span, name);
      }
    }
    diags.emit(D::LANG_GENERIC_NOT_MATCHED, span, { { "name", name } });
    return false;
  }


  NamedType* Checker::template_of(const ast::TypeRef* ref)
  {
    const Held* found = nullptr;
    if (! ref->module)
      found = top->get(std::string(ref->name));
    else if (auto held = module_called(*ref->module); held != nullptr)
      found = held->exports.get(std::string(ref->name));
    return found != nullptr ? found->get<NamedType>() : nullptr;
  }


  ir::Value* Checker::as_wanted(ir::Builder& builder, ir::Value* value, const ir::Type* wanted, Span span)
  {
    auto found = value_type_of(value);
    if (found == wanted || is_error(found))
      return value;
    auto from = ir::as<ir::ArrayType>(found);
    auto into = ir::as<ir::ArrayType>(wanted);
    if (into != nullptr && ! into->fixed()) {
      if (auto made = as_a_view(builder, nullptr, value, into, span); made != nullptr)
        return made;
    } else if (from != nullptr && into != nullptr && lets_go_of(from, into)) {
      auto [start, lengths] = shape_of(builder, value, from, span);
      auto strides = strides_of(builder, value, from, span);
      return view(builder, start, lengths, strides, into, span);
    }
    report_mismatch(span, found, wanted);
    return error_value(module);
  }


  ir::Function* Checker::instance_of(Generic* written, const Dict<std::string, const ir::Type*>& bound_now, Span span)
  {
    Types key;
    for (const auto& name : written->parameters)
      key.types.push_back(*bound_now.get(name));
    if (auto found = written->made.get(key); found != nullptr && *found != nullptr)
      return *found;
    if (written->making.contains(key)) {
      // It calls itself with the types it already has.
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, span,
                 { { "feature", "a generic function that calls itself with the types it was given" } });
      return nullptr;
    }
    if (static_cast<std::int64_t>(written->making.size()) >= instances_deep) {
      // Each instance asks for one with other types, which asks for another:
      // there would be no end to the functions to make.
      diags.emit(D::LANG_GENERIC_TYPES_GROW, span, { { "name", written->node->name }, { "count", instances_deep } });
      return nullptr;
    }
    written->making.insert(key);
    Finally done([&] { written->making.erase(key); });
    return make_instance(written, bound_now, key.types, span);
  }


  ir::Value* Checker::made_call(ir::Builder& builder, const ast::Call* expr, ir::Function* func,
                                std::span<ir::Value* const> args, const ir::Type* expected)
  {
    if (std::ranges::any_of(args, [](auto one) { return is_error(one->ty); }))
      return error_value(module);
    std::vector<ir::Value*> made;
    for (std::size_t at = 0; at < std::min(args.size(), func->fty->params.size()); ++at) {
      // Where the argument is written, where it is; otherwise the call.
      auto where = at < expr->args.size() && ! ast::is<ast::Named>(expr->args[at]) ? expr->args[at]->span : expr->span;
      auto given = as_wanted(builder, args[at], func->fty->params[at], where);
      if (is_error(value_type_of(given)))
        return error_value(module);
      made.push_back(given);
    }
    if (func->hands_out) {
      auto place = arena_for(expr, func->name);
      if (place == nullptr)
        return error_value(module);
      made.push_back(place);
    }
    auto handed = owned_arguments(builder, expr, func, made);
    flows_of_call(expr, func);
    what_it_changes(func, expr->span);
    tables_handed(func->fty->params, handed, func->name, expr->span);
    kept_alike(func, handed, expr->span);
    auto answer = builder.call(func, handed, func->fty->ret, expr->span);
    answer = as_long_as_given(builder, func, handed, answer, expr->span);
    where_made(expr, func);
    if (func->fty->ret == ir::void_type() && expected != nullptr) {
      diags.emit(D::LANG_CALL_HAS_NO_VALUE, expr->span, { { "name", func->name } });
      return error_value(module);
    }
    if (! accepts(expected, answer->ty)) {
      report_mismatch(expr->span, answer->ty, expected);
      return error_value(module);
    }
    return answer;
  }


  std::optional<bool> Checker::system_convention_of(std::span<const BoundAttr> bound, const ast::FuncTypeRef* ref)
  {
    auto found = std::ranges::find_if(bound, [](const auto& one) { return one.name() == "cdecl"; });
    if (found == bound.end())
      return false;
    if (found->as_bool("variadic")) [[unlikely]] {
      diags.emit(D::LANG_CDECL_TYPE_VARIADIC, found->node->span);
      return std::nullopt;
    }
    if (ref->kept_in) [[unlikely]] {
      diags.emit(D::LANG_CDECL_TYPE_ENVIRONMENT, ref->kept_in->span, { { "what", "in " + std::string(ref->kept_in->name) } });
      return std::nullopt;
    }
    if (ref->made_in) [[unlikely]] {
      diags.emit(D::LANG_CDECL_TYPE_ENVIRONMENT, ref->made_in->span,
                 { { "what", "\N{RIGHTWARDS ARROW} \N{HORIZONTAL ELLIPSIS} in " + std::string(ref->made_in->name) } });
      return std::nullopt;
    }
    return true;
  }


  NameSet Checker::promised(std::span<const ast::Attribute* const> attrs)
  {
    NameSet said;
    for (const auto& one : bind_attributes(attrs, AttrTarget::callable))
      said.emplace(one.name());
    return said;
  }


  bool Checker::walks_what_it_is_given(std::span<const ast::Attribute* const> attrs)
  {
    return std::ranges::any_of(bind_attributes(attrs, AttrTarget::callable),
                               [](const auto& one) { return one.name() == "listable"; });
  }


  // -- lambdas ----------------------------------------------------------------

  ir::Value* Checker::lower_lambda(ir::Builder& builder, const ast::Lambda* expr, const ir::Type* expected)
  {
    // The name comes first so that everything recorded about this lambda can
    // say which one it was.
    ++lambdas;
    auto name = lambda_prefix + std::to_string(lambdas);
    module.record("name-lambda", name, "the code of a lambda written here, which the program left unnamed", expr->span);
    auto found_taken = captures_of(expr, name);
    if (! found_taken)
      return error_value(module);
    auto& taken = *found_taken;
    std::vector<const ir::Type*> params;
    const ir::Type* answer;
    {
      auto outer_scopes = lifetime_scopes;
      lifetime_scopes.push_back(lifetime_scope(expr->params, expr));
      Finally put_back([&] { lifetime_scopes = std::move(outer_scopes); });
      for (auto one : expr->params)
        params.push_back(resolve_type(one->type));
      answer = return_type(expr->ret_type, false);
    }
    if (is_error(answer) || std::ranges::any_of(params, is_error))
      return error_value(module);
    // How long a reference it answers with lives, said as a definition says
    // it, or adopted from the signature it is handed to.
    auto adopted = adopted_borrows(expr, answer, expected);
    Dict<std::int64_t, ir::Borrowed> borrows_said;
    if (adopted)
      borrows_said = *adopted;
    else {
      std::vector<const ast::TypeExpr*> param_types;
      for (auto one : expr->params)
        param_types.push_back(one->type);
      borrows_said = borrows_written(param_types, params, expr->ret_type, answer,
                                     expr->ret_type != nullptr ? expr->ret_type->span : expr->span, true);
    }
    if (! writes_arrays(expr, params).empty()) {
      // Called through the value it is, nothing would say the array it is
      // handed has to be one that may be written.
      diags.emit(D::LANG_WRITES_ARRAYS_AS_A_VALUE, expr->span, { { "name", name } });
      return error_value(module);
    }
    auto said = promised(expr->attrs);
    bool walks = said.contains("listable");
    if (said.contains("cdecl")) [[unlikely]] {
      // What it is, is its code and what it brought in, and the system's
      // convention has nowhere to hand the second over.
      diags.emit(D::LANG_CDECL_LAMBDA, expr->span);
      return error_value(module);
    }
    if (said.contains("impure"))
      // What a lambda's body does is what its type says.
      diags.emit(D::LANG_ATTR_NOT_APPLICABLE, expr->span,
                 { { "name", "impure" }, { "target", "a lambda, whose purity is worked out from its body" } });
    std::optional<ArenaGiven> arena;
    bool lasting_lambda = false;
    if (expr->kept_in) {
      auto [where, at] = *expr->kept_in;
      if (taken.empty() && ! expr->brings_in) {
        // Nothing brought in is kept nowhere: there is nothing to say.
        diags.emit(D::LANG_KEPT_IN_NOTHING_BROUGHT, at);
        return error_value(module);
      }
      if (where == names::heap_name)
        lasting_lambda = true;
      else {
        auto written = syntax.make<ast::NameRef>(at);
        written->name = where;
        auto found = arena_named(builder, written);
        if (found == nullptr)
          return error_value(module);
        arena = ArenaGiven{ found, arenas_in(written) };
      }
    } else if (taken.empty()) {
      // Brings nothing in, so it lasts as long as the program.
      auto wanted = ir::as<ir::FuncType>(aiming_at(expected));
      lasting_lambda = wanted != nullptr && wanted->lasting;
    }
    if (walks && params.empty())
      diags.emit(D::LANG_LISTABLE_TAKES_NOTHING, expr->span, { { "name", name } });
    std::vector<const ir::Type*> held;
    for (const auto& [one, local] : taken)
      held.push_back(held_by_capture(one, local));
    if (std::ranges::any_of(held, is_error))
      return error_value(module);
    if (unscanned != nullptr) {
      lambdas_that_stay(unscanned);
      unscanned = nullptr;
    }
    bool stays_here = false;
    if (! arena && ! expr->kept_in)
      if (auto found = stays.get(id_of(expr)); found != nullptr && *found == expr)
        stays_here = true;
    // What it reaches is worked out before the body is lowered and the
    // environment filled.
    auto reaches = reached_by(builder, taken);
    if (lasting_lambda && ! taken.empty()) {
      // Its type says it lasts, so everything it reaches has to.
      NameSet short_lived_ones;
      for (auto one : reaches)
        if (one != heap_key && one != image_key)
          short_lived_ones.insert(reach_written(one));
      if (! short_lived_ones.empty()) {
        diags.emit(D::LANG_LASTING_LAMBDA_REACHES, expr->kept_in ? expr->kept_in->span : expr->span,
                   { { "name", name }, { "found", comma_joined(short_lived_ones, [](const auto& n) { return n; }) } });
        return error_value(module);
      }
    }
    auto [place, offsets, allocator, kept_here] = environment(builder, taken, held, expr->span, stays_here, arena);
    // What the lambda is being given to, which a mismatch below names.
    auto context_operand_of = operand_of;
    auto context_handing_over = handing_over;
    auto context_assigning = assigning;
    auto context_initializing = initializing;
    auto context_filling = filling;
    ir::Function* func;
    {
      Finally put_back([&] {
        operand_of = context_operand_of;
        handing_over = context_handing_over;
        assigning = context_assigning;
        initializing = context_initializing;
        filling = context_filling;
      });
      {
        auto outer_adopting = std::move(adopting);
        adopting.clear();
        if (adopted)
          for (const auto& [_, one] : *adopted)
            for (const auto& pair : one.outer)
              adopting.insert(std::tuple{ pair.first, pair.second });
        Finally put_back_adopting([&] { adopting = std::move(outer_adopting); });
        func = function_of_a_lambda(expr, params, answer, held, offsets, taken, name, borrows_said);
      }
      if (adopted && lambda_notes && handing_over) {
        // What the call is held to: what it brought in that its answer may be.
        std::vector<std::tuple<bool, Local*>> notes_here;
        for (const auto& [one, local] : taken)
          if (one->by_reference || ir::is<ir::PtrType>(held_by(local)))
            notes_here.emplace_back(one->by_reference, local);
        (*lambda_notes)[std::get<1>(*handing_over) - 1] = std::move(notes_here);
      }
    }
    if (func == nullptr)
      return error_value(module);
    // Made once the body is: whether it changes anything is what lowering it
    // found.
    auto ty = function_type(params, answer,
                            { .listable = walks, .hands_out = hands_out_of(expr).has_value(), .lasting = lasting_lambda,
                              .impure = func->attrs.impure, .can_ignore = said.contains("can_ignore"),
                              .borrows = sorted_borrows(borrows_said) });
    // What it carries is an address and says nothing about what is there.
    auto carried_place = place->ty == environment_type()
                           ? place
                           : builder.cast(ir::CastKind::bitcast, place, environment_type(), expr->span);
    auto code = builder.code_address(func, expr->span);
    auto made = builder.make_tuple(std::vector<ir::Value*>{ code, carried_place, allocator }, ty, expr->span);
    if (stays_here && ! taken.empty()) {
      // The frame it is in goes with the scope it was written in.
      auto frame = make<Local>();
      frame->name = "what " + name + " brought in";
      frame->value = place;
      frame->span = expr->span;
      frame->depth = static_cast<std::int64_t>(scopes.size());
      stand_ins[id_of(frame)] = frame;
      arena_by_id[id_of(frame)] = frame;
      kept_here = KeySet{ id_of(frame) };
    }
    auto reached = kept_here;
    reached.insert(reaches.begin(), reaches.end());
    made_from[id_of(expr)] = std::tuple{ static_cast<const ast::Expr*>(expr), reached };
    report_environment(name, taken, stays_here, arena, expr->span);
    if (! accepts(expected, ty)) {
      report_mismatch(expr->span, ty, expected);
      return error_value(module);
    }
    return made;
  }


  KeySet Checker::reached_by(ir::Builder& builder, std::span<const Taken> taken)
  {
    KeySet found;
    for (const auto& [one, local] : taken) {
      if (one->by_reference) {
        found.insert(storage_key(local));
        continue;
      }
      auto what = held_by(local);
      if (! owned::points_somewhere(what) || local->gone_at)
        continue;
      if (auto fty = ir::as<ir::FuncType>(what); fty != nullptr && fty->lasting)
        // Its type says what it reaches lasts, wherever it came from.
        continue;
      found.insert(local->arenas.begin(), local->arenas.end());
      if (holds_a_reference(what) && ! local->placed)
        if (auto named = named_place_of(local->value); named != nullptr)
          found.insert(storage_key(named));
    }
    return found;
  }


  std::int64_t Checker::storage_key(Local* local)
  {
    if (auto found = storage.get(id_of(local)); found != nullptr && std::get<0>(*found) == local)
      return id_of(std::get<1>(*found));
    auto stand_in = make<Local>();
    stand_in->name = local->name;
    stand_in->value = local->value;
    stand_in->span = local->span;
    stand_in->depth = local->depth;
    storage[id_of(local)] = std::tuple{ local, stand_in };
    stand_ins[id_of(stand_in)] = stand_in;
    arena_by_id[id_of(stand_in)] = stand_in;
    return id_of(stand_in);
  }


  void Checker::report_environment(std::string_view name, std::span<const Taken> taken, bool stays_here,
                                   const std::optional<ArenaGiven>& arena, Span span)
  {
    auto func = std::get<1>(demanding);
    std::string said;
    if (taken.empty())
      said = "brings nothing in, so it carries no environment and lasts as long as the program";
    else if (arena)
      said = "keeps what it brought in in the arena 'in' named";
    else if (stays_here)
      said = "keeps what it brought in in this call's frame: it is only called, or handed to a call, and so provably "
             "stays in the call";
    else
      said = "keeps what it brought in in " + std::string(names::heap_name) + ": it may leave the call that wrote it";
    module.record("allocator", func != nullptr ? func->name : std::string(name), "'" + std::string(name) + "' " + said,
                  span);
  }


  void Checker::lambdas_that_stay(const ast::Node* body)
  {
    NameSet taken;
    std::vector<const ast::Lambda*> handed;
    std::vector<std::pair<std::string, const ast::Lambda*>> bound_lambdas;
    lambdas_handed_on(body, [this](const ast::Call* call) { return calls_a_function(call); }, taken, handed,
                      bound_lambdas);
    for (auto one : handed)
      stays[id_of(one)] = one;
    for (const auto& [name, one] : bound_lambdas)
      // Any other mention of the name anywhere in the body, the same name
      // bound again included: the cautious answer is only an allocation.
      if (! taken.contains(name))
        stays[id_of(one)] = one;
  }


  bool Checker::calls_a_function(const ast::Call* call)
  {
    auto callee = call->callee;
    if (auto name = ast::as<ast::NameRef>(callee)) {
      if (name->name.starts_with(names::builtin_glyph))
        return false;
      // A function, or a name bound inside a body -- which, called, holds a
      // lambda.
      auto named = top->get(std::string(name->name));
      return named == nullptr || named->empty() || named->get<ir::Function>() != nullptr
             || named->get<Generic>() != nullptr;
    }
    if (auto member = ast::as<ast::Member>(callee))
      return module_at(member->base) != nullptr && type_exported(member) == nullptr;
    return false;
  }


  ir::Value* Checker::function_as_a_value(ir::Builder& builder, Held held, const ast::NameRef* ref,
                                          const ir::Type* expected)
  {
    auto func = held.get<ir::Function>();
    contract_assert(func != nullptr);
    if (auto node = node_of_func.get(id_of(func)); node != nullptr)
      // Called through a value, what its parameters are given is not seen.
      for (std::size_t at = 0; at < (*node)->params.size(); ++at)
        slots_outside.insert(slot_key("param", id_of(*node), at));
    if (! func->writes_arrays.empty()) {
      diags.emit(D::LANG_WRITES_ARRAYS_AS_A_VALUE, ref->span, { { "name", func->name } });
      return error_value(module);
    }
    if (func->hands_out) {
      // Called through a value, it would hand its arena out where nothing
      // receives it by name.
      diags.emit(D::LANG_HANDED_OUT_UNBOUND, ref->span, { { "name", func->name } });
      return error_value(module);
    }
    if (func->cconv == ir::system_cconv)
      return system_function_value(builder, func, ref, expected);
    // A call through a value passes the environment in the chain register,
    // which a function of the language's own convention never reads: the value
    // points at the function itself.  One of a convention named by an
    // architecture's name is called the way that convention calls, so
    // something has to stand between.
    auto shim = func->cconv == ir::default_cconv && ! func->attrs.chained && func->takes_owned.empty()
                  ? func
                  : shim_for(func, ref->span);
    if (shim == nullptr)
      return error_value(module);
    auto wanted = ir::as<ir::FuncType>(aiming_at(expected));
    auto ty = function_type(func->fty->params, func->fty->ret,
                            { .listable = func->fty->listable, .hands_out = func->fty->hands_out,
                              .lasting = wanted != nullptr && wanted->lasting, .impure = func->attrs.impure,
                              .can_ignore = func->attrs.can_ignore, .borrows = {} });
    auto code = builder.code_address(shim, ref->span);
    auto nothing = builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), environment_type(), ref->span);
    auto no_allocator = tables::no_allocator(builder);
    auto made = builder.make_tuple(std::vector<ir::Value*>{ code, nothing, no_allocator }, ty, ref->span);
    if (! accepts(expected, ty)) {
      report_mismatch(ref->span, ty, expected);
      return error_value(module);
    }
    return made;
  }


  ir::Value* Checker::system_function_value(ir::Builder& builder, ir::Function* func, const ast::NameRef* ref,
                                             const ir::Type* expected)
  {
    if (func->attrs.variadic) [[unlikely]] {
      // It takes more than its type says, so no type could place a call of it.
      diags.emit(D::LANG_CALL_NOT_A_FUNCTION, ref->span, { { "name", func->name } });
      return error_value(module);
    }
    if (! func->takes_owned.empty()) [[unlikely]] {
      // A call of an address has nothing to stand between and make the copy.
      diags.emit(D::LANG_CDECL_VALUE_TAKES_OWNED, ref->span, { { "name", func->name } });
      return error_value(module);
    }
    auto ty = function_type(func->fty->params, func->fty->ret,
                            { .listable = func->fty->listable, .impure = func->attrs.impure,
                              .can_ignore = func->attrs.can_ignore, .borrows = {}, .cconv = ir::system_cconv });
    auto made = builder.make_tuple(std::vector<ir::Value*>{ builder.code_address(func, ref->span) }, ty, ref->span);
    if (! accepts(expected, ty)) {
      report_mismatch(ref->span, ty, expected);
      return error_value(module);
    }
    return made;
  }


  ir::Function* Checker::shim_for(ir::Function* func, Span span)
  {
    if (auto found = shims.get(id_of(func)); found != nullptr)
      return *found;
    if (func->attrs.abi && func->attrs.variadic) {
      // What such a call takes is not what its type says, so there is no shim
      // that could stand for it.
      diags.emit(D::LANG_CALL_NOT_A_FUNCTION, span, { { "name", func->name } });
      return nullptr;
    }
    auto name = shim_prefix + func->name;
    std::vector<const ir::Type*> taking{ environment_type() };
    taking.insert(taking.end(), func->fty->params.begin(), func->fty->params.end());
    auto made = std::make_unique<ir::Function>(name, function_type(taking, func->fty->ret));
    auto shim = made.get();
    shim->attrs.impure = true;
    shim->attrs.chained = true;
    shim->linkage = ir::Linkage::internal;
    shim->span = func->span;
    shim->name_span = func->name_span;
    shim->source_path = path.generic_string();
    shim->param_names.emplace_back("");
    shim->param_names.insert(shim->param_names.end(), func->param_names.begin(), func->param_names.end());
    module.add_function(std::move(made), key(name));
    owned.push_back(Held(static_cast<ir::Value*>(shim)));
    auto block = shim->add_block();
    ir::Builder inner(module, *shim);
    std::vector<ir::Value*> arriving;
    for (auto one : taking)
      arriving.push_back(block->add_param(one, ""));
    std::vector<ir::Value*> handed(arriving.begin() + 1, arriving.end());
    for (auto at : func->takes_owned)
      // Called through a value, nothing knows to copy what the function takes
      // as its own: the shim does.
      handed[static_cast<std::size_t>(at)] = copy_into_heap(inner, handed[static_cast<std::size_t>(at)], func->span);
    auto answer = inner.call(func, handed, func->fty->ret, func->span);
    inner.ret(func->fty->ret == ir::void_type() ? nullptr : answer, func->span);
    shims[id_of(func)] = shim;
    module.record("name-lambda", name,
                  "code that hands an indirect call on to '" + func->name
                    + "', which the program named where a value was wanted",
                  span);
    return shim;
  }


  std::optional<std::vector<Checker::Taken>> Checker::captures_of(const ast::Lambda* expr, std::string_view name)
  {
    std::vector<const ast::Capture*> written;
    if (expr->brings_in)
      written = everything_reached(expr, name);
    else
      written.assign(expr->captures.begin(), expr->captures.end());
    std::vector<Taken> found;
    NameSet seen;
    bool spoiled = false;
    for (auto one : written) {
      if (seen.contains(one->name)) {
        diags.emit(D::LANG_CAPTURE_TWICE, one->span, { { "name", one->name } });
        spoiled = true;
        continue;
      }
      seen.emplace(one->name);
      auto local = find_local(one->name);
      if (local == nullptr) {
        diags.emit(D::LANG_CAPTURE_UNKNOWN, one->span, { { "name", one->name } });
        spoiled = true;
        continue;
      }
      local->read = true;
      auto held = held_by(local);
      if (held != nullptr)
        if (auto closable = closable_in(held); closable != nullptr) {
          diags.emit(D::LANG_CLOSABLE_STORED, one->span,
                     { { "type", closable->written() }, { "what", "what a lambda brings in" } });
          spoiled = true;
          continue;
        }
      if (local->iterable != nullptr || (held != nullptr && short_lived(held) != nullptr)) {
        // What it walks is described in this call's room, which a lambda may
        // outlive.
        diags.emit(D::LANG_ITERATOR_DOES_NOT_LEAVE, one->span,
                   { { "found", held != nullptr ? held->written() : std::string("an iterator") },
                     { "what", "brought into a lambda" } });
        spoiled = true;
        continue;
      }
      found.emplace_back(one, local);
    }
    if (spoiled)
      return std::nullopt;
    return found;
  }


  std::vector<const ast::Capture*> Checker::everything_reached(const ast::Lambda* expr, std::string_view name)
  {
    std::vector<std::string> seen;
    named_in(expr->body, seen);
    NameSet inside_body;
    for (auto one : expr->params)
      inside_body.emplace(one->name);
    bound_in(expr->body, inside_body);
    bool by_reference = expr->brings_in == ast::CaptureAll::by_reference;
    std::vector<const ast::Capture*> found;
    for (const auto& one : seen)
      if (! inside_body.contains(one) && find_local(one) != nullptr) {
        auto capture = syntax.make<ast::Capture>(expr->span);
        capture->name = syntax.text(one);
        capture->by_reference = by_reference;
        found.push_back(capture);
      }
    // The program wrote `[=]` or `[&]` and not the names, so which names those
    // turned out to be is the compiler's answer -- which is what the log is
    // for.
    for (auto one : found)
      module.record("capture", std::string(one->name),
                    "brought into '" + std::string(name) + "' " + (by_reference ? "by reference" : "by value")
                      + ", which is what '" + std::string(describe(*expr->brings_in))
                      + "' said of every name its body reaches",
                    expr->span);
    return found;
  }


  const ir::Type* Checker::held_by_capture(const ast::Capture* one, Local* local)
  {
    auto held = held_by(local);
    if (! one->by_reference)
      return held;
    if (! local->placed) {
      diags.emit(D::LANG_REF_TYPE_NOT_ALLOWED, one->span, { { "found", held->written() } });
      return ir::error_type();
    }
    return ir::ptr_type(held, local->is_mutable);
  }


  std::tuple<ir::Value*, std::vector<std::int64_t>, ir::Value*, KeySet>
  Checker::environment(ir::Builder& builder, std::span<const Taken> taken, std::span<const ir::Type* const> held,
                       Span span, bool stays_here, const std::optional<ArenaGiven>& arena)
  {
    if (taken.empty()) {
      // Room for nothing, which is nowhere.
      auto nothing = builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), environment_type(), span);
      return { nothing, {}, tables::no_allocator(builder), KeySet{} };
    }
    std::vector<const ir::Type*> slots;
    for (auto one : held)
      slots.push_back(slot_for(one));
    const ir::Type* inside_ty;
    std::vector<std::int64_t> offsets;
    if (slots.size() == 1) {
      inside_ty = slots[0];
      offsets = { 0 };
    } else {
      auto tuple = ir::tuple_type(slots);
      inside_ty = tuple;
      for (auto one : ir::member_offsets_of(tuple, layout()))
        offsets.push_back(static_cast<std::int64_t>(one));
    }
    ir::Value* place;
    ir::Value* allocator;
    KeySet kept_in;
    if (stays_here) {
      place = builder.frame(inside_ty, span);
      allocator = tables::no_allocator(builder);
    } else {
      if (arena) {
        allocator = arena->place;
        kept_in = arena->made;
      } else {
        allocator = owner_place(builder, heap_key, span);
        kept_in = KeySet{ heap_key };
      }
      auto size = builder.int_const(ir::u64(), static_cast<__int128>(std::max<std::uint64_t>(ir::size_of(inside_ty, layout()), 1)));
      place = tables::allocate(builder, allocator, size);
    }
    for (std::size_t at = 0; at < taken.size(); ++at) {
      auto [one, local] = taken[at];
      auto what = held[at];
      auto value = read_capture(builder, local, span);
      if (one->by_reference)
        // The place itself, read as the reference the body will have.
        value = local->value->ty == what ? local->value
                                         : builder.cast(ir::CastKind::bitcast, local->value, what, span);
      put_away(builder, place, offsets[at], what, value, span);
    }
    return { place, offsets, allocator, kept_in };
  }


  const ir::Type* Checker::slot_for(const ir::Type* what)
  {
    auto pieces = ir::parts_of(what);
    return pieces.size() == 1 ? what : ir::tuple_type(pieces);
  }


  void Checker::put_away(ir::Builder& builder, ir::Value* place, std::int64_t offset, const ir::Type* what,
                         ir::Value* value, Span span)
  {
    auto pieces = ir::parts_of(what);
    if (pieces.size() == 1) {
      builder.store(inside(builder, place, offset, what, span), value, span);
      return;
    }
    auto slot = ir::tuple_type(pieces);
    auto inner = ir::member_offsets_of(slot, layout());
    for (std::size_t at = 0; at < pieces.size(); ++at) {
      auto where = inside(builder, place, offset + static_cast<std::int64_t>(inner[at]), pieces[at], span);
      auto part = builder.extract(value, static_cast<std::int64_t>(at), pieces[at], span);
      builder.store(where, part, span);
    }
  }


  ir::Value* Checker::taken_out(ir::Builder& builder, ir::Value* place, std::int64_t offset, const ir::Type* what,
                                Span span)
  {
    auto pieces = ir::parts_of(what);
    if (pieces.size() == 1)
      return builder.load(inside(builder, place, offset, what, span), span);
    auto slot = ir::tuple_type(pieces);
    auto inner = ir::member_offsets_of(slot, layout());
    std::vector<ir::Value*> parts;
    for (std::size_t at = 0; at < pieces.size(); ++at)
      parts.push_back(builder.load(inside(builder, place, offset + static_cast<std::int64_t>(inner[at]), pieces[at], span),
                                   span));
    return builder.make_tuple(parts, what, span);
  }


  ir::Value* Checker::read_capture(ir::Builder& builder, Local* local, Span span)
  {
    return local->placed ? builder.load(local->value, span) : local->value;
  }


  ir::Value* Checker::inside(ir::Builder& builder, ir::Value* place, std::int64_t offset, const ir::Type* what,
                             Span span)
  {
    auto start = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(what, true), span);
    if (offset == 0)
      return start;
    return builder.binary(ir::BinOp::add, start, builder.int_const(ir::u64(), offset), span);
  }


  ir::Function* Checker::function_of_a_lambda(const ast::Lambda* expr, std::span<const ir::Type* const> params,
                                              const ir::Type* answer, std::span<const ir::Type* const> held,
                                              std::span<const std::int64_t> offsets, std::span<const Taken> taken,
                                              std::string_view name, const Dict<std::int64_t, ir::Borrowed>& borrows_said)
  {
    auto hands_out_name = hands_out_of(expr);
    std::vector<const ir::Type*> hidden;
    if (hands_out_name)
      hidden.push_back(ir::ptr_type(ir::arena_type(), true));
    std::vector<const ir::Type*> taking{ environment_type() };
    taking.insert(taking.end(), params.begin(), params.end());
    std::vector<const ir::Type*> all = taking;
    all.insert(all.end(), hidden.begin(), hidden.end());
    auto made = std::make_unique<ir::Function>(std::string(name), function_type(all, answer));
    auto func = made.get();
    func->attrs.impure = true;
    func->attrs.chained = true;
    func->linkage = ir::Linkage::internal;
    func->span = expr->span;
    func->name_span = expr->span;
    func->source_path = path.generic_string();
    func->param_names.emplace_back("");
    for (auto one : expr->params)
      func->param_names.emplace_back(one->name);
    func->hands_out = hands_out_name;
    if (hands_out_name)
      func->made_in = std::vector<int>{ static_cast<int>(hands_at) };
    module.add_function(std::move(made), key(name));
    // Owned by the file it is written in, as every other definition is.
    owned.push_back(Held(static_cast<ir::Value*>(func)));
    auto block = func->add_block();
    ir::Builder inner(module, *func);
    std::vector<ir::Value*> arriving;
    for (auto one : taking)
      arriving.push_back(block->add_param(one, ""));
    auto outer_scopes = std::move(scopes);
    auto outer_addressed = std::move(addressed);
    auto outer_answering = answering;
    auto outer_impure = impure;
    auto outer_carried = std::move(carried);
    auto outer_loops = std::move(loops);
    auto outer_outside = outside;
    auto outer_deferred = std::move(deferred);
    auto outer_emptied = std::move(emptied);
    auto outer_out_of_arena = out_of_arena;
    auto outer_out_of_arenas = std::move(out_of_arenas);
    auto outer_answer_made_in = std::move(answer_made_in);
    auto outer_made_from = std::move(made_from);
    auto outer_answer_kinds = std::move(answer_kinds);
    auto outer_thin_to = thin_to;
    auto outer_answer_arms = answer_arms;
    auto outer_yield_answer = yield_answer;
    auto outer_hands_out = std::move(hands_out);
    auto outer_hands_out_local = hands_out_local;
    auto outer_receiving = std::move(receiving);
    auto outer_rooms = std::move(rooms);
    deferred.clear();
    emptied.clear();
    rooms.clear();
    made_from.clear();
    answer_kinds.clear();
    thin_to.reset();
    answer_arms = yield_answer = nullptr;
    hands_out.reset();
    hands_out_local = nullptr;
    receiving.reset();
    out_of_arena = nullptr;
    out_of_arenas = KeySet{ heap_key };
    // A lambda answers in `⎕heap`, saying nothing else, as a function does --
    // or in an arena of its own it hands out, where it says so.
    answer_made_in = std::tuple{ KeySet{ heap_key }, std::string(names::heap_name) };
    if (hands_out_name)
      answer_made_in = std::tuple{ KeySet{}, "'" + *hands_out_name + "'" };
    // A lambda's body binds its own names, so a reference out in the body
    // around it says nothing about a name of the same spelling in here.
    auto outer_borrows = std::move(borrows);
    borrows.clear();
    // The scopes around it, which its body may not reach: the moved-out list
    // keeps its dictionaries where they were, so pointing at them holds.
    for (auto& scope : outer_scopes)
      outside.push_back(&scope);
    scopes.clear();
    addressed.clear();
    // Pure, but observed: whatever the body changes is noted rather than
    // reported, and the lambda's type says what was noted.
    auto outer_observing = std::move(observing);
    observing = std::vector<bool>{ false };
    auto outer_unsettled = std::move(unsettled_tables);
    unsettled_tables.clear();
    auto outer_marking = marking;
    {
      auto& marks_here = lambda_marks[id_of(expr)];
      if (marks_here == nullptr) {
        marks_here = make<Marks>();
        marks_here->node = expr;
      }
      marking = marks_here;
    }
    answering = answer;
    impure = false;
    carried.clear();
    loops.clear();
    std::optional<std::vector<bool>> observed;
    {
      Finally restore([&] {
        scopes = std::move(outer_scopes);
        addressed = std::move(outer_addressed);
        answering = outer_answering;
        impure = outer_impure;
        carried = std::move(outer_carried);
        loops = std::move(outer_loops);
        outside = std::move(outer_outside);
        deferred = std::move(outer_deferred);
        emptied = std::move(outer_emptied);
        rooms = std::move(outer_rooms);
        out_of_arena = outer_out_of_arena;
        out_of_arenas = std::move(outer_out_of_arenas);
        answer_made_in = std::move(outer_answer_made_in);
        made_from = std::move(outer_made_from);
        answer_kinds = std::move(outer_answer_kinds);
        thin_to = outer_thin_to;
        answer_arms = outer_answer_arms;
        yield_answer = outer_yield_answer;
        hands_out = std::move(outer_hands_out);
        hands_out_local = outer_hands_out_local;
        receiving = std::move(outer_receiving);
        borrows = std::move(outer_borrows);
        observed = std::move(observing);
        observing = std::move(outer_observing);
        unsettled_tables = std::move(outer_unsettled);
        marking = outer_marking;
      });
      push_scope();
      if (hands_out_name)
        hands_out = std::tuple{ *hands_out_name,
                                static_cast<ir::Value*>(block->add_param(hidden[0], *hands_out_name + ".out")),
                                static_cast<std::int64_t>(scopes.size()) };
      addressed_in(expr->body, addressed);
      read_through(expr->body, read_through_ids);
      live_for(expr);
      std::vector<Taken> brought;
      std::vector<std::tuple<Local*, Local*, bool>> inherited;
      for (std::size_t at = 0; at < taken.size(); ++at) {
        auto [one, local] = taken[at];
        auto what = held[at];
        // What is in the environment is what was put there: the value for a
        // capture by value, and the address of the variable for one by
        // reference -- which the name then stands for.
        auto inside_value = taken_out(inner, arriving[0], offsets[at], what, expr->span);
        auto pointer = ir::as<ir::PtrType>(what);
        bind_local(one->name, inside_value, one->span, one->by_reference ? local->is_mutable : false, invalid_span,
                   false, &inner, pointer != nullptr ? pointer->pointee : nullptr);
        if (auto bound_here = find_local(one->name); bound_here != nullptr) {
          // Its `mut` is the variable's, reported where that is.
          mut_told.insert(bound_here->span.start);
          brought.emplace_back(one, bound_here);
          inherited.emplace_back(local, bound_here, one->by_reference);
        }
      }
      for (std::size_t i = 0; i < expr->params.size() && i + 1 < arriving.size(); ++i) {
        auto one = expr->params[i];
        auto value = own_when_changed(inner, one, arriving[i + 1], expr->body);
        bind_local(one->name, value, one->span, one->mutable_, invalid_span, true, &inner);
      }
      std::span<ir::Value* const> handed_in(arriving.begin() + 1, arriving.end());
      auto outer_lifetimes = enter_lifetimes(lifetime_key(expr), expr->params, params, handed_in, inherited);
      for (const auto& pair : adopting) {
        // A name of the signature it is handed to: at that call it stands for
        // whatever the call says, so here for every reference the lambda
        // brought in.
        std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>, std::string, bool>> sources;
        for (const auto& [outside_local, inside_local, by_reference] : inherited) {
          bool reference = ir::is<ir::PtrType>(held_by(outside_local));
          if (by_reference || reference)
            sources.emplace_back(inside_local->value, std::nullopt, inside_local->name, by_reference && reference);
        }
        lifetime_sources[pair] = std::move(sources);
      }
      auto calling = std::move(calling_key);
      calling_key.reset();
      auto notes_kept = std::move(lambda_notes);
      lambda_notes.reset();
      // What a lambda is handed, and what it brought in, may have been made
      // anywhere: it carries its allocator, and lasts as long as the call.
      for (auto& scope : scopes)
        for (auto& [_, local] : scope) {
          auto held_there = held_by(local);
          if (! is_arena(local) && (owned::points(held_there) || holds_a_lambda(held_there))) {
            local->arenas = KeySet{ id_of(local) };
            unknown_by_id[id_of(local)] = local;
          }
        }
      auto changed_kept = changed_here;
      auto whole_kept = whole_here;
      {
        NameSet changed = changed_here ? *changed_here : NameSet{};
        auto more = changed_names(expr->body);
        changed.insert(more.begin(), more.end());
        changed_here = std::move(changed);
        auto whole = used_whole(expr->body);
        whole_here.insert(whole.begin(), whole.end());
      }
      lower_block(inner, expr->body, func);
      changed_here = std::move(changed_kept);
      whole_here = std::move(whole_kept);
      std::vector<std::string> param_names;
      for (auto one : expr->params)
        param_names.emplace_back(one->name);
      answered_as_said(func, borrows_said, param_names, handed_in);
      calling_key = std::move(calling);
      lambda_notes = std::move(notes_kept);
      lifetime_scopes = std::move(outer_lifetimes.scopes);
      lifetime_sources = std::move(outer_lifetimes.sources);
      all_of_it_used(brought);
      if (! inner.is_terminated())
        all_closed(scopes, expr->span);
      if (! inner.is_terminated()) {
        if (answer == ir::void_type())
          inner.ret();
        else {
          diags.emit(D::LANG_FUNCDEF_RETURN_MISSING, expr->span, { { "name", name }, { "type", answer->written() } });
          // What was built has to be well formed all the same.
          inner.unreachable(expr->span);
        }
      }
      handed_out_made(func, expr);
      pop_scope();
      settle_tables(func);
    }
    func->attrs.impure = observed && ! observed->empty() && (*observed)[0];
    return func;
  }


  void Checker::all_of_it_used(std::span<const Taken> brought)
  {
    for (const auto& [one, bound_here] : brought) {
      if (bound_here->read || bound_here->written)
        // Written and not read is using it: a name brought in by reference may
        // be brought in to be written.
        continue;
      diags.emit(D::LANG_CAPTURE_NOT_USED, one->span, { { "name", one->name } });
      bound_here->read = true;
    }
  }


  // -- references -------------------------------------------------------------

  void Checker::lower_deref_assign(ir::Builder& builder, const ast::DerefAssign* stmt)
  {
    if (auto name = ast::as<ast::NameRef>(stmt->target);
        name != nullptr && find_local(name->name) != nullptr && lent_out(name->name, stmt->span, true))
      // A copy of the reference is out and was promised the place does not
      // change under it.
      return;
    auto target = lower_expr(builder, stmt->target, nullptr);
    auto found = value_type_of(target);
    if (is_error(found))
      return;
    auto ty = ir::as<ir::PtrType>(found);
    if (ty == nullptr) {
      diags.emit(D::LANG_DEREF_NOT_A_REFERENCE, stmt->target->span, { { "found", found->written() } });
      return;
    }
    if (! ty->is_mutable) {
      diags.emit(D::LANG_REF_NOT_WRITABLE, stmt->span, { { "found", ty->written() } });
      return;
    }
    if (! made_here(target))
      // The place may be the caller's, so writing through it is a change that
      // outlives the call unless the storage is this call's own.
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    auto value = lower_into(builder, stmt->value, ty->pointee, stmt->span);
    if (is_error(value_type_of(value)))
      return;
    auto kept = into_a_place(builder, target, stmt->target, stmt->value, value, ty->pointee, stmt->span, true);
    if (kept == nullptr)
      return;
    value = kept;
    if (holds_a_reference(ty->pointee)) {
      // The place written holds a reference, so what goes into it has to last
      // as long as it does.
      auto named = named_place_of(target);
      if (named == nullptr) {
        // Nothing here made the place, so it came in from outside and how long
        // it lasts is the caller's business.
        if (! lasting(value) && ! said_alike(target, value)) {
          diags.emit(D::LANG_REF_WRITTEN_INTO_A_PLACE_FROM_OUTSIDE, stmt->span);
          return;
        }
      } else if (outlives_it(value, named->depth, written_as(stmt->target) + std::string(names::deref_glyph),
                             stmt->span))
        return;
    }
    builder.store(target, value, stmt->span);
  }


  ir::Value* Checker::lower_deref(ir::Builder& builder, const ast::Deref* expr, const ir::Type* expected)
  {
    auto value = lower_expr(builder, expr->operand, nullptr);
    auto found = value_type_of(value);
    if (is_error(found))
      return error_value(module);
    if (auto cursor = ir::as<ir::CursorType>(found)) {
      auto answer = through_a_cursor(builder, value, cursor, expr->span);
      if (! accepts(expected, value_type_of(answer)))
        report_mismatch(expr->span, value_type_of(answer), expected);
      return answer;
    }
    if (! ir::is<ir::PtrType>(found)) {
      diags.emit(D::LANG_DEREF_NOT_A_REFERENCE, expr->span, { { "found", found->written() } });
      return error_value(module);
    }
    auto answer = loaded_or_in_place(builder, value, expr, expr->span);
    if (! accepts(expected, answer->ty))
      report_mismatch(expr->span, answer->ty, expected);
    return answer;
  }


  void Checker::statement_ended()
  {
    if (! borrows.empty())
      std::erase_if(borrows, [](auto b) { return b->depth == until_the_statement_ends; });
  }


  void Checker::kept_by_a_name(const ir::Type* ty, Span keeper)
  {
    if (ty == nullptr || ! (holds_a_reference(ty) || holds_a_view(ty)))
      return;
    // As deep as the scope of the name keeping it, which is not the scope
    // being lowered where the name is given a value in a block inside its own.
    auto depth = static_cast<std::int64_t>(scopes.size()) - 1;
    for (std::size_t at = 0; at < scopes.size(); ++at)
      if (std::ranges::any_of(scopes[at], [&](const auto& one) { return one.second->span.start == keeper.start; }))
        depth = static_cast<std::int64_t>(at);
    std::pair<std::int64_t, std::int64_t> value{ keeper.start, current_stmt != nullptr ? id_of(current_stmt) : 0 };
    std::vector<Borrow*> carried_now;
    std::vector<Borrow*> dropped;
    for (auto one : borrows) {
      if (one->depth != until_the_statement_ends)
        continue;
      one->depth = depth;
      // Kept by the value the name is given here, which another value given
      // it later does not keep.
      one->keeper = value;
      auto lender = find_local(one->name);
      if (lender == nullptr)
        continue;
      lends[keeper.start].insert(lender->span.start);
      if (! ir::is<ir::PtrType>(held_by(lender)))
        continue;
      // A reborrow of a name holding a reference reaches what that name's
      // value reaches, so it keeps what that value keeps, as long as it lives.
      for (auto kept : borrows)
        if (kept->keeper && kept->keeper->first == lender->span.start && kept->depth != until_the_statement_ends
            && still_out(kept)) {
          auto copy = make<Borrow>(*kept);
          copy->depth = depth;
          copy->keeper = value;
          copy->keeper_name.clear();
          carried_now.push_back(copy);
        }
      if (lender->span.start == keeper.start)
        // The name's new value made from its old one: what it kept, the new
        // value now keeps.
        dropped.push_back(one);
    }
    if (! dropped.empty())
      std::erase_if(borrows, [&](auto one) { return std::ranges::find(dropped, one) != dropped.end(); });
    borrows.insert(borrows.end(), carried_now.begin(), carried_now.end());
  }


  std::string Checker::keeper_named(Span keeper)
  {
    for (auto scope = scopes.rbegin(); scope != scopes.rend(); ++scope)
      for (const auto& [_, local] : *scope)
        if (local->span.start == keeper.start)
          return local->name;
    return "";
  }


  bool Checker::still_out(Borrow* borrow)
  {
    if (! borrow->keeper || ! current_live)
      return true;
    if (! current_live->contains(borrow->keeper->first))
      return false;
    return ! current_reach || current_reach->contains(std::tuple{ borrow->keeper->first, borrow->keeper->second });
  }


  void Checker::borrows_ending(const Dict<std::tuple<std::int64_t, std::int64_t>, std::string>& values, Span where,
                               std::int64_t at)
  {
    for (auto one : borrows) {
      if (! one->keeper)
        continue;
      auto as_key = std::tuple{ one->keeper->first, one->keeper->second };
      auto found = values.get(as_key);
      if (found == nullptr)
        continue;
      auto why = *found;
      auto told_key = std::tuple{ at, one->keeper->first, one->keeper->second, one->name, why };
      if (borrow_ends_told.contains(told_key))
        continue;
      borrow_ends_told.insert(told_key);
      auto keeper = one->keeper_name;
      if (keeper.empty())
        keeper = keeper_named(Span{ one->keeper->first, one->keeper->first });
      if (keeper.empty())
        keeper = "?";
      if (why == "given-after"
          && std::ranges::any_of(borrows, [&](auto other) {
               return other->name == one->name && other->keeper
                      && *other->keeper == std::pair<std::int64_t, std::int64_t>{ one->keeper->first, -at };
             }))
        // Passed on to the new value rather than ended.
        why = "carried";
      auto told = ended_because(why, keeper, one->name);
      module.record("borrow-ended", one->name,
                    "lent to '" + keeper + "' until here: " + told
                      + (why == "carried" ? std::string() : ", so '" + one->name + "' may be lent again from here"),
                    where);
    }
  }


  void Checker::borrows_ended_at(const ast::Stmt* stmt)
  {
    if (auto ending = live_ends.get(id_of(stmt)); ending != nullptr && ! ending->empty())
      borrows_ending(*ending, Span{ stmt->span.start, stmt->span.start }, id_of(stmt));
  }


  void Checker::borrows_ended_after(const ast::Stmt* stmt)
  {
    auto where = line_after(*sources, end_of(stmt), stmt->span.start);
    auto as_reasons = [](const auto& keys, std::string_view why) {
      Dict<std::tuple<std::int64_t, std::int64_t>, std::string> reasons;
      for (const auto& one : keys)
        reasons[one] = std::string(why);
      return reasons;
    };
    if (auto given = live_ends_after.get(id_of(stmt)); given != nullptr && ! given->empty())
      borrows_ending(as_reasons(*given, "given-after"), where, -id_of(stmt));
    if (auto left = live_leave.get(id_of(stmt)); left != nullptr && ! left->empty()) {
      auto why = ast::is<ast::ReturnStmt>(stmt) ? "return" : ast::is<ast::Break>(stmt) ? "break" : "continue";
      borrows_ending(as_reasons(*left, why), where, -id_of(stmt));
    }
  }


  void Checker::borrows_ended_with(const ast::Block* block)
  {
    auto held = live_exit.get(id_of(block));
    if (held == nullptr || held->empty())
      return;
    auto stmts = block->stmts();
    if (stmts.empty())
      return;
    Dict<std::tuple<std::int64_t, std::int64_t>, std::string> reasons;
    for (const auto& one : *held)
      reasons[one] = "scope";
    borrows_ending(reasons, line_after(*sources, end_of(block), stmts[0]->span.start), id_of(block));
  }


  void Checker::live_for(const ast::Node* node)
  {
    if (live_done.contains(id_of(node)))
      return;
    live_done.insert(id_of(node));
    Liveness found;
    if (auto lambda = ast::as<ast::Lambda>(node))
      found.lambda_(lambda);
    else
      found.function(ast::as<ast::FuncDef>(node));
    auto bindings = [](const Bindings& from) {
      KeySet into;
      for (auto one : from)
        into.insert(one);
      return into;
    };
    auto values = [](const LiveValues& from) {
      std::set<std::tuple<std::int64_t, std::int64_t>> into;
      for (const auto& [a, b] : from)
        into.emplace(a, b);
      return into;
    };
    // ORDER: Python's dicts here are filled from the walk's own, and only
    // ever looked up by a statement's identity; the order does not show.
    for (const auto& [at, one] : found.live_in)
      live_in[id_of(at)] = bindings(one);
    for (const auto& [at, one] : found.live_out)
      live_out[id_of(at)] = bindings(one);
    for (const auto& [at, one] : found.reach_in)
      live_reach[id_of(at)] = values(one);
    for (const auto& [at, one] : found.ends) {
      Dict<std::tuple<std::int64_t, std::int64_t>, std::string> why;
      for (const auto& [value, reason] : one)
        why[std::tuple{ value.first, value.second }] = std::string(reason);
      live_ends[id_of(at)] = std::move(why);
    }
    for (const auto& [at, one] : found.ends_after)
      live_ends_after[id_of(at)] = values(one);
    for (const auto& [at, one] : found.leave_ends)
      live_leave[id_of(at)] = values(one);
    for (const auto& [at, one] : found.exit_ends)
      live_exit[id_of(at)] = values(one);
  }


  std::optional<std::tuple<std::string, std::vector<std::string>>> Checker::borrowed_name(const ast::Expr* expr)
  {
    auto seen = expr;
    std::vector<std::string> fields;
    while (true) {
      if (auto name = ast::as<ast::NameRef>(seen)) {
        if (find_local(name->name) == nullptr)
          return std::nullopt;
        std::ranges::reverse(fields);
        return std::tuple{ std::string(name->name), std::move(fields) };
      }
      if (auto member = ast::as<ast::Member>(seen)) {
        fields.emplace_back(member->name);
        seen = member->base;
      } else if (auto element = ast::as<ast::Element>(seen)) {
        std::vector<std::optional<std::int64_t>> at;
        for (auto one : element->indices)
          at.push_back(index_known(one));
        if (std::ranges::any_of(at, [](const auto& one) { return ! one; }))
          // The whole array, whichever element: what was below it is part of
          // that.
          fields.clear();
        else
          // Reversed with the rest below, so in the order written.
          for (auto one = at.rbegin(); one != at.rend(); ++one)
            fields.push_back("\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}" + std::to_string(**one)
                             + "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}");
        seen = element->base;
      } else
        return std::nullopt;
    }
  }


  std::optional<std::int64_t> Checker::index_known(const ast::Expr* expr)
  {
    if (ast::is<ast::Range>(expr))
      return std::nullopt;
    // Asked quietly: what is wrong with it is said where it is lowered, once.
    auto kept = diags.divert([](const Diagnostic&) {});
    std::optional<Folded> found;
    {
      Finally put_back([&] { diags.restore(std::move(kept)); });
      found = constant(expr);
    }
    if (! found || found->refused || found->failed)
      return std::nullopt;
    return static_cast<std::int64_t>(found->value);
  }


  void Checker::lend(std::optional<std::string> name, bool is_mutable, Span span, std::span<const std::string> path_to)
  {
    if (! name)
      return;
    for (auto one : borrows) {
      if (one->name != *name || ! (one->is_mutable || is_mutable) || ! overlap(one->path, path_to) || ! still_out(one))
        continue;
      diags.emit(D::LANG_BORROW_ALIASED, span, { { "name", *name } })
        .note(D::LANG_BORROW_LENT_HERE, one->span, { { "name", *name } });
      return;
    }
    if (is_mutable && changed_here)
      // Lent so that something else may write it, which is a change.
      changed_here->insert(*name);
    auto made = make<Borrow>();
    made->name = *name;
    made->is_mutable = is_mutable;
    made->span = span;
    made->depth = until_the_statement_ends;
    made->path.assign(path_to.begin(), path_to.end());
    borrows.push_back(made);
  }


  bool Checker::lent_out(std::string_view name, Span span, bool writing, std::span<const std::string> path_to)
  {
    for (auto one : borrows) {
      if (one->name != name || ! (writing || one->is_mutable) || ! overlap(one->path, path_to) || ! still_out(one))
        continue;
      diags.emit(writing ? D::LANG_BORROW_NAME_WRITTEN : D::LANG_BORROW_NAME_READ, span, { { "name", name } })
        .note(D::LANG_BORROW_LENT_HERE, one->span, { { "name", name } });
      return true;
    }
    return false;
  }


  ir::Value* Checker::lower_address(ir::Builder& builder, const ast::AddressOf* expr, const ir::Type* expected)
  {
    auto wanted = ir::as<ir::PtrType>(aiming_at(expected));
    bool writes = expr->mutable_ || (wanted != nullptr && wanted->is_mutable);
    // Asked before the place is worked out: working it out reads the name, and
    // a name being lent out is what is being asked about.
    auto borrowed = borrowed_name(expr->operand);
    std::optional<std::string> lent;
    if (borrowed)
      lent = std::get<0>(*borrowed);
    lend(lent, writes, expr->span, borrowed ? std::span<const std::string>(std::get<1>(*borrowed)) : std::span<const std::string>{});
    auto outer = taking_a_reference;
    taking_a_reference = lent;
    std::optional<PlaceTaken> found;
    {
      Finally put_back([&] { taking_a_reference = outer; });
      found = place_written(builder, expr->operand);
    }
    if (! found)
      return error_value(module);
    if (! can_be_referred_to(found->held)) {
      diags.emit(D::LANG_REF_TYPE_NOT_ALLOWED, expr->span, { { "found", found->held->written() } });
      return error_value(module);
    }
    if (writes && ! found->may_change) {
      if (! said_sealed(expr->operand, expr->span))
        diags.emit(D::LANG_REF_PLACE_NOT_MUTABLE, expr->span, { { "name", found->what } });
      return error_value(module);
    }
    auto ty = ir::ptr_type(found->held, writes, found->lasting);
    auto answer = found->address->ty == ty ? found->address
                                           : builder.cast(ir::CastKind::bitcast, found->address, ty, expr->span);
    if (! accepts(expected, answer->ty))
      report_mismatch(expr->span, answer->ty, expected);
    return answer;
  }


  std::optional<Checker::PlaceTaken> Checker::place_written(ir::Builder& builder, const ast::Expr* expr, bool writing)
  {
    if (auto name = ast::as<ast::NameRef>(expr))
      return place_of_a_name(builder, name);
    if (auto element = ast::as<ast::Element>(expr))
      return place_of_an_element(builder, element);
    if (auto member = ast::as<ast::Member>(expr))
      return place_of_a_field(builder, member, writing);
    if (auto deref = ast::as<ast::Deref>(expr)) {
      // `&r⌖` is the place `r` already names, so it is `r`.
      auto value = lower_expr(builder, deref->operand, nullptr);
      auto found = value_type_of(value);
      if (is_error(found))
        return std::nullopt;
      auto ty = ir::as<ir::PtrType>(found);
      if (ty == nullptr) {
        diags.emit(D::LANG_DEREF_NOT_A_REFERENCE, expr->span, { { "found", found->written() } });
        return std::nullopt;
      }
      return PlaceTaken{ .address = value, .held = ty->pointee, .may_change = ty->is_mutable, .what = "what it names",
                         .lasting = ty->lasting };
    }
    diags.emit(D::LANG_REF_NOT_A_PLACE, expr->span);
    return std::nullopt;
  }

} // namespace pl4g::sema
