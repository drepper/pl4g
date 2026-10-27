// The checker, from the builtins that read and write memory in order to the end:
// `⎕acquire`, `⎕release`, `⎕narrow` and `⎕widen`, operators walked over
// arrays, `¨`, what a call names, names read as values, what is lowered where
// a value of a type is wanted, the marks `@[impure]` and `@[io]`, the tables a
// body writes, and the questions about the whole program.  A port of pypl4g's
// sema/check.py, lines 26513 to 28680.
#include "sema/check.hh"

#include <algorithm>
#include <deque>
#include <format>

#include "diag/ids.hh"
#include "front/names.hh"
#include "sema/tables.hh"
#include "target/statuses.hh"


namespace pl4g::sema {

  namespace {

    // A value standing in for one that could not be made: Python's
    // `UndefConst(ERROR)`.
    ir::Value* undef(ir::Module& module, const ir::Type* ty)
    {
      return const_cast<ir::UndefConst*>(module.undef(ty));
    }

    ir::Value* failed_value(ir::Module& module)
    {
      return undef(module, ir::error_type());
    }

    // The last code point there is.
    constexpr __int128 max_code_point = 0x10FFFF;

    // `value` as an instruction of shape `kind`, or nothing.
    ir::Instruction* inst_of(ir::Value* value, ir::InstKind kind) noexcept
    {
      if (value == nullptr || value->vkind != ir::ValueKind::instruction)
        return nullptr;
      auto inst = static_cast<ir::Instruction*>(value);
      return inst->kind == kind ? inst : nullptr;
    }

    // Whether `value` is the constant `false`.
    bool is_false(const ir::Value* value) noexcept
    {
      return value->vkind == ir::ValueKind::bool_const && ! static_cast<const ir::BoolConst*>(value)->value;
    }

    // A shape written as counts, as a type says one.
    std::vector<std::optional<std::uint64_t>> as_shape(std::span<const std::int64_t> counts)
    {
      std::vector<std::optional<std::uint64_t>> shape;
      shape.reserve(counts.size());
      for (auto one : counts)
        shape.emplace_back(static_cast<std::uint64_t>(one));
      return shape;
    }

    // The unit a number's type carries, and the type with no unit on it.
    const ir::Unit* unit_on(const ir::Type* ty)
    {
      if (auto i = ir::as<ir::IntType>(ty))
        return i->unit;
      return ir::as<ir::FloatType>(ty)->unit;
    }

    const ir::Type* bare_of(const ir::Type* ty)
    {
      if (auto i = ir::as<ir::IntType>(ty))
        return i->bare();
      return ir::as<ir::FloatType>(ty)->bare();
    }

    bool is_number(const ir::Type* ty) noexcept { return ir::is<ir::IntType>(ty) || ir::is<ir::FloatType>(ty); }

    // What runs when a block of the port is left however it is left: Python's
    // `finally`.
    struct Finally {
      std::function<void()> run;
      ~Finally() noexcept(false) { run(); }
    };

    // The identity Python's `id()` of what `held` holds would be.
    std::int64_t identity_of(const Held& held)
    {
      return std::visit([](const auto& one) -> std::int64_t {
        using T = std::remove_cvref_t<decltype(one)>;
        if constexpr (std::is_pointer_v<T>)
          return id_of(one);
        else
          return 0;
      }, held.v);
    }

    // Set the member of `node` called `name`, where it holds an expression.
    template<typename T>
    void set_expr(T& node, std::string_view name, const ast::Expr* value)
    {
      constexpr auto ctx = std::meta::access_context::unchecked();
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, ctx)))
        if constexpr (std::is_same_v<typename [:std::meta::type_of(m):], const ast::Expr*>)
          if (std::meta::identifier_of(m) == name)
            node.[:m:] = value;
    }

    // A copy of `expr` with each member named in `taken` holding the value
    // beside it, ready: Python's `replace(expr, **taken)` with `_Ready`s.
    const ast::Expr* with_ready(ast::SyntaxArena& arena, const ast::Expr* expr,
                                std::span<const std::pair<std::string, ir::Value*>> taken)
    {
      return static_cast<const ast::Expr*>(ast::visit(*expr, [&](const auto& n) -> const ast::Node* {
        auto made = arena.copy(n);
        for (const auto& [name, value] : taken) {
          auto ready = arena.make<ast::Ready>(expr->span);
          ready->value = value;
          set_expr(*made, name, ready);
        }
        return made;
      }));
    }

    // A block holding the one statement.
    const ast::Block* block_of(ast::SyntaxArena& arena, Span span, const ast::Stmt* stmt)
    {
      auto block = arena.make<ast::Block>(span);
      block->style = ast::BlockStyle::layout;
      block->statements = arena.list(std::vector<const ast::Stmt*>{ stmt });
      return block;
    }

  } // namespace


  ir::Value* Checker::as_an_address(ir::Builder& builder, const ast::Expr* written, std::string_view name)
  {
    auto value = lower_expr(builder, written, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type())
      return nullptr;
    if (ir::is<ir::PtrType>(ty))
      return value;
    if (auto i = ir::as<ir::IntType>(ty); i != nullptr && i->bits == 64)
      // Signed or unsigned: the bits of an address do not change with how a
      // program reads them.
      return as_count(builder, value, i, written->span);
    diags.emit(D::LANG_ADDRESS_NOT_AN_ADDRESS, written->span, { { "name", name }, { "found", ty->written() } });
    return nullptr;
  }


  ir::Value* Checker::lower_ordered(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto name = std::string(ast::as<ast::NameRef>(expr->callee)->name);
    bool writing = name == names::release_name;
    std::size_t wanted = writing ? 2 : 1;
    if (expr->args.size() != wanted) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", name }, { "expected", wanted }, { "found", expr->args.size() } });
      return failed_value(module);
    }
    an_effect(D::LANG_PURE_CALLS_IMPURE, expr->span, {}, { { "name", name } });
    auto place = lower_expr(builder, expr->args[0], nullptr);
    auto ty = value_type_of(place);
    if (ty == ir::error_type())
      return failed_value(module);
    auto ptr = ir::as<ir::PtrType>(ty);
    if (ptr == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ORDERED_NOT_A_REFERENCE, expr->args[0]->span, { { "name", name }, { "found", ty->written() } });
      return failed_value(module);
    }
    if (ir::made_of_parts(ptr->pointee)) [[unlikely]] {
      // One access and not several: which of the parts the ordering belonged
      // to would have no answer.
      diags.emit(D::LANG_ORDERED_SEVERAL_VALUES, expr->args[0]->span,
                 { { "name", name }, { "found", ptr->pointee->written() } });
      return failed_value(module);
    }
    if (! writing) {
      auto found = builder.load(place, expr->span, ir::Ordering::acquire);
      if (! accepts(expected, found->ty)) [[unlikely]] {
        report_mismatch(expr->span, found->ty, expected);
        return failed_value(module);
      }
      return found;
    }
    if (! ptr->is_mutable) [[unlikely]] {
      diags.emit(D::LANG_ORDERED_NOT_MUTABLE, expr->args[0]->span, { { "name", name } });
      return failed_value(module);
    }
    auto value = lower_into(builder, expr->args[1], ptr->pointee, expr->args[1]->span);
    if (value_type_of(value) == ir::error_type())
      return failed_value(module);
    builder.store(place, value, expr->span, ir::Ordering::release);
    if (expected != nullptr) [[unlikely]] {
      // It writes and answers nothing.
      diags.emit(D::LANG_CALL_HAS_NO_VALUE, expr->span, { { "name", name } });
      return failed_value(module);
    }
    return undef(module, ir::void_type());
  }


  ir::Value* Checker::as_a_word(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    if (ir::is<ir::PtrType>(ty))
      return builder.cast(ir::CastKind::bitcast, value, ir::u64(), span);
    auto i = ir::as<ir::IntType>(ty);
    if (i->bits == 64)
      return value;
    return builder.cast(i->is_signed ? ir::CastKind::sext : ir::CastKind::zext, value,
                        i->is_signed ? ir::i64() : ir::u64(), span);
  }


  ir::Value* Checker::lower_narrow(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2) [[unlikely]] {
      diags.emit(D::LANG_UNIT_TAKES_TWO, expr->span,
                 { { "name", names::narrow_name }, { "wanted", 2 }, { "found", expr->args.size() } });
      return failed_value(module);
    }
    auto written = ast::as<ast::Lifted>(expr->args[1]);
    if (written == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NARROW_NOT_A_TYPE, expr->args[1]->span);
      return failed_value(module);
    }
    auto wanted = lifted_type(written);
    if (wanted == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NARROW_NOT_A_TYPE, written->span);
      return failed_value(module);
    }
    if (auto e = ir::as<ir::EnumType>(wanted))
      return narrowed_to_enum(builder, expr, e, expected);
    if (wanted == ir::char_type())
      return narrowed_to_char(builder, expr, expected);
    if (! ir::is<ir::IntType>(wanted)) [[unlikely]] {
      diags.emit(D::LANG_NARROW_NOT_AN_INTEGER, written->span, { { "found", wanted->written() } });
      return failed_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return failed_value(module);
    auto found = ir::as<ir::IntType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NARROW_NOT_AN_INTEGER, expr->args[0]->span, { { "found", found_ty->written() } });
      return failed_value(module);
    }
    // What it counts is its own and travels with it; what the type says is how
    // much room the number has.
    auto into = ir::as<ir::IntType>(carrying(wanted, found->unit));
    auto answer = ir::result_type(into, ir::narrowing_type());
    auto [value, failed, why] = fitted(builder, given, found, into, expr->span);
    auto made = builder.wrap(value, failed, answer, expr->span, why);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return failed_value(module);
    }
    return made;
  }


  ir::Value* Checker::lower_widen(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::widen_name }, { "expected", 2 }, { "found", expr->args.size() } });
      return failed_value(module);
    }
    auto written = ast::as<ast::Lifted>(expr->args[1]);
    if (written == nullptr) [[unlikely]] {
      diags.emit(D::LANG_WIDEN_NOT_A_TYPE, expr->args[1]->span);
      return failed_value(module);
    }
    auto wanted = lifted_type(written);
    if (wanted == nullptr) [[unlikely]] {
      diags.emit(D::LANG_WIDEN_NOT_A_TYPE, written->span);
      return failed_value(module);
    }
    if (! ir::is<ir::IntType>(wanted)) [[unlikely]] {
      diags.emit(D::LANG_WIDEN_NOT_AN_INTEGER, written->span, { { "found", wanted->written() } });
      return failed_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return failed_value(module);
    auto found = ir::as<ir::IntType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_WIDEN_NOT_AN_INTEGER, expr->args[0]->span, { { "found", found_ty->written() } });
      return failed_value(module);
    }
    auto into = ir::as<ir::IntType>(carrying(wanted, found->unit));
    if (! every_value_fits(found, into)) [[unlikely]] {
      diags.emit(D::LANG_WIDEN_DOES_NOT_FIT, expr->span, { { "found", found->written() }, { "wanted", into->written() } });
      return failed_value(module);
    }
    auto made = found->bits == into->bits
                  ? given
                  : builder.cast(found->is_signed ? ir::CastKind::sext : ir::CastKind::zext, given, into, expr->span);
    if (! accepts(expected, into)) [[unlikely]] {
      report_mismatch(expr->span, into, expected);
      return failed_value(module);
    }
    return made;
  }


  ir::Value* Checker::narrowed_to_enum(ir::Builder& builder, const ast::Call* expr, const ir::EnumType* wanted,
                                       const ir::Type* expected)
  {
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return failed_value(module);
    auto found = ir::as<ir::IntType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NARROW_NOT_AN_INTEGER, expr->args[0]->span, { { "found", found_ty->written() } });
      return failed_value(module);
    }
    auto span = expr->span;
    auto answer = ir::result_type(wanted, ir::narrowing_type());
    ir::Value* holds = nullptr;
    for (auto number : wanted->values) {
      if (! found->holds(number))
        // A value the number's own type cannot reach: the comparison would be
        // false however it were written, so it is not written.
        continue;
      auto constant = builder.int_const(found, number);
      auto same = builder.compare(ir::CmpPred::eq, given, constant, span);
      holds = holds == nullptr ? same : builder.binary(ir::BinOp::or_, holds, same, span);
    }
    if (holds == nullptr)
      holds = builder.bool_const(false);
    auto value = as_enum(builder, given, found, wanted, span);
    auto negated = negate(builder, holds, span);
    auto made = builder.wrap(value, negated, answer, span,
                             const_cast<ir::EnumConst*>(module.enum_const(ir::narrowing_type(), 3)));
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(span, answer, expected);
      return failed_value(module);
    }
    return made;
  }


  ir::Value* Checker::narrowed_to_char(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return failed_value(module);
    auto found = ir::as<ir::IntType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NARROW_NOT_AN_INTEGER, expr->args[0]->span, { { "found", found_ty->written() } });
      return failed_value(module);
    }
    auto span = expr->span;
    auto answer = ir::result_type(ir::char_type(), ir::narrowing_type());
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(span, answer, expected);
      return failed_value(module);
    }
    if (given->vkind == ir::ValueKind::int_const) {
      auto number = static_cast<const ir::IntConst*>(given)->value;
      std::optional<std::size_t> why;
      if (number < 0)
        why = 2;
      else if (number > max_code_point)
        why = 0;
      else if (is_surrogate(number))
        why = 3;
      auto character = const_cast<ir::CharConst*>(module.char_const(why ? 0 : std::uint32_t(number)));
      auto reason_value = const_cast<ir::EnumConst*>(module.enum_const(ir::narrowing_type(), why.value_or(0)));
      return builder.wrap(character, builder.bool_const(why.has_value()), answer, span, reason_value);
    }
    auto no = builder.bool_const(false);
    auto over = no;
    if (found->high() > max_code_point)
      over = builder.compare(found->is_signed ? ir::CmpPred::sgt : ir::CmpPred::ugt, given,
                             builder.int_const(found, max_code_point), span);
    auto wrong_sign = no;
    if (found->is_signed)
      wrong_sign = builder.compare(ir::CmpPred::slt, given, builder.int_const(found, 0), span);
    auto absent = no;
    if (found->high() >= first_surrogate) {
      // One run of numbers, so one subtraction that wraps and one comparison.
      auto moved = builder.binary(ir::BinOp::wrap_sub, given, builder.int_const(found, first_surrogate), span);
      absent = builder.compare(ir::CmpPred::ult, moved, builder.int_const(found, surrogates), span);
    }
    auto failed = either(builder, either(builder, over, wrong_sign, span), absent, span);
    auto why_not = reason(builder, no, wrong_sign, span, absent);
    ir::Value* value;
    if (found->bare() == ir::u32())
      value = builder.cast(ir::CastKind::bitcast, given, ir::char_type(), span);
    else if (found->held() < ir::char_type()->holder()->held())
      value = builder.cast(ir::CastKind::zext, given, ir::char_type(), span);
    else
      value = builder.cast(ir::CastKind::trunc, given, ir::char_type(), span);
    return builder.wrap(value, failed, answer, span, why_not);
  }


  ir::Value* Checker::as_enum(ir::Builder& builder, ir::Value* given, const ir::IntType* found,
                              const ir::EnumType* wanted, Span span)
  {
    if (found->bits >= wanted->holder->bits)
      return builder.cast(ir::CastKind::trunc, given, wanted, span);
    return builder.cast(found->is_signed ? ir::CastKind::sext : ir::CastKind::zext, given, wanted, span);
  }


  std::tuple<ir::Value*, ir::Value*, ir::Value*> Checker::fitted(ir::Builder& builder, ir::Value* given,
                                                                 const ir::IntType* found, const ir::IntType* into,
                                                                 Span span)
  {
    bool is_signed = found->is_signed;
    auto above = is_signed ? ir::CmpPred::sgt : ir::CmpPred::ugt;
    auto below = is_signed ? ir::CmpPred::slt : ir::CmpPred::ult;
    auto no = builder.bool_const(false);

    // Whether the value is past `bound`, where it can be.
    auto past = [&](ir::CmpPred pred, __int128 bound, bool possible) -> ir::Value* {
      if (! possible)
        return no;
      auto limit = builder.int_const(found, bound);
      return builder.compare(pred, given, limit, span);
    };

    auto over = past(above, into->high(), found->high() > into->high());
    // A negative number put where there are no negative values is the case of
    // being below the bottom that has a name of its own.
    auto wrong_sign = past(below, 0, is_signed && ! into->is_signed);
    auto under = past(below, into->low(), into->is_signed && found->low() < into->low());
    auto failed = either(builder, either(builder, over, under, span), wrong_sign, span);
    auto why = reason(builder, under, wrong_sign, span);
    auto wide = as_wide(builder, given, found, into, span);
    return { wide, failed, why };
  }


  ir::Value* Checker::either(ir::Builder& builder, ir::Value* one, ir::Value* other, Span span)
  {
    if (is_false(one))
      return other;
    if (is_false(other))
      return one;
    return builder.binary(ir::BinOp::or_, one, other, span);
  }


  ir::Value* Checker::reason(ir::Builder& builder, ir::Value* under, ir::Value* wrong_sign, Span span,
                             ir::Value* absent)
  {
    auto holder = ir::narrowing_type()->holder;
    ir::Value* made = nullptr;
    // `absent` among them where the type has numbers that are none of its
    // values.
    auto missing = absent != nullptr ? absent : builder.bool_const(false);
    for (auto [at, held] : { std::pair{ 1, under }, std::pair{ 2, wrong_sign }, std::pair{ 3, missing } }) {
      if (is_false(held))
        continue;
      auto one = builder.cast(ir::CastKind::zext, held, holder, span);
      if (at != 1) {
        auto factor = builder.int_const(holder, at);
        one = builder.binary(ir::BinOp::wrap_mul, one, factor, span);
      }
      made = made == nullptr ? one : builder.binary(ir::BinOp::wrap_add, made, one, span);
    }
    if (made == nullptr)
      return const_cast<ir::EnumConst*>(module.enum_const(ir::narrowing_type(), 0));
    return builder.cast(ir::CastKind::bitcast, made, ir::narrowing_type(), span);
  }


  ir::Value* Checker::as_wide(ir::Builder& builder, ir::Value* given, const ir::IntType* found,
                              const ir::IntType* into, Span span)
  {
    if (into->held() <= found->held())
      return builder.cast(ir::CastKind::trunc, given, into, span);
    return builder.cast(found->is_signed ? ir::CastKind::sext : ir::CastKind::zext, given, into, span);
  }


  const ir::Type* Checker::scalar_of(const ir::Type* ty)
  {
    while (true)
      if (auto a = ir::as<ir::ArrayType>(ty))
        ty = a->element;
      else if (auto v = ir::as<ir::VecType>(ty))
        ty = v->element;
      else
        return ty;
  }


  ir::Value* Checker::walk_operands(ir::Builder& builder, const ast::Expr* expr,
                                    std::span<const std::tuple<std::string, ir::Value*>> given,
                                    const ir::Type* expected)
  {
    std::vector<ir::Value*> values;
    for (const auto& one : given)
      values.push_back(std::get<1>(one));
    if (std::ranges::none_of(values, [&](auto value) {
          return ir::is<ir::ArrayType>(value->ty) || runs.contains(id_of(value));
        }))
      return nullptr;
    std::vector<const ir::Type*> wanted;
    for (auto value : values)
      wanted.push_back(scalar_of(value->ty));
    if (std::ranges::any_of(wanted, [](auto ty) { return ty == nullptr || ty == ir::error_type(); }))
      return failed_value(module);
    auto shape = shape_walked(values, wanted, expr->span);
    if (! shape)
      return failed_value(module);
    std::vector<std::string> names;
    for (const auto& one : given)
      names.push_back(std::get<0>(one));
    if (all_at_once(expr, wanted, *shape))
      return run_at_a_time(builder, expr, names, values, wanted, *shape, expected,
                           as_run.contains(id_of(expr)) && shape->size() == 1);
    // An element at a time reads elements out of an array, so a run that came
    // as one value is given the room it would have had.
    for (auto& value : values)
      if (runs.contains(id_of(value)))
        value = as_array(builder, value, expr->span);
    std::int64_t total = 1;
    for (auto along : *shape)
      total *= along;
    std::vector<ir::Value*> made;
    for (std::int64_t at = 0; at < total; ++at) {
      auto one = element_answer(builder, expr, names, values, wanted, spread_out(at, *shape));
      if (value_type_of(one) == ir::error_type())
        return failed_value(module);
      made.push_back(one);
    }
    auto element = value_type_of(made[0]);
    auto answer = ir::array_type(element, as_shape(*shape));
    auto place = builder.frame(answer, expr->span);
    for (std::size_t at = 0; at < made.size(); ++at) {
      auto index = builder.int_const(ir::u64(), at);
      builder.store(element_place(builder, place, element, index, expr->span), made[at], expr->span);
    }
    auto held = builder.cast(ir::CastKind::bitcast, place, answer, expr->span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return failed_value(module);
    }
    return held;
  }


  bool Checker::all_at_once(const ast::Expr* expr, std::span<const ir::Type* const> wanted,
                            std::span<const std::int64_t> shape)
  {
    if (shape.back() < 2 || ! std::ranges::all_of(wanted, [&](auto ty) { return ty == wanted[0]; }))
      return false;
    // A truth value and a whole number are what a lane holds; a floating
    // point number is not yet.
    if (! ir::is<ir::IntType>(wanted[0]) && ! ir::is<ir::BoolType>(wanted[0]))
      return false;
    if (auto binary = ast::as<ast::Binary>(expr))
      return sema::all_at_once().contains(binary->op);
    if (auto unary = ast::as<ast::Unary>(expr))
      return all_at_once_unary().contains(unary->op);
    return false;
  }


  ir::Value* Checker::run_at_a_time(ir::Builder& builder, const ast::Expr* expr, std::span<const std::string> names,
                                    std::span<ir::Value* const> values, std::span<const ir::Type* const> wanted,
                                    std::span<const std::int64_t> shape, const ir::Type* expected, bool as_run)
  {
    auto lanes = shape.back();
    auto outer = shape.first(shape.size() - 1);
    std::int64_t total = 1;
    for (auto along : outer)
      total *= along;
    std::vector<ir::Value*> made;
    for (std::int64_t at = 0; at < total; ++at) {
      std::vector<std::pair<std::string, ir::Value*>> taken;
      for (std::size_t i = 0; i < names.size() && i < values.size() && i < wanted.size(); ++i)
        taken.emplace_back(names[i],
                           run_or_one(builder, values[i], wanted[i], spread_out(at, outer), lanes, expr->span));
      made.push_back(lower_expr(builder, with_ready(syntax, expr, taken), nullptr));
    }
    auto held = ir::as<ir::VecType>(value_type_of(made[0]));
    auto answer = ir::array_type(held->element, as_shape(shape));
    if (as_run) {
      // The answer wanted as the run itself: one value of as many lanes, and
      // no room taken for it.
      if (! accepts(expected, answer)) [[unlikely]] {
        report_mismatch(expr->span, answer, expected);
        return failed_value(module);
      }
      runs.insert(id_of(made[0]));
      return made[0];
    }
    auto place = builder.frame(answer, expr->span);
    for (std::size_t at = 0; at < made.size(); ++at)
      builder.store(run_place(builder, place, held, at * lanes, expr->span), made[at], expr->span);
    auto found = builder.cast(ir::CastKind::bitcast, place, answer, expr->span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return failed_value(module);
    }
    return found;
  }


  ir::Value* Checker::run_or_one(ir::Builder& builder, ir::Value* value, const ir::Type* want,
                                 std::span<const std::int64_t> index, std::int64_t lanes, Span span)
  {
    // A run already held as a value is that run, taken by the operator it
    // stands in.
    if (runs.contains(id_of(value))) {
      runs.erase(id_of(value));
      return value;
    }
    auto picked = value;
    for (auto along : index) {
      auto ty = value_type_of(picked);
      if (ty == want || ! ir::is<ir::ArrayType>(ty))
        break;
      picked = one_of(builder, picked, want, along, span);
    }
    auto held = ir::vec_type(want, static_cast<unsigned>(lanes));
    auto ty = value_type_of(picked);
    auto array = ir::as<ir::ArrayType>(ty);
    if (ty == want || array == nullptr)
      return builder.splat(picked, held, span);
    auto [start, lengths] = shape_of(builder, picked, array, span);
    return builder.load(builder.cast(ir::CastKind::bitcast, start, ir::ptr_type(held, true), span), span);
  }


  ir::Value* Checker::as_array(ir::Builder& builder, ir::Value* value, Span span)
  {
    runs.erase(id_of(value));
    auto held = ir::as<ir::VecType>(value->ty);
    contract_assert(held != nullptr);
    auto answer = ir::array_type(held->element, as_shape(std::vector<std::int64_t>{ std::int64_t(held->lanes) }));
    auto place = builder.frame(answer, span);
    builder.store(run_place(builder, place, held, 0, span), value, span);
    return builder.cast(ir::CastKind::bitcast, place, answer, span);
  }


  ir::Value* Checker::run_place(ir::Builder& builder, ir::Value* base, const ir::VecType* held, std::int64_t at,
                                Span span)
  {
    auto index = builder.int_const(ir::u64(), at);
    auto place = element_place(builder, base, held->element, index, span);
    return builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(held, true), span);
  }


  std::optional<std::vector<std::int64_t>> Checker::shape_walked(std::span<ir::Value* const> values,
                                                                 std::span<const ir::Type* const> wanted, Span span)
  {
    std::vector<std::int64_t> found;
    std::vector<const ir::Type*> seen;
    for (auto value : values)
      seen.push_back(value->ty);
    while (true) {
      std::vector<std::size_t> walked;
      for (std::size_t at = 0; at < seen.size() && at < wanted.size(); ++at)
        if (seen[at] != wanted[at])
          walked.push_back(at);
      if (walked.empty())
        return found;
      std::optional<std::uint64_t> along;
      for (auto at : walked) {
        if (auto vec = ir::as<ir::VecType>(seen[at])) {
          // A run held as a value, which is one dimension of as many as it has
          // lanes.
          if (! along)
            along = vec->lanes;
          else if (*along != vec->lanes) [[unlikely]] {
            diags.emit(D::LANG_LISTABLE_SHAPES_DIFFER, span, { { "found", vec->lanes }, { "wanted", *along } });
            return std::nullopt;
          }
          seen[at] = vec->element;
          continue;
        }
        auto ty = ir::as<ir::ArrayType>(seen[at]);
        if (ty == nullptr || ! ty->fixed()) [[unlikely]] {
          diags.emit(D::LANG_LISTABLE_CANNOT_WALK, span,
                     { { "found", seen[at]->written() }, { "wanted", wanted[at]->written() } });
          return std::nullopt;
        }
        if (! along)
          along = ty->shape[0];
        else if (*along != *ty->shape[0]) [[unlikely]] {
          diags.emit(D::LANG_LISTABLE_SHAPES_DIFFER, span, { { "found", *ty->shape[0] }, { "wanted", *along } });
          return std::nullopt;
        }
        seen[at] = one_less(ty);
      }
      found.push_back(static_cast<std::int64_t>(*along));
    }
  }


  ir::Value* Checker::element_answer(ir::Builder& builder, const ast::Expr* expr, std::span<const std::string> names,
                                     std::span<ir::Value* const> values, std::span<const ir::Type* const> wanted,
                                     std::span<const std::int64_t> index)
  {
    std::vector<std::pair<std::string, ir::Value*>> taken;
    for (std::size_t i = 0; i < names.size() && i < values.size() && i < wanted.size(); ++i) {
      auto picked = values[i];
      for (auto along : index) {
        auto ty = value_type_of(picked);
        if (ty == wanted[i] || ! ir::is<ir::ArrayType>(ty))
          break;
        picked = one_of(builder, picked, wanted[i], along, expr->span);
      }
      taken.emplace_back(names[i], picked);
    }
    return lower_expr(builder, with_ready(syntax, expr, taken), nullptr);
  }


  ir::Value* Checker::walked(ir::Builder& builder, const ir::FuncType* ty,
                             std::function<ir::Value*(std::span<ir::Value* const>, Span)> make,
                             std::span<ir::Value* const> args, const ast::Call* expr, const ir::Type* expected)
  {
    auto shape = walking_shape(ty, args, expr);
    if (! shape)
      return failed_value(module);
    auto answer = ir::array_type(ty->ret, as_shape(*shape));
    auto place = builder.frame(answer, expr->span);
    each_of(builder, ty, make, args, *shape, place, 0, expr->span);
    auto made = builder.cast(ir::CastKind::bitcast, place, answer, expr->span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return failed_value(module);
    }
    return made;
  }


  std::optional<std::vector<std::int64_t>> Checker::walking_shape(const ir::FuncType* ty,
                                                                  std::span<ir::Value* const> args,
                                                                  const ast::Call* expr)
  {
    std::vector<std::int64_t> found;
    std::vector<const ir::Type*> seen;
    for (auto value : args)
      seen.push_back(value->ty);
    while (true) {
      std::vector<std::size_t> walked;
      for (std::size_t at = 0; at < seen.size() && at < ty->params.size(); ++at)
        if (seen[at] != ty->params[at])
          walked.push_back(at);
      if (walked.empty())
        return found;
      std::optional<std::uint64_t> along;
      for (auto at : walked) {
        auto one = ir::as<ir::ArrayType>(seen[at]);
        auto where = at < expr->args.size() ? expr->args[at]->span : expr->span;
        if (one == nullptr || ! one->fixed()) [[unlikely]] {
          diags.emit(D::LANG_LISTABLE_CANNOT_WALK, where,
                     { { "found", seen[at]->written() }, { "wanted", ty->params[at]->written() } });
          return std::nullopt;
        }
        if (! along)
          along = one->shape[0];
        else if (*along != *one->shape[0]) [[unlikely]] {
          diags.emit(D::LANG_LISTABLE_SHAPES_DIFFER, where, { { "found", *one->shape[0] }, { "wanted", *along } });
          return std::nullopt;
        }
        seen[at] = one_less(one);
      }
      found.push_back(static_cast<std::int64_t>(*along));
    }
  }


  const ir::Type* Checker::one_less(const ir::ArrayType* ty)
  {
    if (ty->rank() == 1)
      return ty->element;
    return ir::array_type(ty->element, { ty->shape.begin() + 1, ty->shape.end() });
  }


  void Checker::each_of(ir::Builder& builder, const ir::FuncType* ty,
                        std::function<ir::Value*(std::span<ir::Value* const>, Span)> make,
                        std::span<ir::Value* const> args, std::span<const std::int64_t> shape, ir::Value* place,
                        std::int64_t at, Span span)
  {
    if (shape.empty()) {
      auto answer = make(args, span);
      auto index = builder.int_const(ir::u64(), at);
      builder.store(element_place(builder, place, ty->ret, index, span), answer, span);
      return;
    }
    std::int64_t step = 1;
    for (auto along : shape.subspan(1))
      step *= along;
    for (std::int64_t index = 0; index < shape[0]; ++index) {
      std::vector<ir::Value*> picked;
      for (std::size_t i = 0; i < args.size() && i < ty->params.size(); ++i)
        picked.push_back(one_of(builder, args[i], ty->params[i], index, span));
      each_of(builder, ty, make, picked, shape.subspan(1), place, at + index * step, span);
    }
  }


  ir::Value* Checker::one_of(ir::Builder& builder, ir::Value* value, const ir::Type* wanted, std::int64_t index,
                             Span span)
  {
    auto ty = value_type_of(value);
    auto array = ir::as<ir::ArrayType>(ty);
    if (ty == wanted || array == nullptr)
      return value;
    auto [start, lengths] = shape_of(builder, value, array, span);
    auto strides = strides_of(builder, value, array, span);
    auto count = builder.int_const(ir::u64(), index);
    auto offset = builder.binary(ir::BinOp::wrap_mul, count, strides[0], span);
    if (array->rank() == 1)
      return builder.load(element_place(builder, start, array->element, offset, span), span);
    return row_at(builder, start, array, lengths, strides, offset, 1, span);
  }


  // -- each: `¨` --------------------------------------------------------------

  std::string Checker::each_name()
  {
    each_count += 1;
    return std::format("{}{}", names::each_glyph, each_count);
  }


  void Checker::bind_hidden(ir::Builder& builder, std::string_view name, ir::Value* value, Span span,
                            const ir::Type* placed_as)
  {
    bind_local(name, value, span, false, invalid_span, false, &builder, placed_as);
    if (auto local = scopes.back().get(std::string(name)); local != nullptr && *local != nullptr)
      (*local)->read = true;
  }


  const ir::ArrayType* Checker::walkable(ir::Value* value, std::int64_t levels, Span span)
  {
    auto ty = value_type_of(value);
    if (ty == ir::error_type())
      return nullptr;
    auto array = ir::as<ir::ArrayType>(ty);
    if (array == nullptr) [[unlikely]] {
      diags.emit(D::LANG_EACH_NOT_AN_ARRAY, span, { { "found", ty->written() } });
      return nullptr;
    }
    if (levels > static_cast<std::int64_t>(array->rank())) [[unlikely]] {
      diags.emit(D::LANG_EACH_TOO_DEEP, span, { { "found", ty->written() }, { "levels", levels } });
      return nullptr;
    }
    return array;
  }


  void Checker::bind_element(ir::Builder& builder, std::string_view name, ir::Value* start,
                             const ir::Type* element, ir::Value* at, Span span)
  {
    auto place = element_place(builder, start, element, at, span);
    if (ir::is<ir::ProductType>(element))
      bind_hidden(builder, name, place, span, element);
    else
      bind_hidden(builder, name, builder.load(place, span), span);
  }


  ir::Value* Checker::walk_at(ir::Builder& builder, const ir::ArrayType* ty, std::int64_t levels, ir::Value* at,
                              ir::Value* start, std::span<ir::Value* const> lengths,
                              std::span<ir::Value* const> strides, Span span)
  {
    auto offset = flat_offset(builder, lengths, strides, at, levels, ty->fixed(), span);
    if (levels == static_cast<std::int64_t>(ty->rank()))
      return builder.load(element_place(builder, start, ty->element, offset, span), span);
    return row_at(builder, start, ty, lengths, strides, offset, levels, span);
  }


  ir::Value* Checker::lower_each(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected)
  {
    auto found = marked(expr);
    auto& [written, levels, each] = *found;
    auto base = lower_expr(builder, written, nullptr);
    auto ty = walkable(base, levels, written->span);
    if (ty == nullptr)
      return failed_value(module);
    auto name = each_name();
    auto ref = syntax.make<ast::NameRef>(written->span);
    ref->name = syntax.text(name);
    std::vector<std::tuple<std::string, ir::Value*, const ir::ArrayType*, std::int64_t, Span>> sources{
      { name, base, ty, levels, written->span }
    };
    return each_built(builder, expr, sources, {}, each(syntax, ref), expected);
  }


  ir::Value* Checker::lower_each_call(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    std::vector<std::tuple<std::string, ir::Value*, const ir::ArrayType*, std::int64_t, Span>> sources;
    std::vector<std::tuple<std::string, ir::Value*>> fixed;
    std::vector<const ast::Expr*> args;
    for (auto arg : expr->args) {
      if (ast::is<ast::Spread>(arg)) [[unlikely]] {
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, arg->span,
                   { { "feature", "a tuple spread into a call that '\N{DIAERESIS}' walks" } });
        return failed_value(module);
      }
      auto named = ast::as<ast::Named>(arg);
      auto inner = named != nullptr ? named->value : arg;
      if (auto found = marked(inner)) {
        auto& [written, levels, each] = *found;
        auto value = lower_expr(builder, written, nullptr);
        auto ty = walkable(value, levels, written->span);
        if (ty == nullptr)
          return failed_value(module);
        auto name = each_name();
        sources.emplace_back(name, value, ty, levels, written->span);
        auto ref = syntax.make<ast::NameRef>(written->span);
        ref->name = syntax.text(name);
        inner = each(syntax, ref);
      } else if (! stands_alone(inner)) {
        auto value = lower_expr(builder, inner, nullptr);
        if (value_type_of(value) == ir::error_type())
          return failed_value(module);
        auto name = each_name();
        fixed.emplace_back(name, value);
        auto ref = syntax.make<ast::NameRef>(inner->span);
        ref->name = syntax.text(name);
        inner = ref;
      }
      if (named != nullptr) {
        auto copy = syntax.copy(*named);
        copy->value = inner;
        args.push_back(copy);
      } else {
        args.push_back(inner);
      }
    }
    auto call = syntax.copy(*expr);
    call->args = syntax.list(args);
    return each_built(builder, expr, sources, fixed, call, expected);
  }


  ir::Value* Checker::each_built(
    ir::Builder& builder, const ast::Expr* expr,
    std::span<const std::tuple<std::string, ir::Value*, const ir::ArrayType*, std::int64_t, Span>> sources,
    std::span<const std::tuple<std::string, ir::Value*>> fixed, const ast::Expr* template_, const ir::Type* expected)
  {
    auto first = std::get<2>(sources[0]);
    auto depth = std::get<3>(sources[0]);
    std::vector<std::optional<std::uint64_t>> walked(first->shape.begin(), first->shape.begin() + depth);
    for (const auto& [_, value, ty, levels, span] : sources) {
      std::vector<std::optional<std::uint64_t>> shape(ty->shape.begin(), ty->shape.begin() + levels);
      if (std::ranges::any_of(shape, [](const auto& along) { return ! along.has_value(); })) [[unlikely]] {
        diags.emit(D::LANG_EACH_SHAPE_UNSTATED, span, { { "found", ty->written() } });
        return failed_value(module);
      }
      if (shape != walked) [[unlikely]] {
        diags.emit(D::LANG_EACH_SHAPES_DIFFER, span, { { "found", ty->written() }, { "wanted", first->written() } });
        return failed_value(module);
      }
    }
    std::uint64_t count = 1;
    for (const auto& along : walked)
      count *= *along;
    if (count == 0) [[unlikely]] {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                 { { "feature", "walking with '\N{DIAERESIS}' an array with nothing in it, "
                                "which leaves nothing to say what the answer holds" } });
      return failed_value(module);
    }
    struct Laid {
      ir::Value* start;
      std::vector<ir::Value*> lengths;
      std::vector<ir::Value*> strides;
    };
    std::vector<Laid> shapes;
    for (const auto& [_, value, ty, levels, span] : sources) {
      auto [start, lengths] = shape_of(builder, value, ty, span);
      auto strides = strides_of(builder, value, ty, span);
      shapes.push_back({ start, std::move(lengths), std::move(strides) });
    }
    auto expected_array = ir::as<ir::ArrayType>(expected);
    auto wanted = expected_array != nullptr && expected_array->rank() == walked.size() ? expected_array->element
                                                                                        : nullptr;
    const ir::ArrayType* answer = nullptr;
    ir::Value* place = nullptr;
    for (std::uint64_t at = 0; at < count; ++at) {
      auto errors = diags.error_count;
      ir::Value* one = nullptr;
      {
        push_scope();
        Finally popped{ [this] { pop_scope(); } };
        for (const auto& [name, value] : fixed)
          bind_hidden(builder, name, value, expr->span);
        for (std::size_t i = 0; i < sources.size(); ++i) {
          const auto& [name, _, ty, levels, span] = sources[i];
          const auto& [start, lengths, strides] = shapes[i];
          if (levels == static_cast<std::int64_t>(ty->rank())) {
            auto index = builder.int_const(ir::u64(), at);
            auto offset = flat_offset(builder, lengths, strides, index, levels, ty->fixed(), span);
            bind_element(builder, name, start, ty->element, offset, span);
          } else {
            auto index = builder.int_const(ir::u64(), at);
            bind_hidden(builder, name, walk_at(builder, ty, levels, index, start, lengths, strides, span), span);
          }
        }
        one = lower_expr(builder, template_, wanted);
      }
      auto got = value_type_of(one);
      if (got == ir::error_type() || diags.error_count > errors)
        return failed_value(module);
      if (answer == nullptr) {
        if (ir::is<ir::ArrayType>(got)) [[unlikely]] {
          diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                     { { "feature", std::format("an array of what each element of a walk comes to, where that is "
                                                "itself the array '{}'",
                                                got->written()) } });
          return failed_value(module);
        }
        answer = ir::array_type(got, walked);
        place = builder.frame(answer, expr->span);
      }
      auto index = builder.int_const(ir::u64(), at);
      builder.store(element_place(builder, place, got, index, expr->span), one, expr->span);
    }
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return failed_value(module);
    }
    return builder.cast(ir::CastKind::bitcast, place, answer, expr->span);
  }


  void Checker::assign_each(ir::Builder& builder, const ast::Stmt* stmt, ir::Function* func)
  {
    auto member_assign = ast::as<ast::MemberAssign>(stmt);
    auto element_assign = ast::as<ast::ElementAssign>(stmt);
    auto stmt_base = member_assign != nullptr ? member_assign->base : element_assign->base;
    auto found = marked(stmt_base);
    auto [written, levels, each] = *found;
    auto span = stmt->span;
    if (levels != 1) [[unlikely]] {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, span,
                 { { "feature", "writing with '\N{DIAERESIS}' through more than one dimension" } });
      return;
    }
    push_scope();
    Finally popped{ [this] { pop_scope(); } };
    auto name_ref = [&](Span at, std::string_view name) {
      auto ref = syntax.make<ast::NameRef>(at);
      ref->name = syntax.text(name);
      return ref;
    };
    std::optional<std::string> mask;
    auto target = written;
    if (auto element = ast::as<ast::Element>(written);
        element != nullptr && element->indices.size() == 1 && ! ast::is<ast::Range>(element->indices[0])) {
      auto index = on_its_own(builder, element->indices[0]);
      auto held = value_type_of(index);
      if (held == ir::error_type())
        return;
      auto name = each_name();
      bind_hidden(builder, name, index, span);
      if (auto array = ir::as<ir::ArrayType>(held); array != nullptr && array->element == ir::bool_type()) {
        if (array->rank() != 1) [[unlikely]] {
          diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, span,
                     { { "feature", "writing with '\N{DIAERESIS}' through a mask of more than one dimension" } });
          return;
        }
        mask = name;
        target = element->base;
      } else {
        auto copy = syntax.copy(*element);
        copy->indices = syntax.list(std::vector<const ast::Expr*>{ name_ref(element->indices[0]->span, name) });
        target = copy;
      }
    }
    if (! a_place_again(target)) [[unlikely]] {
      diags.emit(D::LANG_EACH_WRITES_A_PLACE, target->span);
      return;
    }
    auto array = lower_expr(builder, target, nullptr);
    auto ty = walkable(array, 1, target->span);
    if (ty == nullptr)
      return;
    auto [_, lengths] = shape_of(builder, array, ty, span);
    // What the loop counts is places, so it counts in the unit an index is.
    auto places = static_cast<const ir::IntType*>(ir::stray_copy(ir::int_type(64, false, idx_unit())));
    auto count = each_name();
    auto first = each_name();
    bind_hidden(builder, count, builder.cast(ir::CastKind::bitcast, lengths[0], places, span), span);
    bind_hidden(builder, first, builder.int_const(places, 0), span);
    if (mask)
      each_agree(builder, name_ref(span, *mask), lengths[0], ty, span);
    auto value = member_assign != nullptr ? member_assign->value : element_assign->value;
    std::string index;
    if (auto given = marked(value)) {
      auto& [source, depth, each_value] = *given;
      auto walked = lower_expr(builder, source, nullptr);
      auto held = walkable(walked, depth, source->span);
      if (held == nullptr)
        return;
      if (depth != 1) [[unlikely]] {
        diags.emit(D::LANG_EACH_SHAPES_DIFFER, source->span, { { "found", held->written() }, { "wanted", ty->written() } });
        return;
      }
      auto name = each_name();
      bind_hidden(builder, name, walked, span);
      each_agree(builder, name_ref(span, name), lengths[0], ty, span);
      index = each_name();
      auto element = syntax.make<ast::Element>(source->span);
      element->base = name_ref(source->span, name);
      element->indices = syntax.list(std::vector<const ast::Expr*>{ name_ref(source->span, index) });
      value = each_value(syntax, element);
    } else {
      index = each_name();
      if (! stands_alone(value)) {
        auto worked = lower_expr(builder, value, nullptr);
        if (value_type_of(worked) == ir::error_type())
          return;
        auto name = each_name();
        bind_hidden(builder, name, worked, span);
        value = name_ref(value->span, name);
      }
    }
    auto at = name_ref(span, index);
    auto element = syntax.make<ast::Element>(target->span);
    element->base = target;
    element->indices = syntax.list(std::vector<const ast::Expr*>{ at });
    auto base = each(syntax, element);
    const ast::Stmt* one = nullptr;
    if (member_assign != nullptr) {
      auto copy = syntax.copy(*member_assign);
      copy->base = base;
      copy->value = value;
      one = copy;
    } else {
      auto copy = syntax.copy(*element_assign);
      copy->base = base;
      copy->value = value;
      one = copy;
    }
    if (mask) {
      auto picked = syntax.make<ast::Element>(span);
      picked->base = name_ref(span, *mask);
      picked->indices = syntax.list(std::vector<const ast::Expr*>{ at });
      auto arm = syntax.make<ast::IfArm>(span);
      arm->condition = picked;
      arm->body = block_of(syntax, span, one);
      auto choice = syntax.make<ast::If>(span);
      choice->arms = syntax.list(std::vector<const ast::IfArm*>{ arm });
      auto stmt_of = syntax.make<ast::ExprStmt>(span);
      stmt_of->value = choice;
      one = stmt_of;
    }
    auto range = syntax.make<ast::Range>(span);
    range->start = name_ref(span, first);
    range->stop = name_ref(span, count);
    auto loop = syntax.make<ast::ForEach>(span);
    loop->name = syntax.text(index);
    loop->name_span = span;
    loop->iterable = range;
    loop->body = block_of(syntax, span, one);
    auto run = syntax.make<ast::ExprStmt>(span);
    run->value = loop;
    lower_stmt(builder, run, func, false);
  }


  void Checker::each_agree(ir::Builder& builder, const ast::Expr* other, ir::Value* length, const ir::ArrayType* ty,
                           Span span)
  {
    auto value = lower_expr(builder, other, nullptr);
    auto held = ir::as<ir::ArrayType>(value_type_of(value));
    if (held == nullptr)
      return;
    if (held->shape[0] && ty->shape[0]) {
      if (*held->shape[0] != *ty->shape[0]) [[unlikely]]
        diags.emit(D::LANG_EACH_SHAPES_DIFFER, span, { { "found", held->written() }, { "wanted", ty->written() } });
      return;
    }
    auto [_, lengths] = shape_of(builder, value, held, span);
    builder.check(builder.compare(ir::CmpPred::eq, lengths[0], length, span),
                  "arrays walked together whose lengths differ", target::statuses::OUT_OF_RANGE, span);
  }


  std::optional<std::vector<ir::Value*>> Checker::one_by_one(ir::Builder& builder,
                                                             std::span<const ast::Expr* const> written,
                                                             std::optional<std::span<const ir::Type* const>> wanted,
                                                             std::optional<std::string> callee)
  {
    std::vector<ir::Value*> values;

    // What the next value is to be, where anything says.
    auto place = [&]() -> const ir::Type* {
      return wanted && values.size() < wanted->size() ? (*wanted)[values.size()] : nullptr;
    };

    for (auto one : written) {
      auto outer = handing_over;
      if (callee)
        handing_over = std::tuple{ *callee, static_cast<std::int64_t>(values.size() + 1) };
      Finally restored{ [&] { handing_over = outer; } };
      if (auto spread = ast::as<ast::Spread>(one)) {
        auto taken = taken_one_each(builder, spread);
        if (! taken)
          return std::nullopt;
        for (auto value : *taken) {
          auto ty = place();
          if (ty != nullptr && value->ty != ty)
            report_mismatch(spread->operand->span, value->ty, ty);
          values.push_back(value);
        }
        continue;
      }
      auto ty = place();
      values.push_back(ty != nullptr ? lower_into(builder, one, ty, one->span) : lower_expr(builder, one, nullptr));
    }
    return values;
  }


  std::optional<std::vector<ir::Value*>> Checker::taken_one_each(ir::Builder& builder, const ast::Spread* spread)
  {
    auto value = lower_expr(builder, spread->operand, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type())
      return std::nullopt;
    if (auto tuple = ir::as<ir::TupleType>(ty)) {
      std::vector<ir::Value*> members;
      for (std::size_t at = 0; at < tuple->members.size(); ++at)
        members.push_back(member(builder, value, tuple, at, spread->span));
      return members;
    }
    auto array = ir::as<ir::ArrayType>(ty);
    if (array == nullptr) [[unlikely]] {
      diags.emit(D::LANG_SPREAD_NOT_SEVERAL, spread->operand->span, { { "found", ty->written() } });
      return std::nullopt;
    }
    if (! array->fixed()) [[unlikely]] {
      // The length is beside the elements rather than in the type.
      diags.emit(D::LANG_SPREAD_NOT_FIXED, spread->operand->span, { { "found", ty->written() } });
      return std::nullopt;
    }
    auto [start, lengths] = shape_of(builder, value, array, spread->span);
    auto along = *array->shape[0];
    std::uint64_t row = 1;
    for (std::size_t at = 1; at < array->shape.size(); ++at)
      row *= *array->shape[at];
    std::vector<ir::Value*> taken;
    for (std::uint64_t at = 0; at < along; ++at) {
      auto offset = builder.int_const(ir::u64(), at * row);
      if (array->rank() == 1) {
        taken.push_back(
          builder.load(element_place(builder, start, array->element, offset, spread->span), spread->span));
      } else {
        auto strides = strides_of(builder, value, array, spread->span);
        taken.push_back(row_at(builder, start, array, lengths, strides, offset, 1, spread->span));
      }
    }
    return taken;
  }


  void Checker::only_at_build(ir::Function* func, Span span)
  {
    if (func->attrs.builtin && in_build_function == nullptr) [[unlikely]]
      diags.emit(D::LANG_BUILTIN_AT_RUNTIME, span, { { "name", func->name } });
  }


  ir::Function* Checker::callee(const ast::Expr* expr)
  {
    if (auto ref = ast::as<ast::NameRef>(expr)) {
      auto found = top->get(std::string(ref->name));
      if (found != nullptr)
        if (auto func = found->get<ir::Function>()) {
          if (notes != nullptr)
            note(expr->span, note_kind::function, ref->name, func->ty->written(), func->name_span,
                 func->doc.empty() ? std::nullopt : std::optional<std::string>(func->doc));
          only_at_build(func, expr->span);
          return func;
        }
      auto local = find_local(ref->name);
      if ((found == nullptr || found->empty()) && local == nullptr) [[unlikely]] {
        diags.emit(D::LANG_FILESTRUCT_UNDEFINED_NAME, expr->span, { { "name", ref->name } });
        return nullptr;
      }
      if (local != nullptr && held_by(local) == ir::error_type())
        // Its definition was reported, and is why it holds nothing.
        return nullptr;
      diags.emit(D::LANG_CALL_NOT_A_FUNCTION, expr->span, { { "name", ref->name } });
      return nullptr;
    }
    if (auto member = ast::as<ast::Member>(expr))
      return callee_of_module(member);
    diags.emit(D::LANG_CALL_NOT_A_FUNCTION, expr->span, { { "name", "this" } });
    return nullptr;
  }


  const ir::Type* Checker::type_exported(const ast::Member* expr)
  {
    auto held = module_at(expr->base);
    if (held == nullptr)
      return nullptr;
    auto found = held->exports.get(std::string(expr->name));
    if (found == nullptr)
      return nullptr;
    auto named = found->get<NamedType>();
    return named == nullptr ? nullptr : named->ty;
  }


  Generic* Checker::generic_named(const ast::Expr* callee)
  {
    // The bare name first, which is what almost every callee is.
    Held found;
    if (auto ref = ast::as<ast::NameRef>(callee)) {
      if (auto held = top->get(std::string(ref->name)))
        found = *held;
    } else if (auto member = ast::as<ast::Member>(callee)) {
      if (auto held = module_at(member->base))
        if (auto one = held->exports.get(std::string(member->name)))
          found = *one;
    } else {
      return nullptr;
    }
    if (auto generic = found.get<Generic>())
      return generic;
    return member_named(callee).get<Generic>();
  }


  Held Checker::member_named(const ast::Expr* callee)
  {
    auto member = ast::as<ast::Member>(callee);
    if (member == nullptr)
      return {};
    if (auto base = ast::as<ast::NameRef>(member->base)) {
      if (members.empty())
        return {};
      auto held = top->get(std::string(base->name));
      if (held == nullptr || held->get<NamedType>() == nullptr)
        return {};
      auto found = members.get({ std::string(base->name), std::string(member->name) });
      return found == nullptr ? Held{} : *found;
    }
    if (auto through = ast::as<ast::Member>(member->base)) {
      auto held = module_at(through->base);
      if (held == nullptr)
        return {};
      auto found = held->members.get({ std::string(through->name), std::string(member->name) });
      return found == nullptr ? Held{} : *found;
    }
    return {};
  }


  std::optional<std::tuple<std::string, std::string>> Checker::a_member_path(const ast::Expr* callee)
  {
    auto member = ast::as<ast::Member>(callee);
    if (member == nullptr)
      return std::nullopt;
    if (auto base = ast::as<ast::NameRef>(member->base)) {
      auto held = top->get(std::string(base->name));
      if (held != nullptr && held->get<NamedType>() != nullptr)
        return std::tuple{ std::string(base->name), std::string(member->name) };
      return std::nullopt;
    }
    if (auto through = ast::as<ast::Member>(member->base); through != nullptr && module_at(through->base) != nullptr) {
      if (type_exported(through) == nullptr)
        return std::nullopt;
      return std::tuple{ std::string(through->name), std::string(member->name) };
    }
    return std::nullopt;
  }


  ir::Function* Checker::callee_of_module(const ast::Member* expr)
  {
    if (auto func = member_named(expr).get<ir::Function>()) {
      only_at_build(func, expr->span);
      return func;
    }
    if (auto named = a_member_path(expr)) [[unlikely]] {
      diags.emit(D::LANG_MEMBER_UNKNOWN, expr->name_span, { { "name", std::get<1>(*named) }, { "held", std::get<0>(*named) } });
      return nullptr;
    }
    auto base = expr->base;
    auto held = module_at(base);
    if (held == nullptr) [[unlikely]] {
      if (auto ref = ast::as<ast::NameRef>(base))
        diags.emit(D::LANG_IMPORT_NOT_A_MODULE, base->span, { { "name", ref->name } });
      else if (auto member = ast::as<ast::Member>(base); member != nullptr && module_at(member->base) != nullptr)
        // A module the one before it does not hand on, or nothing at all by
        // that name: either way it is not something it exports.
        diags.emit(D::LANG_IMPORT_NOT_EXPORTED, member->name_span,
                   { { "name", member->name }, { "module", path_text(member->base) } });
      else
        diags.emit(D::LANG_IMPORT_NOT_A_MODULE, expr->span, { { "name", "an expression" } });
      return nullptr;
    }
    auto found = held->exports.get(std::string(expr->name));
    if (found == nullptr || found->empty()) [[unlikely]] {
      diags.emit(D::LANG_IMPORT_NOT_EXPORTED, expr->name_span, { { "name", expr->name }, { "module", path_text(base) } });
      return nullptr;
    }
    auto func = found->get<ir::Function>();
    if (func == nullptr) [[unlikely]] {
      diags.emit(D::LANG_CALL_NOT_A_FUNCTION, expr->span, { { "name", expr->name } });
      return nullptr;
    }
    if (notes != nullptr) {
      // The module the name is reached through, and then the function itself:
      // two notes over two spans, and the narrower is the one the cursor is on.
      auto where = defined.get(path_text(base));
      note(base->span, note_kind::module, path_text(base), held->path.generic_string(),
           where != nullptr ? std::get<0>(*where) : invalid_span);
      note(expr->name_span, note_kind::function, expr->name, func->ty->written(), func->name_span,
           func->doc.empty() ? std::nullopt : std::optional<std::string>(func->doc));
    }
    only_at_build(func, expr->span);
    return func;
  }


  bool Checker::would_copy(Local* local, const ast::NameRef* ref)
  {
    auto held = ir::as<ir::ProductType>(held_by(local));
    if (held == nullptr || ! held->unique)
      return false;
    diags.emit(D::LANG_UNIQUE_COPIED, ref->span,
               { { "type", held->written() }, { "what", std::format("reading '{}' here would be a copy of it", ref->name) } });
    return true;
  }


  ir::Value* Checker::lower_name(ir::Builder& builder, const ast::NameRef* ref, const ir::Type* expected)
  {
    if (ref->name == names::wrap_name) [[unlikely]] {
      // It looks like a function and is not one.
      diags.emit(D::LANG_WRAP_IS_NOT_A_VALUE, ref->span);
      return failed_value(module);
    }
    if (ref->name.starts_with(names::syscall_number_prefix))
      return syscall_number(ref);
    if (ref->name == names::empty_arena_name) {
      // An arena that has asked the system for nothing yet: three words of
      // nought.
      std::vector<ir::Value*> words;
      for (std::size_t i = 0; i < ir::arena_type()->fields.size(); ++i)
        words.push_back(builder.int_const(ir::u64(), 0));
      return builder.make_tuple(words, ir::arena_type(), ref->span);
    }
    auto local = find_local(ref->name);
    if (local != nullptr && local->iterable != nullptr)
      return iterator_named(builder, ref, local, expected);
    if (local == nullptr) {
      if (auto held = top->get(std::string(ref->name)); held != nullptr && held->get<Generic>() != nullptr) [[unlikely]] {
        // Compiled once per set of types, and which sets those are is what the
        // calls ask for: there is no one function for the name to stand for.
        diags.emit(D::LANG_GENERIC_IS_NOT_A_VALUE, ref->span, { { "name", ref->name } });
        return failed_value(module);
      }
      if (auto found = provided(ref->name); found.get<ir::Function>() != nullptr)
        // A function named where a value is wanted, which a call is not.
        return function_as_a_value(builder, found, ref, expected);
    }
    if (local != nullptr && would_copy(local, ref))
      return failed_value(module);
    if (local != nullptr && ! local->closer.empty()) {
      if (auto stored = stored_into.get(id_of(ref))) [[unlikely]] {
        diags.emit(D::LANG_CLOSABLE_STORED, ref->span, { { "type", held_by(local)->written() }, { "what", *stored } });
        // Reported here, and not again where its scope ends.
        local->owes = false;
        return failed_value(module);
      }
      if (! moved(local, ref->span, move_named(ref->name)))
        return failed_value(module);
    }
    if (local != nullptr && ! local->placed && ir::is<ir::PtrType>(held_by(local))) {
      // A reference held in a register is lent the way a place is.
      if (lent_out(ref->name, ref->span, false))
        return failed_value(module);
      auto held = ir::as<ir::PtrType>(held_by(local));
      if (held->is_mutable && held->pointee != ir::arena_type() && ! read_through_ids.contains(id_of(ref))) {
        // As writable as the copy: a `&` wanted is a shared reborrow.
        auto wanted = ir::as<ir::PtrType>(aiming_at(expected));
        lend(std::string(ref->name), ! (wanted != nullptr && ! wanted->is_mutable), ref->span);
      }
    }
    if (local != nullptr && local->gone_at) [[unlikely]] {
      // Read all the same, so that what is said about it is the one thing.
      local->read = true;
      told_gone(local, ref->name, ref->span);
      return failed_value(module);
    }
    if (local != nullptr && local->placed) {
      // The name stands for storage of its own: reading it is a load.
      if ((! taking_a_reference || *taking_a_reference != ref->name) && lent_out(ref->name, ref->span, false))
        return failed_value(module);
      local->read = true;
      auto held = local->held;
      auto found = ir::is<ir::ProductType>(held)
                     ? record_from(builder, local->value, ir::as<ir::ProductType>(held), ref->span)
                     : builder.load(local->value, ref->span);
      if (! accepts(expected, found->ty)) [[unlikely]]
        report_mismatch(ref->span, found->ty, expected);
      return found;
    }
    auto resolved = lookup(ref);
    if (resolved == nullptr)
      return failed_value(module);
    if (resolved->vkind == ir::ValueKind::global) {
      auto var = static_cast<ir::GlobalVar*>(resolved);
      if (var->value_type == ir::error_type())
        return failed_value(module);
      if (auto closable = closable_in(var->value_type)) [[unlikely]] {
        diags.emit(D::LANG_CLOSABLE_MOVED_OUT_OF_PLACE, ref->span,
                   { { "type", closable->written() }, { "what", "a variable at the top level" } });
        return failed_value(module);
      }
      // What a value of an array type whose shape is known *is*, is where the
      // elements are, and the same holds of a sum.
      resolved = read_global(builder, var, ref->span);
    }
    if (! accepts(expected, resolved->ty)) [[unlikely]]
      report_mismatch(ref->span, resolved->ty, expected);
    return resolved;
  }


  ir::Value* Checker::lower_into(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected, Span span)
  {
    if (auto array = ir::as<ir::ArrayType>(expected); array != nullptr && ! array->fixed())
      return spread(builder, expr, array, span);
    if (auto iter = ir::as<ir::IterType>(expected))
      return as_an_iterator(builder, expr, iter, span);
    auto result = ir::as<ir::ResultType>(expected);
    if (result == nullptr)
      return shorter_life(builder, lower_expr(builder, expr, expected), expected, span);
    // The whole type goes down, not nothing and not the answer alone.
    auto value = lower_expr(builder, expr, expected);
    auto found = value_type_of(value);
    if (found == expected || found == ir::error_type())
      return value;
    if (found == result->ok) {
      // An answer, so what the error would have carried is not there.
      auto no = builder.bool_const(false);
      return builder.wrap(value, no, expected, span, result->err == nullptr ? nullptr : undef(module, result->err));
    }
    report_mismatch(span, found, expected);
    return failed_value(module);
  }


  ir::Value* Checker::shorter_life(ir::Builder& builder, ir::Value* value, const ir::Type* expected, Span span)
  {
    auto found = value_type_of(value);
    if (is_number(expected) && is_number(found) && found != expected && bare_of(found) == bare_of(expected)
        && stands_for(unit_on(found), unit_on(expected)))
      // A number in a unit the program said may stand here: the same bits
      // under the type wanted, so that what reads the name reads that type.
      return builder.cast(ir::CastKind::bitcast, value, expected, span);
    if (! promises_as_much(expected, found, calling_key))
      return value;
    if (ir::is<ir::FuncType>(expected) || ir::is<ir::RunningType>(expected)) {
      // A function is three words and not one: the three are taken out and
      // put back under the type that promises less.
      std::vector<ir::Value*> parts;
      auto types = ir::parts_of(expected);
      for (std::size_t at = 0; at < types.size(); ++at)
        parts.push_back(builder.extract(value, at, types[at], span));
      auto read = builder.make_tuple(parts, expected, span);
      // What it promised, for a call to hold it to.
      promised_as[id_of(read)] = found;
      return read;
    }
    return builder.cast(ir::CastKind::bitcast, value, expected, span);
  }


  ir::Value* Checker::spread(ir::Builder& builder, const ast::Expr* expr, const ir::ArrayType* expected, Span span)
  {
    // An `if` or a `match` is told too, so that each arm is lowered into the
    // view and arms of different lengths meet as one.
    auto value = lower_expr(builder, expr,
                            ast::is<ast::ArrayLit>(expr) || in_the_image(expr) || ast::is<ast::If>(expr)
                                || ast::is<ast::Match>(expr)
                              ? expected
                              : nullptr);
    auto found = value_type_of(value);
    if (found == expected || found == ir::error_type())
      return value;
    if (auto made = as_a_view(builder, expr, value, expected, span); made != nullptr)
      return made;
    report_mismatch(span, found, expected);
    return failed_value(module);
  }


  ir::Value* Checker::as_declared(ir::Value* value, const ir::Type* expected)
  {
    if (value->ty == expected || value->ty == ir::error_type())
      return value;
    return failed_value(module);
  }


  std::optional<std::int64_t> Checker::walks_down_to(const ir::Type* found, const ir::Type* wanted)
  {
    std::int64_t depth = 0;
    auto seen = found;
    while (seen != wanted) {
      auto array = ir::as<ir::ArrayType>(seen);
      if (array == nullptr || ! array->fixed())
        return std::nullopt;
      seen = array->rank() == 1 ? array->element
                                : ir::array_type(array->element, { array->shape.begin() + 1, array->shape.end() });
      ++depth;
    }
    return depth;
  }


  const ir::Type* Checker::aiming_at(const ir::Type* expected)
  {
    auto result = ir::as<ir::ResultType>(expected);
    return result != nullptr ? result->ok : expected;
  }


  bool Checker::accepts(const ir::Type* expected, const ir::Type* found)
  {
    if (expected == nullptr || found == ir::error_type() || expected == ir::error_type())
      return true;
    if (found == expected)
      return true;
    if (is_number(expected) && is_number(found) && bare_of(found) == bare_of(expected)
        && stands_for(unit_on(found), unit_on(expected)))
      // The same number in a unit the program said may stand here.
      return true;
    if (promises_as_much(expected, found, calling_key))
      // A reference that promises at least what is wanted.
      return true;
    if (deriving && is_number(expected) && is_number(found) && bare_of(found) == bare_of(expected))
      // An operand of a product or a quotient.
      return true;
    if (listing && ir::is<ir::ArrayType>(found))
      // An argument of a call being walked.
      return true;
    auto result = ir::as<ir::ResultType>(expected);
    return result != nullptr && found == result->ok;
  }


  Marks* Checker::marks_for(const ast::FuncDef* node, ir::Function* func, Generator* walked)
  {
    auto found_at = marks.get(id_of(node));
    Marks* found = found_at != nullptr ? *found_at : nullptr;
    if (found == nullptr) {
      found = make<Marks>();
      found->node = node;
      for (auto one : node->attrs)
        if (one->name == "impure") {
          found->impure_at = one->name_span;
          break;
        }
      for (auto one : node->attrs)
        if (one->name == "io") {
          found->io_at = one->name_span;
          break;
        }
      marks[id_of(node)] = found;
    }
    std::span<const ir::Type* const> params = walked != nullptr ? std::span<const ir::Type* const>(walked->params)
                                                                : std::span<const ir::Type* const>(func->fty->params);
    if (std::ranges::any_of(params, [](auto one) { return reaches_a_device(one); }))
      found->holds_device = true;
    marks_of[id_of(func)] = found;
    return found;
  }


  void Checker::slot(const SlotKey& key, const ast::TypeExpr* written, std::string_view what, bool outside)
  {
    if (outside)
      slots_outside.insert(key);
    if (auto func = ast::as<ast::FuncTypeRef>(written))
      for (auto one : func->attrs)
        if (one->name == "impure") {
          slots_marked.setdefault(key, std::tuple{ one->name_span, std::string(what) });
          break;
        }
  }


  void Checker::flow(const SlotKey& key, const ast::Expr* written)
  {
    // An empty key is Python's `None`: no slot.
    if (! key.empty())
      slot_flows[key].push_back(written == nullptr ? Held{} : source_of(written));
  }


  Held Checker::source_of(const ast::Expr* written)
  {
    // A slot is said by its key, a named function by itself, and a lambda by
    // its node: Python's `("slot", key)`, `("function", f)` and
    // `("lambda", id)`.
    if (auto ref = ast::as<ast::NameRef>(written)) {
      if (auto local = find_local(ref->name)) {
        auto key = slot_of_local.get(id_of(local));
        return key == nullptr ? Held{} : Held(*key);
      }
      auto found = provided(ref->name);
      if (auto func = found.get<ir::Function>())
        return Held(static_cast<ir::Value*>(func));
      return {};
    }
    if (ast::is<ast::Lambda>(written))
      return Held(static_cast<const ast::Node*>(written));
    if (auto member = ast::as<ast::Member>(written)) {
      auto ty = said_to_be(member->base);
      if (auto ptr = ir::as<ir::PtrType>(ty))
        ty = ptr->pointee;
      if (auto record = ir::as<ir::ProductType>(ty))
        return Held(slot_key("field", record->name, member->name));
      return {};
    }
    if (auto call = ast::as<ast::Call>(written)) {
      auto ref = ast::as<ast::NameRef>(call->callee);
      if (ref != nullptr && find_local(ref->name) == nullptr) {
        auto found = provided(ref->name);
        auto func = found.get<ir::Function>();
        auto node = func != nullptr ? node_of_func.get(id_of(func)) : nullptr;
        if (node != nullptr)
          return Held(slot_key("answer", id_of(*node)));
      }
    }
    return {};
  }


  void Checker::field_slots(const ast::Node* node)
  {
    auto def = ast::as<ast::TypeDef>(node);
    if (def == nullptr || def->kind != ast::TypeKind::product || ! def->params.empty())
      return;
    bool outside = std::ranges::any_of(def->attrs, [](auto one) { return one->name == "export"; });
    for (auto one : def->fields)
      slot(slot_key("field", def->name, one->name), one->type,
           std::format("the field '{}' of '{}'", one->name, def->name), outside);
  }


  void Checker::variable_slot(const ast::VarDef* stmt)
  {
    auto local = find_local(stmt->name);
    if (local == nullptr || ! stmt->more.empty() || ! ir::is<ir::FuncType>(held_by(local)))
      return;
    auto key = slot_key("variable", id_of(local));
    slot_of_local[id_of(local)] = key;
    slot(key, stmt->type, std::format("the variable '{}'", stmt->name), false);
    flow(key, stmt->value);
  }


  void Checker::flows_of_call(const ast::Call* expr, ir::Function* func)
  {
    auto node = node_of_func.get(id_of(func));
    if (node == nullptr)
      return;
    auto written = written_for(expr, func);
    for (std::size_t at = 0; at < (*node)->params.size(); ++at) {
      auto given = written.get(static_cast<std::int64_t>(at));
      flow(slot_key("param", id_of(*node), at), given == nullptr ? nullptr : *given);
    }
  }


  void Checker::parameter_slots(const ast::FuncDef* node, ir::Function* func)
  {
    bool outside = func->linkage != ir::Linkage::internal;
    slot(slot_key("answer", id_of(node)), node->ret_type, std::format("what '{}' answers", node->name), outside);
    for (std::size_t at = 0; at < node->params.size(); ++at) {
      auto param = node->params[at];
      auto key = slot_key("param", id_of(node), at);
      slot(key, param->type, std::format("the parameter '{}' of '{}'", param->name, node->name), outside);
      if (auto local = find_local(param->name))
        slot_of_local[id_of(local)] = key;
    }
  }


  void Checker::check_marks()
  {
    auto record = [this](const Held& target) -> Marks* {
      if (auto def = target.get<ast::FuncDef>()) {
        auto found = marks.get(id_of(def));
        return found == nullptr ? nullptr : *found;
      }
      auto found = marks_of.get(identity_of(target));
      return found == nullptr ? nullptr : *found;
    };

    std::vector<Marks*> all;
    for (const auto& [_, one] : marks)
      all.push_back(one);
    for (const auto& [_, one] : lambda_marks)
      all.push_back(one);
    std::set<const Marks*> changes;
    std::set<const Marks*> io;
    for (auto one : all) {
      if (one->changes)
        changes.insert(one);
      if (one->does_io)
        io.insert(one);
    }
    std::set<SlotKey> slots(slots_outside.begin(), slots_outside.end());

    // Whether what comes from `source` may change things.
    auto gives = [&](const Held& source) -> bool {
      if (auto key = source.text())
        return slots.contains(*key);
      if (auto func = source.get<ir::Function>()) {
        if (! func->attrs.impure || func->attrs.io)
          return false;
        auto found = marks_of.get(id_of(func));
        return found == nullptr || ! (*found)->impure_at || changes.contains(*found);
      }
      if (auto lambda = source.get<ast::Lambda>()) {
        auto found = lambda_marks.get(id_of(lambda));
        return found == nullptr || changes.contains(*found);
      }
      return true;
    };

    // Whether calling `target` may change things.
    auto calls = [&](const Held& target) -> bool {
      if (auto key = target.text())
        return slots.contains(*key);
      auto found = record(target);
      return found == nullptr || ! found->impure_at || changes.contains(found);
    };

    bool grew = true;
    while (grew) {
      grew = false;
      for (const auto& [key, sources] : slot_flows)
        if (! slots.contains(key) && std::ranges::any_of(sources, gives)) {
          slots.insert(key);
          grew = true;
        }
      for (auto one : all) {
        if (! changes.contains(one) && std::ranges::any_of(one->changes_through, calls)) {
          changes.insert(one);
          grew = true;
        }
        if (! io.contains(one) && std::ranges::any_of(one->io_through, [&](const Held& target) {
              auto found = record(target);
              return found == nullptr || io.contains(found);
            })) {
          io.insert(one);
          grew = true;
        }
      }
    }
    for (const auto& [key, marked_at] : slots_marked)
      if (! slots.contains(key))
        diags.emit(D::LANG_TYPE_MARK_NOT_NEEDED, std::get<0>(marked_at), { { "what", std::get<1>(marked_at) } });
    for (auto one : all) {
      auto def = ast::as<ast::FuncDef>(one->node);
      if (def == nullptr)
        continue;
      if (session != nullptr && def->name == session->function)
        // What an input of the session does is whatever was typed; the mark
        // is the session's, not the reader's.
        continue;
      if (one->impure_at && ! changes.contains(one))
        diags.emit(D::LANG_MARK_NOT_NEEDED, *one->impure_at,
                   { { "name", def->name },
                     { "mark", "impure" },
                     { "why", "nothing it does changes anything outside the call" } });
      if (one->io_at && (! io.contains(one) || one->holds_device))
        diags.emit(D::LANG_MARK_NOT_NEEDED, *one->io_at,
                   { { "name", def->name },
                     { "mark", "io" },
                     { "why", io.contains(one) ? "the device it is handed is what lets it" : "it does no input or output" } });
    }
  }


  void Checker::what_it_changes(ir::Function* func, Span span)
  {
    if (func->attrs.opaque) {
      // A call that has to stay is one its caller's callers may not drop either.
      auto owner = std::get<1>(demanding);
      if (owner != nullptr && ! owner->attrs.opaque)
        owner->attrs.opaque = true;
    }
    if (! func->attrs.impure)
      return;
    if (func->attrs.io) {
      if (marking != nullptr)
        marking->io_through.push_back(Held(static_cast<ir::Value*>(func)));
      if (! may_do_io)
        an_effect(D::LANG_IO_WITHOUT_A_DEVICE, span, {}, { { "name", func->name } });
      // It did it, so this function does it.
      did_io = true;
      return;
    }
    an_effect(D::LANG_PURE_CALLS_IMPURE, span, Held(static_cast<ir::Value*>(func)), { { "name", func->name } });
  }


  void Checker::an_effect(DiagId which, Span span, Held through, DiagArgs args)
  {
    if (marking != nullptr && which != D::LANG_IO_WITHOUT_A_DEVICE) {
      // Written down whether or not it is allowed here: what a marked function
      // did is what says its mark is needed.
      if (! through.empty())
        marking->changes_through.push_back(through);
      else
        marking->changes = true;
    }
    if (impure && which != D::LANG_IO_WITHOUT_A_DEVICE)
      return;
    if (observing) {
      (*observing)[0] = true;
      return;
    }
    if (giving != nullptr)
      // The copy into the variable the initializer gives a value.
      return;
    if (constinit_of != nullptr && std::get<1>(demanding) == nullptr) {
      // The initializer of a `constinit` variable, which is pure however the
      // effect came about: there is no function to mark.
      diags.emit(D::LANG_CONSTINIT_HAS_AN_EFFECT, span, { { "name", constinit_of->name } });
      return;
    }
    auto held = std::get<0>(demanding);
    std::string_view name = held != nullptr ? held->name : std::string_view();
    DiagArgs said;
    if (! name.empty() && (which == D::LANG_PURE_CALLS_IMPURE || which == D::LANG_IO_WITHOUT_A_DEVICE))
      said.emplace_back("caller", name);
    said.insert(said.end(), args.begin(), args.end());
    auto told = diags.emit(which, span, std::move(said));
    if (held != nullptr && ! name.empty())
      // Which attribute would answer it: a device asks for `@[io]`, not for
      // `@[impure]`.
      told.note(which == D::LANG_IO_WITHOUT_A_DEVICE ? D::LANG_IO_FUNCTION_HERE : D::LANG_PURE_FUNCTION_HERE,
                held->name_span, { { "name", name } });
  }


  bool Checker::made_here(ir::Value* place)
  {
    auto seen = place;
    while (true) {
      if (seen == nullptr || seen->vkind != ir::ValueKind::instruction)
        return false;
      auto inst = static_cast<ir::Instruction*>(seen);
      if (inst->kind == ir::InstKind::FrameInst)
        return true;
      if (inst->kind == ir::InstKind::CastInst
          || (inst->kind == ir::InstKind::BinaryInst && (inst->bin == ir::BinOp::add || inst->bin == ir::BinOp::sub))) {
        seen = inst->operands[0];
        continue;
      }
      if (inst->kind == ir::InstKind::ExtractInst) {
        auto made = inst->operands[0];
        if (inst->index == 0)
          if (auto load = inst_of(made, ir::InstKind::LoadInst); load != nullptr && ir::is<ir::ListType>(load->ty)) {
            // The elements of a list are that list's alone, so those of a list
            // in a place this call made are too.
            seen = load->operands[1];
            continue;
          }
        // A part of a view made here is what was put in it.
        auto tuple = inst_of(made, ir::InstKind::TupleInst);
        seen = tuple != nullptr && inst->index < static_cast<std::int64_t>(tuple->operands.size())
                 ? tuple->operands[inst->index]
                 : made;
        continue;
      }
      return false;
    }
  }


  std::optional<bool> Checker::table_made_here(ir::Value* table)
  {
    auto seen = table;
    while (auto cast = inst_of(seen, ir::InstKind::CastInst))
      seen = cast->operands[0];
    if (seen->vkind == ir::ValueKind::block_param)
      // Where branches meet, or the top of a loop: known only once the body is
      // complete (`settle_tables`).
      return std::nullopt;
    auto call = inst_of(seen, ir::InstKind::CallInst);
    return call != nullptr && call->callee == module.function(tables::new_symbol);
  }


  void Checker::written_table(ir::Value* table, Span span, std::string_view filler)
  {
    auto made = table_made_here(table);
    if (! made)
      unsettled_tables.emplace_back(table, span, std::string(filler));
    else if (! *made)
      table_not_ours(span, filler);
  }


  void Checker::table_not_ours(Span span, std::string_view filler)
  {
    if (! filler.empty())
      an_effect(D::LANG_TABLE_HANDED_TO_A_FILLER, span, {}, { { "func", filler } });
    else
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, span);
  }


  void Checker::tables_handed(std::span<const ir::Type* const> params, std::span<ir::Value* const> args,
                              std::string_view name, Span span)
  {
    for (std::size_t i = 0; i < params.size() && i < args.size(); ++i)
      if (fills(params[i]))
        written_table(args[i], span, name);
  }


  bool Checker::through_a_parameter(ir::Value* table, ir::Function* func)
  {
    while (auto cast = inst_of(table, ir::InstKind::CastInst))
      table = cast->operands[0];
    auto entry = func->entry();
    return table->vkind == ir::ValueKind::block_param && entry != nullptr
           && static_cast<ir::BlockParam*>(table)->block == entry && fills(table->ty);
  }


  void Checker::settle_tables(ir::Function* func)
  {
    if (unsettled_tables.empty())
      return;
    auto incoming = incoming_of(func);
    auto made_by = module.function(tables::new_symbol);

    std::function<bool(ir::Value*, KeySet&)> made = [&](ir::Value* value, KeySet& seen) -> bool {
      while (auto cast = inst_of(value, ir::InstKind::CastInst))
        value = cast->operands[0];
      if (auto call = inst_of(value, ir::InstKind::CallInst))
        return call->callee == made_by;
      if (value->vkind != ir::ValueKind::block_param)
        return false;
      if (through_a_parameter(value, func))
        // Handed over to be filled: the caller's to answer for.
        return true;
      if (seen.contains(id_of(value)))
        // Back where this walk already is.
        return true;
      seen.insert(id_of(value));
      auto param = static_cast<ir::BlockParam*>(value);
      auto arms = incoming.find(param->block);
      if (arms == incoming.end() || arms->second.empty())
        return false;
      return std::ranges::all_of(arms->second, [&](const ir::BlockTarget* arm) {
        return param->index < arm->args.size() && made(arm->args[param->index], seen);
      });
    };

    auto pending = std::move(unsettled_tables);
    unsettled_tables.clear();
    for (const auto& [table, span, filler] : pending) {
      if (through_a_parameter(table, func)) {
        // The table the caller handed over: the callee fills it, and a call to
        // it is kept as an opaque one is.
        func->attrs.opaque = true;
      } else {
        KeySet seen;
        if (! made(table, seen))
          table_not_ours(span, filler);
      }
    }
  }


  void Checker::report_mismatch(Span span, const ir::Type* found, const ir::Type* expected)
  {
    if (found == ir::error_type() || expected == ir::error_type())
      return;
    auto func_f = ir::as<ir::FuncType>(found);
    auto func_e = ir::as<ir::FuncType>(expected);
    if (func_f != nullptr && func_e != nullptr && func_f->cconv != func_e->cconv) {
      // Said for what it is wherever it happens: the two are different kinds
      // of thing, and a message about an argument or a field would read as
      // though the parameters were what differed.
      diags.emit(D::LANG_CONVENTIONS_DO_NOT_CONVERT, span,
                 { { "found", found->written() }, { "expected", expected->written() } });
      return;
    }
    if (leaving) {
      diags.emit(D::LANG_LOOP_VALUE_MISMATCH, span, { { "found", found->written() }, { "expected", expected->written() } });
      return;
    }
    if (operand_of) {
      // Three different mistakes: an operand where a truth value is what the
      // operator takes, one of a kind the operator has no meaning for, and two
      // operands of one kind whose widths differ.
      if (expected == ir::bool_type())
        diags.emit(D::LANG_TYPE_OPERAND_NOT_BOOLEAN, span, { { "operator", *operand_of }, { "found", found->written() } });
      else if (! ir::is<ir::IntType>(found))
        diags.emit(D::LANG_TYPE_OPERAND_NOT_INTEGER, span, { { "operator", *operand_of }, { "found", found->written() } });
      else
        diags.emit(D::LANG_TYPE_OPERAND_MISMATCH, span,
                   { { "operator", *operand_of }, { "expected", expected->written() }, { "found", found->written() } });
      return;
    }
    if (handing_over) {
      const auto& [name, position] = *handing_over;
      diags.emit(D::LANG_CALL_ARGUMENT_MISMATCH, span,
                 { { "position", position }, { "name", name }, { "expected", expected->written() }, { "found", found->written() } });
      return;
    }
    if (filling) {
      const auto& [owner, name] = *filling;
      diags.emit(D::LANG_FIELD_VALUE_MISMATCH, span,
                 { { "name", name }, { "type", owner }, { "expected", expected->written() }, { "found", found->written() } });
      return;
    }
    if (assigning) {
      diags.emit(D::LANG_TYPE_ASSIGNMENT_MISMATCH, span,
                 { { "name", *assigning }, { "expected", expected->written() }, { "found", found->written() } });
      return;
    }
    if (initializing) {
      diags.emit(D::LANG_TYPE_INITIALIZER_MISMATCH, span,
                 { { "name", *initializing }, { "expected", expected->written() }, { "found", found->written() } });
      return;
    }
    diags.emit(D::LANG_TYPE_RETURN_MISMATCH, span, { { "found", found->written() }, { "expected", expected->written() } });
  }


  // -- program level ----------------------------------------------------------

  void Checker::check_program()
  {
    if (module.startup == nullptr && module.build == nullptr && module.output != ir::Output::shared)
      // A file with a build function in it describes a build rather than being
      // a program, so nothing of it is ever started; a shared object is called
      // by the program that loads it, and has no start of its own.
      diags.emit(D::LANG_FUNCDEF_SPECIAL_NO_STARTUP);
    order_initializers();
    report_unread_variables();
  }


  namespace {

    // Every variable at the top level the initializer `init` reads, itself or
    // through a function with a body it names, in the order first met: its own
    // variable only where an instruction of its own function reads it, the
    // store and a call answering into it naming it too (check.py:_variables_reached).
    std::vector<ir::GlobalVar*> variables_reached(const ir::Initializer& init)
    {
      std::vector<ir::GlobalVar*> found;
      std::unordered_set<const ir::GlobalVar*> met;
      std::unordered_set<const ir::Function*> seen;
      std::deque<ir::Function*> queue{ init.func };
      while (! queue.empty()) {
        auto one = queue.front();
        queue.pop_front();
        if (! seen.insert(one).second)
          continue;
        for (const auto& block : one->blocks)
          for (auto inst : block->insts) {
            for (auto named : inst->operands)
              if (named != nullptr && named->vkind == ir::ValueKind::global) {
                auto var = static_cast<ir::GlobalVar*>(named);
                bool reads = inst->kind == ir::InstKind::LoadInst && inst->operands[1] == named;
                if ((var != init.var || one != init.func || reads) && met.insert(var).second)
                  found.push_back(var);
              }
            if (inst->kind == ir::InstKind::CallInst || inst->kind == ir::InstKind::CodeInst)
              if (auto callee = inst->callee; callee != nullptr && ! callee->is_declaration())
                queue.push_back(callee);
          }
      }
      return found;
    }

  } // anonymous namespace


  void Checker::order_initializers()
  {
    // Where nothing else decides, the order the variables were defined in,
    // which is the order they are in the module; an initializer reading
    // another `constinit` variable comes after that one's.
    auto& initializers = module.initializers;
    if (initializers.empty())
      return;
    std::unordered_map<const ir::GlobalVar*, std::size_t> rank;
    for (const auto& [_, var] : module.globals)
      rank.try_emplace(var.get(), rank.size());
    auto rank_of = [&](const ir::Initializer& one) {
      auto at = rank.find(one.var);
      return at == rank.end() ? rank.size() : at->second;
    };
    std::ranges::stable_sort(initializers, {}, rank_of);
    std::unordered_map<const ir::GlobalVar*, std::size_t> of_var;
    for (std::size_t at = 0; at < initializers.size(); ++at)
      of_var.try_emplace(initializers[at].var, at);
    // A table the generated constructor builds is nought until it has run,
    // which is after every initializer.
    std::unordered_set<const ir::Value*> built;
    for (auto func : module.ctors)
      if (func->name.starts_with(globals_symbol))
        for (const auto& block : func->blocks)
          for (auto inst : block->insts)
            if (inst->kind == ir::InstKind::StoreInst && inst->operands[1]->vkind == ir::ValueKind::global)
              built.insert(inst->operands[1]);
    std::vector<ir::Initializer> ordered;
    std::unordered_set<std::size_t> done;
    std::vector<std::size_t> path;
    auto visit = [&](this auto& self, std::size_t one) -> void {
      path.push_back(one);
      const auto& init = initializers[one];
      for (auto var : variables_reached(init)) {
        if (built.contains(var)) [[unlikely]] {
          diags.emit(D::LANG_CONSTINIT_READS_A_TABLE, init.var->name_span,
                     { { "name", init.var->name }, { "table", var->name } });
          continue;
        }
        auto read = of_var.find(var);
        if (read == of_var.end() || done.contains(read->second))
          continue;
        if (auto open = std::ranges::find(path, read->second); open != path.end()) [[unlikely]] {
          std::string circle;
          for (auto step = open; step != path.end(); ++step) {
            circle += initializers[*step].var->name;
            circle += " \N{RIGHTWARDS ARROW} ";
          }
          circle += initializers[read->second].var->name;
          const auto& closed = initializers[read->second];
          diags.emit(D::LANG_CONSTINIT_READS_ITSELF, closed.var->name_span,
                     { { "name", closed.var->name }, { "cycle", circle } });
          continue;
        }
        self(read->second);
      }
      path.pop_back();
      done.insert(one);
      ordered.push_back(init);
    };
    for (std::size_t one = 0; one < initializers.size(); ++one)
      if (! done.contains(one))
        visit(one);
    initializers = std::move(ordered);
  }


  void Checker::report_unread_variables()
  {
    auto [reads, writes] = variable_uses();
    for (auto entry : top_level) {
      auto var = entry->var;
      if (var->linkage == ir::Linkage::visible || var->exported) {
        // Something this compilation cannot see may read it.
        settle_global(entry);
        continue;
      }
      auto count = writes.get_or(id_of(var), 0);
      if (count != 0 && ! reads.contains(id_of(var)) && var->value_type != ir::error_type()) {
        if (entry->expectation != nullptr)
          diags.resume(*entry->expectation);
        Finally ended{ [&] { end_expecting(entry->expectation); } };
        diags.emit(D::LANG_VARDEF_TOPLEVEL_UNREAD, entry->span, { { "name", var->name } });
      }
      settle_global(entry);
    }
  }


  void Checker::settle_global(Global* entry)
  {
    settle_expecting(entry->expectation, entry->expected_pairs);
    entry->expectation = nullptr;
  }


  std::tuple<KeySet, Dict<std::int64_t, std::int64_t>> Checker::variable_uses()
  {
    KeySet reads;
    Dict<std::int64_t, std::int64_t> writes;
    // What Python's dict of functions holds: a key filed twice holds the last.
    std::set<std::string_view> later;
    std::vector<ir::Function*> listed;
    for (auto it = module.functions.rbegin(); it != module.functions.rend(); ++it)
      if (later.insert(it->first).second)
        listed.push_back(it->second.get());
    std::ranges::reverse(listed);
    for (auto func : listed) {
      if (func->attrs.special == ir::SpecialKind::initializer)
        // Giving a `constinit` variable its value is what defining one does,
        // and no more a write than an ordinary initializer is.
        continue;
      for (const auto& block : func->blocks)
        for (auto inst : block->insts) {
          if (inst->kind == ir::InstKind::LoadInst && inst->operands[1]->vkind == ir::ValueKind::global)
            reads.insert(id_of(inst->operands[1]));
          if (inst->kind == ir::InstKind::StoreInst && inst->operands[1]->vkind == ir::ValueKind::global)
            writes[id_of(inst->operands[1])] += 1;
        }
    }
    return { std::move(reads), std::move(writes) };
  }

} // namespace pl4g::sema
