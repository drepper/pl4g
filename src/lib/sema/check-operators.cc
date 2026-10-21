// The checker: joining text, the operators, calls, and what a call of one of
// the compiler's own functions comes to.
//
// A port of pypl4g/sema/check.py, lines 23413 to 26512, method for method.
#include <cmath>
#include <format>
#include <limits>

#include "ir/printer.hh"
#include "sema/check.hh"
#include "sema/lists.hh"
#include "sema/owned.hh"
#include "sema/strings.hh"
#include "sema/tables.hh"
#include "target/statuses.hh"
#include "target/syscalls.hh"
#include "target/registry.hh"


namespace pl4g::sema {

  namespace {

    // A value standing for what could not be worked out: Python's
    // `UndefConst(ERROR)`.
    ir::Value* error_value(ir::Module& module)
    {
      return const_cast<ir::UndefConst*>(module.undef(ir::error_type()));
    }

    // A constant as a value an instruction may use.
    ir::Value* used(const ir::Value* value)
    {
      return const_cast<ir::Value*>(value);
    }

    // A member of the checker set to something else for as long as this
    // lives, and put back however the scope is left: Python's
    // `outer, self._x = self._x, new` with the `finally` that undoes it.
    template<typename T>
    class Setting {
    public:
      Setting(T& place, T value) : place(place), kept(std::move(place)) { place = std::move(value); }
      Setting(const Setting&) = delete;
      Setting& operator=(const Setting&) = delete;
      ~Setting() { place = std::move(kept); }

    private:
      T& place;
      T kept;
    };

    // The glyph an operator is written with: Python's `op.value`.
    std::string glyph(ast::BinaryOp op)
    {
      return std::string(describe(op));
    }
    std::string glyph(ast::UnaryOp op)
    {
      return std::string(describe(op));
    }

    bool is_int_const(const ir::Value* value)
    {
      return value != nullptr && value->vkind == ir::ValueKind::int_const;
    }

    bool is_float_const(const ir::Value* value)
    {
      return value != nullptr && value->vkind == ir::ValueKind::float_const;
    }

    // The largest code point there is.
    constexpr std::uint32_t max_code_point = 0x10FFFF;

    // The names of the compiler's own functions this part lowers: front/token.py's.
    constexpr std::string_view typename_name = "\N{APL FUNCTIONAL SYMBOL QUAD}typename";
    constexpr std::string_view builtin_glyph = "\N{APL FUNCTIONAL SYMBOL QUAD}";
    constexpr std::string_view wrap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}wrap";
    constexpr std::string_view ones_name = "\N{APL FUNCTIONAL SYMBOL QUAD}ones";
    constexpr std::string_view lead_name = "\N{APL FUNCTIONAL SYMBOL QUAD}lead";
    constexpr std::string_view mask_name = "\N{APL FUNCTIONAL SYMBOL QUAD}mask";
    constexpr std::string_view ord_name = "\N{APL FUNCTIONAL SYMBOL QUAD}ord";
    constexpr std::string_view chr_name = "\N{APL FUNCTIONAL SYMBOL QUAD}chr";
    constexpr std::string_view drop_name = "\N{APL FUNCTIONAL SYMBOL QUAD}drop";
    constexpr std::string_view unit_name = "\N{APL FUNCTIONAL SYMBOL QUAD}unit";
    constexpr std::string_view entry_name = "\N{APL FUNCTIONAL SYMBOL QUAD}entry";
    constexpr std::string_view narrow_name = "\N{APL FUNCTIONAL SYMBOL QUAD}narrow";
    constexpr std::string_view widen_name = "\N{APL FUNCTIONAL SYMBOL QUAD}widen";
    constexpr std::string_view syscall_name = "\N{APL FUNCTIONAL SYMBOL QUAD}syscall";
    constexpr std::string_view acquire_name = "\N{APL FUNCTIONAL SYMBOL QUAD}acquire";
    constexpr std::string_view release_name = "\N{APL FUNCTIONAL SYMBOL QUAD}release";
    constexpr std::string_view at_name = "\N{APL FUNCTIONAL SYMBOL QUAD}at";
    constexpr std::string_view span_name = "\N{APL FUNCTIONAL SYMBOL QUAD}span";
    constexpr std::string_view address_name = "\N{APL FUNCTIONAL SYMBOL QUAD}address";
    constexpr std::string_view iter_name = "\N{APL FUNCTIONAL SYMBOL QUAD}iter";
    constexpr std::string_view reserve_name = "\N{APL FUNCTIONAL SYMBOL QUAD}reserve";
    constexpr std::string_view start_name = "\N{APL FUNCTIONAL SYMBOL QUAD}start";
    constexpr std::string_view resume_name = "\N{APL FUNCTIONAL SYMBOL QUAD}resume";
    constexpr std::string_view bytes_name = "\N{APL FUNCTIONAL SYMBOL QUAD}bytes";
    constexpr std::string_view str_of_name = "\N{APL FUNCTIONAL SYMBOL QUAD}str";
    constexpr std::string_view text_name = names::text_name;
    constexpr std::string_view one_run_name = "\N{APL FUNCTIONAL SYMBOL QUAD}one_run";
    constexpr std::string_view empty_name = "\N{APL FUNCTIONAL SYMBOL QUAD}empty";
    constexpr std::string_view fields_name = "\N{APL FUNCTIONAL SYMBOL QUAD}fields";
    constexpr std::string_view is_record_name = "\N{APL FUNCTIONAL SYMBOL QUAD}isrecord";
    constexpr std::string_view typeof_name = "\N{APL FUNCTIONAL SYMBOL QUAD}typeof";
    constexpr std::string_view heap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";
    constexpr std::string_view static_name = "\N{APL FUNCTIONAL SYMBOL QUAD}static";
    constexpr std::string_view syscall_number_prefix = "\N{APL FUNCTIONAL SYMBOL QUAD}sc@";

    // What a dict of names holds under `name`, or nothing: Python's `d.get(name)`.
    Held held_in(const OrderedMap<std::string, Held>& names, std::string_view name)
    {
      auto found = names.get(std::string(name));
      return found == nullptr ? Held{} : *found;
    }

  } // anonymous namespace


  ir::Value* Checker::joined_text(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right,
                                  const ir::Type* expected)
  {
    for (auto [side, value] : { std::pair{ expr->left, left }, std::pair{ expr->right, right } }) {
      auto found = value_type_of(value);
      if (found != ir::str_type() && found != ir::error_type()) [[unlikely]] {
        diags.emit(D::LANG_CONCAT_NEEDS_AN_ARRAY, side->span, { { "found", found->written() } });
        return error_value(module);
      }
    }
    if (value_type_of(left) == ir::error_type() || value_type_of(right) == ir::error_type()) [[unlikely]]
      return error_value(module);
    // The bytes of both are copied, so the answer is made in its arena and
    // nowhere else: what the two halves were made in stays theirs.
    made_from[id_of(expr)] = { expr, out_of_arenas };
    auto pointer = ir::parts_of(ir::str_type())[0];
    auto first = builder.extract(left, 0, pointer, expr->span);
    auto first_len = builder.extract(left, 1, ir::u64(), expr->span);
    auto second = builder.extract(right, 0, pointer, expr->span);
    auto second_len = builder.extract(right, 1, ir::u64(), expr->span);
    auto arena = out_of(builder, expr->span);
    auto bytes = strings::joined(builder, arena, std::vector<ir::Value*>{ first, first_len, second, second_len },
                                 pointer, expr->span);
    auto length = builder.binary(ir::BinOp::wrap_add, first_len, second_len, expr->span);
    auto found = builder.make_tuple(std::vector<ir::Value*>{ bytes, length, arena }, ir::str_type(), expr->span);
    if (! accepts(expected, ir::str_type())) [[unlikely]] {
      report_mismatch(expr->span, ir::str_type(), expected);
      return error_value(module);
    }
    return found;
  }


  const ir::ArrayType* Checker::joined_type(const ast::Binary* expr, ir::Value* left, ir::Value* right)
  {
    const ir::Type* sides[] = { value_type_of(left), value_type_of(right) };
    if (sides[0] == ir::error_type() || sides[1] == ir::error_type()) [[unlikely]]
      return nullptr;
    const ast::Expr* written[] = { expr->left, expr->right };
    for (int at = 0; at < 2; ++at) {
      auto array = ir::as<ir::ArrayType>(sides[at]);
      if (array == nullptr) [[unlikely]] {
        diags.emit(D::LANG_CONCAT_NEEDS_AN_ARRAY, written[at]->span, { { "found", sides[at]->written() } });
        return nullptr;
      }
      if (! array->fixed()) [[unlikely]] {
        diags.emit(D::LANG_CONCAT_NEEDS_A_STATED_SHAPE, written[at]->span, { { "found", sides[at]->written() } });
        return nullptr;
      }
    }
    auto first = ir::as<ir::ArrayType>(sides[0]);
    auto second = ir::as<ir::ArrayType>(sides[1]);
    if (first->element != second->element) [[unlikely]] {
      diags.emit(D::LANG_CONCAT_ELEMENTS_DIFFER, expr->span,
                 { { "left", first->written() }, { "right", second->written() } });
      return nullptr;
    }
    if (! std::ranges::equal(std::span(first->shape).subspan(1), std::span(second->shape).subspan(1))) [[unlikely]] {
      diags.emit(D::LANG_CONCAT_SHAPES_DIFFER, expr->span,
                 { { "left", first->written() }, { "right", second->written() } });
      return nullptr;
    }
    auto along = first->shape[0].value_or(0) + second->shape[0].value_or(0);
    std::vector<std::optional<std::uint64_t>> shape{ along };
    shape.insert(shape.end(), first->shape.begin() + 1, first->shape.end());
    return ir::array_type(first->element, std::move(shape));
  }


  void Checker::copied(ir::Builder& builder, ir::Value* side, const ir::ArrayType* ty, ir::Value* place,
                       const ir::Type* element, std::int64_t at, Span span)
  {
    auto count = ty->count();
    contract_assert(count.has_value());
    auto held = ir::vec_type(element, unsigned(*count));
    auto pointer = ir::ptr_type(held, true);
    auto [start, _] = shape_of(builder, side, ty, span);
    auto into = builder.cast(ir::CastKind::bitcast,
                             element_place(builder, place, element, builder.int_const(ir::u64(), at), span), pointer,
                             span);
    auto from = builder.load(builder.cast(ir::CastKind::bitcast, start, pointer, span), span);
    builder.store(into, from, span);
  }


  ir::Value* Checker::lower_binary(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    if (expr->op == ast::BinaryOp::concat)
      return lower_concat(builder, expr, expected);
    if (expr->op == ast::BinaryOp::shape)
      return lower_reshape(builder, expr, expected);
    if (expr->op == ast::BinaryOp::power)
      return lower_power(builder, expr, expected);
    if (sema::divides().contains(expr->op))
      return lower_divides(builder, expr, expr->left, expr->right, glyph(expr->op),
                           expr->op == ast::BinaryOp::not_divides, expected);
    // An operator is defined on values and not on arrays, so what is wanted
    // of each side is what an array of them would be an array of.
    auto context = expected != nullptr ? scalar_of(aiming_at(expected)) : hint_of(expr);
    if (auto set = ir::as<ir::SetType>(context); set != nullptr && set->is_mutable)
      // A set operator reads its operands and answers a set of its own.
      context = ir::set_type(set->element);
    // What a program said this operator means, where it said anything: asked
    // before either side is lowered.
    Held written = operators.empty() ? Held{} : operator_written(glyph(expr->op), 2);
    if (written && ! operator_stands(expr, context)) {
      const ast::Expr* operands[] = { expr->left, expr->right };
      if (auto found = operator_call(builder, glyph(expr->op), operands, expr->span, expected); found != nullptr)
        return found;
    }
    ir::Value* left;
    ir::Value* right;
    const ir::Type* ty;
    {
      Setting was_deriving(deriving, sema::derives().contains(expr->op));
      Setting outer(operand_of, std::optional<std::string>(glyph(expr->op)));
      Setting was_listing(listing, true);
      left = lower_expr(builder, expr->left, context);
      ty = scalar_of(value_type_of(left));
      contract_assert(ty != nullptr);
      if (ty != ir::error_type() && ! operand_type_stands(expr->op, ty)) [[unlikely]] {
        diags.emit(D::LANG_TYPE_OPERAND_NOT_INTEGER, expr->left->span,
                   { { "operator", glyph(expr->op) }, { "found", ty->written() } });
        ty = ir::error_type();
      }
      right = lower_expr(builder, expr->right, ty != ir::error_type() ? ty : context);
    }
    if (wrapping && sema::saturates().contains(expr->op)) [[unlikely]] {
      // What is reported here is the one thing that is about the operator
      // rather than about its operands.
      diags.emit(D::LANG_WRAP_SATURATING_INSIDE, expr->span, { { "operator", glyph(expr->op) } });
      return error_value(module);
    }
    if (ty != ir::error_type()) {
      const std::tuple<std::string, ir::Value*> given[] = { { "left", left }, { "right", right } };
      if (auto walked = walk_operands(builder, expr, given, expected); walked != nullptr)
        return walked;
    }
    auto found = scalar_of(value_type_of(right));
    contract_assert(found != nullptr);
    if (ty == ir::error_type() || found == ir::error_type()) [[unlikely]]
      return error_value(module);
    if (! operand_type_stands(expr->op, found)) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_NOT_INTEGER, expr->right->span,
                 { { "operator", glyph(expr->op) }, { "found", found->written() } });
      return error_value(module);
    }
    if (found != ty
        && ! (sema::derives().contains(expr->op) && ir::without_units(found) == ir::without_units(ty))) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_MISMATCH, expr->right->span,
                 { { "operator", glyph(expr->op) }, { "expected", ty->written() }, { "found", found->written() } });
      return error_value(module);
    }
    const ir::Type* derived = nullptr;
    if (sema::derives().contains(expr->op) && value_type_of(left) == ty) {
      // The unit of what comes out is worked out from the two that went in.
      auto made = expr->op == ast::BinaryOp::multiply ? unit_of(ty)->times(*unit_of(found))
                                                       : unit_of(ty)->over(*unit_of(found));
      if (made != unit_of(ty)) {
        ty = carrying(ty, made);
        derived = ty;
      }
      // What the operands carried was not measured against what the place
      // wants, so what came out of them is measured here instead.
      auto aimed = expected != nullptr ? scalar_of(aiming_at(expected)) : nullptr;
      if (aimed != nullptr && ! accepts(aimed, ty)) [[unlikely]] {
        report_mismatch(expr->span, ty, aimed);
        return error_value(module);
      }
    }
    if (answer_is_already_known(expr, ty, left, right)) [[unlikely]]
      return error_value(module);
    if (auto set = ir::as<ir::SetType>(ty); set != nullptr)
      return lower_set_operation(builder, expr->op, set, left, right, expr->span);
    if (auto extremum = sema::extrema().find(expr->op); extremum != sema::extrema().end()) {
      // Which of the two are ordered is the same question a comparison asks.
      if (comparable(expr->span, ast::BinaryOp::less, ty) == ir::error_type()) [[unlikely]]
        return error_value(module);
      bool is_signed = ir::is<ir::FloatType>(ty) || (ir::is<ir::IntType>(ty) && ir::as<ir::IntType>(ty)->is_signed);
      return builder.binary(is_signed ? extremum->second.first : extremum->second.second, left, right, expr->span);
    }
    if (auto shift = sema::shifts().find(expr->op); shift != sema::shifts().end()) {
      auto integer = ir::as<ir::IntType>(ty);
      if ((expr->op == ast::BinaryOp::rotate_left || expr->op == ast::BinaryOp::rotate_right) && integer != nullptr
          && integer->is_signed) [[unlikely]] {
        // Turning the bits of a signed number round has no meaning as a number.
        diags.emit(D::LANG_TYPE_ROTATE_IS_UNSIGNED, expr->span, { { "found", ty->written() } });
        return error_value(module);
      }
      bool is_signed = integer != nullptr && integer->is_signed;
      return builder.binary(wrapped(is_signed ? shift->second.first : shift->second.second), left, right, expr->span);
    }
    if (expr->op == ast::BinaryOp::divide || expr->op == ast::BinaryOp::remainder) {
      // These have no answer for some pairs of operands, so what they answer
      // with is a result.
      auto answer = ir::result_type(ty);
      if (ir::is<ir::FloatType>(ty))
        return builder.binary(ir::BinOp::fdiv, left, right, expr->span, answer);
      // Dividing signed numbers and dividing unsigned ones are different
      // questions, and the type says which was asked.
      bool is_signed = ir::is<ir::IntType>(ty) && ir::as<ir::IntType>(ty)->is_signed;
      auto wanted = expr->op == ast::BinaryOp::divide ? std::pair{ ir::BinOp::sdiv, ir::BinOp::udiv }
                                                      : std::pair{ ir::BinOp::srem, ir::BinOp::urem };
      return builder.binary(is_signed ? wanted.first : wanted.second, left, right, expr->span, answer);
    }
    return builder.binary(wrapped(sema::binary_ops().at(expr->op)), left, right, expr->span, derived);
  }


  bool Checker::operand_type_stands(ast::BinaryOp op, const ir::Type* ty)
  {
    if (sema::extrema().contains(op))
      // Whatever a comparison orders.
      return ir::is<ir::IntType>(ty) || ir::is<ir::FloatType>(ty) || ty == ir::char_type();
    if (ir::is<ir::IntType>(ty))
      return true;
    if (ir::is<ir::SetType>(ty))
      return sema::on_sets().contains(op);
    if (auto enumeration = ir::as<ir::EnumType>(ty); enumeration != nullptr)
      // Only where the definition said the values are meant to be combined.
      return enumeration->flag && sema::on_flags().contains(op);
    return ir::is<ir::FloatType>(ty) && sema::on_floats().contains(op);
  }


  bool Checker::answer_is_already_known(const ast::Binary* expr, const ir::Type* ty, ir::Value* left, ir::Value* right)
  {
    if (wrapping)
      // Inside a wrap there is no answer that does not fit.
      return false;
    if (is_float_const(left) && is_float_const(right))
      return float_answer_is_already_known(expr, static_cast<const ir::FloatConst*>(left),
                                           static_cast<const ir::FloatConst*>(right));
    if (! is_int_const(left) || ! is_int_const(right))
      return false;
    auto a = static_cast<const ir::IntConst*>(left)->value;
    auto b = static_cast<const ir::IntConst*>(right)->value;
    auto integer = ir::as<ir::IntType>(ty);
    if (expr->op == ast::BinaryOp::divide || expr->op == ast::BinaryOp::remainder) {
      // A division answers with a result, so one that cannot answer is still
      // well formed; it is reported anyway, as a warning.
      if (b == 0)
        diags.emit(D::LANG_TYPE_DIVISION_BY_ZERO, expr->span);
      else if (integer != nullptr && integer->is_signed && a == integer->low() && b == -1)
        // The one division that overflows, and the one pair that does it.
        diags.emit(D::LANG_TYPE_DIVISION_DOES_NOT_FIT, expr->span,
                   { { "value", ir::decimal(-integer->low()) }, { "type", ty->written() } });
      return false;
    }
    if (integer == nullptr)
      return false;
    __int128 answer;
    switch (expr->op) {
    case ast::BinaryOp::add:
      answer = a + b;
      break;
    case ast::BinaryOp::subtract:
      answer = a - b;
      break;
    case ast::BinaryOp::multiply:
      answer = a * b;
      break;
    default:
      return false;
    }
    if (integer->holds(answer))
      return false;
    diags.emit(D::LANG_TYPE_ANSWER_DOES_NOT_FIT, expr->span,
               { { "value", ir::decimal(answer) }, { "type", ty->written() } });
    return true;
  }


  bool Checker::float_answer_is_already_known(const ast::Binary* expr, const ir::FloatConst* left,
                                              const ir::FloatConst* right)
  {
    double answer;
    switch (expr->op) {
    case ast::BinaryOp::divide:
      if (right->value == 0.0) {
        // Well formed, and the answer is the error; saying so is a warning.
        diags.emit(D::LANG_TYPE_DIVISION_BY_ZERO, expr->span);
        return false;
      }
      answer = left->value / right->value;
      break;
    case ast::BinaryOp::add:
      answer = left->value + right->value;
      break;
    case ast::BinaryOp::subtract:
      answer = left->value - right->value;
      break;
    case ast::BinaryOp::multiply:
      answer = left->value * right->value;
      break;
    default:
      return false;
    }
    if (std::isfinite(answer))
      return false;
    diags.emit(D::LANG_TYPE_ANSWER_DOES_NOT_FIT, expr->span,
               { { "value", std::isnan(answer) ? "not a number" : "an infinity" }, { "type", left->ty->written() } });
    return true;
  }


  ir::Value* Checker::lower_unary(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected)
  {
    // What a program said this operator means before one operand of its own
    // type, asked first.
    if (! operators.empty() && ! operator_written(glyph(expr->op), 1).empty() && ! operator_stands_alone(expr)) {
      const ast::Expr* operands[] = { expr->operand };
      if (auto found = operator_call(builder, glyph(expr->op), operands, expr->span, expected); found != nullptr)
        return found;
    }
    if (expr->op == ast::UnaryOp::logic_not)
      return lower_not(builder, expr, expected);
    if (expr->op == ast::UnaryOp::length)
      return lower_length(builder, expr, expected);
    if (expr->op == ast::UnaryOp::shape)
      return lower_shape(builder, expr, expected);
    if (expr->op == ast::UnaryOp::max || expr->op == ast::UnaryOp::min)
      return lower_extremum(builder, expr, expected);
    if (sema::roundings().contains(expr->op))
      return lower_rounding(builder, expr, expected);
    if (expr->op == ast::UnaryOp::next || expr->op == ast::UnaryOp::prev) {
      auto moved = lower_expr(builder, expr->operand, nullptr);
      auto found = value_type_of(moved);
      if (found == ir::error_type()) [[unlikely]]
        return error_value(module);
      auto cursor = ir::as<ir::CursorType>(found);
      if (cursor == nullptr) [[unlikely]] {
        diags.emit(D::LANG_CURSOR_STEP_NOT_A_CURSOR, expr->operand->span,
                   { { "operator", glyph(expr->op) }, { "found", found->written() } });
        return error_value(module);
      }
      return lower_step(builder, expr, moved, cursor, expected);
    }
    if (sema::divides_unary().contains(expr->op))
      // The same operator with two on the left: "is it even".
      return lower_divides(builder, expr, nullptr, expr->operand, glyph(expr->op),
                           expr->op == ast::UnaryOp::not_divides, expected);
    ir::Value* operand;
    {
      Setting outer(operand_of, std::optional<std::string>(glyph(expr->op)));
      Setting was_listing(listing, true);
      operand = lower_expr(builder, expr->operand, scalar_of(expected));
    }
    const std::tuple<std::string, ir::Value*> given[] = { { "operand", operand } };
    if (auto walked = walk_operands(builder, expr, given, expected); walked != nullptr)
      return walked;
    // What the operator is defined on is never a run of values.
    auto ty = scalar_of(value_type_of(operand));
    if (ty == ir::error_type() || ty == nullptr) [[unlikely]]
      return error_value(module);
    auto enumeration = ir::as<ir::EnumType>(ty);
    bool flagged = enumeration != nullptr && enumeration->flag;
    if (! ir::is<ir::IntType>(ty) && ! flagged) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_NOT_INTEGER, expr->operand->span,
                 { { "operator", glyph(expr->op) }, { "found", ty->written() } });
      return error_value(module);
    }
    return builder.unary(sema::unary_ops().at(expr->op), operand, nullptr, expr->span);
  }


  ir::Value* Checker::lower_rounding(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected)
  {
    if (expr->op == ast::UnaryOp::rounded)
      an_effect(D::LANG_PURE_READS_THE_ROUNDING_MODE, expr->span);
    ir::Value* operand;
    {
      Setting outer(operand_of, std::optional<std::string>(glyph(expr->op)));
      Setting was_listing(listing, true);
      operand = lower_expr(builder, expr->operand, scalar_of(expected));
    }
    const std::tuple<std::string, ir::Value*> given[] = { { "operand", operand } };
    if (auto walked = walk_operands(builder, expr, given, expected); walked != nullptr)
      return walked;
    auto ty = scalar_of(value_type_of(operand));
    if (ty == ir::error_type() || ty == nullptr) [[unlikely]]
      return error_value(module);
    if (! ir::is<ir::FloatType>(ty)) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_NOT_FLOAT, expr->operand->span,
                 { { "operator", glyph(expr->op) }, { "found", ty->written() } });
      return error_value(module);
    }
    return builder.unary(sema::roundings().at(expr->op), operand, nullptr, expr->span);
  }


  ir::Value* Checker::lower_length(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected)
  {
    auto value = lower_expr(builder, expr->operand, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto found = counted(builder, value, ty, expr->span);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_LENGTH_HAS_NO_COUNT, expr->operand->span, { { "found", ty->written() } });
      return error_value(module);
    }
    if (! accepts(expected, sema::size_type())) [[unlikely]] {
      report_mismatch(expr->span, sema::size_type(), expected);
      return error_value(module);
    }
    // How many there are is a count, and a count is a quantity like any other.
    if (is_int_const(found))
      return builder.int_const(sema::size_type(), static_cast<const ir::IntConst*>(found)->value);
    return builder.cast(ir::CastKind::bitcast, found, sema::size_type(), expr->span);
  }


  ir::Value* Checker::counted(ir::Builder& builder, ir::Value* value, const ir::Type* ty, Span span)
  {
    if (auto tuple = ir::as<ir::TupleType>(ty); tuple != nullptr)
      return builder.int_const(ir::u64(), tuple->members.size());
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr) {
      if (auto along = array->shape[0]; along.has_value())
        return builder.int_const(ir::u64(), *along);
      // The outermost dimension, which such an array carries beside where its
      // elements are.
      return builder.extract(value, 1, ir::u64(), span);
    }
    if (ir::is<ir::ListType>(ty))
      return builder.extract(value, 1, ir::u64(), span);
    if (ty == ir::str_type()) {
      auto length = strings::length_function(module);
      auto bytes = builder.extract(value, 0, ir::parts_of(ir::str_type())[0], span);
      auto count = builder.extract(value, 1, ir::u64(), span);
      return builder.call(length, std::vector<ir::Value*>{ bytes, count }, ir::u64(), span);
    }
    if (ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty))
      // The count is a field of the table, kept by the two operations that put
      // things in.
      return tables::count_of(builder,
                              builder.cast(ir::CastKind::bitcast, value, tables::table_type(module), span));
    return nullptr;
  }


  ir::Value* Checker::lower_extremum(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected)
  {
    if (auto lifted = ast::as<ast::Lifted>(expr->operand); lifted != nullptr)
      return end_of_a_type(expr, lifted, expected);
    auto value = lower_expr(builder, expr->operand, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto found = extremum_of(builder, expr, value, ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_EXTREMUM_HAS_NONE, expr->operand->span,
                 { { "operator", glyph(expr->op) }, { "found", ty->written() } });
      return error_value(module);
    }
    auto answer = value_type_of(found);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    return found;
  }


  ir::Value* Checker::end_of_a_type(const ast::Unary* expr, const ast::Lifted* lifted, const ir::Type* expected)
  {
    auto ty = lifted_type(lifted);
    if (ty == nullptr) [[unlikely]] {
      diags.emit(D::LANG_EXTREMUM_HAS_NONE, lifted->span,
                 { { "operator", glyph(expr->op) }, { "found", "what was lifted" } });
      return error_value(module);
    }
    bool largest = expr->op == ast::UnaryOp::max;
    ir::Value* found;
    if (auto integer = ir::as<ir::IntType>(ty); integer != nullptr)
      found = used(module.constant<ir::IntConst>(ty, largest ? integer->high() : integer->low()));
    else if (ty == ir::char_type())
      found = used(module.char_const(largest ? max_code_point : 0));
    else if (auto floating = ir::as<ir::FloatType>(ty); floating != nullptr) {
      // The largest finite number of the format, and its negation.
      double widest = floating->bits == 32 ? 3.4028234663852886e38 : 1.7976931348623157e308;
      found = used(module.constant<ir::FloatConst>(ty, largest ? widest : -widest));
    } else [[unlikely]] {
      diags.emit(D::LANG_EXTREMUM_HAS_NONE, lifted->span,
                 { { "operator", glyph(expr->op) }, { "found", ty->written() } });
      return error_value(module);
    }
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return error_value(module);
    }
    return found;
  }


  ir::Value* Checker::extremum_of(ir::Builder& builder, const ast::Unary* expr, ir::Value* value, const ir::Type* ty)
  {
    if (auto tuple = ir::as<ir::TupleType>(ty); tuple != nullptr) {
      if (tuple->members.empty()
          || std::ranges::any_of(tuple->members, [&](auto m) { return m != tuple->members[0]; }))
        return nullptr;
      if (! ordered(tuple->members[0]))
        return nullptr;
      std::vector<ir::Value*> members;
      for (std::size_t at = 0; at < tuple->members.size(); ++at)
        members.push_back(member(builder, value, ty, at, expr->span));
      return folded(builder, expr, members, tuple->members[0]);
    }
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr) {
      // A type that says its shape is walked while compiling, whatever its
      // rank; one that does not leaves only the one-dimensional case.
      if (array->fixed())
        return extremum_of_array(builder, expr, value, array);
      if (array->rank() > 1) [[unlikely]] {
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                   { { "feature", "the largest or smallest of an array of more than one dimension whose type does "
                                  "not say its shape" } });
        return error_value(module);
      }
      return extremum_walked(builder, expr, value, ty);
    }
    if (ir::is<ir::ListType>(ty) || ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty) || ty == ir::str_type())
      return extremum_walked(builder, expr, value, ty);
    return nullptr;
  }


  bool Checker::ordered(const ir::Type* ty)
  {
    return ir::is<ir::IntType>(ty) || ir::is<ir::FloatType>(ty) || ty == ir::char_type();
  }


  ir::Value* Checker::folded(ir::Builder& builder, const ast::Unary* expr, std::span<ir::Value* const> values,
                             const ir::Type* element)
  {
    auto op = extremum_op(expr, element);
    auto found = values[0];
    for (auto one : values.subspan(1))
      found = builder.binary(op, found, one, expr->span);
    return found;
  }


  ir::Value* Checker::extremum_of_array(ir::Builder& builder, const ast::Unary* expr, ir::Value* value,
                                        const ir::ArrayType* ty)
  {
    if (! ordered(ty->element))
      return nullptr;
    auto along = ty->shape[0];
    contract_assert(along.has_value());
    auto [start, _] = shape_of(builder, value, ty, expr->span);
    std::uint64_t wide = 1;
    for (auto further : std::span(ty->shape).subspan(1))
      wide *= *further;
    auto held = [&](std::uint64_t row, std::uint64_t at) {
      return builder.load(element_place(builder, start, ty->element, builder.int_const(ir::u64(), row * wide + at),
                                        expr->span),
                          expr->span);
    };
    if (ty->rank() == 1) {
      std::vector<ir::Value*> each;
      for (std::uint64_t at = 0; at < *along; ++at)
        each.push_back(held(0, at));
      return folded(builder, expr, each, ty->element);
    }
    auto answer = ir::array_type(ty->element, { ty->shape.begin() + 1, ty->shape.end() });
    auto place = builder.frame(answer, expr->span);
    for (std::uint64_t at = 0; at < wide; ++at) {
      auto into = element_place(builder, place, ty->element, builder.int_const(ir::u64(), at), expr->span);
      std::vector<ir::Value*> rows;
      for (std::uint64_t row = 0; row < *along; ++row)
        rows.push_back(held(row, at));
      builder.store(into, folded(builder, expr, rows, ty->element), expr->span);
    }
    return builder.cast(ir::CastKind::bitcast, place, answer, expr->span);
  }


  ir::Value* Checker::extremum_walked(ir::Builder& builder, const ast::Unary* expr, ir::Value* value,
                                      const ir::Type* ty)
  {
    Iteration found;
    if (auto list = ir::as<ir::ListType>(ty); list != nullptr)
      found = over_a_list(builder, value, list, expr->span);
    else if (ty == ir::str_type())
      found = over_a_string(builder, value, expr->span);
    else if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr)
      found = over_an_array(builder, value, array, expr->span);
    else {
      contract_assert(ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty));
      found = over_a_table(builder, value, ty, expr->span);
    }
    auto element = found.element;
    if (ir::is<ir::DictType>(ty)) {
      // A turn of a dictionary gives a key and what it stands for; which of
      // the two is being looked at is the key.
      contract_assert(ir::is<ir::TupleType>(element));
      element = ir::as<ir::TupleType>(element)->members[0];
    }
    if (! ordered(element))
      return nullptr;
    // How many there are, except for a string, where all this asks is whether
    // there is one.
    auto count = ty == ir::str_type() ? builder.extract(value, 1, ir::u64(), expr->span)
                                      : counted(builder, value, ty, expr->span);
    contract_assert(count != nullptr);
    builder.check(builder.compare(ir::CmpPred::ne, count, builder.int_const(ir::u64(), 0), expr->span),
                  std::format("the {} of nothing", expr->op == ast::UnaryOp::max ? "largest" : "smallest"),
                  target::statuses::OUT_OF_RANGE, expr->span);
    auto op = extremum_op(expr, element);
    auto header = builder.new_block("finding");
    auto body = builder.new_block("comparing");
    auto done = builder.new_block("found");
    {
      std::vector<ir::Value*> args(found.start.begin(), found.start.end());
      args.push_back(offered(builder, found, found.start, ty, element, expr->span));
      args.push_back(builder.memory());
      builder.br(header, args, expr->span);
    }
    builder.position_at(header);
    std::vector<ir::Value*> state;
    for (auto one : found.start)
      state.push_back(header->add_param(value_type_of(one), "at"));
    ir::Value* best = header->add_param(element, "best");
    builder.set_memory(header->add_param(ir::mem_type(), "mem"));
    builder.condbr(found.more(builder, state), body, done, {}, {}, expr->span);
    builder.position_at(body);
    auto one = offered(builder, found, state, ty, element, expr->span);
    {
      auto args = found.step(builder, state);
      args.push_back(builder.binary(op, best, one, expr->span));
      args.push_back(builder.memory());
      builder.br(header, args, expr->span);
    }
    builder.position_at(done);
    return best;
  }


  ir::Value* Checker::offered(ir::Builder& builder, const Iteration& found, std::span<ir::Value* const> state,
                              const ir::Type* ty, const ir::Type* element, Span span)
  {
    auto one = found.take(builder, state);
    return ir::is<ir::DictType>(ty) ? builder.extract(one, 0, element, span) : one;
  }


  const ir::Type* Checker::extremum_type(const ir::Type* of)
  {
    if (auto tuple = ir::as<ir::TupleType>(of); tuple != nullptr)
      return tuple->members.empty() ? nullptr : tuple->members[0];
    if (auto array = ir::as<ir::ArrayType>(of); array != nullptr) {
      if (array->rank() == 1)
        return array->element;
      return array->fixed() ? ir::array_type(array->element, { array->shape.begin() + 1, array->shape.end() }) : nullptr;
    }
    if (auto list = ir::as<ir::ListType>(of); list != nullptr)
      return list->element;
    if (auto set = ir::as<ir::SetType>(of); set != nullptr)
      return set->element;
    if (auto dict = ir::as<ir::DictType>(of); dict != nullptr)
      return dict->key;
    return of == ir::str_type() ? ir::char_type() : nullptr;
  }


  ir::BinOp Checker::extremum_op(const ast::Unary* expr, const ir::Type* element)
  {
    bool is_signed = ir::is<ir::FloatType>(element)
                     || (ir::is<ir::IntType>(element) && ir::as<ir::IntType>(element)->is_signed);
    auto& ops = sema::extrema().at(expr->op == ast::UnaryOp::max ? ast::BinaryOp::max : ast::BinaryOp::min);
    return is_signed ? ops.first : ops.second;
  }


  ir::Value* Checker::lower_shape(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected)
  {
    auto value = lower_expr(builder, expr->operand, nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto array = ir::as<ir::ArrayType>(ty);
    if (array == nullptr) [[unlikely]] {
      diags.emit(D::LANG_SHAPE_HAS_NONE, expr->operand->span, { { "found", ty->written() } });
      return error_value(module);
    }
    std::vector<ir::Value*> along;
    for (std::size_t at = 0; at < array->rank(); ++at)
      along.push_back(array->shape[at].has_value() ? builder.int_const(ir::u64(), *array->shape[at])
                                                   : builder.extract(value, at + 1, ir::u64(), expr->span));
    auto found = array->rank() == 1
                   ? along[0]
                   : builder.make_tuple(along, ir::tuple_type(std::vector<const ir::Type*>(array->rank(), ir::u64())),
                                        expr->span);
    auto answer = value_type_of(found);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    return found;
  }


  ir::Value* Checker::lower_not(ir::Builder& builder, const ast::Unary* expr, const ir::Type* expected)
  {
    if (expected != nullptr && scalar_of(expected) != ir::bool_type()) [[unlikely]] {
      report_mismatch(expr->span, ir::bool_type(), expected);
      return error_value(module);
    }
    ir::Value* operand;
    {
      Setting outer(operand_of, std::optional<std::string>(glyph(expr->op)));
      Setting was_listing(listing, true);
      operand = boolean(builder, expr->operand, glyph(expr->op), true);
    }
    if (operand == nullptr) [[unlikely]]
      return error_value(module);
    const std::tuple<std::string, ir::Value*> given[] = { { "operand", operand } };
    if (auto walked = walk_operands(builder, expr, given, expected); walked != nullptr)
      return walked;
    if (! accepts(expected, ir::bool_type())) [[unlikely]] {
      report_mismatch(expr->span, ir::bool_type(), expected);
      return error_value(module);
    }
    return negate(builder, operand, expr->span);
  }


  const ir::Type* Checker::hint_of(const ast::Expr* expr)
  {
    // An operator a program defined for a type of its own answers what the
    // definition answers.  Before the match, so that a program that defined
    // no operator pays one test.
    if (! operators.empty() && (ast::is<ast::Unary>(expr) || ast::is<ast::Binary>(expr)))
      if (auto found = answers_written(expr); found != nullptr)
        return found;
    if (expr == nullptr)
      return nullptr;
    auto either = [&](const ast::Expr* one, const ast::Expr* other) {
      auto found = hint_of(one);
      return found != nullptr ? found : hint_of(other);
    };
    switch (expr->tag) {
    case ast::NodeKind::Ready:
      return value_type_of(static_cast<const ast::Ready*>(expr)->value);
    case ast::NodeKind::Fresh: {
      // It has no meaning but the definition's, so what it answers is what the
      // definition answers.
      auto fresh = static_cast<const ast::Fresh*>(expr);
      auto written = operator_written(fresh->glyph, std::int64_t(fresh->operands.size()));
      auto func = written.get<ir::Function>();
      if (func == nullptr)
        return nullptr;
      return func->fty->ret == ir::void_type() ? nullptr : func->fty->ret;
    }
    case ast::NodeKind::IntLit: {
      auto literal = static_cast<const ast::IntLit*>(expr);
      if (literal->type_name.has_value())
        return ir::builtin_type(*literal->type_name);
      return nullptr;
    }
    case ast::NodeKind::BoolLit:
      return ir::bool_type();
    case ast::NodeKind::NameRef:
      return type_of_name(static_cast<const ast::NameRef*>(expr)->name);
    case ast::NodeKind::Quote:
      return ir::syntax_type();
    case ast::NodeKind::Call: {
      auto call = static_cast<const ast::Call*>(expr);
      auto callee = ast::as<ast::NameRef>(call->callee);
      if (callee == nullptr)
        return nullptr;
      if (callee->name == typename_name)
        return ir::str_type();
      if (auto answer = sema::syntax_answers().get(std::string(callee->name)); answer != nullptr)
        return *answer;
      // What a call answers with, which its callee says.
      auto found = top->get(std::string(callee->name));
      Held named = found == nullptr ? Held{} : *found;
      if (auto defined = named.get<NamedType>(); defined != nullptr) {
        if (defined->takes_types())
          // Which types it is given is what lowering it settles.
          return nullptr;
        auto ty = resolved(defined);
        return ty == ir::error_type() ? nullptr : ty;
      }
      auto func = named.get<ir::Function>();
      return func != nullptr ? func->fty->ret : nullptr;
    }
    case ast::NodeKind::AddressOf: {
      if (operators.empty())
        return nullptr;
      // A reference to the place a name stands for, only where a program
      // defined an operator, the one thing that reads it.
      auto address = static_cast<const ast::AddressOf*>(expr);
      auto found = hint_of(address->operand);
      return found == nullptr || found == ir::error_type() ? nullptr : ir::ptr_type(found, address->mutable_);
    }
    case ast::NodeKind::Deref: {
      // What is at the place a reference names.
      auto found = ir::as<ir::PtrType>(hint_of(static_cast<const ast::Deref*>(expr)->operand));
      return found != nullptr ? found->pointee : nullptr;
    }
    case ast::NodeKind::Try: {
      auto found = hint_of(static_cast<const ast::Try*>(expr)->operand);
      auto result = ir::as<ir::ResultType>(found);
      return result != nullptr ? result->ok : found;
    }
    case ast::NodeKind::Raised: {
      // A power of a written-down number is a value of what was raised, except
      // where the number is negative: that is a division.
      auto raised = static_cast<const ast::Raised*>(expr);
      auto found = hint_of(raised->base);
      if (found == nullptr || raised->exponent.value >= 0)
        return found;
      return ir::result_type(found);
    }
    case ast::NodeKind::Binary: {
      auto binary = static_cast<const ast::Binary*>(expr);
      auto op = binary->op;
      if (sema::comparisons().contains(op) || sema::logic_ops().contains(op) || sema::short_circuit().contains(op))
        // What the operator answers with, not what it was given.
        return ir::bool_type();
      if (sema::divides().contains(op)) {
        // A truth value where the divisor is written down, and a result where
        // it is not.
        if (ast::is<ast::IntLit>(binary->left))
          return ir::bool_type();
        auto found = either(binary->left, binary->right);
        return found == nullptr ? nullptr : ir::result_type(ir::bool_type(), found);
      }
      if (op == ast::BinaryOp::or_else) {
        // The answer inside the result: the left side's type with the mark off.
        auto found = hint_of(binary->left);
        auto result = ir::as<ir::ResultType>(found);
        return result != nullptr ? result->ok : found;
      }
      if (op == ast::BinaryOp::power) {
        // The operator takes an exponent it cannot see.
        auto found = hint_of(binary->left);
        return found == nullptr ? nullptr : ir::result_type(found);
      }
      return either(binary->left, binary->right);
    }
    case ast::NodeKind::Unary: {
      auto unary = static_cast<const ast::Unary*>(expr);
      if (unary->op == ast::UnaryOp::logic_not)
        return ir::bool_type();
      if (sema::divides_unary().contains(unary->op))
        // Whether two divides it, which is a truth value about a number.
        return ir::bool_type();
      if (unary->op == ast::UnaryOp::length)
        // How many, which is a count.
        return sema::size_type();
      if (unary->op == ast::UnaryOp::max || unary->op == ast::UnaryOp::min)
        return extremum_type(hint_of(unary->operand));
      return hint_of(unary->operand);
    }
    default:
      return nullptr;
    }
  }


  const ir::Type* Checker::type_of_name(std::string_view name)
  {
    if (auto local = find_local(name); local != nullptr)
      return held_by(local);
    auto found = provided(name);
    auto var = found.get<ir::GlobalVar>();
    return var != nullptr ? value_type_of(var) : nullptr;
  }


  const ir::FloatType* Checker::float_literal_type(const ast::FloatLit* expr, const ir::Type* expected)
  {
    expected = aiming_at(expected);
    auto named = expr->type_name.has_value() ? ir::builtin_type(*expr->type_name) : nullptr;
    if (named != nullptr && expected != nullptr && named != ir::without_units(expected)) [[unlikely]] {
      // A suffix names a type and says nothing about a unit.
      report_mismatch(expr->span, named, expected);
      return nullptr;
    }
    auto found = named != nullptr ? named : expected;
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                 { { "feature", "a floating-point literal with neither a type suffix nor a context that gives it a "
                                "type" } });
      return nullptr;
    }
    if (found == ir::error_type()) [[unlikely]]
      return nullptr;
    auto floating = ir::as<ir::FloatType>(found);
    if (floating == nullptr) [[unlikely]] {
      report_mismatch(expr->span, named != nullptr ? named : ir::f64(), found);
      return nullptr;
    }
    // A number written down beside a product or a quotient counts nothing.
    return deriving ? floating->bare() : floating;
  }


  const ir::IntType* Checker::literal_type(const ast::IntLit* expr, const ir::Type* expected)
  {
    expected = aiming_at(expected);
    if (expected == ir::error_type()) [[unlikely]]
      // Something this literal would have taken its type from is wrong, and
      // that has been reported.
      return nullptr;
    auto named = expr->type_name.has_value() ? ir::builtin_type(*expr->type_name) : nullptr;
    if (named != nullptr && expected != nullptr && named != expected) [[unlikely]] {
      report_mismatch(expr->span, named, expected);
      return nullptr;
    }
    auto chosen = named != nullptr ? named : expected;
    if (chosen == nullptr) [[unlikely]] {
      diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, expr->span,
                 { { "feature", "an integer literal with neither a type suffix nor a context that gives it a type" } });
      return nullptr;
    }
    auto integer = ir::as<ir::IntType>(chosen);
    if (integer == nullptr) [[unlikely]] {
      report_mismatch(expr->span, ir::builtin_type("i32"), chosen);
      return nullptr;
    }
    // A number written down beside a product or a quotient counts nothing.
    return deriving ? integer->bare() : integer;
  }


  ir::Value* Checker::lower_bit_count(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto callee = ast::as<ast::NameRef>(expr->callee);
    std::string name(callee != nullptr ? callee->name : "");
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto integer = ir::as<ir::IntType>(found);
    if (integer == nullptr) [[unlikely]] {
      diags.emit(D::LANG_BITS_NOT_AN_INTEGER, expr->args[0]->span, { { "name", name }, { "found", found->written() } });
      return error_value(module);
    }
    if (! accepts(expected, ir::u8())) [[unlikely]] {
      report_mismatch(expr->span, ir::u8(), expected);
      return error_value(module);
    }
    auto op = name == ones_name ? ir::UnOp::count_ones
              : name == lead_name ? ir::UnOp::count_leading : ir::UnOp::count_trailing;
    if (is_int_const(given))
      // A number the program wrote down is counted here.
      return builder.int_const(ir::u8(), counted_bits(op, static_cast<const ir::IntConst*>(given)->value, integer));
    return builder.unary(op, given, ir::u8(), expr->span);
  }


  ir::Value* Checker::lower_mask(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", std::string(mask_name) }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto written = expr->args[0];
    // What it is handed is lowered with its answer wanted as one run of lanes,
    // and so is every walked operator inside it: no array is built for any of
    // them.
    auto outer = std::move(as_run);
    as_run = KeySet{ id_of(written) };
    ir::Value* given = nullptr;
    try {
      given = lower_expr(builder, written, nullptr);
    } catch (...) {
      as_run = std::move(outer);
      throw;
    }
    as_run = std::move(outer);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    bool held = runs.contains(id_of(given));
    runs.erase(id_of(given));
    std::uint64_t lanes = 0;
    if (held) {
      auto run = ir::as<ir::VecType>(found);
      if (run->element != ir::bool_type()) [[unlikely]] {
        // An operator walked at once that answers something other than truth
        // values.
        auto array = ir::array_type(run->element, std::vector<std::optional<std::uint64_t>>{ run->lanes });
        diags.emit(D::LANG_MASK_NOT_TRUTH_VALUES, written->span,
                   { { "name", std::string(mask_name) }, { "found", array->written() } });
        return error_value(module);
      }
      lanes = run->lanes;
    } else {
      auto array = ir::as<ir::ArrayType>(found);
      if (array == nullptr || array->element != ir::bool_type()) [[unlikely]] {
        diags.emit(D::LANG_MASK_NOT_TRUTH_VALUES, written->span,
                   { { "name", std::string(mask_name) }, { "found", found->written() } });
        return error_value(module);
      }
      if (array->rank() != 1 || ! array->fixed()) [[unlikely]] {
        diags.emit(D::LANG_MASK_SHAPE_NOT_ONE_RUN, written->span,
                   { { "name", std::string(mask_name) }, { "found", found->written() } });
        return error_value(module);
      }
      lanes = *array->shape[0];
    }
    if (lanes > 64) [[unlikely]] {
      diags.emit(D::LANG_MASK_TOO_LONG, written->span, { { "name", std::string(mask_name) }, { "found", lanes } });
      return error_value(module);
    }
    // The narrowest of the four machine widths with a bit for every one.
    const ir::IntType* answer = lanes <= 8 ? ir::u8() : lanes <= 16 ? ir::as<ir::IntType>(ir::builtin_type("u16")) : lanes <= 32 ? ir::u32() : ir::u64();
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    if (held)
      return builder.unary(ir::UnOp::mask, given, answer, expr->span);
    if (lanes == 0)
      return builder.int_const(answer, 0);
    auto [start, lengths] = shape_of(builder, given, ir::as<ir::ArrayType>(found), expr->span);
    if (lanes == 1) {
      // One truth value is one bit already, and a run of one is not a run.
      auto one = builder.load(builder.cast(ir::CastKind::bitcast, start, ir::ptr_type(ir::bool_type(), true),
                                           expr->span),
                              expr->span);
      return builder.cast(ir::CastKind::zext, one, answer, expr->span);
    }
    auto run = ir::vec_type(ir::bool_type(), unsigned(lanes));
    auto read = builder.load(builder.cast(ir::CastKind::bitcast, start, ir::ptr_type(run, true), expr->span),
                             expr->span);
    return builder.unary(ir::UnOp::mask, read, answer, expr->span);
  }


  ir::Value* Checker::lower_code_point(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto callee = ast::as<ast::NameRef>(expr->callee);
    std::string name(callee != nullptr ? callee->name : "");
    bool to_a_number = name == ord_name;
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CHAR_TAKES_ONE, expr->span, { { "name", name }, { "found", expr->args.size() } });
      return error_value(module);
    }
    const ir::Type* wanted = to_a_number ? static_cast<const ir::Type*>(ir::char_type()) : ir::u32();
    auto given = lower_expr(builder, expr->args[0], wanted);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    if (found != wanted) [[unlikely]] {
      report_mismatch(expr->args[0]->span, wanted, found);
      return error_value(module);
    }
    const ir::Type* answer = to_a_number ? static_cast<const ir::Type*>(ir::u32()) : ir::char_type();
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    if (given->vkind == ir::ValueKind::char_const && to_a_number)
      return builder.int_const(ir::u32(), static_cast<const ir::CharConst*>(given)->value);
    if (! to_a_number) {
      // Every number that is not written down has to be asked; one written
      // down is asked here instead.
      if (is_int_const(given))
        return code_point(builder, std::int64_t(static_cast<const ir::IntConst*>(given)->value), expr->args[0]->span);
      builder.check(is_code_point(builder, given, expr->span), "a number that is not a code point",
                    target::statuses::NOT_A_CODE_POINT, expr->span);
    }
    return builder.cast(ir::CastKind::bitcast, given, answer, expr->span);
  }


  ir::Value* Checker::lower_list(ir::Builder& builder, const ast::ListLit* expr, const ir::Type* expected)
  {
    auto wanted = aiming_at(expected);
    auto list = ir::as<ir::ListType>(wanted);
    const ir::Type* holds = list != nullptr ? list->element : nullptr;
    if (holds == nullptr)
      // What one of them says is what they all are, read off the writing.
      holds = said_by(expr->elements);
    std::vector<ir::Value*> values;
    for (auto written : expr->elements) {
      auto one = lower_expr(builder, written, taken_from(written, holds));
      auto found = value_type_of(one);
      if (found == ir::error_type()) [[unlikely]]
        return error_value(module);
      if (holds == nullptr)
        holds = found;
      else if (found != holds) [[unlikely]] {
        diags.emit(D::LANG_LIST_ELEMENTS_DIFFER, written->span,
                   { { "found", found->written() }, { "wanted", holds->written() } });
        return error_value(module);
      }
      values.push_back(one);
    }
    if (holds == nullptr) [[unlikely]] {
      diags.emit(D::LANG_LIST_HOLDS_NOTHING, expr->span);
      return error_value(module);
    }
    auto answer = ir::list_type(holds);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    // The list is made in the arena `in` named, or `⎕heap`, and what its
    // elements point into is there too.
    auto owner = owner_of(out_of_arenas);
    KeySet made;
    if (owner.has_value() && owned::points(holds)) {
      std::vector<ir::Value*> stored_values;
      for (std::size_t at = 0; at < values.size(); ++at) {
        auto written = expr->elements[at];
        auto [kept, added, spare] = stored(builder, written, values[at], holds, *owner,
                                           std::int64_t(scopes.size()), true, written->span);
        if (value_type_of(kept) == ir::error_type()) [[unlikely]]
          return error_value(module);
        made.insert(added.begin(), added.end());
        if (spare != nullptr)
          owned::give_back(builder, spare, holds,
                           freshly_heap(written) ? std::optional<std::string_view>("heap") : std::nullopt);
        stored_values.push_back(kept);
      }
      values = std::move(stored_values);
    }
    auto elements = room_for(builder, holds, std::int64_t(values.size()), expr->span);
    for (std::size_t at = 0; at < values.size(); ++at)
      builder.store(element_place(builder, elements, holds, builder.int_const(ir::u64(), at), expr->span), values[at],
                    expr->span);
    KeySet from = out_of_arenas;
    from.insert(made.begin(), made.end());
    made_from[id_of(expr)] = { expr, from };
    auto count = builder.int_const(ir::u64(), values.size());
    auto arena = out_of(builder, expr->span);
    return builder.make_tuple(std::vector<ir::Value*>{ elements, count, arena }, answer, expr->span);
  }


  ir::Value* Checker::room_for(ir::Builder& builder, const ir::Type* element, std::variant<ir::Value*, std::int64_t> count,
                               Span span)
  {
    auto pointer = ir::ptr_type(element, true);
    auto number = std::get_if<std::int64_t>(&count);
    if (number != nullptr && *number == 0)
      return builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), pointer, span);
    auto room_function = lists::room_function(module);
    auto arena = out_of(builder, span);
    auto many = number != nullptr ? builder.int_const(ir::u64(), *number) : std::get<ir::Value*>(count);
    auto stride = builder.int_const(ir::u64(), ir::stride_of(element, sema::layout()));
    auto room = builder.call(room_function, std::vector<ir::Value*>{ arena, many, stride },
                             ir::ptr_type(ir::u8(), true), span);
    return builder.cast(ir::CastKind::bitcast, room, pointer, span);
  }


  ir::Value* Checker::written_text(ir::Builder& builder, std::string_view text, Span span)
  {
    auto found = text_variable(text);
    auto bytes = builder.cast(ir::CastKind::bitcast, builder.address(found, span), ir::ptr_type(ir::u8(), true), span);
    auto count = builder.int_const(ir::u64(), text.size());
    auto none = tables::no_allocator(builder);
    return builder.make_tuple(std::vector<ir::Value*>{ bytes, count, none }, ir::str_type(), span);
  }


  ir::GlobalVar* Checker::text_variable(std::string_view text)
  {
    std::string key(text);
    if (auto found = module.text_of.find(key); found != module.text_of.end())
      return found->second;
    auto held = ir::array_type(ir::u8(), { text.size() });
    std::vector<const ir::Value*> bytes;
    bytes.reserve(text.size());
    for (unsigned char byte : text)
      bytes.push_back(module.int_const(ir::u8(), byte));
    auto var = std::make_unique<ir::GlobalVar>(std::format("{}{}", text_symbol, module.texts.size()), held,
                                               ir::ptr_type(held, true));
    var->initializer = module.array_const(held, std::move(bytes));
    var->linkage = ir::Linkage::internal;
    auto found = module.add_global(std::move(var));
    module.texts.push_back(found);
    module.text_of.emplace(std::move(key), found);
    return found;
  }


  ir::Value* Checker::code_point(ir::Builder& builder, std::int64_t value, Span span)
  {
    if (value < 0 || value > std::int64_t(max_code_point)) [[unlikely]] {
      diags.emit(D::LANG_CHAR_OUTSIDE_UNICODE, span,
                 { { "value", std::to_string(value) }, { "last", std::format("{:X}", max_code_point) } });
      return used(builder.module().char_const(0));
    }
    if (is_surrogate(value)) [[unlikely]] {
      diags.emit(D::LANG_CHAR_IS_A_SURROGATE, span, { { "value", std::format("{:04X}", value) } });
      return used(builder.module().char_const(0));
    }
    return used(builder.module().char_const(std::uint32_t(value)));
  }


  ir::Value* Checker::is_code_point(ir::Builder& builder, ir::Value* given, Span span)
  {
    // Two questions: no further than the last code point, and not a
    // surrogate -- one run of numbers, so one subtraction that wraps and one
    // comparison, what lies below the first of them coming out very large.
    auto below_the_last = builder.compare(ir::CmpPred::ule, given, builder.int_const(ir::u32(), max_code_point), span);
    auto moved = builder.binary(ir::BinOp::wrap_sub, given, builder.int_const(ir::u32(), first_surrogate), span);
    auto not_a_surrogate = builder.compare(ir::CmpPred::uge, moved, builder.int_const(ir::u32(), surrogates), span);
    return builder.binary(ir::BinOp::and_, below_the_last, not_a_surrogate, span);
  }


  ir::Value* Checker::lower_wide_mul(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::wide_mul_name }, { "expected", 2 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    // The two are operands of one multiplication, so they take their type from
    // each other as an operator's do, and a mismatch is reported as one
    // between an operator's.
    auto context = hint_of(expr->args[0]);
    if (context == nullptr)
      context = hint_of(expr->args[1]);
    ir::Value* left = nullptr;
    ir::Value* right = nullptr;
    const ir::IntType* found = nullptr;
    const ir::Type* other = nullptr;
    {
      Setting outer(operand_of, std::optional<std::string>(names::wide_mul_name));
      left = lower_expr(builder, expr->args[0], context);
      auto found_ty = value_type_of(left);
      if (found_ty == ir::error_type())
        return error_value(module);
      found = ir::as<ir::IntType>(found_ty);
      if (found == nullptr || found->bits != 64) [[unlikely]] {
        diags.emit(D::LANG_WIDE_MUL_NOT_A_WORD, expr->args[0]->span, { { "found", found_ty->written() } });
        return error_value(module);
      }
      auto told = diags.error_count;
      right = lower_expr(builder, expr->args[1], found);
      other = value_type_of(right);
      if (other != found && other != ir::error_type() && diags.error_count == told) [[unlikely]]
        // Not every expression says so itself when it is not of the type it
        // was asked for, and one that did has been heard.
        report_mismatch(expr->args[1]->span, other, found);
    }
    if (other != found) [[unlikely]]
      return error_value(module);
    auto upper = found->bare();
    auto answer = ir::tuple_type({ upper, ir::u64() });
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    if (is_int_const(left) && is_int_const(right)) {
      // Two numbers written down are multiplied here, where 128 bits are all
      // the room the product needs.
      auto a = static_cast<const ir::IntConst*>(left)->value;
      auto b = static_cast<const ir::IntConst*>(right)->value;
      auto whole = upper->is_signed ? (unsigned __int128)(a * b)
                                    : (unsigned __int128)a * (unsigned __int128)b;
      auto high_bits = std::uint64_t(whole >> 64);
      __int128 high = upper->is_signed ? __int128(std::int64_t(high_bits)) : __int128(high_bits);
      std::vector<ir::Value*> halves{ builder.int_const(upper, high), builder.int_const(ir::u64(), std::uint64_t(whole)) };
      return builder.make_tuple(halves, answer, expr->span);
    }
    return builder.binary(ir::BinOp::wide_mul, left, right, expr->span, answer);
  }


  const ir::Type* Checker::numeric_target(const ast::Expr* written, std::string_view name)
  {
    auto lifted = ast::as<ast::Lifted>(written);
    auto wanted = lifted != nullptr ? lifted_type(lifted) : nullptr;
    if (wanted == nullptr) [[unlikely]]
      diags.emit(D::LANG_NUMBER_NOT_A_TYPE, written->span, { { "name", name } });
    return wanted;
  }


  ir::Value* Checker::lower_to_float(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::float_name }, { "expected", 2 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto wanted = numeric_target(expr->args[1], names::float_name);
    if (wanted == nullptr) [[unlikely]]
      return error_value(module);
    if (! ir::is<ir::FloatType>(wanted)) [[unlikely]] {
      diags.emit(D::LANG_NUMBER_NOT_A_FLOAT, expr->args[1]->span,
                 { { "name", names::float_name }, { "found", wanted->written() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return error_value(module);
    auto found = ir::as<ir::IntType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NUMBER_NOT_AN_INTEGER, expr->args[0]->span,
                 { { "name", names::float_name }, { "found", found_ty->written() } });
      return error_value(module);
    }
    auto into = ir::as<ir::FloatType>(carrying(wanted, found->unit));
    if (! accepts(expected, into)) [[unlikely]] {
      report_mismatch(expr->span, into, expected);
      return error_value(module);
    }
    if (is_int_const(given))
      return builder.float_const(into, nearest_float(static_cast<const ir::IntConst*>(given)->value, into->bits));
    return builder.cast(ir::CastKind::itof, given, into, expr->span);
  }


  ir::Value* Checker::lower_trunc(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::trunc_name }, { "expected", 2 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto wanted = numeric_target(expr->args[1], names::trunc_name);
    if (wanted == nullptr) [[unlikely]]
      return error_value(module);
    if (! ir::is<ir::IntType>(wanted)) [[unlikely]] {
      diags.emit(D::LANG_NUMBER_NOT_AN_INTEGER, expr->args[1]->span,
                 { { "name", names::trunc_name }, { "found", wanted->written() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return error_value(module);
    auto found = ir::as<ir::FloatType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NUMBER_NOT_A_FLOAT, expr->args[0]->span,
                 { { "name", names::trunc_name }, { "found", found_ty->written() } });
      return error_value(module);
    }
    auto into = ir::as<ir::IntType>(carrying(wanted, found->unit));
    auto answer = ir::result_type(into, ir::narrowing_type());
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    auto span = expr->span;
    if (is_float_const(given)) {
      // A number written down is converted here, and what it comes to -- the
      // number, or which way it did not fit -- is the result.
      auto value = static_cast<const ir::FloatConst*>(given)->value;
      auto why = truncated(value, into);
      auto number = builder.int_const(into, why ? __int128(0) : __int128(std::trunc(value)));
      auto reason_value = const_cast<ir::EnumConst*>(module.enum_const(ir::narrowing_type(), why.value_or(0)));
      return builder.wrap(number, builder.bool_const(why.has_value()), answer, span, reason_value);
    }
    auto no = builder.bool_const(false);
    auto top = builder.float_const(found, std::ldexp(1.0, int(into->is_signed ? into->bits - 1 : into->bits)));
    auto over = builder.compare(ir::CmpPred::sge, given, top, span);
    auto under = no;
    auto wrong_sign = no;
    if (into->is_signed) {
      // Below the bottom where it is no further up than the bottom less one,
      // the truncation of anything above that being in range.
      auto edge = into->low() - 1;
      auto nearest = nearest_float(edge, found->bits);
      if (__int128(nearest) == edge)
        under = builder.compare(ir::CmpPred::sle, given, builder.float_const(found, double(edge)), span);
      else
        under = builder.compare(ir::CmpPred::slt, given, builder.float_const(found, double(into->low())), span);
    } else
      wrong_sign = builder.compare(ir::CmpPred::sle, given, builder.float_const(found, -1.0), span);
    // A not-a-number is the one value that is not equal to itself, and the one
    // every ordered comparison above is false for.
    auto absent = builder.compare(ir::CmpPred::ne, given, given, span);
    auto failed = either(builder, either(builder, either(builder, over, under, span), wrong_sign, span), absent, span);
    auto why_not = reason(builder, under, wrong_sign, span, absent);
    auto value = builder.cast(ir::CastKind::ftoi, given, into, span);
    return builder.wrap(value, failed, answer, span, why_not);
  }


  ir::Value* Checker::lower_bits(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::bits_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found_ty = value_type_of(given);
    if (found_ty == ir::error_type())
      return error_value(module);
    auto found = ir::as<ir::FloatType>(found_ty);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NUMBER_NOT_A_FLOAT, expr->args[0]->span,
                 { { "name", names::bits_name }, { "found", found_ty->written() } });
      return error_value(module);
    }
    const ir::Type* into = found->bits == 64 ? ir::u64() : ir::u32();
    if (! accepts(expected, into)) [[unlikely]] {
      report_mismatch(expr->span, into, expected);
      return error_value(module);
    }
    if (is_float_const(given))
      return builder.int_const(into, float_bits(static_cast<const ir::FloatConst*>(given)->value, found->bits));
    return builder.cast(ir::CastKind::fbits, given, into, expr->span);
  }


  ir::Value* Checker::lower_from_bits(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::from_bits_name }, { "expected", 2 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto wanted_ty = numeric_target(expr->args[1], names::from_bits_name);
    if (wanted_ty == nullptr) [[unlikely]]
      return error_value(module);
    auto wanted = ir::as<ir::FloatType>(wanted_ty);
    if (wanted == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NUMBER_NOT_A_FLOAT, expr->args[1]->span,
                 { { "name", names::from_bits_name }, { "found", wanted_ty->written() } });
      return error_value(module);
    }
    const ir::Type* holder = wanted->bits == 64 ? ir::u64() : ir::u32();
    auto told = diags.error_count;
    auto given = lower_expr(builder, expr->args[0], holder);
    auto found = value_type_of(given);
    if (found == ir::error_type())
      return error_value(module);
    if (found != holder) [[unlikely]] {
      if (diags.error_count == told)
        report_mismatch(expr->args[0]->span, found, holder);
      return error_value(module);
    }
    if (! accepts(expected, wanted)) [[unlikely]] {
      report_mismatch(expr->span, wanted, expected);
      return error_value(module);
    }
    if (is_int_const(given)) {
      auto number = from_bits(std::uint64_t(static_cast<const ir::IntConst*>(given)->value), wanted->bits);
      if (! std::isnan(number))
        // A number written down is that number, unless it is a not-a-number:
        // what one of those carries is not something a constant keeps.
        return builder.float_const(wanted, number);
    }
    return builder.cast(ir::CastKind::fbits, given, wanted, expr->span);
  }


  ir::Value* Checker::lower_wrap(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_WRAP_TAKES_ONE_EXPRESSION, expr->span, { { "found", expr->args.size() } });
      return error_value(module);
    }
    Setting outer(wrapping, true);
    return lower_expr(builder, expr->args[0], expected);
  }


  ir::BinOp Checker::wrapped(ir::BinOp op)
  {
    if (! wrapping)
      return op;
    auto found = sema::wrapped().find(op);
    return found != sema::wrapped().end() ? found->second : op;
  }


  ir::Value* Checker::lower_call(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    // Every name the compiler provides begins with the quad and no name a
    // program may write does, so one character says whether any can match.
    if (auto called = ast::as<ast::NameRef>(expr->callee);
        called != nullptr && called->name.starts_with(builtin_glyph)) {
      auto given = called->name;
      if (given == wrap_name)
        return lower_wrap(builder, expr, expected);
      if (given == ord_name || given == chr_name)
        return lower_code_point(builder, expr, expected);
      if (given == ones_name || given == lead_name || given == names::trail_name)
        return lower_bit_count(builder, expr, expected);
      if (given == mask_name)
        return lower_mask(builder, expr, expected);
      if (given == names::wide_mul_name)
        return lower_wide_mul(builder, expr, expected);
      if (given == names::float_name)
        return lower_to_float(builder, expr, expected);
      if (given == names::trunc_name)
        return lower_trunc(builder, expr, expected);
      if (given == names::bits_name)
        return lower_bits(builder, expr, expected);
      if (given == names::from_bits_name)
        return lower_from_bits(builder, expr, expected);
      if (given == drop_name || given == unit_name)
        return lower_unit_call(builder, expr, expected);
      if (given == entry_name)
        return lower_at_entry(builder, expr, expected);
      if (given == narrow_name)
        return lower_narrow(builder, expr, expected);
      if (given == widen_name)
        return lower_widen(builder, expr, expected);
      if (given == syscall_name)
        return lower_syscall(builder, expr, expected);
      if (given == acquire_name || given == release_name)
        return lower_ordered(builder, expr, expected);
      if (given == at_name || given == span_name)
        return lower_place_at(builder, expr, expected);
      if (given == address_name)
        return lower_address_of(builder, expr, expected);
      if (given == iter_name)
        return lower_iter(builder, expr, expected);
      if (given == reserve_name)
        return lower_reserve(builder, expr, expected);
      if (given == start_name)
        return lower_start(builder, expr, expected);
      if (given == resume_name)
        return lower_resume(builder, expr, expected);
      if (given == bytes_name)
        return lower_bytes(builder, expr, expected);
      if (given == str_of_name)
        return lower_str_of(builder, expr, expected);
      if (given == text_name)
        return lower_text(builder, expr, expected);
      if (given == one_run_name)
        return lower_one_run(builder, expr, expected);
      if (given == empty_name)
        return lower_empty(builder, expr, expected);
      if (given == fields_name)
        return lower_fields(builder, expr, expected);
      if (given == typename_name)
        return lower_typename(builder, expr, expected);
      if (given == is_record_name) [[unlikely]] {
        // Reaching here means it stood where a value was wanted.
        diags.emit(D::LANG_ISRECORD_OUTSIDE_COMPTIME, expr->span);
        return error_value(module);
      }
      if (given == names::kind_of_name) [[unlikely]] {
        diags.emit(D::LANG_KIND_ASKED_OUTSIDE_COMPTIME, expr->span);
        return error_value(module);
      }
      if (given == names::alternatives_name)
        return lower_alternatives(builder, expr, expected);
      if (sema::asks_about_a_piece().contains(std::string(given)))
        return lower_asked(builder, expr, given, expected);
      if (given == typeof_name) [[unlikely]] {
        // Reaching here means it stood somewhere a value was wanted.
        diags.emit(D::LANG_TYPEOF_OUTSIDE_COMPTIME, expr->span);
        return error_value(module);
      }
    }
    if (auto template_ = template_named(expr->callee); template_ != nullptr)
      return lower_generic_value(builder, expr, template_, expected);
    if (auto name = ast::as<ast::NameRef>(expr->callee); name != nullptr) {
      auto named = held_in(*top, name->name).get<NamedType>();
      if (named != nullptr && ir::is<ir::ProductType>(resolved(named)))
        return lower_record(builder, expr, resolved(named), expected);
      if (named != nullptr && ir::is<ir::SumType>(resolved(named)))
        return lower_sum(builder, expr, resolved(named), expected);
    }
    if (auto member = ast::as<ast::Member>(expr->callee); member != nullptr) {
      // A record another module defined, written the way one defined here is.
      auto through_type = type_exported(member);
      if (ir::is<ir::ProductType>(through_type))
        return lower_record(builder, expr, through_type, expected);
      // And a sum, which is made the same way wherever it was defined.
      if (ir::is<ir::SumType>(through_type))
        return lower_sum(builder, expr, through_type, expected);
      auto written = through_a_value(builder, expr);
      if (written.is_refused()) [[unlikely]]
        return error_value(module);
      if (auto call = written.get<ast::Call>(); call != nullptr)
        return lower_call(builder, call, expected);
    }
    if (auto held = callee_value(expr->callee); held != nullptr)
      return lower_indirect(builder, expr, held, expected);
    // A function whose types this call settles, which is not a function yet.
    if (auto generator = generator_named(expr->callee); generator != nullptr)
      return lower_generator_call(builder, expr, generator, expected);
    if (auto template_ = generic_named(expr->callee); template_ != nullptr)
      return lower_generic(builder, expr, template_, expected);
    auto func = callee(expr->callee);
    if (func == nullptr) [[unlikely]]
      return error_value(module);
    return through(builder, expr, func, expected);
  }


  Held Checker::through_a_value(ir::Builder& builder, const ast::Call* expr)
  {
    auto callee = ast::as<ast::Member>(expr->callee);
    contract_assert(callee != nullptr);
    auto base = callee->base;
    if (module_at(base) != nullptr || ! member_named(callee).empty())
      return {};
    if (auto inner = ast::as<ast::Member>(base); inner != nullptr && type_exported(inner) != nullptr)
      return {};
    if (auto name = ast::as<ast::NameRef>(base);
        name != nullptr && find_local(name->name) == nullptr && provided(name->name).get<ir::GlobalVar>() == nullptr)
      // A name that is no value is the ordinary path's to say something about.
      return {};
    bool place = (ast::is<ast::NameRef>(base) || ast::is<ast::Member>(base) || ast::is<ast::Deref>(base)
                  || ast::is<ast::Element>(base))
                 && record_type_of(base) != nullptr;
    ir::Value* lowered = nullptr;
    const ir::Type* ty;
    if (place)
      ty = record_type_of(base);
    else {
      lowered = lower_expr(builder, base, nullptr);
      ty = value_type_of(lowered);
      if (ty == ir::error_type()) [[unlikely]]
        return Refused{};
    }
    auto pointer = ir::as<ir::PtrType>(ty);
    auto named = pointer != nullptr ? pointer->pointee : ty;
    std::string owner;
    if (auto record = ir::as<ir::ProductType>(named); record != nullptr)
      owner = record->name;
    else if (auto sum = ir::as<ir::SumType>(named); sum != nullptr)
      owner = sum->name;
    else if (auto enumeration = ir::as<ir::EnumType>(named); enumeration != nullptr)
      owner = enumeration->name;
    else if (auto frame = ir::as<ir::CoroutineFrame>(named); frame != nullptr)
      owner = frame->name;
    else if (auto opaque = ir::as<ir::OpaqueType>(named); opaque != nullptr)
      owner = opaque->name;
    if (owner.empty()
        || ! (ir::is<ir::ProductType>(named) || ir::is<ir::SumType>(named) || ir::is<ir::EnumType>(named)))
      return lowered == nullptr ? Held{} : no_such_member(callee, ty);
    auto path = path_to_member(named, callee->name, callee);
    if (path == nullptr) {
      if (lowered == nullptr && ir::is<ir::ProductType>(named)
          && field_index(ir::as<ir::ProductType>(named), callee->name).has_value())
        return {};
      return no_such_member(callee, named);
    }
    if (auto record = ir::as<ir::ProductType>(named); record != nullptr) {
      auto at = field_index(record, callee->name);
      if (at.has_value() && ir::is<ir::FuncType>(record->fields[*at].second)) [[unlikely]] {
        diags.emit(D::LANG_METHOD_OR_FIELD, callee->name_span,
                   { { "type", owner }, { "name", callee->name }, { "value", place ? path_text(base) : "\N{HORIZONTAL ELLIPSIS}" } });
        return Refused{};
      }
    }
    auto takes = first_taken(member_named(path));
    const ast::Expr* receiver = base;
    if (lowered != nullptr) {
      auto ready = syntax.make<ast::Ready>(base->span);
      ready->value = lowered;
      receiver = ready;
    }
    if (pointer != nullptr) {
      if (! takes.has_value()) [[unlikely]] {
        diags.emit(D::LANG_METHOD_RECEIVER_IS_A_REFERENCE, base->span, { { "type", owner }, { "name", callee->name } });
        return Refused{};
      }
    } else if (takes.has_value()) {
      if (lowered != nullptr) [[unlikely]] {
        diags.emit(D::LANG_REF_NOT_A_PLACE, base->span);
        return Refused{};
      }
      auto address = syntax.make<ast::AddressOf>(base->span);
      address->operand = base;
      address->mutable_ = *takes;
      receiver = address;
    }
    auto call = syntax.copy(*expr);
    call->callee = path;
    std::vector<const ast::Expr*> args{ receiver };
    args.insert(args.end(), expr->args.begin(), expr->args.end());
    call->args = syntax.list(args);
    return static_cast<const ast::Node*>(call);
  }


  Held Checker::no_such_member(const ast::Member* callee, const ir::Type* ty)
  {
    diags.emit(D::LANG_MEMBER_UNKNOWN, callee->name_span, { { "name", callee->name }, { "held", ty->written() } });
    return Refused{};
  }


  const ast::Expr* Checker::path_to_member(const ir::Type* ty, std::string_view name, const ast::Member* at)
  {
    // A record or a sum of a type with type parameters is reached through the
    // definition it was made from, whose name its functions are written under.
    const void* family = nullptr;
    std::string owner;
    if (auto record = ir::as<ir::ProductType>(ty); record != nullptr) {
      family = record->family;
      owner = record->name;
    } else if (auto sum = ir::as<ir::SumType>(ty); sum != nullptr) {
      family = sum->family;
      owner = sum->name;
    } else if (auto enumeration = ir::as<ir::EnumType>(ty); enumeration != nullptr)
      owner = enumeration->name;
    if (family != nullptr)
      owner = static_cast<const NamedType*>(family)->name;
    auto same = [&](const Held& held) {
      if (family != nullptr)
        return held.get<NamedType>() == family;
      auto defined = held.get<NamedType>();
      return defined != nullptr && ! defined->takes_types() && resolved(defined) == ty;
    };
    auto span = at->span;
    auto where = at->name_span;
    auto kept_owner = syntax.text(owner);
    auto kept_name = syntax.text(name);
    auto name_ref = [&](std::string_view n) {
      auto ref = syntax.make<ast::NameRef>(span);
      ref->name = syntax.text(n);
      return ref;
    };
    auto member_of = [&](const ast::Expr* base, ast::Text n) {
      auto made = syntax.make<ast::Member>(span);
      made->base = base;
      made->name = n;
      made->name_span = where;
      return made;
    };
    if (members.contains({ owner, std::string(name) }) && same(held_in(*top, owner)))
      return member_of(name_ref(kept_owner), kept_name);
    std::deque<std::pair<const ast::Expr*, LoadedModule*>> queue;
    for (auto& [key, held] : *top)
      if (auto loaded = held.get<LoadedModule>(); loaded != nullptr)
        queue.emplace_back(name_ref(key), loaded);
    std::unordered_set<const LoadedModule*> seen;
    while (! queue.empty()) {
      auto [path, held] = queue.front();
      queue.pop_front();
      if (! seen.insert(held).second)
        continue;
      if (held->members.contains({ owner, std::string(name) }) && same(held_in(held->exports, owner)))
        return member_of(member_of(path, kept_owner), kept_name);
      for (auto& [key, inner] : held->exports)
        if (auto loaded = inner.get<LoadedModule>(); loaded != nullptr)
          queue.emplace_back(member_of(path, syntax.text(key)), loaded);
    }
    return nullptr;
  }


  std::optional<bool> Checker::first_taken(Held found)
  {
    if (auto func = found.get<ir::Function>(); func != nullptr) {
      auto first = func->fty->params.empty() ? nullptr : ir::as<ir::PtrType>(func->fty->params[0]);
      return first != nullptr ? std::optional<bool>(first->is_mutable) : std::nullopt;
    }
    const ast::FuncDef* node = nullptr;
    if (auto generic = found.get<Generic>(); generic != nullptr)
      node = generic->node;
    else if (auto generator = found.get<Generator>(); generator != nullptr)
      node = generator->node;
    if (node != nullptr && ! node->params.empty()) {
      auto written = ast::as<ast::RefTypeRef>(node->params[0]->type);
      return written != nullptr ? std::optional<bool>(written->mutable_) : std::nullopt;
    }
    return std::nullopt;
  }


  InScope Checker::in_scope_of(Held defined)
  {
    if (auto named = defined.get<NamedType>(); named != nullptr)
      return InScope(*this, named->scope, named->siblings);
    if (auto generic = defined.get<Generic>(); generic != nullptr)
      return InScope(*this, generic->scope, generic->siblings);
    if (auto generator = defined.get<Generator>(); generator != nullptr)
      return InScope(*this, generator->scope, generator->siblings);
    return InScope(*this, nullptr, nullptr);
  }


  NamedType* Checker::template_named(const ast::Expr* callee)
  {
    Held found;
    if (auto name = ast::as<ast::NameRef>(callee); name != nullptr)
      found = held_in(*top, name->name);
    else if (auto member = ast::as<ast::Member>(callee); member != nullptr) {
      auto held = module_at(member->base);
      if (held != nullptr)
        found = held_in(held->exports, member->name);
    } else
      return nullptr;
    auto named = found.get<NamedType>();
    return named != nullptr && named->takes_types() ? named : nullptr;
  }


  ir::Value* Checker::lower_generic_value(ir::Builder& builder, const ast::Call* expr, NamedType* template_,
                                          const ir::Type* expected)
  {
    auto node = ast::as<ast::TypeDef>(template_->node);
    contract_assert(node != nullptr);
    if (template_->refused) [[unlikely]]
      return error_value(module);
    auto aim = aiming_at(expected);
    Dict<std::string, const ir::Type*> bound;
    const void* family = nullptr;
    const std::vector<const ir::Type*>* args = nullptr;
    if (auto record = ir::as<ir::ProductType>(aim); record != nullptr) {
      family = record->family;
      args = &record->args;
    } else if (auto sum = ir::as<ir::SumType>(aim); sum != nullptr) {
      family = sum->family;
      args = &sum->args;
    }
    if (args != nullptr && family == template_)
      for (std::size_t at = 0; at < node->params.size() && at < args->size(); ++at)
        bound[std::string(node->params[at])] = (*args)[at];
    Dict<std::string, const ast::Field*> fields;
    for (auto one : node->fields)
      fields[std::string(one->name)] = one;
    std::vector<const ast::Expr*> rebuilt;
    for (std::size_t at = 0; at < expr->args.size(); ++at) {
      auto one = expr->args[at];
      auto named = ast::as<ast::Named>(one);
      auto written = named != nullptr ? named->value : one;
      const ast::Field* field = nullptr;
      if (named != nullptr) {
        if (auto found = fields.get(std::string(named->name)); found != nullptr)
          field = *found;
      } else if (at < node->fields.size())
        field = node->fields[at];
      if (field == nullptr) {
        // The record's own lowering says what is wrong with it.
        rebuilt.push_back(one);
        continue;
      }
      // The field's type is read where the definition is written, and the
      // value where the call is.
      const ir::Type* wanted;
      {
        auto scope = in_scope_of(template_);
        wanted = worked_out(field->type, bound);
      }
      if (wanted == ir::void_type()) {
        // A variant that carries nothing: the sum's own lowering says what may
        // be written there.
        rebuilt.push_back(one);
        continue;
      }
      ir::Value* value;
      {
        Setting outer_handing(handing_over,
                              std::optional<std::tuple<std::string, std::int64_t>>({ template_->name, at + 1 }));
        Setting outer_initializing(initializing, std::optional<std::string>{});
        value = wanted != nullptr ? lower_into(builder, written, wanted, written->span)
                                  : lower_expr(builder, written, nullptr);
      }
      auto found = value_type_of(value);
      if (found == ir::error_type()) [[unlikely]]
        return error_value(module);
      if (wanted == nullptr) {
        bool read;
        {
          auto scope = in_scope_of(template_);
          read = reading(field->type, found, bound, written->span, field->name);
        }
        if (! read) [[unlikely]]
          return error_value(module);
      }
      auto ready = syntax.make<ast::Ready>(written->span);
      ready->value = value;
      if (named != nullptr) {
        auto copy = syntax.copy(*named);
        copy->value = ready;
        rebuilt.push_back(copy);
      } else
        rebuilt.push_back(ready);
    }
    for (auto name : node->params)
      if (! bound.contains(std::string(name))) [[unlikely]] {
        diags.emit(D::LANG_GENERIC_NOT_DETERMINED, expr->span, { { "name", name } });
        return error_value(module);
      }
    std::vector<const ir::Type*> given;
    for (auto name : node->params)
      given.push_back(bound[std::string(name)]);
    auto made = instance_type(template_, given, expr->span);
    auto call = syntax.copy(*expr);
    call->args = syntax.list(rebuilt);
    if (ir::is<ir::ProductType>(made))
      return lower_record(builder, call, made, expected);
    if (ir::is<ir::SumType>(made))
      return lower_sum(builder, call, made, expected);
    return error_value(module);
  }


  ir::Value* Checker::through(ir::Builder& builder, const ast::Call* expr, ir::Function* func, const ir::Type* expected)
  {
    const auto& wanted = func->fty->params;
    // Left to right, one argument at a time, so that which parameter an
    // argument goes to is known by the time it is lowered.
    std::optional<std::vector<ir::Value*>> given;
    {
      Setting outer_listing(listing, func->attrs.listable);
      given = given_arguments(builder, expr, func);
    }
    if (! given.has_value()) [[unlikely]]
      return error_value(module);
    if (std::ranges::any_of(*given, [](auto value) { return value->ty == ir::error_type(); })) [[unlikely]]
      return error_value(module);
    // Asked before the walk and not after, a walked call being as much a call
    // as any other.
    auto args = owned_arguments(builder, expr, func, *given);
    flows_of_call(expr, func);
    what_it_changes(func, expr->span);
    tables_handed(wanted, args, func->name, expr->span);
    if (func->attrs.listable) {
      bool differs = false;
      for (std::size_t at = 0; at < args.size() && at < wanted.size(); ++at)
        differs = differs || args[at]->ty != wanted[at];
      if (differs)
        return walked(
          builder, func->fty,
          [&builder, func](std::span<ir::Value* const> each, Span span) {
            return builder.call(func, each, func->fty->ret, span);
          },
          args, expr, expected);
    }
    kept_alike(func, args, expr->span);
    auto answer = builder.call(func, args, func->fty->ret, expr->span);
    answer = as_long_as_given(builder, func, args, answer, expr->span);
    where_made(expr, func);
    if (func->fty->ret == ir::void_type() && expected != nullptr) [[unlikely]] {
      // Somewhere wants a value and there is none.
      diags.emit(D::LANG_CALL_HAS_NO_VALUE, expr->span, { { "name", func->name } });
      return error_value(module);
    }
    if (! accepts(expected, answer->ty)) [[unlikely]] {
      report_mismatch(expr->span, answer->ty, expected);
      return error_value(module);
    }
    return answer;
  }


  std::optional<std::vector<ir::Value*>> Checker::given_arguments(ir::Builder& builder, const ast::Call* expr,
                                                                  ir::Function* func)
  {
    std::span<const ir::Type* const> wanted(func->fty->params);
    if (func->hands_out.has_value())
      wanted = wanted.first(wanted.size() - 1);
    std::vector<const ast::Expr*> placed;
    std::vector<const ast::Named*> named;
    bool failed = false;
    for (auto one : expr->args)
      if (auto by_name = ast::as<ast::Named>(one); by_name != nullptr)
        named.push_back(by_name);
      else if (! named.empty()) [[unlikely]] {
        diags.emit(D::LANG_CALL_POSITION_AFTER_NAME, one->span);
        failed = true;
      } else
        placed.push_back(one);
    std::optional<std::vector<ir::Value*>> values;
    if (func->lifetimes.empty() || std::ranges::none_of(wanted, [](auto one) { return ir::is<ir::FuncType>(one); }))
      // Nothing a function type among the parameters could be held to.
      values = one_by_one(builder, placed, wanted, func->name);
    else {
      Setting calling(calling_key, func->lifetime_key.empty() ? std::optional<std::string>{}
                                                              : std::optional<std::string>(func->lifetime_key));
      Setting notes(lambda_notes, std::optional<Dict<std::int64_t, std::vector<std::tuple<bool, Local*>>>>(
                                    Dict<std::int64_t, std::vector<std::tuple<bool, Local*>>>{}));
      values = one_by_one(builder, placed, wanted, func->name);
      if (values.has_value() && values->size() == wanted.size())
        answers_as_long(func, *values, *lambda_notes, expr->span);
    }
    if (! values.has_value()) [[unlikely]]
      return std::nullopt;
    if (values->size() > wanted.size()) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", func->name }, { "expected", wanted.size() }, { "found", values->size() + named.size() } });
      return std::nullopt;
    }
    if (values->size() < wanted.size() && named.empty()
        && std::ranges::none_of(func->defaults, [](auto one) { return one != nullptr; })
        && ! std::ranges::all_of(wanted.subspan(values->size()), [](auto one) { return is_an_arena_handed(one); }))
      [[unlikely]] {
      // Nothing in the call or the definition says anything but the places.
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", func->name }, { "expected", wanted.size() }, { "found", values->size() } });
      return std::nullopt;
    }
    std::vector<ir::Value*> held(*values);
    held.resize(wanted.size(), nullptr);
    for (auto one : named) {
      auto where = std::ranges::find(func->param_names, one->name);
      std::optional<std::size_t> at;
      if (where != func->param_names.end())
        at = std::size_t(where - func->param_names.begin());
      if (! at.has_value() || *at >= wanted.size()) [[unlikely]] {
        diags.emit(D::LANG_CALL_UNKNOWN_PARAMETER, one->name_span, { { "name", one->name }, { "func", func->name } });
        lower_expr(builder, one->value, nullptr);
        failed = true;
        continue;
      }
      if (held[*at] != nullptr) [[unlikely]] {
        diags.emit(D::LANG_CALL_PARAMETER_TWICE, one->name_span, { { "name", one->name } });
        failed = true;
      }
      Setting outer(handing_over, std::optional<std::tuple<std::string, std::int64_t>>({ func->name, *at + 1 }));
      held[*at] = lower_into(builder, one->value, wanted[*at], one->value->span);
    }
    for (std::size_t at = 0; at < held.size(); ++at) {
      if (held[at] != nullptr)
        continue;
      auto given = at < func->defaults.size() ? func->defaults[at] : nullptr;
      if (given == nullptr && is_an_arena_handed(wanted[at])) {
        // An arena the call does not name is the one it stands in.
        held[at] = out_of(builder, expr->span);
        arena_in_force[id_of(expr)].insert(std::int64_t(at));
        continue;
      }
      if (given == nullptr) [[unlikely]] {
        diags.emit(D::LANG_CALL_PARAMETER_MISSING, expr->span,
                   { { "name", at < func->param_names.size() ? func->param_names[at] : std::to_string(at + 1) } });
        failed = true;
        continue;
      }
      held[at] = used(given);
    }
    if (failed || std::ranges::any_of(held, [](auto value) { return value == nullptr; })) [[unlikely]]
      return std::nullopt;
    if (func->hands_out.has_value()) {
      auto place = arena_for(expr, func->name);
      if (place == nullptr) [[unlikely]]
        return std::nullopt;
      held.push_back(place);
    }
    return held;
  }


  ir::Value* Checker::lower_unit_call(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto callee = ast::as<ast::NameRef>(expr->callee);
    std::string name(callee != nullptr ? callee->name : "");
    bool dropping = name == drop_name;
    if (expr->args.size() != (dropping ? 1u : 2u)) [[unlikely]] {
      diags.emit(D::LANG_UNIT_TAKES_TWO, expr->span,
                 { { "name", name }, { "wanted", dropping ? 1 : 2 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto integer = ir::as<ir::IntType>(found);
    auto floating = ir::as<ir::FloatType>(found);
    if (integer == nullptr && floating == nullptr) [[unlikely]] {
      diags.emit(D::LANG_UNIT_NOT_A_NUMBER, expr->args[0]->span, { { "found", found->written() } });
      return error_value(module);
    }
    auto unit = integer != nullptr ? integer->unit : floating->unit;
    if (dropping) {
      if (unit->is_none()) [[unlikely]] {
        diags.emit(D::LANG_UNIT_DROP_HAS_NONE, expr->span);
        return error_value(module);
      }
      const ir::Type* bare = integer != nullptr ? static_cast<const ir::Type*>(integer->bare()) : floating->bare();
      return as_united(builder, given, bare, expected, expr->span);
    }
    if (! unit->is_none()) [[unlikely]] {
      diags.emit(D::LANG_UNIT_PUT_ON_A_UNIT, expr->args[1]->span, { { "found", unit->render() } });
      return error_value(module);
    }
    auto written = ast::as<ast::Lifted>(expr->args[1]);
    if (written == nullptr) [[unlikely]] {
      diags.emit(D::LANG_UNIT_NOT_LIFTED, expr->args[1]->span);
      return error_value(module);
    }
    auto lifted = unit_lifted(written);
    if (lifted == nullptr) [[unlikely]]
      return error_value(module);
    return as_united(builder, given, carrying(found, lifted), expected, expr->span);
  }


  ir::Value* Checker::as_united(ir::Builder& builder, ir::Value* given, const ir::Type* wanted, const ir::Type* expected,
                                Span span)
  {
    if (! accepts(expected, wanted)) [[unlikely]] {
      report_mismatch(span, wanted, expected);
      return error_value(module);
    }
    if (is_int_const(given))
      return builder.int_const(wanted, static_cast<const ir::IntConst*>(given)->value);
    if (is_float_const(given))
      return builder.float_const(wanted, static_cast<const ir::FloatConst*>(given)->value);
    return builder.cast(ir::CastKind::bitcast, given, wanted, span);
  }


  const ir::Unit* Checker::unit_lifted(const ast::Lifted* written)
  {
    auto found = written->value;
    if (found == nullptr)
      if (auto ref = ast::as<ast::TypeRef>(written->written); ref != nullptr && ! ref->module.has_value())
        return unit_named(ref->name, written->span);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_UNIT_NOT_LIFTED, written->span);
      return nullptr;
    }
    return unit_expression(found, 1);
  }


  const ir::Unit* Checker::unit_expression(const ast::Expr* expr, std::int64_t sign)
  {
    auto signed_ = [&](const ir::Unit* found) -> const ir::Unit* {
      if (found == nullptr)
        return nullptr;
      return sign > 0 ? found : ir::Unit::none()->over(*found);
    };
    switch (expr->tag) {
    case ast::NodeKind::NameRef:
      return signed_(unit_named(static_cast<const ast::NameRef*>(expr)->name, expr->span));
    case ast::NodeKind::StringLit:
      return signed_(unit_named(static_cast<const ast::StringLit*>(expr)->value, expr->span));
    case ast::NodeKind::Raised: {
      auto raised = static_cast<const ast::Raised*>(expr);
      auto inner = unit_expression(raised->base, 1);
      if (inner == nullptr)
        return nullptr;
      auto exponent = raised->exponent.value;
      auto made = inner->raised(std::int64_t(exponent < 0 ? -exponent : exponent));
      return sign > 0 && exponent > 0 ? made : ir::Unit::none()->over(*made);
    }
    case ast::NodeKind::Binary: {
      auto binary = static_cast<const ast::Binary*>(expr);
      if (binary->op == ast::BinaryOp::multiply || binary->op == ast::BinaryOp::divide) {
        auto left = unit_expression(binary->left, sign);
        auto right = unit_expression(binary->right, binary->op == ast::BinaryOp::multiply ? sign : -sign);
        if (left == nullptr || right == nullptr)
          return nullptr;
        return left->times(*right);
      }
      break;
    }
    default:
      break;
    }
    diags.emit(D::LANG_SYNTAX_EXPECTED_UNIT, expr->span);
    return nullptr;
  }


  ir::Value* Checker::syscall_number(const ast::NameRef* ref)
  {
    auto call = ref->name.substr(syscall_number_prefix.size());
    auto architecture = target::architecture_of(module.triple);
    if (auto found = target::syscall_number(architecture, call); found.has_value())
      return used(module.int_const(ir::u32(), *found));
    auto known = target::known_syscalls();
    if (std::ranges::find(known, call) != known.end())
      diags.emit(D::LANG_SYSCALL_NOT_ON_THIS_ARCHITECTURE, ref->span,
                 { { "name", call }, { "architecture", architecture } });
    else
      diags.emit(D::LANG_SYSCALL_UNKNOWN_NAME, ref->span, { { "name", call } });
    return error_value(module);
  }


  ir::Value* Checker::lower_record(ir::Builder& builder, const ast::Call* expr, const ir::Type* ty,
                                   const ir::Type* expected)
  {
    auto record = ir::as<ir::ProductType>(ty);
    contract_assert(record != nullptr);
    Dict<std::string, ir::Value*> given;
    bool spoiled = false;
    for (auto one : expr->args) {
      auto named = ast::as<ast::Named>(one);
      if (named == nullptr) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_FIELD_NOT_NAMED, one->span, { { "type", ty->written() } });
        spoiled = true;
        continue;
      }
      auto at = field_index(record, named->name);
      if (! at.has_value()) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, one->span, { { "type", ty->written() }, { "name", named->name } });
        spoiled = true;
        continue;
      }
      if (given.contains(std::string(named->name))) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_FIELD_TWICE, one->span, { { "name", named->name } });
        spoiled = true;
        continue;
      }
      given[std::string(named->name)] = lower_into(builder, named->value, record->fields[*at].second, one->span);
      owning_field(named->value, ty, named->name, record->fields[*at].second);
    }
    for (const auto& [name, _] : record->fields)
      if (! given.contains(name)) [[unlikely]] {
        diags.emit(D::LANG_PRODUCT_FIELD_MISSING, expr->span, { { "type", ty->written() }, { "name", name } });
        spoiled = true;
      }
    if (spoiled) [[unlikely]]
      return error_value(module);
    ir::Value* made;
    if (ir::held_in_memory(ty)) {
      // Bytes in a place as a whole: room in the frame, each field written
      // where the layout puts it, and the room is the value.
      auto room = builder.frame(ty, expr->span);
      for (std::size_t at = 0; at < record->fields.size(); ++at)
        builder.store(field_place(builder, room, record, at, expr->span), given[record->fields[at].first],
                      expr->span);
      made = builder.cast(ir::CastKind::bitcast, room, ty, expr->span);
    } else {
      std::vector<ir::Value*> leaves;
      for (const auto& [name, held] : record->fields) {
        auto some = leaves_of(builder, given[name], held, expr->span);
        leaves.insert(leaves.end(), some.begin(), some.end());
      }
      made = builder.make_tuple(leaves, ty, expr->span);
    }
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return error_value(module);
    }
    return made;
  }


  ir::Value* Checker::lower_sum(ir::Builder& builder, const ast::Call* expr, const ir::Type* ty,
                                const ir::Type* expected)
  {
    auto sum = ir::as<ir::SumType>(ty);
    contract_assert(sum != nullptr);
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_SUM_ONE_PART, expr->span,
                 { { "type", ty->written() },
                   { "found", expr->args.empty() ? std::string("none") : std::to_string(expr->args.size()) } });
      return error_value(module);
    }
    auto written = ast::as<ast::Named>(expr->args[0]);
    if (written == nullptr) [[unlikely]] {
      diags.emit(D::LANG_PRODUCT_FIELD_NOT_NAMED, expr->args[0]->span, { { "type", ty->written() } });
      return error_value(module);
    }
    auto found = std::ranges::find_if(sum->variants, [&](const auto& v) { return v.first == written->name; });
    if (found == sum->variants.end()) [[unlikely]] {
      diags.emit(D::LANG_SUM_NO_SUCH_PART, written->span, { { "type", ty->written() }, { "name", written->name } });
      return error_value(module);
    }
    auto at = found - sum->variants.begin();
    auto held = found->second;
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return error_value(module);
    }
    auto place = builder.frame(ty, expr->span);
    if (held != ir::void_type()) {
      auto value = lower_into(builder, written->value, held, written->span);
      if (value_type_of(value) == ir::error_type()) [[unlikely]]
        return error_value(module);
      payload_into(builder, place, value, held, expr->span);
    } else if (auto truth = ast::as<ast::BoolLit>(written->value); truth == nullptr || ! truth->value) [[unlikely]] {
      // A part carrying nothing is written `.name ← true`.
      diags.emit(D::LANG_SUM_PART_CARRIES_NOTHING, written->span, { { "type", ty->written() }, { "name", written->name } });
      return error_value(module);
    }
    auto tag = tag_place(builder, place, sum, expr->span);
    builder.store(tag, builder.int_const(ir::u8(), at), expr->span);
    return builder.cast(ir::CastKind::bitcast, place, ty, expr->span);
  }


  ir::Value* Checker::tag_place(ir::Builder& builder, ir::Value* place, const ir::SumType* ty, Span span)
  {
    auto at = builder.binary(ir::BinOp::add, place, builder.int_const(ir::u64(), ir::tag_offset_of(ty, sema::layout())), span);
    return builder.cast(ir::CastKind::bitcast, at, ir::ptr_type(ir::u8(), true), span);
  }


  void Checker::payload_into(ir::Builder& builder, ir::Value* place, ir::Value* value, const ir::Type* held, Span span)
  {
    auto where = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(held, true), span);
    if (auto record = ir::as<ir::ProductType>(held); record != nullptr) {
      record_into(builder, where, value, record, span);
      return;
    }
    if (ir::held_in_memory(held)) {
      // A part that is itself bytes in a place is copied into this one's room.
      copy_bytes(builder, where, value, std::int64_t(ir::size_of(held, sema::layout())), span);
      return;
    }
    builder.store(where, value, span);
  }


  bool Checker::copy_elements(ir::Builder& builder, ir::Value* into, const ir::ArrayType* into_ty, ir::Value* from_,
                              const ir::Type* from_ty, Span span)
  {
    if (from_ty == ir::error_type()) [[unlikely]]
      return false;
    auto from_array = ir::as<ir::ArrayType>(from_ty);
    if (from_array == nullptr || from_array->element != into_ty->element || from_array->rank() != into_ty->rank())
      [[unlikely]] {
      report_mismatch(span, from_ty, into_ty);
      return false;
    }
    if (auto why = not_copied_because(into_ty->element); why.has_value()) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_COPIED, span, { { "type", into_ty->element->written() }, { "why", *why } });
      return false;
    }
    for (std::size_t at = 0; at < from_array->shape.size() && at < into_ty->shape.size(); ++at)
      if (from_array->shape[at].has_value() && into_ty->shape[at].has_value()
          && *from_array->shape[at] != *into_ty->shape[at]) [[unlikely]] {
        diags.emit(D::LANG_ARRAY_SHAPES_DIFFER, span,
                   { { "found", shape_of_type(from_array) }, { "wanted", shape_of_type(into_ty) } });
        return false;
      }
    auto [start, lengths] = shape_of(builder, into, into_ty, span);
    auto strides = strides_of(builder, into, into_ty, span);
    if (read_only(start)) [[unlikely]] {
      diags.emit(D::LANG_STATIC_CHANGES, span, { { "name", "the array written" } });
      return false;
    }
    if (! made_here(start))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, span);
    auto [source, given] = shape_of(builder, from_, from_array, span);
    auto apart = strides_of(builder, from_, from_array, span);
    ir::Value* count = nullptr;
    std::optional<std::int64_t> known = 1;
    for (std::size_t at = 0; at < given.size() && at < lengths.size(); ++at) {
      auto one = given[at];
      auto other = lengths[at];
      if (! from_array->shape[at].has_value() || ! into_ty->shape[at].has_value())
        builder.check(builder.compare(ir::CmpPred::eq, one, other, span), "arrays of different shapes",
                      target::statuses::OUT_OF_RANGE, span);
      auto along = into_ty->shape[at].has_value() ? into_ty->shape[at] : from_array->shape[at];
      if (along.has_value() && known.has_value())
        *known *= std::int64_t(*along);
      else
        known.reset();
      count = count == nullptr ? other : builder.binary(ir::BinOp::wrap_mul, count, other, span);
    }
    contract_assert(count != nullptr);
    move_elements(builder, Laid{ start, lengths, strides, into_ty->fixed() },
                  Laid{ source, given, apart, from_array->fixed() }, into_ty->element, count, known, span);
    return true;
  }


  void Checker::move_elements(ir::Builder& builder, const Laid& into_shape, const Laid& from_shape,
                              const ir::Type* element, ir::Value* count, std::optional<std::int64_t> known, Span span)
  {
    auto into = into_shape.first;
    auto from = from_shape.first;
    if (known == 0)
      return;
    bool lanes = ir::is<ir::IntType>(element) || ir::is<ir::FloatType>(element) || ir::is<ir::BoolType>(element);
    if (known.has_value() && into_shape.one_run && from_shape.one_run && lanes) {
      move_as_one(builder, into, from, element, *known, span);
      return;
    }
    if (known.has_value() && into_shape.one_run && from_shape.strides.size() == 1
        && *known * std::int64_t(ir::stride_of(element, sema::layout())) <= as_one_at_most && lanes) {
      // A view whose type cannot say its elements are one run nearly always
      // is one, which is asked while the program runs.
      auto one_run = builder.compare(ir::CmpPred::eq, from_shape.strides[0], builder.int_const(ir::u64(), 1), span);
      auto whole = builder.new_block("copy.run");
      auto apart = builder.new_block("copy.apart");
      auto done = builder.new_block("copied.run");
      auto before = builder.memory();
      builder.condbr(one_run, whole, apart, {}, {}, span);
      builder.position_at(whole);
      builder.set_memory(before);
      move_as_one(builder, into, from, element, *known, span);
      builder.br(done, std::vector<ir::Value*>{ builder.memory() }, span);
      builder.position_at(apart);
      builder.set_memory(before);
      move_apart(builder, into_shape, from_shape, element, count, span);
      builder.br(done, std::vector<ir::Value*>{ builder.memory() }, span);
      builder.position_at(done);
      builder.set_memory(done->add_param(ir::mem_type(), "mem"));
      return;
    }
    move_apart(builder, into_shape, from_shape, element, count, span);
  }


  void Checker::move_as_one(ir::Builder& builder, ir::Value* into, ir::Value* from, const ir::Type* element,
                            std::int64_t known, Span span)
  {
    auto held = ir::vec_type(element, unsigned(known));
    auto pointer = ir::ptr_type(held, true);
    auto to = builder.cast(ir::CastKind::bitcast, into, pointer, span);
    auto value = builder.load(builder.cast(ir::CastKind::bitcast, from, pointer, span), span);
    builder.store(to, value, span);
  }


  void Checker::move_apart(ir::Builder& builder, const Laid& into_shape, const Laid& from_shape,
                           const ir::Type* element, ir::Value* count, Span span)
  {
    auto into = into_shape.first;
    auto from = from_shape.first;
    auto to_word = builder.cast(ir::CastKind::bitcast, into, ir::u64(), span);
    auto from_word = builder.cast(ir::CastKind::bitcast, from, ir::u64(), span);
    auto later = builder.compare(ir::CmpPred::ugt, to_word, from_word, span);
    auto backward = builder.new_block("copy.back");
    auto forward = builder.new_block("copy.ahead");
    auto done = builder.new_block("copied.all");
    auto before = builder.memory();
    builder.condbr(later, backward, forward, {}, {}, span);
    for (auto [block, downward] : { std::pair{ backward, true }, std::pair{ forward, false } }) {
      builder.position_at(block);
      builder.set_memory(before);
      element_by_element(builder, into_shape, from_shape, element, count, downward, span);
      builder.br(done, std::vector<ir::Value*>{ builder.memory() }, span);
    }
    builder.position_at(done);
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
  }


  void Checker::element_by_element(ir::Builder& builder, const Laid& into_shape, const Laid& from_shape,
                                   const ir::Type* element, ir::Value* count, bool downward, Span span)
  {
    auto header = builder.new_block("copy.element");
    auto body = builder.new_block("copying.element");
    auto after = builder.new_block("copied.elements");
    // Where either side is a view of several dimensions, an index along each
    // is carried with the loop, so that no turn divides to find where it is.
    const auto& lengths = into_shape.counts;
    bool counted = lengths.size() > 1 && ! (into_shape.one_run && from_shape.one_run);
    auto one = builder.int_const(ir::u64(), 1);
    std::vector<ir::Value*> first;
    if (counted)
      for (auto length : lengths)
        first.push_back(downward ? builder.binary(ir::BinOp::wrap_sub, length, one, span)
                                 : builder.int_const(ir::u64(), 0));
    {
      std::vector<ir::Value*> args{ builder.int_const(ir::u64(), 0) };
      args.insert(args.end(), first.begin(), first.end());
      args.push_back(builder.memory());
      builder.br(header, args, span);
    }
    builder.position_at(header);
    ir::Value* turn = header->add_param(ir::u64(), "turn");
    std::vector<ir::Value*> indices;
    for (std::size_t i = 0; i < first.size(); ++i)
      indices.push_back(header->add_param(ir::u64(), "along"));
    ir::Value* token = header->add_param(ir::mem_type(), "mem");
    builder.set_memory(token);
    builder.condbr(builder.compare(ir::CmpPred::ult, turn, count, span), body, after, {}, {}, span);
    builder.position_at(body);
    ir::Value* at = turn;
    if (downward) {
      auto last = builder.binary(ir::BinOp::wrap_sub, count, builder.int_const(ir::u64(), 1), span);
      at = builder.binary(ir::BinOp::wrap_sub, last, turn, span);
    }
    std::vector<ir::Value*> places;
    for (const Laid* laid : { &into_shape, &from_shape }) {
      auto offset = counted ? offset_at(builder, indices, laid->strides, span)
                            : flat_offset(builder, laid->counts, laid->strides, at, std::int64_t(laid->counts.size()),
                                          laid->one_run, span);
      places.push_back(element_place(builder, laid->first, element, offset, span));
    }
    copy_bytes(builder, places[0], places[1], std::int64_t(ir::size_of(element, sema::layout())), span);
    std::vector<ir::Value*> moved;
    if (counted)
      moved = odometer(builder, indices, lengths, downward, span);
    {
      std::vector<ir::Value*> args{ builder.binary(ir::BinOp::wrap_add, turn, one, span) };
      args.insert(args.end(), moved.begin(), moved.end());
      args.push_back(builder.memory());
      builder.br(header, args, span);
    }
    builder.position_at(after);
    // What holds after the loop is what the header carried.
    builder.set_memory(token);
  }


  void Checker::copy_bytes(ir::Builder& builder, ir::Value* into, ir::Value* from_, std::int64_t size, Span span)
  {
    std::int64_t at = 0;
    while (at < size) {
      auto piece = std::ranges::find_if(sema::chunks(), [&](const auto& one) { return one.second <= size - at; });
      auto [part, width] = *piece;
      auto to = byte_place(builder, into, part, at, span);
      auto value = builder.load(byte_place(builder, from_, part, at, span), span);
      builder.store(to, value, span);
      at += width;
    }
  }


  ir::Value* Checker::byte_place(ir::Builder& builder, ir::Value* place, const ir::Type* part, std::int64_t offset,
                                 Span span)
  {
    auto where = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(part, true), span);
    if (offset == 0)
      return where;
    return builder.binary(ir::BinOp::add, where, builder.int_const(ir::u64(), offset), span);
  }


  ir::Value* Checker::payload_from(ir::Builder& builder, ir::Value* place, const ir::Type* held, Span span,
                                   bool in_place)
  {
    auto where = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(held, true), span);
    if (auto record = ir::as<ir::ProductType>(held); record != nullptr)
      return record_from(builder, where, record, span);
    // A part that is itself a place is answered as where it is, except where
    // the sum is the place the program named: the arm may write that place,
    // so the part is copied, that part and not the whole sum.
    if (ir::held_in_memory(held) && ! in_place)
      return builder.cast(ir::CastKind::bitcast, place, held, span);
    return builder.load(where, span);
  }


  ir::Value* Checker::loaded_or_in_place(ir::Builder& builder, ir::Value* address, const ast::Expr* expr, Span span)
  {
    // A sum read out of a place is copied into room of its own; one a `match`
    // takes apart is read twice, its tag and its part, both before anything
    // else can write there, so the copy is skipped.
    auto pointer = ir::as<ir::PtrType>(value_type_of(address));
    if (match_in_place.contains(id_of(expr)) && pointer != nullptr && ir::is<ir::SumType>(pointer->pointee)) {
      match_in_place.erase(id_of(expr));
      matched_in_place.insert(id_of(expr));
      return builder.cast(ir::CastKind::bitcast, address, pointer->pointee, span);
    }
    return builder.load(address, span);
  }


  ir::Value* Checker::lower_syscall(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.empty()) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", syscall_name }, { "expected", 1 }, { "found", 0 } });
      return error_value(module);
    }
    if (std::int64_t(expr->args.size()) - 1 > syscall_arguments) [[unlikely]] {
      diags.emit(D::LANG_SYSCALL_TOO_MANY, expr->span,
                 { { "expected", syscall_arguments }, { "found", expr->args.size() - 1 } });
      return error_value(module);
    }
    an_effect(D::LANG_PURE_CALLS_IMPURE, expr->span, {}, { { "name", syscall_name } });
    std::vector<ir::Value*> given;
    for (std::size_t at = 0; at < expr->args.size(); ++at) {
      auto one = expr->args[at];
      auto value = lower_expr(builder, one, nullptr);
      auto ty = value_type_of(value);
      if (ty == ir::error_type()) [[unlikely]]
        return error_value(module);
      if (! ir::is<ir::IntType>(ty) && ! ir::is<ir::PtrType>(ty)) [[unlikely]] {
        diags.emit(D::LANG_SYSCALL_BAD_ARGUMENT, one->span, { { "position", at + 1 }, { "found", ty->written() } });
        return error_value(module);
      }
      given.push_back(as_a_word(builder, value, ty, expr->span));
    }
    auto answer = builder.syscall(given[0], std::span(given).subspan(1), ir::i64(), expr->span);
    if (! accepts(expected, answer->ty)) [[unlikely]] {
      report_mismatch(expr->span, answer->ty, expected);
      return error_value(module);
    }
    return answer;
  }


  ir::Value* Checker::lower_place_at(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    auto callee = ast::as<ast::NameRef>(expr->callee);
    contract_assert(callee != nullptr);
    std::string name(callee->name);
    bool several = name == span_name;
    std::size_t wanted = several ? 3 : 2;
    if (expr->args.size() != wanted) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", name }, { "expected", wanted }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto written = ast::as<ast::Lifted>(expr->args.back());
    if (written == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ADDRESS_NOT_A_TYPE, expr->args.back()->span, { { "name", name } });
      return error_value(module);
    }
    auto answer = lifted_type(written);
    if (answer == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ADDRESS_NOT_A_TYPE, written->span, { { "name", name } });
      return error_value(module);
    }
    const ir::Type* place_ty;
    if (several) {
      auto list = ir::as<ir::ListType>(answer);
      auto array = ir::as<ir::ArrayType>(answer);
      if (list == nullptr && (array == nullptr || array->fixed() || array->rank() != 1)) [[unlikely]] {
        diags.emit(D::LANG_SPAN_NOT_AN_ARRAY, written->span, { { "found", answer->written() } });
        return error_value(module);
      }
      place_ty = ir::ptr_type(list != nullptr ? list->element : array->element, true);
    } else {
      if (! ir::is<ir::PtrType>(answer)) [[unlikely]] {
        diags.emit(D::LANG_AT_NOT_A_REFERENCE, written->span, { { "found", answer->written() } });
        return error_value(module);
      }
      place_ty = answer;
    }
    auto address = as_an_address(builder, expr->args[0], name);
    if (address == nullptr) [[unlikely]]
      return error_value(module);
    auto place = builder.cast(ir::CastKind::bitcast, address, place_ty, expr->span);
    if (! several) {
      if (! accepts(expected, answer)) [[unlikely]] {
        report_mismatch(expr->span, answer, expected);
        return error_value(module);
      }
      return place;
    }
    auto count = lower_expr(builder, expr->args[1], nullptr);
    auto counting = value_type_of(count);
    if (counting == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto integer = ir::as<ir::IntType>(counting);
    if (integer == nullptr) [[unlikely]] {
      diags.emit(D::LANG_SPAN_NOT_A_COUNT, expr->args[1]->span, { { "found", counting->written() } });
      return error_value(module);
    }
    ir::Value* made;
    if (ir::is<ir::ListType>(answer)) {
      // A list of the heap's whose room is there, laid out as a list's: the
      // list owns it from here on, and gives it back as any.
      auto pieces = ir::parts_of(answer);
      auto heap = provided(heap_name).get<ir::GlobalVar>();
      contract_assert(heap != nullptr);
      auto first = builder.cast(ir::CastKind::bitcast, place, pieces[0], expr->span);
      auto many = as_count(builder, count, integer, expr->span);
      auto arena = builder.cast(ir::CastKind::bitcast, builder.address(heap, expr->span), pieces[2], expr->span);
      made = builder.make_tuple(std::vector<ir::Value*>{ first, many, arena }, answer, expr->span);
      made_from[id_of(expr)] = { expr, KeySet{ heap_key } };
    } else {
      std::vector<ir::Value*> lengths{ as_count(builder, count, integer, expr->span) };
      made = dense_view(builder, place, lengths, ir::as<ir::ArrayType>(answer), expr->span);
    }
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    return made;
  }


  ir::Value* Checker::lower_bytes(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", bytes_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], ir::str_type());
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    if (found != ir::str_type()) [[unlikely]] {
      diags.emit(D::LANG_BYTES_NOT_TEXT, expr->args[0]->span, { { "found", found->written() } });
      return error_value(module);
    }
    auto answer = ir::array_type(ir::u8(), { std::nullopt });
    // Where the bytes are and how many: an array of bytes is a view of them
    // that gives nothing back.
    auto parts = ir::parts_of(ir::str_type());
    auto bytes = builder.extract(given, 0, parts[0], expr->span);
    std::vector<ir::Value*> lengths{ builder.extract(given, 1, parts[1], expr->span) };
    auto made = dense_view(builder, bytes, lengths, answer, expr->span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    return made;
  }


  ir::Value* Checker::lower_text(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", text_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    const ast::Expr* written = expr->args[0];
    auto wanted = ir::array_type(ir::u8(), { std::nullopt });
    auto given =
      lower_expr(builder, written, ast::is<ast::ArrayLit>(written) || in_the_image(written) ? wanted : nullptr);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto view = as_a_view(builder, written, given, wanted, written->span);
    if (view == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TEXT_NOT_BYTES, written->span, { { "found", found->written() } });
      return error_value(module);
    }
    auto answer = ir::result_type(ir::str_type(), ir::u64());
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    made_from[id_of(expr)] = { expr, out_of_arenas };
    auto span = expr->span;
    auto run = as_one_run(builder, view, wanted, span);
    auto bytes = ir::ptr_type(ir::u8(), true);
    auto start = builder.extract(run, 0, ir::parts_of(wanted)[0], span);
    auto count = builder.extract(run, 1, ir::u64(), span);
    auto upto = builder.call(strings::valid_function(module), std::vector<ir::Value*>{ start, count }, ir::u64(), span);
    auto well_formed = builder.compare(ir::CmpPred::eq, upto, count, span);
    // Asked before the branch: the answer carries it on both ways.
    auto where = out_of(builder, span);
    auto copying = builder.new_block("text.copy");
    auto done = builder.new_block("text");
    auto before = builder.memory();
    auto nowhere = builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), bytes, span);
    builder.condbr(well_formed, copying, done, {}, std::vector<ir::Value*>{ nowhere, before }, span);
    builder.position_at(copying);
    builder.set_memory(before);
    auto room = tables::allocate(builder, where, count);
    strings::moved(builder, room, start, count, span);
    builder.br(done, std::vector<ir::Value*>{ room, builder.memory() }, span);
    builder.position_at(done);
    ir::Value* made = done->add_param(bytes, "text");
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    // On the way that failed the answer half is nowhere, which is what the
    // truth value beside it says nothing may read.
    auto text = builder.make_tuple(std::vector<ir::Value*>{ made, count, where }, ir::str_type(), span);
    auto failed = builder.compare(ir::CmpPred::ne, upto, count, span);
    return builder.wrap(text, failed, answer, span, upto);
  }


  ir::Value* Checker::as_one_run(ir::Builder& builder, ir::Value* view, const ir::ArrayType* ty, Span span)
  {
    auto stride = builder.extract(view, 2, ir::u64(), span);
    auto kept = builder.new_block("one_run.already");
    auto copying = builder.new_block("one_run.copy");
    auto done = builder.new_block("one_run");
    auto before = builder.memory();
    builder.condbr(builder.compare(ir::CmpPred::eq, stride, builder.int_const(ir::u64(), 1), span), kept, copying, {},
                   {}, span);
    builder.position_at(kept);
    builder.br(done, std::vector<ir::Value*>{ view, before }, span);
    builder.position_at(copying);
    builder.set_memory(before);
    auto func = one_run_function(ty);
    auto arena = out_of(builder, span);
    auto copied = builder.call(func, std::vector<ir::Value*>{ arena, view }, ty, span);
    builder.br(done, std::vector<ir::Value*>{ copied, builder.memory() }, span);
    builder.position_at(done);
    ir::Value* answer = done->add_param(ty, "run");
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    return answer;
  }


  ir::Value* Checker::as_a_view(ir::Builder& builder, const ast::Expr* written, ir::Value* value,
                                const ir::ArrayType* wanted, Span span)
  {
    auto found = value_type_of(value);
    if (found == wanted)
      return value;
    if (auto array = ir::as<ir::ArrayType>(found); array != nullptr && lets_go_of(array, wanted)) {
      auto [start, lengths] = shape_of(builder, value, array, span);
      auto strides = strides_of(builder, value, array, span);
      return view(builder, start, lengths, strides, wanted, span);
    }
    if (auto list = ir::as<ir::ListType>(found);
        list != nullptr && wanted->rank() == 1 && list->element == wanted->element) {
      if (auto borrowed = written != nullptr ? borrowed_name(written) : std::nullopt) {
        const auto& [name, path] = *borrowed;
        lend(name, false, written->span, path);
      }
      if (written != nullptr)
        list_views[id_of(written)] = list;
      auto start = builder.extract(value, 0, ir::parts_of(list)[0], span);
      std::vector<ir::Value*> lengths{ builder.extract(value, 1, ir::u64(), span) };
      return dense_view(builder, start, lengths, wanted, span);
    }
    return nullptr;
  }


  ir::Value* Checker::lower_typename(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", typename_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto found = type_stood_for(expr->args[0]);
    if (found == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TYPENAME_TAKES_A_LIFT, expr->args[0]->span);
      return error_value(module);
    }
    if (! accepts(expected, ir::str_type())) [[unlikely]] {
      report_mismatch(expr->span, ir::str_type(), expected);
      return error_value(module);
    }
    return written_text(builder, found->written(), expr->span);
  }


  ir::Value* Checker::lower_fields(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", fields_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto asked = fields_of_a_type(expr);
    if (asked == ir::error_type()) [[unlikely]]
      return error_value(module);
    if (auto enumeration = ir::as<ir::EnumType>(asked); enumeration != nullptr)
      return members_of(builder, enumeration, expr, expected);
    if (asked != nullptr) [[unlikely]] {
      // A record's type answers types, and only `comptime foreach` can hold one.
      diags.emit(D::LANG_FIELDS_OF_A_TYPE_NOT_WALKED, expr->span, { { "type", asked->written() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto record = ir::as<ir::ProductType>(found);
    if (record == nullptr) [[unlikely]] {
      diags.emit(D::LANG_FIELDS_NOT_A_RECORD, expr->args[0]->span, { { "found", found->written() } });
      return error_value(module);
    }
    if (ir::held_in_memory(found)) {
      // A record held in memory: each field read where it is, and each pair and
      // the whole made of members rather than of leaves.
      std::vector<ir::Value*> pairs;
      for (std::size_t at = 0; at < record->fields.size(); ++at) {
        const auto& [name, kind] = record->fields[at];
        auto pair = ir::tuple_type({ ir::str_type(), kind });
        auto text = written_text(builder, name, expr->span);
        auto value = member(builder, given, found, std::int64_t(at), expr->span);
        pairs.push_back(made_tuple(builder, std::vector<ir::Value*>{ text, value }, pair, expr->span));
      }
      std::vector<const ir::Type*> types;
      for (auto one : pairs)
        types.push_back(one->ty);
      auto whole = ir::tuple_type(std::move(types));
      if (! accepts(expected, whole)) [[unlikely]] {
        report_mismatch(expr->span, whole, expected);
        return error_value(module);
      }
      return made_tuple(builder, pairs, whole, expr->span);
    }
    auto inside = ir::parts_within(found);
    auto held = ir::parts_of(found);
    auto text = ir::parts_of(ir::str_type());
    std::vector<ir::Value*> leaves;
    std::vector<const ir::Type*> kinds;
    for (std::size_t at = 0; at < record->fields.size(); ++at) {
      const auto& [name, kind] = record->fields[at];
      auto written = written_text(builder, name, expr->span);
      for (std::size_t which = 0; which < text.size(); ++which)
        leaves.push_back(builder.extract(written, std::int64_t(which), text[which], expr->span));
      auto [first, count] = inside[at];
      for (std::size_t which = 0; which < count; ++which)
        leaves.push_back(builder.extract(given, std::int64_t(first + which), held[first + which], expr->span));
      kinds.push_back(ir::tuple_type({ ir::str_type(), kind }));
    }
    auto answer = ir::tuple_type(std::move(kinds));
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    return builder.make_tuple(leaves, answer, expr->span);
  }


  ir::Value* Checker::lower_alternatives(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", names::alternatives_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], nullptr);
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto sum = ir::as<ir::SumType>(found);
    if (sum == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ALTERNATIVES_NOT_A_SUM, expr->args[0]->span, { { "found", found->written() } });
      return error_value(module);
    }
    Span span = expr->span;
    auto place = builder.cast(ir::CastKind::bitcast, given, ir::ptr_type(sum, true), span);
    auto tag = builder.load(tag_place(builder, place, sum, span), span);
    ir::Value* truth = nullptr;
    std::vector<ir::Value*> pairs;
    for (std::size_t at = 0; at < sum->variants.size(); ++at) {
      const auto& [name, held] = sum->variants[at];
      bool nothing = held == ir::void_type();
      auto shown = nothing ? ir::bool_type() : held;
      auto view_ty = ir::array_type(shown);
      if (nothing && truth == nullptr) {
        truth = builder.frame(ir::bool_type(), span);
        builder.store(truth, builder.bool_const(true), span);
      }
      auto where = nothing ? truth : place;
      auto start = builder.cast(ir::CastKind::bitcast, where, ir::parts_of(view_ty)[0], span);
      auto holds = builder.compare(ir::CmpPred::eq, tag, builder.int_const(ir::u8(), std::int64_t(at)), span);
      auto count = builder.cast(ir::CastKind::zext, holds, ir::u64(), span);
      auto text = written_text(builder, name, span);
      std::vector<ir::Value*> lengths{ count };
      std::vector<ir::Value*> strides{ builder.int_const(ir::u64(), 1) };
      auto seen = view(builder, start, lengths, strides, view_ty, span);
      pairs.push_back(made_tuple(builder, std::vector<ir::Value*>{ text, seen },
                                 ir::tuple_type({ ir::str_type(), view_ty }), span));
    }
    std::vector<const ir::Type*> types;
    for (auto one : pairs)
      types.push_back(one->ty);
    auto whole = ir::tuple_type(std::move(types));
    if (! accepts(expected, whole)) [[unlikely]] {
      report_mismatch(span, whole, expected);
      return error_value(module);
    }
    return made_tuple(builder, pairs, whole, span);
  }


  const ir::Type* Checker::fields_of_a_type(const ast::Expr* expr)
  {
    auto call = ast::as<ast::Call>(expr);
    if (call == nullptr)
      return nullptr;
    auto callee = ast::as<ast::NameRef>(call->callee);
    if (callee == nullptr || callee->name != fields_name || call->args.size() != 1)
      return nullptr;
    auto lifted = ast::as<ast::Lifted>(call->args[0]);
    if (lifted == nullptr)
      return nullptr;
    auto found = lifted_type(lifted);
    if (ir::is<ir::EnumType>(found) || ir::is<ir::ProductType>(found))
      return found;
    if (found != nullptr && found != ir::error_type()) [[unlikely]] {
      diags.emit(D::LANG_FIELDS_NOT_A_RECORD, call->args[0]->span, { { "found", found->written() } });
      return ir::error_type();
    }
    return nullptr;
  }


  ir::Value* Checker::members_of(ir::Builder& builder, const ir::EnumType* ty, const ast::Call* expr,
                                 const ir::Type* expected)
  {
    auto text = ir::parts_of(ir::str_type());
    std::vector<ir::Value*> leaves;
    std::vector<const ir::Type*> kinds;
    for (std::size_t at = 0; at < ty->members.size(); ++at) {
      auto written = written_text(builder, ty->members[at], expr->span);
      for (std::size_t which = 0; which < text.size(); ++which)
        leaves.push_back(builder.extract(written, std::int64_t(which), text[which], expr->span));
      leaves.push_back(used(module.enum_const(ty, at)));
      kinds.push_back(ir::tuple_type({ ir::str_type(), ty }));
    }
    auto answer = ir::tuple_type(std::move(kinds));
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return error_value(module);
    }
    return builder.make_tuple(leaves, answer, expr->span);
  }


  ir::Value* Checker::lower_one_run(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", one_run_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto value = lower_expr(builder, expr->args[0], nullptr);
    auto ty = value_type_of(value);
    if (ty == ir::error_type()) [[unlikely]]
      return error_value(module);
    auto array = ir::as<ir::ArrayType>(ty);
    if (array == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, expr->args[0]->span, { { "found", ty->written() } });
      return error_value(module);
    }
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return error_value(module);
    }
    KeySet from = out_of_arenas;
    auto inside = arenas_in(expr->args[0]);
    from.insert(inside.begin(), inside.end());
    made_from[id_of(expr)] = { expr, from };
    if (array->fixed())
      return value;
    if (auto why = not_copied_because(array->element); why.has_value()) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_COPIED, expr->args[0]->span, { { "type", array->element->written() }, { "why", *why } });
      return error_value(module);
    }
    auto span = expr->span;
    auto [start, lengths] = shape_of(builder, value, array, span);
    auto strides = strides_of(builder, value, array, span);
    auto dense = dense_strides(builder, lengths, span);
    ir::Value* apart = builder.bool_const(true);
    for (std::size_t at = 0; at < strides.size() && at < dense.size(); ++at)
      apart = builder.binary(ir::BinOp::and_, apart, builder.compare(ir::CmpPred::eq, strides[at], dense[at], span),
                             span);
    auto kept = builder.new_block("one_run.already");
    auto copying = builder.new_block("one_run.copy");
    auto done = builder.new_block("one_run");
    auto before = builder.memory();
    builder.condbr(apart, kept, copying, {}, {}, span);
    builder.position_at(kept);
    builder.br(done, std::vector<ir::Value*>{ value, before }, span);
    builder.position_at(copying);
    builder.set_memory(before);
    // The copy is the uncommon way through and a loop, so it is one function
    // for every view of this type rather than written out at each.
    auto func = one_run_function(array);
    auto arena = out_of(builder, span);
    auto copied = builder.call(func, std::vector<ir::Value*>{ arena, value }, ty, span);
    builder.br(done, std::vector<ir::Value*>{ copied, builder.memory() }, span);
    builder.position_at(done);
    ir::Value* answer = done->add_param(ty, "run");
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    return answer;
  }


  ir::Function* Checker::one_run_function(const ir::ArrayType* ty)
  {
    auto arena = ir::ptr_type(ir::arena_type(), true);
    const ir::Type* params[] = { arena, ty };
    auto [func, fresh] = tables::generated(module, std::format("__pl4g_one_run.{}", ty->mangled()), params, ty);
    if (! fresh)
      return func;
    func->attrs.inline_ = ir::InlineHint::never;
    auto block = func->add_block();
    ir::Builder builder(module, *func);
    builder.position_at(block);
    ir::Value* where = block->add_param(arena, "arena");
    ir::Value* value = block->add_param(ty, "v");
    auto [start, lengths] = shape_of(builder, value, ty, invalid_span);
    auto strides = strides_of(builder, value, ty, invalid_span);
    auto dense = dense_strides(builder, lengths, invalid_span);
    auto count = by_row(builder, builder.int_const(ir::u64(), 1), lengths, invalid_span);
    auto size = builder.binary(ir::BinOp::wrap_mul, count,
                               builder.int_const(ir::u64(), ir::stride_of(ty->element, sema::layout())));
    auto room = builder.cast(ir::CastKind::bitcast, tables::allocate(builder, where, size),
                             ir::ptr_type(ty->element, true));
    element_by_element(builder, Laid{ room, lengths, dense, true }, Laid{ start, lengths, strides, false },
                       ty->element, count, false, invalid_span);
    builder.ret(view(builder, room, lengths, dense, ty, invalid_span));
    return func;
  }


  ir::Value* Checker::lower_str_of(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.empty() || expr->args.size() > 2) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", str_of_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    // Where the bytes go, where a second argument said.
    ir::Value* where = nullptr;
    if (expr->args.size() == 2) {
      where = arena_place(builder, expr->args[1]);
      if (where == nullptr) [[unlikely]]
        return error_value(module);
    }
    auto given = lower_expr(builder, expr->args[0], ir::char_type());
    auto found = value_type_of(given);
    if (found == ir::error_type()) [[unlikely]]
      return error_value(module);
    if (found != ir::char_type()) [[unlikely]] {
      diags.emit(D::LANG_STR_OF_NOT_A_CHARACTER, expr->args[0]->span, { { "found", found->written() } });
      return error_value(module);
    }
    if (! accepts(expected, ir::str_type())) [[unlikely]] {
      report_mismatch(expr->span, ir::str_type(), expected);
      return error_value(module);
    }
    if (where == nullptr)
      where = out_of(builder, expr->span);
    // The character itself goes over, not its number.
    auto bytes = builder.call(strings::char_function(module), std::vector<ir::Value*>{ where, given },
                              ir::ptr_type(ir::u8(), true), expr->span);
    auto count = builder.call(strings::length_in_bytes(module), std::vector<ir::Value*>{ given }, ir::u64(), expr->span);
    return builder.make_tuple(std::vector<ir::Value*>{ bytes, count, where }, ir::str_type(), expr->span);
  }


  ir::Value* Checker::lower_empty(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (! implicit_empties.contains(id_of(expr))) [[unlikely]] {
      diags.emit(D::LANG_EMPTY_WRITTEN, expr->span);
      return error_value(module);
    }
    auto written = expr->args[0];
    auto owner = arena_owned(written);
    if (owner == nullptr) [[unlikely]]
      return error_value(module);
    auto place = arena_place(builder, written);
    if (place == nullptr) [[unlikely]]
      return error_value(module);
    if (expected != nullptr) [[unlikely]] {
      report_mismatch(expr->span, ir::void_type(), expected);
      return error_value(module);
    }
    given_back(owner, expr->span);
    builder.call(tables::release_function(module), std::vector<ir::Value*>{ place }, ir::void_type(), expr->span);
    return used(module.undef(ir::void_type()));
  }


  Local* Checker::arena_owned(const ast::Expr* written)
  {
    auto name = ast::as<ast::NameRef>(written);
    contract_assert(name != nullptr);
    auto local = find_local(name->name);
    [[maybe_unused]] bool owned_here = local != nullptr && ! local->handed_out && is_arena(local)
                                       && held_by(local) == ir::arena_type();
    contract_assert(owned_here);
    return local;
  }


  ir::Value* Checker::arena_place(ir::Builder& builder, const ast::Expr* written)
  {
    if (ast::is<ast::NameRef>(written))
      return arena_named(builder, written);
    auto value = lower_expr(builder, written, nullptr);
    auto held = value_type_of(value);
    if (auto pointer = ir::as<ir::PtrType>(held); pointer != nullptr && pointer->pointee == ir::arena_type())
      return value;
    if (held != ir::error_type()) [[unlikely]]
      diags.emit(D::LANG_EMPTY_NOT_AN_ARENA, written->span, { { "found", held->written() } });
    return nullptr;
  }


  bool Checker::operator_takes_an_arena(const ast::Expr* expr)
  {
    auto found = operator_written(glyph_of(expr), std::int64_t(operands_of(expr).size()) + 1);
    if (auto generic = found.get<Generic>(); generic != nullptr && generic->node != nullptr) {
      auto node = generic->node;
      auto written = node->params.empty() ? nullptr : ast::as<ast::RefTypeRef>(node->params.back()->type);
      if (written == nullptr)
        return false;
      auto pointee = ast::as<ast::TypeRef>(written->pointee);
      return pointee != nullptr && pointee->name == "arena";
    }
    auto func = found.get<ir::Function>();
    if (func == nullptr || func->fty->params.empty())
      return false;
    auto last = ir::as<ir::PtrType>(func->fty->params.back());
    return last != nullptr && last->pointee == ir::arena_type();
  }


  ir::Value* Checker::out_of(ir::Builder& builder, Span span)
  {
    if (out_of_arena != nullptr)
      return out_of_arena;
    auto heap = provided(heap_name).get<ir::GlobalVar>();
    contract_assert(heap != nullptr);
    return builder.address(heap, span);
  }


  void Checker::owning_field(const ast::Expr* written, const ir::Type* record, std::string_view name,
                             const ir::Type* held)
  {
    if (! is_owning(held))
      return;
    if (! is_temporary(written)) [[unlikely]] {
      diags.emit(D::LANG_CONTAINER_CANNOT_COPY, written->span, { { "type", held->written() } });
      return;
    }
    auto holds = holder_of(written);
    if (holds != heap_key) [[unlikely]]
      diags.emit(D::LANG_CONTAINER_ALLOCATOR_MISMATCH, written->span,
                 { { "param", name },
                   { "func", record->written() },
                   { "wanted", heap_name },
                   { "found", holds.has_value() ? owner_written(*holds) : std::string("an allocator nothing says") } });
  }


  bool Checker::held_by_reference(const ast::Expr* written)
  {
    auto name = ast::as<ast::NameRef>(written);
    if (name == nullptr)
      return false;
    auto local = find_local(name->name);
    if (local == nullptr)
      return false;
    auto held = ir::as<ir::PtrType>(held_by(local));
    return held != nullptr && held->pointee == ir::arena_type();
  }


  ir::Value* Checker::lower_allocated(ir::Builder& builder, const ast::Allocated* expr, const ir::Type* expected)
  {
    if (in_the_image(expr))
      return lower_static(builder, expr, expr->value, expected);
    auto place = arena_named(builder, expr->arena);
    if (place == nullptr) [[unlikely]]
      return error_value(module);
    auto inside = expr->value;
    if (! members.empty() && ! ast::is<ast::Call>(inside) && ! takes_room(inside)) {
      // A value of a type with an `each` takes room where it is walked: the
      // walk is `T.each(v)`, whose frame `in` says where to keep.
      auto call = each_call(builder, inside, hint_of(inside), inside->span);
      if (call.is_refused()) [[unlikely]]
        return error_value(module);
      if (auto made = call.get<ast::Call>(); made != nullptr) {
        auto walked = syntax.copy(*expr);
        walked->value = made;
        if (pick_in == expr)
          pick_in = walked;
        expr = walked;
        inside = made;
      }
    }
    if ((ast::is<ast::Unary>(inside) || ast::is<ast::Binary>(inside) || ast::is<ast::Fresh>(inside))
        && ! operators.empty() && operator_takes_an_arena(inside)) {
      // A program's own operator, handed the arena as its last operand: the
      // place and not the name, and one held by reference as it is.
      const ast::Expr* handed = expr->arena;
      if (! held_by_reference(expr->arena)) {
        auto address = syntax.make<ast::AddressOf>(expr->arena->span);
        address->operand = expr->arena;
        address->mutable_ = true;
        handed = address;
      }
      last_made_in.reset();
      ir::Value* found;
      {
        // And what its operands take room from where they name none.
        Setting outer_arena(out_of_arena, place);
        Setting outer_arenas(out_of_arenas, arenas_in(expr->arena));
        auto operands = operands_of(inside);
        operands.push_back(handed);
        found = operator_call(builder, glyph_of(inside), operands, expr->span, expected);
      }
      if (last_made_in.has_value())
        // The operator says its answer is made in the arena it is handed.
        made_from[id_of(expr)] = { expr, *last_made_in };
      return found;
    }
    if (auto call = ast::as<ast::Call>(inside); call != nullptr && generator_named(call->callee) != nullptr)
      return generator_in(builder, expr, place, expected);
    if (auto handing = handing_callee(inside); handing.has_value()) [[unlikely]] {
      diags.emit(D::LANG_HANDED_OUT_IN_AFTER_CALL, expr->span, { { "name", *handing } });
      return error_value(module);
    }
    bool calling = ast::is<ast::Call>(inside);
    if (! calling && ! takes_room(inside)) [[unlikely]] {
      diags.emit(D::LANG_ALLOCATED_NOT_A_PLACE, expr->span, { { "found", about_the_shape(inside) } });
      return error_value(module);
    }
    ir::Value* found;
    {
      Setting outer_arena(out_of_arena, place);
      Setting outer_arenas(out_of_arenas, arenas_in(expr->arena));
      found = lower_expr(builder, inside, expected);
    }
    if (calling && ! takes_room(inside) && ! arena_in_force.contains(id_of(inside))
        && value_type_of(found) != ir::error_type()) [[unlikely]] {
      // A call takes room from an arena where it leaves its arena to the one
      // it stands in, and from nothing `in` could name otherwise.
      diags.emit(D::LANG_ALLOCATED_NOT_A_PLACE, expr->span, { { "found", about_the_shape(inside) } });
      return error_value(module);
    }
    auto held = value_type_of(found);
    if (auto array = ir::as<ir::ArrayType>(held);
        ast::is<ast::ArrayLit>(inside) && array != nullptr && ! owned::points(array->element)) [[unlikely]] {
      // An array is room in the frame, so `in` says which allocator what its
      // elements point into comes from -- and these point nowhere.
      diags.emit(D::LANG_ALLOCATED_NOT_A_PLACE, expr->span, { { "found", "an array of values that point nowhere" } });
      return error_value(module);
    }
    return found;
  }


  ir::Value* Checker::lower_static(ir::Builder& builder, const ast::Expr* whole, const ast::Expr* written,
                                   const ir::Type* expected)
  {
    auto ty = expected;
    if (auto array = ir::as<ir::ArrayType>(ty); array != nullptr && ! array->fixed()) {
      // What is wanted does not say the length, which what is written does.
      std::optional<std::vector<std::int64_t>> shape;
      if (auto literal = ast::as<ast::ArrayLit>(written); literal != nullptr)
        shape = shape_of_literal(literal);
      if (shape.has_value() && shape->size() == array->shape.size()) {
        std::vector<std::optional<std::uint64_t>> dimensions;
        for (auto one : *shape)
          dimensions.push_back(std::uint64_t(one));
        ty = ir::array_type(array->element, std::move(dimensions));
      } else
        ty = nullptr;
    } else if (ty == nullptr)
      // Nothing around says the type: what is written says it.
      ty = static_type(written).type();
    if (ty == nullptr || ty == ir::error_type()) [[unlikely]] {
      if (ty == nullptr)
        diags.emit(D::IMPL_UNIMPLEMENTED_FEATURE, whole->span,
                   { { "feature", "a value in the image whose type nothing around it says" } });
      return error_value(module);
    }
    auto set = ir::as<ir::SetType>(ty);
    auto dict = ir::as<ir::DictType>(ty);
    if ((set != nullptr && set->is_mutable) || (dict != nullptr && dict->is_mutable)) [[unlikely]] {
      diags.emit(D::LANG_STATIC_CHANGES, whole->span, { { "name", ty->written() } });
      return error_value(module);
    }
    ir::Value* found;
    {
      Setting outer(for_the_image, true);
      auto node = syntax.make<ast::VarDef>(written->span);
      node->name = static_name;
      node->name_span = written->span;
      node->value = written;
      found = constant_value(node, ty);
    }
    if (found == nullptr || ! found->is_const()) [[unlikely]]
      return error_value(module);
    made_from[id_of(whole)] = { whole, KeySet{} };
    auto span = whole->span;
    switch (found->vkind) {
    case ir::ValueKind::array_const:
    case ir::ValueKind::record_const: {
      // A place, as an array or a record always is: the variable is it.
      auto at = builder.address(image_variable(ty, found), span);
      if (auto record = ir::as<ir::ProductType>(ty); record != nullptr)
        return record_from(builder, at, record, span);
      return builder.cast(ir::CastKind::bitcast, at, ty, span);
    }
    case ir::ValueKind::parts_const: {
      auto parts = static_cast<const ir::PartsConst*>(found);
      auto pieces = ir::parts_of(ty);
      std::vector<ir::Value*> values;
      for (std::size_t at = 0; at < parts->parts.size() && at < pieces.size(); ++at)
        values.push_back(from_the_image(builder, parts->parts[at], pieces[at], span));
      return builder.make_tuple(values, ty, span);
    }
    case ir::ValueKind::address_const:
      return from_the_image(builder, found, ty, span);
    default:
      return found;
    }
  }


  ir::Value* Checker::from_the_image(ir::Builder& builder, const ir::Value* part, const ir::Type* ty, Span span)
  {
    if (part->vkind == ir::ValueKind::address_const) {
      auto address = static_cast<const ir::AddressConst*>(part);
      if (address->target == nullptr)
        return builder.cast(ir::CastKind::bitcast, builder.int_const(ir::u64(), 0), ty, span);
      contract_assert(address->target->vkind == ir::ValueKind::global);
      return builder.cast(ir::CastKind::bitcast, builder.address(used(address->target), span), ty, span);
    }
    return used(part);
  }


  ir::Value* Checker::lower_address_of(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", address_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return error_value(module);
    }
    auto place = lower_expr(builder, expr->args[0], nullptr);
    auto ty = value_type_of(place);
    if (ty == ir::error_type()) [[unlikely]]
      return error_value(module);
    if (! ir::is<ir::PtrType>(ty)) [[unlikely]] {
      diags.emit(D::LANG_ADDRESS_OF_NOT_A_REFERENCE, expr->args[0]->span, { { "found", ty->written() } });
      return error_value(module);
    }
    auto found = builder.cast(ir::CastKind::bitcast, place, ir::u64(), expr->span);
    if (! accepts(expected, ir::u64())) [[unlikely]] {
      report_mismatch(expr->span, ir::u64(), expected);
      return error_value(module);
    }
    return found;
  }


} // namespace pl4g::sema
