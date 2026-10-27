// The checker: `match`, the statements that bind and assign names, and the
// dispatch over expressions with the operators it hands to nobody else --
// comparisons, the logical ones, `?` and `??`, `⍴`, `⊥`, `∣`, powers and joins.
//
// A port of pypl4g's sema/check.py, lines 20793 to 23412.
#include "sema/check.hh"

#include <algorithm>
#include <format>
#include <iterator>
#include <limits>

#include "diag/ids.hh"
#include "ir/printer.hh"
#include "sema/lists.hh"
#include "sema/owned.hh"
#include "sema/strings.hh"


namespace pl4g::sema {

  namespace {

    // The names of front/token.py this part reads.
    constexpr std::string_view wildcard_name = "_";
    constexpr std::string_view bottom_glyph = "\N{UP TACK}";
    constexpr std::string_view empty_arena_name = "\N{APL FUNCTIONAL SYMBOL QUAD}arena";
    constexpr std::string_view tolerance_name = "\N{APL FUNCTIONAL SYMBOL QUAD}tolerance";

    const ir::Type* error_ty() { return ir::error_type(); }

    // The token the memory chain began with, at the top of the entry block: what a
    // way that wrote nothing hands a join where the other way began the chain.
    ir::Value* chain_start(ir::Builder& builder)
    {
      auto first = builder.function().blocks[0]->insts[0];
      contract_assert(first->kind == ir::InstKind::MemStartInst);
      return first;
    }
    const ir::Type* void_ty() { return ir::void_type(); }
    const ir::Type* bool_ty() { return ir::bool_type(); }

    // A decimal of an integer, as Python's `str` writes one.
    std::string decimal(__int128 value) { return ir::decimal(value); }

    // The digits of a number too large for the compiler's integers, as
    // Python's `str` would write them: read off the source, where they are
    // written in decimal, and said to be large otherwise.
    std::string big_decimal(const SourceManager* sources, Span span)
    {
      std::string digits;
      if (sources != nullptr)
        if (auto text = sources->snippet(span)) {
          for (char c : *text)
            if (c >= '0' && c <= '9')
              digits += c;
            else if (c == '_')
              continue;
            else
              break;
          if (text->size() > 1 && (*text)[0] == '0' && ((*text)[1] == 'x' || (*text)[1] == 'b' || (*text)[1] == 'o'))
            digits.clear();
        }
      return digits.empty() ? std::string("a number too large to write") : digits;
    }

    // `value ** times` in decimal, for a number the compiler's integers do
    // not hold: Python's integers are unbounded and say it.
    std::string power_decimal(__int128 value, __int128 times)
    {
      bool negative = value < 0 && (times & 1) != 0;
      unsigned __int128 magnitude = value < 0 ? -static_cast<unsigned __int128>(value) : value;
      // Little-endian decimal digits.
      std::vector<unsigned> digits{ 1 };
      std::vector<unsigned> factor;
      for (auto m = magnitude; m != 0; m /= 10)
        factor.push_back(static_cast<unsigned>(m % 10));
      for (__int128 i = 0; i < times; ++i) {
        std::vector<unsigned> product(digits.size() + factor.size(), 0);
        for (std::size_t a = 0; a < digits.size(); ++a)
          for (std::size_t b = 0; b < factor.size(); ++b)
            product[a + b] += digits[a] * factor[b];
        unsigned carry = 0;
        for (auto& d : product) {
          d += carry;
          carry = d / 10;
          d %= 10;
        }
        while (carry != 0) {
          product.push_back(carry % 10);
          carry /= 10;
        }
        while (product.size() > 1 && product.back() == 0)
          product.pop_back();
        digits = std::move(product);
      }
      std::string text = negative ? "-" : "";
      for (auto it = digits.rbegin(); it != digits.rend(); ++it)
        text += static_cast<char>('0' + *it);
      return text;
    }

    // `base ** times` where it fits in 128 bits.
    std::optional<__int128> power_of(__int128 base, __int128 times)
    {
      __int128 answer = 1;
      for (__int128 i = 0; i < times; ++i)
        if (__builtin_mul_overflow(answer, base, &answer))
          return std::nullopt;
      return answer;
    }

    // One arm to run, as Python's `_ArmPlan(...)` writes one.
    ArmPlan arm_plan(const ast::Block* body, ir::BasicBlock* block = nullptr, std::optional<ArmBinding> binds = {},
                     bool carried = false, bool payload = false)
    {
      ArmPlan plan;
      plan.body = body;
      plan.block = block;
      plan.binds = std::move(binds);
      plan.carried = carried;
      plan.payload = payload;
      return plan;
    }

    // What each operator a set answers comes to, as walks of the two tables:
    // which table is walked, and what is wanted of the answer.
    const std::vector<std::pair<bool, std::int64_t>>& set_walks(ast::BinaryOp op)
    {
      static const std::map<ast::BinaryOp, std::vector<std::pair<bool, std::int64_t>>> walks{
        { ast::BinaryOp::bit_or, { { true, tables::want_either }, { false, tables::want_either } } },
        { ast::BinaryOp::bit_and, { { true, tables::want_present } } },
        { ast::BinaryOp::bit_xor, { { true, tables::want_absent }, { false, tables::want_absent } } },
        { ast::BinaryOp::subtract, { { true, tables::want_absent } } },
      };
      return walks.at(op);
    }

  } // namespace


  ir::Value* Checker::fresh_undef(const ir::Type* ty)
  {
    return const_cast<ir::UndefConst*>(module.constant<ir::UndefConst>(ty));
  }


  // -- match --------------------------------------------------------------------

  ir::Value* Checker::lower_match(ir::Builder& builder, const ast::Match* stmt, ir::Function* func,
                                  const ir::Type* wanted, bool produces)
  {
    // A sum that is somewhere -- what a reference names, an element, a field
    // -- is taken apart where it is: its tag is read there before any arm runs
    // and the part the chosen arm binds as that arm begins, so a copy of the
    // whole would be bytes nothing else reads.
    auto asked = id_of(stmt->subject);
    match_in_place.insert(asked);
    ir::Value* subject;
    try {
      subject = lower_expr(builder, stmt->subject, nullptr);
    } catch (...) {
      match_in_place.erase(asked);
      throw;
    }
    match_in_place.erase(asked);
    bool in_place = matched_in_place.contains(asked);
    matched_in_place.erase(asked);
    auto ty = value_type_of(subject);
    if (ty == error_ty())
      return fresh_undef(error_ty());
    if (! ir::is<ir::ResultType>(ty) && ! ir::is<ir::SumType>(ty) && ! ir::is<ir::EnumType>(ty)) [[unlikely]] {
      diags.emit(D::LANG_MATCH_NOT_A_CHOICE, stmt->subject->span, { { "found", ty->written() } });
      return fresh_undef(error_ty());
    }
    auto taken = matched_arms(stmt, ty);
    if (! taken)
      return fresh_undef(error_ty());
    if (auto sum = ir::as<ir::SumType>(ty))
      return lower_match_on_sum(builder, stmt, func, subject, sum, *taken, wanted, produces, in_place);
    if (auto en = ir::as<ir::EnumType>(ty))
      return lower_match_on_enum(builder, stmt, func, subject, en, *taken, wanted, produces);
    return lower_match_on_result(builder, stmt, func, subject, ir::as<ir::ResultType>(ty), *taken, wanted, produces);
  }

  std::vector<std::tuple<std::string, const ir::Type*, bool>> Checker::alternatives(const ir::Type* ty)
  {
    if (auto result = ir::as<ir::ResultType>(ty))
      return { { result->ok->written(), result->ok, false }, { std::string(bottom_glyph), result->err, true } };
    if (auto en = ir::as<ir::EnumType>(ty)) {
      // One alternative per number, not per name: two names given one number
      // are one value.
      Dict<std::int64_t, std::string> seen;
      for (std::size_t i = 0; i < en->members.size() && i < en->values.size(); ++i)
        seen.setdefault(en->values[i], en->members[i]);
      std::vector<std::tuple<std::string, const ir::Type*, bool>> found;
      for (const auto& [_, member] : seen)
        found.emplace_back(member, nullptr, false);
      return found;
    }
    auto sum = ir::as<ir::SumType>(ty);
    std::vector<std::tuple<std::string, const ir::Type*, bool>> found;
    for (const auto& [_, variant] : sum->variants)
      found.emplace_back(variant->written(), variant, false);
    return found;
  }

  std::optional<std::vector<std::tuple<const ast::MatchArm*, KeySet>>> Checker::matched_arms(const ast::Match* stmt,
                                                                                            const ir::Type* ty)
  {
    auto alts = alternatives(ty);
    std::vector<std::tuple<const ast::MatchArm*, KeySet>> found;
    KeySet settled;
    bool spoiled = false;
    for (auto arm : stmt->arms) {
      if (arm->pattern->wildcard) {
        if (arm->pattern->name) [[unlikely]] {
          diags.emit(D::LANG_MATCH_WILDCARD_TAKES_NO_NAME, arm->pattern->span);
          spoiled = true;
          continue;
        }
        KeySet left;
        for (std::int64_t i = 0; i < std::int64_t(alts.size()); ++i)
          if (! settled.contains(i))
            left.insert(i);
        if (left.empty()) [[unlikely]] {
          diags.emit(D::LANG_MATCH_ARM_NEVER_RUNS, arm->pattern->span);
          spoiled = true;
          continue;
        }
        settled.insert(left.begin(), left.end());
        found.emplace_back(arm, left);
        continue;
      }
      auto index = alternative_of(arm->pattern, ty, alts);
      if (! index) {
        spoiled = true;
        continue;
      }
      if (settled.contains(*index)) [[unlikely]] {
        diags.emit(D::LANG_MATCH_REPEATED_ARM, arm->pattern->span);
        spoiled = true;
        continue;
      }
      settled.insert(*index);
      auto carried = std::get<1>(alts[*index]);
      if (arm->pattern->name && (carried == nullptr || carried == void_ty())) [[unlikely]] {
        diags.emit(D::LANG_MATCH_BINDS_NOTHING, arm->pattern->span, { { "name", *arm->pattern->name } });
        spoiled = true;
        continue;
      }
      found.emplace_back(arm, KeySet{ *index });
    }
    for (std::int64_t index = 0; index < std::int64_t(alts.size()); ++index)
      if (! settled.contains(index)) [[unlikely]] {
        diags.emit(D::LANG_MATCH_NOT_EXHAUSTIVE, stmt->span, { { "missing", std::get<0>(alts[index]) } });
        spoiled = true;
      }
    if (auto en = ir::as<ir::EnumType>(ty); en != nullptr && en->flag
        && std::ranges::none_of(found, [](const auto& one) { return std::get<0>(one)->pattern->wildcard; })) {
      // Its values combine, so a value of it may be one no single name stands
      // for.
      diags.emit(D::LANG_MATCH_FLAG_NEEDS_A_REST, stmt->span, { { "type", ty->written() } });
      spoiled = true;
    }
    if (spoiled)
      return std::nullopt;
    return found;
  }

  std::optional<std::int64_t> Checker::alternative_of(const ast::Pattern* pattern, const ir::Type* ty,
                                                      std::span<const std::tuple<std::string, const ir::Type*, bool>> alts)
  {
    if (pattern->type == nullptr) {
      if (! ir::is<ir::ResultType>(ty)) [[unlikely]] {
        diags.emit(D::LANG_MATCH_BOTTOM_NEEDS_A_RESULT, pattern->span, { { "found", ty->written() } });
        return std::nullopt;
      }
      for (std::size_t i = 0; i < alts.size(); ++i)
        if (std::get<2>(alts[i]))
          return std::int64_t(i);
      return std::nullopt;
    }
    if (auto en = ir::as<ir::EnumType>(ty)) {
      // The alternatives of an enumeration are its values.
      auto written = ast::as<ast::TypeRef>(pattern->type);
      if (written == nullptr) [[unlikely]] {
        diags.emit(D::LANG_ENUM_UNKNOWN_VALUE, pattern->span, { { "name", "that" }, { "type", ty->written() } });
        return std::nullopt;
      }
      auto index = en->index_of(written->name);
      if (! index || written->module || written->result) [[unlikely]] {
        diags.emit(D::LANG_ENUM_UNKNOWN_VALUE, pattern->span, { { "name", written->name }, { "type", ty->written() } });
        return std::nullopt;
      }
      // Which alternative is which number.
      auto wanted = en->values[*index];
      std::vector<std::int64_t> distinct;
      for (auto one : en->values)
        if (std::ranges::find(distinct, one) == distinct.end())
          distinct.push_back(one);
      return std::int64_t(std::ranges::find(distinct, wanted) - distinct.begin());
    }
    auto named = resolve_type(pattern->type);
    if (named == error_ty())
      return std::nullopt;
    for (std::size_t index = 0; index < alts.size(); ++index)
      if (! std::get<2>(alts[index]) && std::get<1>(alts[index]) == named)
        return std::int64_t(index);
    diags.emit(D::LANG_MATCH_UNKNOWN_ALTERNATIVE, pattern->span, { { "type", named->written() }, { "found", ty->written() } });
    return std::nullopt;
  }

  ir::Value* Checker::lower_match_on_result(ir::Builder& builder, const ast::Match* stmt, ir::Function* func,
                                            ir::Value* subject, const ir::ResultType* ty,
                                            std::span<const std::tuple<const ast::MatchArm*, KeySet>> taken,
                                            const ir::Type* wanted, bool produces)
  {
    if (taken.size() == 1)
      return run_arms(builder, stmt, func, std::vector<ArmPlan>{ arm_plan(std::get<0>(taken[0])->body) },
                      wanted, produces);
    std::map<std::int64_t, const ast::MatchArm*> holders;
    for (const auto& [arm, indices] : taken)
      for (auto index : indices)
        holders[index] = arm;
    auto answered = builder.new_block("answer");
    auto failed = builder.new_block("error");
    builder.condbr(builder.failed(subject, stmt->span), failed, answered, {}, {}, stmt->span);
    auto first = holders.at(0);
    auto second = holders.at(1);
    std::optional<ArmBinding> binds;
    if (first->pattern->name)
      binds = ArmBinding{ std::string(*first->pattern->name), first->pattern->name_span, first->pattern->span, subject,
                          ty->ok };
    // The error's arm binds too, where the error carries something.
    std::optional<ArmBinding> carried;
    if (second->pattern->name && ty->err != nullptr)
      carried = ArmBinding{ std::string(*second->pattern->name), second->pattern->name_span, second->pattern->span,
                            subject, ty->err };
    else if (second->pattern->name) [[unlikely]] {
      diags.emit(D::LANG_MATCH_BOTTOM_CARRIES_NOTHING, second->pattern->span, { { "found", ty->written() } });
      return fresh_undef(error_ty());
    }
    return run_arms(builder, stmt, func,
                    std::vector<ArmPlan>{ arm_plan(first->body, answered, binds),
                                          arm_plan(second->body, failed, carried, true) },
                    wanted, produces);
  }

  ir::Value* Checker::lower_match_on_sum(ir::Builder& builder, const ast::Match* stmt, ir::Function* func,
                                         ir::Value* subject, const ir::SumType* ty,
                                         std::span<const std::tuple<const ast::MatchArm*, KeySet>> taken,
                                         const ir::Type* wanted, bool produces, bool in_place)
  {
    if (taken.size() == 1 && std::get<0>(taken[0])->pattern->wildcard)
      return run_arms(builder, stmt, func, std::vector<ArmPlan>{ arm_plan(std::get<0>(taken[0])->body) },
                      wanted, produces);
    auto place = builder.cast(ir::CastKind::bitcast, subject, ir::ptr_type(ty, true), stmt->span);
    auto tag = builder.load(tag_place(builder, place, ty, stmt->span), stmt->span);
    const ast::MatchArm* fallback = std::get<0>(taken.back());
    for (const auto& [arm, _] : taken)
      if (arm->pattern->wildcard) {
        fallback = arm;
        break;
      }
    std::unordered_map<const ast::MatchArm*, ir::BasicBlock*> blocks;
    for (const auto& [arm, _] : taken)
      blocks[arm] = builder.new_block("case");
    for (const auto& [arm, indices] : taken) {
      if (arm == fallback)
        continue;
      for (auto index : indices) {
        auto following = builder.new_block("otherwise");
        auto number = builder.int_const(ir::u8(), index);
        auto same = builder.compare(ir::CmpPred::eq, tag, number, arm->pattern->span);
        builder.condbr(same, blocks[arm], following, {}, {}, arm->pattern->span);
        builder.position_at(following);
      }
    }
    builder.br(blocks[fallback], {}, stmt->span);
    std::vector<ArmPlan> plan;
    for (const auto& [arm, indices] : taken) {
      std::optional<ArmBinding> binds;
      if (arm->pattern->name) {
        // One part, since an arm that binds names one type.
        auto held = ty->variants[*indices.begin()].second;
        binds = ArmBinding{ std::string(*arm->pattern->name), arm->pattern->name_span, arm->pattern->span, place, held };
      }
      plan.push_back(arm_plan(arm->body, blocks[arm], binds, false, true));
      plan.back().in_place = in_place;
    }
    return run_arms(builder, stmt, func, plan, wanted, produces);
  }

  ir::Value* Checker::lower_match_on_enum(ir::Builder& builder, const ast::Match* stmt, ir::Function* func,
                                          ir::Value* subject, const ir::EnumType* ty,
                                          std::span<const std::tuple<const ast::MatchArm*, KeySet>> taken,
                                          const ir::Type* wanted, bool produces)
  {
    const ast::MatchArm* fallback = std::get<0>(taken.back());
    for (const auto& [arm, _] : taken)
      if (arm->pattern->wildcard) {
        fallback = arm;
        break;
      }
    // The way taken when no case matches gets its block first, so that it is
    // the block laid out after this one.
    std::unordered_map<const ast::MatchArm*, ir::BasicBlock*> blocks;
    blocks[fallback] = builder.new_block("case");
    for (const auto& [arm, _] : taken)
      if (! blocks.contains(arm))
        blocks[arm] = builder.new_block("case");
    std::vector<std::int64_t> distinct;
    for (auto one : ty->values)
      if (std::ranges::find(distinct, one) == distinct.end())
        distinct.push_back(one);
    std::vector<std::pair<__int128, ir::BasicBlock*>> cases;
    for (const auto& [arm, indices] : taken) {
      if (arm == fallback)
        continue;
      for (auto index : indices)
        cases.emplace_back(distinct[index], blocks[arm]);
    }
    builder.switch_(subject, cases, blocks[fallback], stmt->span);
    std::vector<ArmPlan> plan;
    for (const auto& [arm, _] : taken)
      plan.push_back(arm_plan(arm->body, blocks[arm]));
    return run_arms(builder, stmt, func, plan, wanted, produces);
  }

  ir::Value* Checker::run_arms(ir::Builder& builder, const ast::Expr* stmt, ir::Function* func,
                               std::span<const ArmPlan> plan, const ir::Type* wanted, bool produces,
                               ir::BasicBlock* otherwise, ir::Value* otherwise_memory)
  {
    auto joined_block = builder.new_block("matched");
    struct Before {
      Local* local;
      ir::Value* value;
      Span value_span;
    };
    std::vector<Before> before;
    for (auto& scope : scopes)
      for (auto& [_, local] : scope)
        before.push_back({ local, local->value, local->value_span });
    // What a name was made in differs from arm to arm the way its value does.
    auto state = provenance();
    // Where this is what the function answers, each arm answers for itself.
    auto answering_func = answer_arms;
    answer_arms = nullptr;
    std::vector<Provenance> reaching;
    std::vector<KeySet> given_back;
    KeySet yielded;
    auto outer_carried = carried;
    for (const auto& one : before)
      carried.insert(id_of(one.local));
    auto before_memory = builder.memory();
    if (otherwise_memory == nullptr)
      otherwise_memory = before_memory;
    struct Outcome {
      ir::BasicBlock* block;
      std::unordered_map<const Local*, ir::Value*> moved;
      ir::Value* token;
      ir::Value* given;
    };
    std::vector<Outcome> outcomes;
    Dict<std::int64_t, Local*> changed;
    bool touched = false;
    const ir::Type* answer = wanted;
    for (const auto& arm : plan) {
      if (arm.block != nullptr)
        builder.position_at(arm.block);
      builder.set_memory(arm.memory != nullptr ? arm.memory : before_memory);
      push_scope();
      if (arm.binds) {
        const auto& b = *arm.binds;
        auto bound = arm.payload   ? payload_from(builder, b.value, b.ty, b.value_span, arm.in_place)
                     : arm.carried ? builder.error(b.value, b.ty, b.value_span)
                                   : builder.unwrap(b.value, b.ty, b.value_span);
        name_value(bound, b.name, b.span);
        bind_local(b.name, bound, b.span, false, b.value_span, false, &builder);
        if (auto match = ast::as<ast::Match>(stmt))
          taken_out_of(match->subject, std::vector<std::string>{ b.name });
      }
      put_back(state);
      auto outer_yield = yield_answer;
      yield_answer = answering_func;
      ir::Value* given = nullptr;
      try {
        given = lower_block(builder, arm.body, func, false, answer, produces);
      } catch (...) {
        yield_answer = outer_yield;
        throw;
      }
      yield_answer = outer_yield;
      auto stmts = arm.body->stmts();
      const ast::Stmt* last = stmts.empty() ? nullptr : stmts.back();
      if (answering_func != nullptr && given != nullptr && ! builder.is_terminated())
        if (auto expr_stmt = ast::as<ast::ExprStmt>(last);
            expr_stmt != nullptr && ! ast::is<ast::If>(expr_stmt->value) && ! ast::is<ast::Match>(expr_stmt->value))
          given = answered(builder, expr_stmt->value, given, answering_func, expr_stmt->span);
      auto emptied_here = last_emptied;
      if (produces && given != nullptr && ! builder.is_terminated()) {
        // What this arm comes to, asked while the names it bound are still in
        // reach.
        if (auto expr_stmt = ast::as<ast::ExprStmt>(last)) {
          auto found = arenas_in(expr_stmt->value);
          yielded.insert(found.begin(), found.end());
        } else if (last != nullptr) {
          auto found = arenas_in(arm.body);
          yielded.insert(found.begin(), found.end());
        }
      }
      if (produces && given != nullptr && answer == nullptr) {
        // Nothing said what the arms answer with, so the first one that does
        // say.
        auto found = value_type_of(given);
        answer = found != error_ty() ? found : nullptr;
      }
      std::unordered_map<const Local*, ir::Value*> moved;
      for (const auto& one : before)
        if (one.local->value != one.value)
          moved[one.local] = one.local->value;
      for (const auto& one : before)
        if (moved.contains(one.local))
          changed[id_of(one.local)] = one.local;
      pop_scope(! builder.is_terminated() ? std::optional<Span>(arm.body->span) : std::nullopt);
      if (! builder.is_terminated() && builder.block() != nullptr) {
        auto token = builder.memory();
        outcomes.push_back({ builder.block(), std::move(moved), token, given });
        touched = touched || token != before_memory;
        reaching.push_back(provenance());
        given_back.push_back(emptied_here);
      }
      for (const auto& one : before) {
        one.local->value = one.value;
        one.local->value_span = one.value_span;
      }
    }
    if (otherwise != nullptr) {
      // A way through that runs no arm at all.
      outcomes.push_back({ otherwise, {}, otherwise_memory, nullptr });
      reaching.push_back(state);
      given_back.push_back({});
    }
    carried = outer_carried;
    put_back(! reaching.empty() ? joined(state, reaching) : state);
    if (! given_back.empty() && ! emptied.empty()) {
      // Given back along every way that reaches the join is given back.
      KeySet common = given_back.front();
      for (std::size_t i = 1; i < given_back.size(); ++i) {
        KeySet both;
        std::ranges::set_intersection(common, given_back[i], std::inserter(both, both.end()));
        common = std::move(both);
      }
      emptied.back().insert(common.begin(), common.end());
    }
    if (produces)
      made_from[id_of(stmt)] = { stmt, yielded };
    if (outcomes.empty()) {
      // Every arm left the function, so nothing arrives at the join.
      std::erase_if(func->blocks, [&](const auto& one) { return one.get() == joined_block; });
      builder.set_memory(before_memory);
      return fresh_undef(answer != nullptr ? answer : error_ty());
    }
    std::vector<Local*> merged;
    for (const auto& [_, local] : changed)
      merged.push_back(local);
    std::vector<ir::BlockParam*> params;
    for (auto local : merged)
      params.push_back(joined_block->add_param(value_type_of(local->value), local->name));
    ir::BlockParam* memory = touched ? joined_block->add_param(ir::mem_type(), "mem") : nullptr;
    ir::BlockParam* produced = produces && answer != nullptr ? joined_block->add_param(answer, "answer") : nullptr;
    std::unordered_map<const Local*, ir::Value*> held_before;
    for (const auto& one : before)
      held_before[one.local] = one.value;
    for (auto& outcome : outcomes) {
      builder.position_at(outcome.block);
      std::vector<ir::Value*> args;
      for (auto local : merged) {
        auto it = outcome.moved.find(local);
        args.push_back(it != outcome.moved.end() ? it->second : held_before.at(local));
      }
      if (memory != nullptr)
        args.push_back(outcome.token);
      if (produced != nullptr)
        args.push_back(outcome.given != nullptr ? outcome.given : fresh_undef(produced->ty));
      builder.br(joined_block, args, stmt->span);
    }
    builder.position_at(joined_block);
    for (std::size_t i = 0; i < merged.size(); ++i) {
      merged[i]->value = params[i];
      // The value it now stands for was given by the match.
      merged[i]->value_span = stmt->span;
      merged[i]->read = false;
    }
    // Where no way through wrote anything, the token is the one every way that
    // arrives carries.
    builder.set_memory(memory != nullptr ? memory : outcomes.back().token);
    return produced != nullptr ? produced : fresh_undef(void_ty());
  }

  void Checker::check_value_is_used(const ast::Expr* expr)
  {
    if (auto call = ast::as<ast::Call>(expr)) {
      answer_is_taken(call);
      return;
    }
    if (ast::is<ast::Take>(expr) || ast::is<ast::TakeAt>(expr) || ast::is<ast::TakeThrough>(expr)
        || ast::is<ast::Emit>(expr) || ast::is<ast::YieldValue>(expr))
      return;
    auto found = diags.emit(D::LANG_STMT_VALUE_DISCARDED, expr->span);
    if (auto binary = ast::as<ast::Binary>(expr); binary != nullptr && binary->op == ast::BinaryOp::equal)
      found.note(D::LANG_STMT_ASSIGNMENT_IS_AN_ARROW, expr->span);
  }

  void Checker::answer_is_taken(const ast::Call* expr)
  {
    auto func = callee_named(expr->callee);
    if (func == nullptr) {
      // Through a value: what its type says is what there is to go by.
      auto name = ast::as<ast::NameRef>(expr->callee);
      auto local = name != nullptr ? find_local(name->name) : nullptr;
      auto held = local != nullptr ? held_by(local) : nullptr;
      if (auto fty = ir::as<ir::FuncType>(held); fty != nullptr && fty->ret != void_ty() && ! fty->can_ignore)
        diags.emit(D::LANG_CALL_ANSWER_DROPPED, expr->span, { { "name", name->name } });
      return;
    }
    if (func->fty->ret == void_ty() || func->attrs.can_ignore)
      return;
    diags.emit(D::LANG_CALL_ANSWER_DROPPED, expr->span, { { "name", func->name } });
  }

  ir::Function* Checker::callee_named(const ast::Expr* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr)) {
      auto found = top->get(std::string(name->name));
      return found != nullptr ? found->get<ir::Function>() : nullptr;
    }
    return nullptr;
  }

  ir::Value* Checker::dropped(ir::Builder& builder, const ast::AssignStmt* node, bool wants_value)
  {
    auto value = lower_expr(builder, node->value, nullptr);
    auto ty = value_type_of(value);
    if (ty == error_ty())
      return nullptr;
    if (ty == void_ty()) [[unlikely]] {
      std::string name = "this";
      if (auto call = ast::as<ast::Call>(node->value))
        if (auto callee = callee_named(call->callee); callee != nullptr && callee_named(call->callee) != nullptr)
          name = callee->name;
      diags.emit(D::LANG_WILDCARD_TAKES_A_VALUE, node->span, { { "name", name } });
      return nullptr;
    }
    if (wants_value) [[unlikely]] {
      // What this statement leaves behind is nothing anybody may read.
      diags.emit(D::LANG_WILDCARD_IS_NOT_READ, node->span);
      return nullptr;
    }
    return nullptr;
  }

  void Checker::lower_local(ir::Builder& builder, const ast::VarDef* node)
  {
    bind_attributes(node->attrs, AttrTarget::variable);
    if (node->name == wildcard_name) [[unlikely]] {
      diags.emit(D::LANG_WILDCARD_IS_NOT_DEFINED, node->span);
      return;
    }
    if (! image_definition(node, nullptr)) {
      bind_local(node->name, fresh_undef(error_ty()), node->name_span, node->mutable_, node->span);
      return;
    }
    if (auto array = ast::as<ast::ArrayTypeRef>(node->type); array != nullptr && array->element == nullptr
        && node->more.empty()) {
      lower_array_definition(builder, node, array);
      return;
    }
    if (node->type == nullptr && ! says_its_type(node->value)) {
      // Nothing written and nothing to read off the value: what it turns out
      // to be is what the name stands for.
      lower_derived(builder, node);
      return;
    }
    auto declared = node->type != nullptr ? resolve_type(node->type) : variable_type(node);
    if (auto iter = ir::as<ir::IterType>(declared); iter != nullptr && node->more.empty()) {
      lower_iterator_definition(builder, node, iter);
      return;
    }
    if (declared == nullptr || ! literal_matches(node, declared)) {
      // Binding the name anyway keeps every later mention of it from
      // reporting the same thing again as undefined.
      bind_local(node->name, fresh_undef(error_ty()), node->name_span, node->mutable_, node->span);
      return;
    }
    if (auto record = ir::as<ir::ProductType>(declared); record != nullptr && node->more.empty()) {
      // Built where it will live rather than made and then copied.
      bool out = hands_out && declared == ir::arena_type() && node->name == std::get<0>(*hands_out)
                 && std::int64_t(scopes.size()) == std::get<2>(*hands_out);
      // The arena this body hands out lives where the caller keeps it.
      auto place = out ? std::get<1>(*hands_out) : builder.frame(declared, node->span);
      initializing = std::string(node->name);
      try {
        build_record(builder, place, node->value, record, node->span);
      } catch (...) {
        initializing.reset();
        throw;
      }
      initializing.reset();
      bind_local(node->name, place, node->name_span, node->mutable_, node->span, false, &builder, declared);
      if (! out && declared == ir::arena_type() && ! deferred.empty())
        if (auto name = ast::as<ast::NameRef>(node->value); name != nullptr && name->name == empty_arena_name)
          give_back_on_leaving(node);
      if (out) {
        auto local = find_local(node->name);
        local->handed_out = true;
        hands_out_local = local;
        answer_made_in = std::tuple<KeySet, std::string>{ KeySet{ id_of(local) },
                                                          std::format("'{}'", node->name) };
      }
      return;
    }
    if (auto array = ir::as<ir::ArrayType>(declared);
        array != nullptr && array->fixed() && node->more.empty() && given_a_view(node->value)) {
      lower_view_copied(builder, node, array);
      return;
    }
    initializing = std::string(node->name);
    ir::Value* value = nullptr;
    try {
      value = lower_into(builder, node->value, declared, node->span);
    } catch (...) {
      initializing.reset();
      throw;
    }
    initializing.reset();
    if (! node->more.empty()) {
      bind_apart(builder, node, value);
      return;
    }
    auto bound = owned_array(builder, node, as_declared(value, declared));
    kept_by_a_name(declared, node->name_span);
    name_value(bound, node->name, node->name_span);
    bind_local(node->name, bound, node->name_span, node->mutable_, node->span, false, &builder);
  }

  bool Checker::given_a_view(const ast::Expr* expr)
  {
    if (auto element = ast::as<ast::Element>(expr))
      return std::ranges::any_of(element->indices, [](const ast::Expr* one) { return ast::is<ast::Range>(one); });
    if (auto name = ast::as<ast::NameRef>(expr)) {
      auto local = find_local(name->name);
      if (local == nullptr)
        return false;
      auto held = ir::as<ir::ArrayType>(held_by(local));
      return held != nullptr && ! held->fixed();
    }
    return false;
  }

  void Checker::lower_view_copied(ir::Builder& builder, const ast::VarDef* node, const ir::ArrayType* declared)
  {
    // Giving an array to an array copies its elements, the two counts
    // compared while running where either is unknown.  A name whose type
    // states its shape owns elements that are one run, so this is a copy even
    // where nothing may write either side.
    initializing = std::string(node->name);
    ir::Value* room = nullptr;
    try {
      auto value = lower_expr(builder, node->value, nullptr);
      auto found = value_type_of(value);
      if (found != ir::error_type()) {
        auto made = builder.cast(ir::CastKind::bitcast, builder.frame(declared, node->span), declared, node->span);
        if (copy_elements(builder, made, declared, value, found, node->value->span))
          room = made;
      }
    } catch (...) {
      initializing.reset();
      throw;
    }
    initializing.reset();
    if (room == nullptr)
      room = fresh_undef(error_ty());
    kept_by_a_name(declared, node->name_span);
    name_value(room, node->name, node->name_span);
    bind_local(node->name, room, node->name_span, node->mutable_, node->span, false, &builder);
  }

  void Checker::lower_derived(ir::Builder& builder, const ast::VarDef* node)
  {
    initializing = std::string(node->name);
    auto outer_in = pick_in;
    auto outer_walked = pick_walked;
    pick_in = node->more.empty() ? node->value : nullptr;
    pick_walked = std::monostate{};
    ir::Value* value = nullptr;
    decltype(pick_walked) walked;
    try {
      value = lower_expr(builder, node->value, nullptr);
      walked = pick_walked;
    } catch (...) {
      initializing.reset();
      pick_in = outer_in;
      pick_walked = outer_walked;
      throw;
    }
    initializing.reset();
    pick_in = outer_in;
    pick_walked = outer_walked;
    if (! std::holds_alternative<std::monostate>(walked)) {
      // What a mask picks, with nothing asking for an array: the name is the
      // walk, described and not made.
      bind_iterator(builder, node, walked, nullptr);
      return;
    }
    if (! node->more.empty()) {
      bind_apart(builder, node, value);
      return;
    }
    value = owned_array(builder, node, value);
    kept_by_a_name(value_type_of(value), node->name_span);
    name_value(value, node->name, node->name_span);
    bind_local(node->name, value, node->name_span, node->mutable_, node->span, false, &builder);
  }

  void Checker::bind_apart(ir::Builder& builder, const ast::Node* node, ir::Value* value)
  {
    // A loop's names are never `mut`.
    auto def = ast::as<ast::VarDef>(node);
    auto loop = ast::as<ast::ForEach>(node);
    bool is_mutable = def != nullptr && def->mutable_;
    std::vector<std::tuple<std::string, Span>> names;
    if (def != nullptr) {
      names.emplace_back(std::string(def->name), def->name_span);
      for (const auto& one : def->more)
        names.emplace_back(std::string(one.name), one.span);
    } else {
      names.emplace_back(std::string(loop->name), loop->name_span);
      for (const auto& one : loop->more)
        names.emplace_back(std::string(one.name), one.span);
    }
    auto members = taken_apart(value, names, node->span);
    for (std::size_t index = 0; index < names.size(); ++index) {
      const auto& [name, where] = names[index];
      if (! members) {
        bind_local(name, fresh_undef(error_ty()), where, is_mutable, node->span);
        continue;
      }
      if (name == wildcard_name && loop != nullptr)
        // A part of a turn that is not wanted: nothing is bound.
        continue;
      auto part = member(builder, value, value_type_of(value), std::int64_t(index), node->span);
      name_value(part, name, where);
      bind_local(name, part, where, is_mutable, node->span, false, &builder);
    }
  }

  void Checker::name_value(ir::Value* value, std::string_view name, Span where)
  {
    if (value->vkind == ir::ValueKind::instruction && ! value->name_hint) {
      value->name_hint = std::string(name);
      value->name_span = where;
    } else if (value->vkind == ir::ValueKind::block_param && value->name_hint == "answer") {
      // What an `if` or a `match` comes to, joined: the name it is bound to is
      // a better one than the word the join was made with.
      value->name_hint = std::string(name);
      value->name_span = where;
    }
  }

  ir::Value* Checker::lower_assignment(ir::Builder& builder, const ast::AssignStmt* node, bool wants_value)
  {
    std::vector<std::string_view> all{ node->name };
    for (const auto& one : node->more)
      all.push_back(one.name);
    for (auto name : all) {
      auto target = find_local(name);
      if (target == nullptr)
        continue;
      // A slot nothing registered is no slot, and `_flow` notes nothing of it.
      if (auto key = slot_of_local.get(id_of(target)))
        flow(*key, node->more.empty() ? node->value : nullptr);
    }
    if (! node->more.empty()) {
      assign_apart(builder, node);
      return nullptr;
    }
    if (node->name == wildcard_name)
      return dropped(builder, node, wants_value);
    auto ref = syntax.make<ast::NameRef>(node->name_span);
    ref->name = node->name;
    if (lent_to_an_iterator(ref, node->span))
      return nullptr;
    auto local = find_local(node->name);
    if (local != nullptr && ir::is<ir::ArrayType>(held_by(local)))
      return array_assigned(builder, node, local, wants_value);
    if (local != nullptr && local->placed) {
      // A name with storage of its own is written the way a variable at the
      // top level is.
      if (! check_mutable(node, local->is_mutable, local->span))
        return nullptr;
      if (lent_out(node->name, node->span, true))
        return nullptr;
      if (! local->closer.empty() && local->owes) [[unlikely]] {
        diags.emit(D::LANG_CLOSABLE_OVERWRITTEN, node->span, { { "name", node->name }, { "type", local->held->written() } });
        return nullptr;
      }
      auto value = checked_value(builder, node, local->held);
      if (! local->closer.empty() && value_type_of(value) != error_ty()) {
        // Given a value again: it owes the close again.
        local->owes = true;
        local->ended_at.reset();
        local->ended_how.clear();
      }
      if (reaches_a_place(local->held) && outlives_it(value, local->depth, node->name, node->span))
        return nullptr;
      kept_by_a_name(local->held, local->span);
      // What it held goes, where it was its own, once what replaces it has
      // been worked out -- which may have read it.
      room_replaced(builder, local, node->span);
      if (auto record = ir::as<ir::ProductType>(local->held))
        record_into(builder, local->value, value, record, node->span);
      else
        builder.store(local->value, value, node->span);
      local->written = true;
      local->is_parameter = false;
      if (! wants_value)
        return nullptr;
      if (auto record = ir::as<ir::ProductType>(local->held))
        return record_from(builder, local->value, record, node->span);
      return builder.load(local->value, node->span);
    }
    if (local != nullptr) {
      if (! check_mutable(node, local->is_mutable, local->span))
        return nullptr;
      if (ir::is<ir::PtrType>(held_by(local)) && lent_out(node->name, node->span, true))
        // A copy of it is out, promised the place it named.
        return nullptr;
      auto value = checked_value(builder, node, local->value->ty);
      if (reaches_a_place(local->value->ty) && outlives_it(value, local->depth, node->name, node->span))
        return nullptr;
      kept_by_a_name(local->value->ty, local->span);
      // The value the name stood for is gone; if nothing read it, giving it
      // cannot have affected what the program does.
      if (! carried.contains(id_of(local)))
        // Inside an arm of a `match` the question cannot be answered here.
        report_unused(local);
      room_replaced(builder, local, node->span);
      local->value = value;
      local->value_span = node->span;
      local->read = wants_value;
      // What the name stands for is no longer what the caller gave.
      local->is_parameter = false;
      return value;
    }
    auto target = provided(node->name).get<ir::GlobalVar>();
    if (target == nullptr) [[unlikely]] {
      diags.emit(D::LANG_FILESTRUCT_UNDEFINED_NAME, node->name_span, { { "name", node->name } });
      return nullptr;
    }
    stored_into[id_of(node->value)] = "a variable at the top level";
    if (sealed(target)) [[unlikely]] {
      refuse_sealed(target, node->name, node->name_span);
      return nullptr;
    }
    if (! check_mutable(node, target->is_mutable(), target->span))
      return nullptr;
    if (read_only_table(target->value_type)) [[unlikely]] {
      // A name at the top level standing for a table nothing may put anything
      // in is not a place to put a different table either.
      diags.emit(D::LANG_PLACE_NOT_WRITABLE, node->name_span,
                 { { "name", node->name }, { "found", target->value_type->written() } });
      return nullptr;
    }
    an_effect(D::LANG_PURE_CHANGES_A_VARIABLE, node->span, {}, { { "name", node->name } });
    if (auto array = ir::as<ir::ArrayType>(target->value_type)) {
      // Its elements are in the image, and what is given is copied there.
      auto into = read_global(builder, target, node->span);
      array_into(builder, node->value, into, array, node->span);
      return wants_value ? read_global(builder, target, node->span) : nullptr;
    }
    auto value = checked_value(builder, node, target->value_type);
    if (ir::is<ir::SumType>(target->value_type)) {
      // Bytes in a place: the new value's are copied into the variable's.
      auto into = builder.address(target, node->span);
      copy_bytes(builder, into, value, std::int64_t(ir::size_of(target->value_type, layout())), node->span);
    } else
      builder.store(target, value, node->span);
    return wants_value ? read_global(builder, target, node->span) : nullptr;
  }

  ir::Value* Checker::array_assigned(ir::Builder& builder, const ast::AssignStmt* node, Local* local, bool wants_value)
  {
    if (! check_mutable(node, local->is_mutable, local->span))
      return nullptr;
    if (lent_out(node->name, node->span, true))
      return nullptr;
    local->read = true;
    auto held = ir::as<ir::ArrayType>(held_by(local));
    auto current = local->placed ? builder.load(local->value, node->span) : local->value;
    if (! array_into(builder, node->value, current, held, node->span))
      return nullptr;
    local->written = true;
    return wants_value ? current : nullptr;
  }

  bool Checker::array_into(ir::Builder& builder, const ast::Expr* written, ir::Value* into, const ir::ArrayType* ty,
                           Span span)
  {
    auto value = lower_expr(builder, written, ast::is<ast::ArrayLit>(written) ? ty : nullptr);
    return copy_elements(builder, into, ty, value, value_type_of(value), span);
  }

  bool Checker::check_mutable(const ast::AssignStmt* node, bool mutable_, Span where)
  {
    if (mutable_)
      return true;
    diags.emit(D::LANG_VARDEF_NOT_MUTABLE, node->name_span, { { "name", node->name } })
      .note(D::LANG_VARDEF_DEFINED_HERE, where, { { "name", node->name } });
    return false;
  }

  void Checker::assign_apart(ir::Builder& builder, const ast::AssignStmt* node)
  {
    auto value = lower_expr(builder, node->value, nullptr);
    std::vector<std::tuple<std::string, Span>> names{ { std::string(node->name), node->name_span } };
    for (const auto& one : node->more)
      names.emplace_back(std::string(one.name), one.span);
    auto members = taken_apart(value, names, node->span);
    if (! members)
      return;
    for (std::size_t index = 0; index < names.size(); ++index) {
      const auto& [name, where] = names[index];
      auto part = member(builder, value, value_type_of(value), std::int64_t(index), node->span);
      auto one = syntax.make<ast::AssignStmt>(where);
      one->name = syntax.text(name);
      one->name_span = where;
      one->value = node->value;
      store_into(builder, one, part);
    }
  }

  void Checker::store_into(ir::Builder& builder, const ast::AssignStmt* node, ir::Value* value)
  {
    auto local = find_local(node->name);
    if (local != nullptr && local->placed) {
      // A name with storage of its own: what it holds is compared, and the
      // value is put in its storage.
      if (! check_mutable(node, local->is_mutable, local->span))
        return;
      if (value_type_of(value) != local->held) [[unlikely]] {
        report_mismatch(node->span, value_type_of(value), local->held);
        return;
      }
      if (lent_out(node->name, node->span, true))
        return;
      // Taken out of a tuple, the part is nobody's alone that anything here
      // knows of: what was held goes, and what comes is not given back.
      room_replaced(builder, local, node->span);
      if (auto record = ir::as<ir::ProductType>(local->held))
        record_into(builder, local->value, value, record, node->span);
      else
        builder.store(local->value, value, node->span);
      local->written = true;
      local->is_parameter = false;
      return;
    }
    if (local != nullptr) {
      if (! check_mutable(node, local->is_mutable, local->span))
        return;
      if (value_type_of(local->value) != value_type_of(value)) [[unlikely]] {
        report_mismatch(node->span, value_type_of(value), value_type_of(local->value));
        return;
      }
      if (! carried.contains(id_of(local)))
        report_unused(local);
      room_replaced(builder, local, node->span);
      local->value = value;
      local->value_span = node->span;
      local->read = false;
      return;
    }
    auto target = provided(node->name).get<ir::GlobalVar>();
    if (target == nullptr) [[unlikely]] {
      diags.emit(D::LANG_FILESTRUCT_UNDEFINED_NAME, node->name_span, { { "name", node->name } });
      return;
    }
    if (sealed(target)) [[unlikely]] {
      refuse_sealed(target, node->name, node->name_span);
      return;
    }
    if (! check_mutable(node, target->is_mutable(), target->span))
      return;
    if (value_type_of(value) != target->value_type) [[unlikely]] {
      report_mismatch(node->span, value_type_of(value), target->value_type);
      return;
    }
    an_effect(D::LANG_PURE_CHANGES_A_VARIABLE, node->span, {}, { { "name", node->name } });
    builder.store(target, value, node->span);
  }

  ir::Value* Checker::checked_value(ir::Builder& builder, const ast::AssignStmt* node, const ir::Type* expected)
  {
    assigning = std::string(node->name);
    ir::Value* value = nullptr;
    try {
      value = lower_into(builder, node->value, expected, node->span);
    } catch (...) {
      assigning.reset();
      throw;
    }
    assigning.reset();
    return as_declared(value, expected);
  }

  const ir::Type* Checker::return_type(const ast::TypeExpr* ref, bool views)
  {
    if (ref == nullptr)
      return void_ty();
    if (auto named = ast::as<ast::TypeRef>(ref); named != nullptr && named->name == "void" && ! named->result) [[unlikely]] {
      diags.emit(D::LANG_TYPE_NOTHING_IS_NOT_WRITTEN, ref->span);
      return void_ty();
    }
    auto found = resolve_type(ref);
    if (auto short_one = short_lived(found)) [[unlikely]] {
      // What it walks is described in the room of the call that made it.
      diags.emit(D::LANG_ITERATOR_DOES_NOT_LEAVE, ref->span, { { "found", short_one->written() }, { "what", "answered" } });
      return error_ty();
    }
    if (auto array = ir::as<ir::ArrayType>(found); array != nullptr && ! array->fixed() && ! views) [[unlikely]] {
      // What a view is, is where its elements are, and a lambda or a function
      // value has no `in` to say where.
      diags.emit(D::LANG_ARRAY_ANSWERED_WITH, ref->span, { { "found", found->written() } });
      return error_ty();
    }
    return found;
  }

  void Checker::lower_return(ir::Builder& builder, const ast::ReturnStmt* stmt, ir::Function* func)
  {
    if (generating != nullptr) {
      // A generator gives what it gives by `yield`; returning is having nothing
      // more -- and, where it says what it answers when it ends, that answer
      // written where the body that handed it the walk reads.
      auto found = generating;
      if (found->finishes != nullptr) {
        if (stmt->value == nullptr) [[unlikely]]
          diags.emit(D::LANG_FUNCDEF_RETURN_MISSING, stmt->span,
                     { { "name", found->node->name }, { "type", found->finishes->written() } });
        else {
          auto value = lower_into(builder, stmt->value, found->finishes, stmt->value->span);
          if (value_type_of(value) != error_ty() && generating_args != nullptr) {
            auto place = finish_place(builder, generating_args, found, stmt->span);
            words_into(builder, place, value, found->finishes, stmt->span);
          }
        }
      } else if (stmt->value != nullptr) [[unlikely]]
        diags.emit(D::LANG_GENERATOR_RETURNS_A_VALUE, stmt->span, { { "name", found->node->name } });
      returning(builder, builder.int_const(ir::u8(), 0), stmt->span);
      return;
    }
    if (stmt->value == nullptr) {
      if (func->fty->ret != void_ty()) [[unlikely]]
        diags.emit(D::LANG_FUNCDEF_RETURN_MISSING, stmt->span,
                   { { "name", func->name }, { "type", func->fty->ret->written() } });
      returning(builder, nullptr, stmt->span);
      return;
    }
    if (func->fty->ret == void_ty()) {
      if (ast::is<ast::Call>(stmt->value)) {
        // `return f()` where both answer with nothing is the call and then a
        // return carrying nothing.
        auto answer = lower_expr(builder, stmt->value, nullptr);
        if (answer->ty == void_ty() || answer->ty == error_ty()) {
          returning(builder, nullptr, stmt->span);
          return;
        }
      }
      diags.emit(D::LANG_FUNCDEF_RETURN_VALUE_IN_VOID, stmt->span, { { "name", func->name } });
      returning(builder, nullptr, stmt->span);
      return;
    }
    auto value = lower_into(builder, stmt->value, func->fty->ret, stmt->span);
    auto answer = answered(builder, stmt->value, value, func, stmt->span);
    returning(builder, answer, stmt->span);
  }

  ir::Value* Checker::lower_expr(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected)
  {
    if (! copying.empty() && copying.contains(id_of(expr))) {
      copying.erase(id_of(expr));
      auto value = lower_expr(builder, expr, expected);
      return copy_of(builder, value, expr->span);
    }
    if (! as_run.empty() && as_run.contains(id_of(expr))) {
      // An operator whose answer is wanted as a run asks the same of the
      // operators it is made of.
      if (auto binary = ast::as<ast::Binary>(expr); binary != nullptr && sema::all_at_once().contains(binary->op)) {
        as_run.insert(id_of(binary->left));
        as_run.insert(id_of(binary->right));
      } else if (auto unary = ast::as<ast::Unary>(expr);
                 unary != nullptr && all_at_once_unary().contains(unary->op))
        as_run.insert(id_of(unary->operand));
    }
    if (abstract != nullptr && is_applying(expr)) {
      auto handled = abstractly(builder, expr);
      if (auto value = handled.get<ir::Value>())
        return value;
      expr = static_cast<const ast::Expr*>(handled.get<ast::Node>());
    }
    if ((ast::is<ast::Member>(expr) || ast::is<ast::Element>(expr) || ast::is<ast::Index>(expr))
        && read_through_the_dead(expr))
      return fresh_undef(error_ty());
    mark_stored(expr);
    if (! is_calling(expr)) {
      auto found = lower_written(builder, expr, expected);
      if (ast::is<ast::Member>(expr) || ast::is<ast::Element>(expr) || ast::is<ast::Index>(expr)
          || ast::is<ast::Deref>(expr) || stored_into.contains(id_of(expr)))
        found = not_moved_out(expr, found);
      return found;
    }
    auto before = borrows.size();
    auto found = lower_written(builder, expr, expected);
    if (stored_into.contains(id_of(expr)))
      found = not_moved_out(expr, found);
    if (borrows.size() > before && ! holds_a_reference(value_type_of(found)))
      borrows.erase(std::remove_if(borrows.begin() + std::ptrdiff_t(before), borrows.end(),
                                   [](const Borrow* one) { return one->depth == until_the_statement_ends; }),
                    borrows.end());
    return found;
  }

  void Checker::mark_stored(const ast::Expr* expr)
  {
    std::vector<const ast::Expr*> parts;
    std::string what;
    if (auto tuple = ast::as<ast::TupleLit>(expr)) {
      parts.assign(tuple->members.begin(), tuple->members.end());
      what = "a tuple";
    } else if (auto array = ast::as<ast::ArrayLit>(expr)) {
      parts.assign(array->elements.begin(), array->elements.end());
      what = "an array";
    } else if (auto list = ast::as<ast::ListLit>(expr)) {
      parts.assign(list->elements.begin(), list->elements.end());
      what = "a list";
    } else if (auto call = ast::as<ast::Call>(expr); call != nullptr && makes_a_record(call->callee)) {
      for (auto one : call->args)
        parts.push_back(ast::is<ast::Named>(one) ? ast::as<ast::Named>(one)->value : one);
      what = "a record";
    } else
      return;
    for (auto one : parts)
      if (one != nullptr)
        stored_into[id_of(one)] = what;
  }

  bool Checker::makes_a_record(const ast::Expr* callee)
  {
    if (auto name = ast::as<ast::NameRef>(callee)) {
      auto found = top->get(std::string(name->name));
      return found != nullptr && found->get<NamedType>() != nullptr && find_local(name->name) == nullptr;
    }
    if (auto member = ast::as<ast::Member>(callee))
      return type_exported(member) != nullptr;
    return false;
  }

  std::string Checker::move_named(std::string_view name)
  {
    if (handing_over) {
      const auto& [callee, at] = *handing_over;
      return std::format("handed to '{}' as its argument {}", callee, at);
    }
    return std::format("'{}' read as a value: bound to another name, answered or handed out", name);
  }

  void Checker::told_gone(Local* local, std::string_view name, Span span)
  {
    if (local->gone_with.starts_with(replaced)) {
      diags.emit(D::LANG_ELEMENT_REPLACED, span,
                 { { "name", name }, { "container", std::string_view(local->gone_with).substr(replaced.size()) } });
      return;
    }
    diags.emit(D::LANG_ARENA_VALUE_GIVEN_BACK, span, { { "name", name }, { "arena", local->gone_with } });
  }

  bool Checker::read_through_the_dead(const ast::Expr* expr)
  {
    auto root = expr;
    for (;;)
      if (auto m = ast::as<ast::Member>(root))
        root = m->base;
      else if (auto e = ast::as<ast::Element>(root))
        root = e->base;
      else if (auto i = ast::as<ast::Index>(root))
        root = i->base;
      else
        break;
    auto name = ast::as<ast::NameRef>(root);
    if (name == nullptr)
      return false;
    auto local = find_local(name->name);
    if (local == nullptr || ! local->gone_at)
      return false;
    local->read = true;
    told_gone(local, name->name, root->span);
    return true;
  }

  ir::Value* Checker::lower_written(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected)
  {
    if (auto emit = ast::as<ast::Emit>(expr)) {
      (*emit->run)(builder);
      return fresh_undef(void_ty());
    }
    if (auto yielded = ast::as<ast::YieldValue>(expr))
      return lower_yield_value(builder, yielded, expected);
    if ((ast::is<ast::Each>(expr) || is_each_step(expr)) && marked(expr))
      return lower_each(builder, expr, expected);
    if (auto call = ast::as<ast::Call>(expr))
      for (auto one : call->args) {
        auto arg = ast::is<ast::Named>(one) ? ast::as<ast::Named>(one)->value : one;
        if (marked(arg))
          return lower_each_call(builder, call, expected);
      }
    switch (expr->tag) {
    case ast::NodeKind::Spread:
      // What a macro holds is spread where it writes `⁂$args`, so one left
      // here was written by the program.
      diags.emit(D::LANG_SPREAD_NOT_HERE, expr->span);
      return fresh_undef(error_ty());
    case ast::NodeKind::IntLit: {
      auto lit = ast::as<ast::IntLit>(expr);
      if (aiming_at(expected) == ir::char_type() && ! lit->type_name)
        // A number written where a code point is wanted is that code point.
        return code_point(builder,
                          lit->value.too_large ? std::numeric_limits<std::int64_t>::max()
                                               : std::int64_t(std::clamp<__int128>(lit->value.value, std::numeric_limits<std::int64_t>::min(), std::numeric_limits<std::int64_t>::max())),
                          expr->span);
      auto ty = literal_type(lit, expected);
      if (ty == nullptr)
        return fresh_undef(error_ty());
      if (lit->value.too_large || ! ty->holds(lit->value.value)) [[unlikely]] {
        auto literal = lit->value.too_large ? big_decimal(sources, expr->span) : decimal(lit->value.value);
        diags.emit(D::LANG_SYNTAX_INTEGER_RANGE, expr->span, { { "literal", literal }, { "type", ty->written() } });
        return builder.int_const(ty, 0);
      }
      return builder.int_const(ty, lit->value.value);
    }
    case ast::NodeKind::CharLit:
      if (! accepts(expected, ir::char_type())) [[unlikely]] {
        report_mismatch(expr->span, ir::char_type(), expected);
        return fresh_undef(error_ty());
      }
      return const_cast<ir::CharConst*>(
        builder.module().char_const(std::uint32_t(ast::as<ast::CharLit>(expr)->value.value)));
    case ast::NodeKind::FloatLit: {
      auto lit = ast::as<ast::FloatLit>(expr);
      auto ty = float_literal_type(lit, expected);
      if (ty == nullptr)
        return fresh_undef(error_ty());
      return builder.float_const(ty, lit->value);
    }
    case ast::NodeKind::BoolLit:
      if (! accepts(expected, bool_ty())) [[unlikely]] {
        report_mismatch(expr->span, bool_ty(), expected);
        // Carrying on with a truth value would have whatever reads it report
        // the same thing again.
        return fresh_undef(error_ty());
      }
      return builder.bool_const(ast::as<ast::BoolLit>(expr)->value);
    case ast::NodeKind::Call:
      return lower_call(builder, ast::as<ast::Call>(expr), expected);
    case ast::NodeKind::Allocated:
      return lower_allocated(builder, ast::as<ast::Allocated>(expr), expected);
    case ast::NodeKind::NameRef:
      return lower_name(builder, ast::as<ast::NameRef>(expr), expected);
    case ast::NodeKind::TupleLit:
      return lower_tuple(builder, ast::as<ast::TupleLit>(expr), expected);
    case ast::NodeKind::ArrayLit:
      return lower_array(builder, ast::as<ast::ArrayLit>(expr), expected);
    case ast::NodeKind::Element: {
      auto element = ast::as<ast::Element>(expr);
      if (! field_types.empty() && field_type_named(expr) != nullptr) [[unlikely]] {
        auto kind = field_type_named(expr);
        auto base = ast::as<ast::NameRef>(element->base);
        diags.emit(D::LANG_FIELD_TYPE_NOT_A_VALUE, expr->span,
                   { { "what", std::format("{}\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}",
                                           base != nullptr ? base->name : std::string_view()) },
                     { "type", kind->written() } });
        return fresh_undef(error_ty());
      }
      return lower_element(builder, element, expected);
    }
    case ast::NodeKind::Range:
      // A range is a source of values for a loop and not a value.
      diags.emit(D::LANG_RANGE_OUTSIDE_A_LOOP, expr->span);
      return fresh_undef(error_ty());
    case ast::NodeKind::SetLit:
    case ast::NodeKind::DictLit:
      return lower_collection(builder, expr, expected);
    case ast::NodeKind::Take:
      return lower_take(builder, ast::as<ast::Take>(expr), expected);
    case ast::NodeKind::TakeAt:
      return lower_take_at(builder, ast::as<ast::TakeAt>(expr), expected);
    case ast::NodeKind::TakeThrough:
      return lower_take_through(builder, ast::as<ast::TakeThrough>(expr), expected);
    case ast::NodeKind::Index:
      return lower_index(builder, ast::as<ast::Index>(expr), expected);
    case ast::NodeKind::If:
      return lower_if(builder, ast::as<ast::If>(expr), &builder.function(), expected, true);
    case ast::NodeKind::Scoped:
      return lower_scoped(builder, ast::as<ast::Scoped>(expr), expected);
    case ast::NodeKind::While:
      return lower_while(builder, ast::as<ast::While>(expr), &builder.function(), expected, true);
    case ast::NodeKind::ForEach:
      return lower_foreach(builder, ast::as<ast::ForEach>(expr), &builder.function(), expected, true);
    case ast::NodeKind::Match:
      return lower_match(builder, ast::as<ast::Match>(expr), &builder.function(), expected, true);
    case ast::NodeKind::Ready:
      return ast::as<ast::Ready>(expr)->value;
    case ast::NodeKind::Lambda:
      return lower_lambda(builder, ast::as<ast::Lambda>(expr), expected);
    case ast::NodeKind::AddressOf:
      return lower_address(builder, ast::as<ast::AddressOf>(expr), expected);
    case ast::NodeKind::Deref:
      return lower_deref(builder, ast::as<ast::Deref>(expr), expected);
    case ast::NodeKind::Failure:
      return lower_failure(builder, ast::as<ast::Failure>(expr), expected);
    case ast::NodeKind::Quote:
      return lower_quote(builder, ast::as<ast::Quote>(expr), expected);
    case ast::NodeKind::Hole:
      // Outside a quote there is nothing for one to go into.
      diags.emit(D::LANG_MACRO_HOLE_OUTSIDE, expr->span);
      return fresh_undef(error_ty());
    case ast::NodeKind::Fresh:
      return lower_fresh(builder, ast::as<ast::Fresh>(expr), expected);
    case ast::NodeKind::Lifted: {
      auto lifted = ast::as<ast::Lifted>(expr);
      if (auto written = ast::as<ast::TypeRef>(lifted->written);
          expected == ir::syntax_type() && written != nullptr && ! written->module) {
        // A piece of the program is wanted, and what stands between the marks
        // is a bare name: the name as a piece.
        auto name = syntax.make<ast::NameRef>(expr->span);
        name->name = written->name;
        auto quote = syntax.make<ast::Quote>(expr->span);
        quote->pieces = syntax.list(std::vector<const ast::Expr*>{ name });
        return lower_quote(builder, quote, expected);
      }
      // Reaching this is standing where a value stands.
      diags.emit(D::LANG_LIFT_IS_NOT_A_VALUE, expr->span);
      return fresh_undef(error_ty());
    }
    case ast::NodeKind::Raised:
      return lower_raised(builder, ast::as<ast::Raised>(expr), expected);
    case ast::NodeKind::Try:
      return lower_try(builder, ast::as<ast::Try>(expr), expected);
    case ast::NodeKind::Binary: {
      auto binary = ast::as<ast::Binary>(expr);
      if (binary->op == ast::BinaryOp::or_else)
        return lower_or_else(builder, binary, expected);
      if (approximate().contains(binary->op))
        return lower_approximate(builder, binary, expected);
      if (comparisons().contains(binary->op))
        return lower_comparison(builder, binary, expected);
      if (logic_ops().contains(binary->op))
        return lower_logic(builder, binary, expected);
      if (short_circuit().contains(binary->op))
        return lower_short_circuit(builder, binary, expected);
      return lower_binary(builder, binary, expected);
    }
    case ast::NodeKind::Unary:
      return lower_unary(builder, ast::as<ast::Unary>(expr), expected);
    case ast::NodeKind::Member:
      return lower_member(builder, ast::as<ast::Member>(expr), expected);
    case ast::NodeKind::ListLit:
      return lower_list(builder, ast::as<ast::ListLit>(expr), expected);
    case ast::NodeKind::StringLit:
      if (! accepts(expected, ir::str_type())) [[unlikely]] {
        report_mismatch(expr->span, ir::str_type(), expected);
        return fresh_undef(error_ty());
      }
      return written_text(builder, ast::as<ast::StringLit>(expr)->value, expr->span);
    default:
      // Python's case for `⎕enumerate` written as a value stands after the
      // one for every call, which takes it first: it is never reached.
      diags.internal("unknown expression kind in lowering");
      return fresh_undef(error_ty());
    }
  }

  ir::Value* Checker::lower_comparison(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    // What is wanted must be a truth value, or an array of them.
    if (expected != nullptr && scalar_of(expected) != bool_ty()) [[unlikely]] {
      report_mismatch(expr->span, bool_ty(), expected);
      return fresh_undef(error_ty());
    }
    auto context = hint_of(expr->left);
    if (context == nullptr)
      context = hint_of(expr->right);
    std::string glyph(describe(expr->op));
    if (context == ir::syntax_type() && (expr->op == ast::BinaryOp::equal || expr->op == ast::BinaryOp::not_equal)) {
      // Two pieces of the program compared: a question about trees.
      auto left = lower_into(builder, expr->left, ir::syntax_type(), expr->left->span);
      auto right = lower_into(builder, expr->right, ir::syntax_type(), expr->right->span);
      if (value_type_of(left) == error_ty() || value_type_of(right) == error_ty())
        return fresh_undef(error_ty());
      return pieces_alike(builder, expr, left, right);
    }
    auto outer = operand_of;
    operand_of = glyph;
    auto was_listing = listing;
    listing = true;
    ir::Value* left = nullptr;
    ir::Value* right = nullptr;
    const ir::Type* ty = nullptr;
    try {
      left = lower_expr(builder, expr->left, context);
      ty = comparable(expr->left->span, expr->op, scalar_of(value_type_of(left)));
      const ir::Type* other;
      if (ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty))
        // Either side may be the writable kind: what is compared is what the
        // two hold, and the right is asked for the kind both stand as.
        other = table_read_only_of(ty);
      else if (ty != error_ty())
        other = ty;
      else if (value_type_of(left) == error_ty())
        // Whatever is wrong with the left has been reported, and the right
        // takes its type from it.
        other = error_ty();
      else
        other = context;
      right = lower_expr(builder, expr->right, other);
    } catch (...) {
      operand_of = outer;
      listing = was_listing;
      throw;
    }
    operand_of = outer;
    listing = was_listing;
    if (ty != error_ty()) {
      auto walked = walk_operands(builder, expr,
                                  std::vector<std::tuple<std::string, ir::Value*>>{ { "left", left }, { "right", right } },
                                  expected);
      if (walked != nullptr)
        return walked;
    }
    auto found = comparable(expr->right->span, expr->op, scalar_of(value_type_of(right)));
    if (ty == error_ty() || found == error_ty())
      return fresh_undef(error_ty());
    auto tabled = [](const ir::Type* one) { return ir::is<ir::SetType>(one) || ir::is<ir::DictType>(one); };
    if (found != ty && ! (tabled(ty) && tabled(found) && table_read_only_of(found) == table_read_only_of(ty))) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_MISMATCH, expr->right->span,
                 { { "operator", glyph }, { "expected", ty->written() }, { "found", found->written() } });
      return fresh_undef(error_ty());
    }
    if (tabled(ty)) {
      if (! accepts(expected, bool_ty())) [[unlikely]] {
        report_mismatch(expr->span, bool_ty(), expected);
        return fresh_undef(error_ty());
      }
      return compared_tables(builder, expr, left, right, ty);
    }
    if (ir::is<ir::FloatType>(ty) && exact_on_floats().contains(expr->op))
      // Two floating-point values computed different ways are rarely the one
      // value.
      diags.emit(D::LANG_TYPE_EXACT_FLOAT_COMPARISON, expr->span, { { "operator", glyph }, { "type", ty->written() } });
    if (ty == ir::str_type()) {
      if (! accepts(expected, bool_ty())) [[unlikely]] {
        report_mismatch(expr->span, bool_ty(), expected);
        return fresh_undef(error_ty());
      }
      return compared_text(builder, expr, left, right);
    }
    auto [is_signed, is_unsigned] = comparisons().at(expr->op);
    // A truth value is ordered as an unsigned number; a floating-point value
    // the way a signed number is.
    auto integer = ir::as<ir::IntType>(ty);
    bool signed_reading = ir::is<ir::FloatType>(ty) || (integer != nullptr && integer->is_signed);
    if (! accepts(expected, bool_ty())) [[unlikely]] {
      // Nothing was walked, so the answer is one truth value after all.
      report_mismatch(expr->span, bool_ty(), expected);
      return fresh_undef(error_ty());
    }
    return builder.compare(signed_reading ? is_signed : is_unsigned, left, right, expr->span);
  }

  const ir::Type* Checker::table_read_only_of(const ir::Type* ty)
  {
    if (auto set = ir::as<ir::SetType>(ty); set != nullptr)
      return ir::set_type(set->element, false);
    auto dict = ir::as<ir::DictType>(ty);
    return ir::dict_type(dict->key, dict->value, false);
  }

  ir::Value* Checker::compared_tables(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right,
                                      const ir::Type* ty)
  {
    if (auto dict = ir::as<ir::DictType>(ty); dict != nullptr)
      if (auto missing = tables::not_compared(dict->value); missing != nullptr) [[unlikely]] {
        diags.emit(D::LANG_TABLE_VALUES_NOT_COMPARABLE, expr->span,
                   { { "operator", std::string(describe(expr->op)) }, { "found", missing->written() } });
        return fresh_undef(error_ty());
      }
    auto table_ptr = tables::table_type(module);
    auto callee = tables::ensure_same(module, table_shape(ty));
    std::vector<ir::Value*> args;
    args.push_back(builder.cast(ir::CastKind::bitcast, left, table_ptr, expr->span));
    args.push_back(builder.cast(ir::CastKind::bitcast, right, table_ptr, expr->span));
    auto same = builder.call(callee, args, bool_ty(), expr->span);
    if (expr->op == ast::BinaryOp::equal)
      return same;
    return builder.binary(ir::BinOp::xor_, same, builder.bool_const(true), expr->span);
  }

  ir::Value* Checker::compared_text(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right)
  {
    auto bytes = ir::parts_of(ir::str_type())[0];
    auto compare = strings::compare_function(module);
    std::vector<ir::Value*> args;
    args.push_back(builder.extract(left, 0, bytes, expr->span));
    args.push_back(builder.extract(left, 1, ir::u64(), expr->span));
    args.push_back(builder.extract(right, 0, bytes, expr->span));
    args.push_back(builder.extract(right, 1, ir::u64(), expr->span));
    auto which = builder.call(compare, args, ir::i64(), expr->span);
    return builder.compare(comparisons().at(expr->op).first, which, builder.int_const(ir::i64(), 0), expr->span);
  }

  ir::Value* Checker::lower_approximate(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    if (expected != nullptr && scalar_of(expected) != bool_ty()) [[unlikely]] {
      report_mismatch(expr->span, bool_ty(), expected);
      return fresh_undef(error_ty());
    }
    auto context = hint_of(expr->left);
    if (context == nullptr)
      context = hint_of(expr->right);
    std::string glyph(describe(expr->op));
    auto outer = operand_of;
    operand_of = glyph;
    auto was_listing = listing;
    listing = true;
    ir::Value* left = nullptr;
    ir::Value* right = nullptr;
    const ir::Type* ty = nullptr;
    try {
      left = lower_expr(builder, expr->left, context);
      ty = scalar_of(value_type_of(left));
      if (ty != error_ty() && ! ir::is<ir::FloatType>(ty)) [[unlikely]] {
        diags.emit(D::LANG_TYPE_APPROXIMATE_NEEDS_A_FLOAT, expr->left->span,
                   { { "operator", glyph }, { "found", ty->written() } });
        ty = error_ty();
      }
      right = lower_expr(builder, expr->right, ty != error_ty() ? ty : context);
    } catch (...) {
      operand_of = outer;
      listing = was_listing;
      throw;
    }
    operand_of = outer;
    listing = was_listing;
    if (ty != error_ty()) {
      auto walked = walk_operands(builder, expr,
                                  std::vector<std::tuple<std::string, ir::Value*>>{ { "left", left }, { "right", right } },
                                  expected);
      if (walked != nullptr)
        return walked;
    }
    auto found = scalar_of(value_type_of(right));
    if (ty == error_ty() || found == error_ty())
      return fresh_undef(error_ty());
    if (! ir::is<ir::FloatType>(found)) [[unlikely]] {
      diags.emit(D::LANG_TYPE_APPROXIMATE_NEEDS_A_FLOAT, expr->right->span,
                 { { "operator", glyph }, { "found", found->written() } });
      return fresh_undef(error_ty());
    }
    if (found != ty) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_MISMATCH, expr->right->span,
                 { { "operator", glyph }, { "expected", ty->written() }, { "found", found->written() } });
      return fresh_undef(error_ty());
    }
    auto [exchanged, magnitude, pred] = approximate().at(expr->op);
    auto first = exchanged ? right : left;
    auto second = exchanged ? left : right;
    auto difference = builder.binary(ir::BinOp::sub, first, second, expr->span);
    if (magnitude)
      difference = builder.unary(ir::UnOp::fabs, difference, nullptr, expr->span);
    if (ir::without_units(ty) != ir::f64())
      // A unit is no part of the bits, so what is widened is decided by the
      // width alone.
      difference = builder.cast(ir::CastKind::fext, difference, ir::f64(), expr->span);
    else if (! unit_of(ty)->is_none())
      difference = builder.cast(ir::CastKind::bitcast, difference, ir::f64(), expr->span);
    auto tolerance = builder.load(tolerance_variable(), expr->span);
    return builder.compare(pred, difference, tolerance, expr->span);
  }

  ir::GlobalVar* Checker::tolerance_variable()
  {
    return provided(tolerance_name).get<ir::GlobalVar>();
  }

  ir::Value* Checker::lower_logic(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    if (expected != nullptr && scalar_of(expected) != bool_ty()) [[unlikely]] {
      report_mismatch(expr->span, bool_ty(), expected);
      return fresh_undef(error_ty());
    }
    auto outer = operand_of;
    operand_of = std::string(describe(expr->op));
    auto was_listing = listing;
    listing = true;
    ir::Value* left = nullptr;
    ir::Value* right = nullptr;
    try {
      left = boolean(builder, expr->left, describe(expr->op), true);
      right = boolean(builder, expr->right, describe(expr->op), true);
    } catch (...) {
      operand_of = outer;
      listing = was_listing;
      throw;
    }
    operand_of = outer;
    listing = was_listing;
    if (left == nullptr || right == nullptr)
      return fresh_undef(error_ty());
    auto walked = walk_operands(builder, expr,
                                std::vector<std::tuple<std::string, ir::Value*>>{ { "left", left }, { "right", right } },
                                expected);
    if (walked != nullptr)
      return walked;
    if (! accepts(expected, bool_ty())) [[unlikely]] {
      report_mismatch(expr->span, bool_ty(), expected);
      return fresh_undef(error_ty());
    }
    auto [operation, inverted] = logic_ops().at(expr->op);
    auto found = builder.binary(operation, left, right, expr->span);
    return inverted ? negate(builder, found, expr->span) : found;
  }

  ir::Value* Checker::lower_try(ir::Builder& builder, const ast::Try* expr, const ir::Type* expected)
  {
    auto value = lower_expr(builder, expr->operand, nullptr);
    auto ty = value_type_of(value);
    if (ty == error_ty())
      return fresh_undef(error_ty());
    auto result = ir::as<ir::ResultType>(ty);
    if (result == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TYPE_NOT_A_RESULT, expr->operand->span, { { "operator", "?" }, { "found", ty->written() } });
      return fresh_undef(error_ty());
    }
    auto answering_ty = answering;
    if (generating != nullptr)
      // In a generator, what a failure leaves is the walk.
      answering_ty = generating->element;
    auto leaves_as = ir::as<ir::ResultType>(answering_ty);
    if (leaves_as == nullptr || ! ir::same(leaves_as->err, result->err)) [[unlikely]] {
      diags.emit(D::LANG_TYPE_TRY_NEEDS_A_RESULT, expr->span,
                 { { "found", answering_ty != nullptr ? answering_ty->written() : void_ty()->written() } });
      return fresh_undef(error_ty());
    }
    if (! accepts(expected, result->ok)) [[unlikely]] {
      report_mismatch(expr->span, result->ok, expected);
      return fresh_undef(error_ty());
    }
    auto leaving = builder.new_block("leaving");
    auto answered_block = builder.new_block("answered");
    builder.condbr(builder.failed(value, expr->span), leaving, answered_block, {}, {}, expr->span);
    builder.position_at(leaving);
    // What the way out runs may write memory and give names new values, and
    // none of it happened on the way that goes on.
    auto token = builder.memory();
    std::vector<std::tuple<Local*, ir::Value*, Span>> held;
    for (auto& scope : scopes)
      for (auto& [_, local] : scope)
        held.emplace_back(local, local->value, local->value_span);
    // What is handed back is an answer nothing may read beside the truth value
    // that forbids reading it, and what the error carries.
    auto nothing = fresh_undef(leaves_as->ok);
    auto bad = builder.bool_const(true);
    ir::Value* carried_error = leaves_as->err == nullptr ? nullptr : builder.error(value, leaves_as->err, expr->span);
    auto failure = builder.wrap(nothing, bad, leaves_as, expr->span, carried_error);
    if (generating != nullptr && generating_out != nullptr) {
      words_into(builder, generating_out, failure, leaves_as, expr->span);
      auto finished = builder.new_block("finished");
      builder.yield(finished, expr->span);
      builder.position_at(finished);
      returning(builder, builder.int_const(ir::u8(), 0), expr->span);
    } else
      returning(builder, failure, expr->span);
    builder.set_memory(token);
    for (auto& [local, was, where] : held) {
      local->value = was;
      local->value_span = where;
    }
    builder.position_at(answered_block);
    return builder.unwrap(value, result->ok, expr->span);
  }

  ir::Value* Checker::lower_or_else(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    auto value = lower_expr(builder, expr->left, nullptr);
    auto ty = value_type_of(value);
    if (ty == error_ty())
      return fresh_undef(error_ty());
    auto result = ir::as<ir::ResultType>(ty);
    if (result == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TYPE_NOT_A_RESULT, expr->left->span,
                 { { "operator", describe(expr->op) }, { "found", ty->written() } });
      return fresh_undef(error_ty());
    }
    if (! accepts(expected, result->ok)) [[unlikely]] {
      report_mismatch(expr->span, result->ok, expected);
      return fresh_undef(error_ty());
    }
    auto instead = builder.new_block("instead");
    auto answered_block = builder.new_block("answered");
    auto joined_block = builder.new_block("joined");
    auto answer = joined_block->add_param(result->ok, "answer");
    builder.condbr(builder.failed(value, expr->span), instead, answered_block, {}, {}, expr->span);
    auto before = builder.memory_so_far();
    builder.position_at(instead);
    auto fallback = lower_expr(builder, expr->right, result->ok);
    auto found = value_type_of(fallback);
    if (found != result->ok && found != error_ty()) {
      report_mismatch(expr->right->span, found, result->ok);
      fallback = fresh_undef(result->ok);
    } else if (found == error_ty())
      fallback = fresh_undef(result->ok);
    auto after = builder.memory_so_far();
    if (after == before) {
      builder.br(joined_block, std::vector<ir::Value*>{ fallback }, expr->span);
      builder.position_at(answered_block);
      builder.br(joined_block, std::vector<ir::Value*>{ builder.unwrap(value, result->ok, expr->span) }, expr->span);
      builder.position_at(joined_block);
      return answer;
    }
    // The default wrote memory -- a sum made in room of its own -- so the two
    // tokens meet where the ways do, as they meet after an `if`.
    auto token = joined_block->add_param(ir::mem_type(), "mem");
    builder.br(joined_block, std::vector<ir::Value*>{ fallback, after }, expr->span);
    builder.position_at(answered_block);
    {
      auto unwrapped = builder.unwrap(value, result->ok, expr->span);
      auto mem = before != nullptr ? before : chain_start(builder);
      builder.br(joined_block, std::vector<ir::Value*>{ unwrapped, mem }, expr->span);
    }
    builder.position_at(joined_block);
    builder.set_memory(token);
    return answer;
  }

  ir::Value* Checker::lower_short_circuit(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    if (! accepts(expected, bool_ty())) [[unlikely]] {
      report_mismatch(expr->span, bool_ty(), expected);
      return fresh_undef(error_ty());
    }
    auto outer = operand_of;
    operand_of = std::string(describe(expr->op));
    ir::Value* left = nullptr;
    try {
      left = boolean(builder, expr->left, describe(expr->op));
    } catch (...) {
      operand_of = outer;
      throw;
    }
    operand_of = outer;
    if (left == nullptr)
      return fresh_undef(error_ty());
    bool decides = short_circuit().at(expr->op);
    auto rest = builder.new_block("rest");
    auto decided = builder.new_block("decided");
    auto joined_block = builder.new_block("joined");
    auto answer = joined_block->add_param(bool_ty(), "answer");
    // The left side being what decides sends control straight to the answer.
    builder.condbr(left, decides ? decided : rest, decides ? rest : decided, {}, {}, expr->span);
    auto before = builder.memory_so_far();
    builder.position_at(rest);
    outer = operand_of;
    operand_of = std::string(describe(expr->op));
    ir::Value* right = nullptr;
    try {
      right = boolean(builder, expr->right, describe(expr->op));
    } catch (...) {
      operand_of = outer;
      throw;
    }
    operand_of = outer;
    if (right == nullptr)
      // Giving the join something of the right type keeps the representation
      // well formed while the error is reported.
      right = fresh_undef(bool_ty());
    auto after = builder.memory_so_far();
    if (after == before) {
      builder.br(joined_block, std::vector<ir::Value*>{ right }, expr->span);
      builder.position_at(decided);
      builder.br(joined_block, std::vector<ir::Value*>{ builder.bool_const(decides) }, expr->span);
      builder.position_at(joined_block);
      return answer;
    }
    // The right side wrote memory -- an answer put in room of its own, text
    // made -- so the two tokens meet where the ways do, as they do after `??`.
    auto token = joined_block->add_param(ir::mem_type(), "mem");
    builder.br(joined_block, std::vector<ir::Value*>{ right, after }, expr->span);
    builder.position_at(decided);
    {
      auto mem = before != nullptr ? before : chain_start(builder);
      builder.br(joined_block, std::vector<ir::Value*>{ builder.bool_const(decides), mem }, expr->span);
    }
    builder.position_at(joined_block);
    builder.set_memory(token);
    return answer;
  }

  ir::Value* Checker::boolean(ir::Builder& builder, const ast::Expr* expr, std::string_view op, bool walked)
  {
    auto value = lower_expr(builder, expr, bool_ty());
    auto ty = value_type_of(value);
    if (ty == error_ty())
      return nullptr;
    if ((walked ? scalar_of(ty) : ty) != bool_ty()) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_NOT_BOOLEAN, expr->span, { { "operator", op }, { "found", ty->written() } });
      return nullptr;
    }
    return value;
  }

  ir::Value* Checker::negate(ir::Builder& builder, ir::Value* value, Span span)
  {
    ir::Value* one = builder.bool_const(true);
    if (ir::is<ir::VecType>(value->ty))
      one = builder.splat(one, value->ty, span);
    return builder.binary(ir::BinOp::xor_, value, one, span);
  }

  ir::Value* Checker::lower_set_operation(ir::Builder& builder, ast::BinaryOp op, const ir::SetType* ty,
                                          ir::Value* left, ir::Value* right, Span span)
  {
    auto answer_ty = ir::set_type(ty->element, true);
    auto select = tables::ensure_operators(module, table_shape(answer_ty));
    auto table_ptr = tables::table_type(module);
    auto left_table = builder.cast(ir::CastKind::bitcast, left, table_ptr, span);
    auto right_table = builder.cast(ir::CastKind::bitcast, right, table_ptr, span);
    // Out of the same arena the left operand came from.
    auto out = new_table(builder, answer_ty, span, nullptr, left_table);
    auto into = builder.cast(ir::CastKind::bitcast, out, table_ptr, span);
    for (auto [first, want] : set_walks(op)) {
      auto wanted_number = builder.int_const(ir::u64(), want);
      std::vector<ir::Value*> args{ into, first ? left_table : right_table, first ? right_table : left_table,
                                    wanted_number };
      builder.call(select, args, void_ty(), span);
    }
    return out;
  }

  const ir::Type* Checker::comparable(Span span, ast::BinaryOp op, const ir::Type* ty)
  {
    if (ty == error_ty())
      return error_ty();
    if (ir::is<ir::IntType>(ty) || ir::is<ir::FloatType>(ty))
      return ty;
    if (ty == ir::char_type())
      // Code points are ordered, and the order is a real one.
      return ty;
    if (ty == ir::str_type())
      // Text is ordered, in the order the bytes are already in.
      return ty;
    bool ordering = orderings().contains(op);
    if ((ir::is<ir::SetType>(ty) || ir::is<ir::DictType>(ty)) && ! ordering)
      return ty;
    if (ir::is<ir::EnumType>(ty) && ! ordering)
      return ty;
    if (ty == bool_ty() && ! ordering)
      return ty;
    if (ordering)
      diags.emit(D::LANG_TYPE_OPERAND_NOT_INTEGER, span, { { "operator", describe(op) }, { "found", ty->written() } });
    else
      diags.emit(D::LANG_TYPE_OPERAND_NOT_COMPARABLE, span, { { "operator", describe(op) }, { "found", ty->written() } });
    return error_ty();
  }

  ir::Value* Checker::lower_reshape(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    auto shape = written_shape(expr->left);
    auto source = lower_expr(builder, expr->right, nullptr);
    auto found = value_type_of(source);
    if (! shape || found == error_ty())
      return fresh_undef(error_ty());
    std::int64_t total = 1;
    for (auto along : *shape)
      total *= along;
    auto array = ir::as<ir::ArrayType>(found);
    auto element = array != nullptr ? array->element : found;
    std::optional<std::uint64_t> count = array != nullptr ? array->count() : std::nullopt;
    if (array != nullptr && ! array->fixed()) [[unlikely]] {
      diags.emit(D::LANG_CONCAT_NEEDS_A_STATED_SHAPE, expr->right->span, { { "found", found->written() } });
      return fresh_undef(error_ty());
    }
    if (count && std::int64_t(*count) > total) [[unlikely]] {
      diags.emit(D::LANG_SHAPE_TOO_MANY, expr->span, { { "found", *count }, { "wanted", total } });
      return fresh_undef(error_ty());
    }
    std::vector<std::optional<std::uint64_t>> dims;
    for (auto along : *shape)
      dims.push_back(std::uint64_t(along));
    auto answer = ir::array_type(element, dims);
    auto place = builder.frame(answer, expr->span);
    if (! count) {
      // One value everywhere, which is one operation over the whole run.
      auto held = ir::vec_type(element, unsigned(total));
      auto where = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(held, true), expr->span);
      auto value = builder.splat(source, held, expr->span);
      builder.store(where, value, expr->span);
    } else {
      auto [start, _] = shape_of(builder, source, array, expr->span);
      for (std::int64_t at = 0; at < total; ++at) {
        auto into = element_place(builder, place, element, builder.int_const(ir::u64(), at), expr->span);
        auto from = element_place(builder, start, element, builder.int_const(ir::u64(), at % std::int64_t(*count)),
                                  expr->span);
        auto value = builder.load(from, expr->span);
        builder.store(into, value, expr->span);
      }
    }
    auto made = builder.cast(ir::CastKind::bitcast, place, answer, expr->span);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return fresh_undef(error_ty());
    }
    return made;
  }

  std::optional<std::vector<std::int64_t>> Checker::written_shape(const ast::Expr* expr)
  {
    if (auto unary = ast::as<ast::Unary>(expr); unary != nullptr && unary->op == ast::UnaryOp::shape) {
      // The shape of something, which is known where its type says it.
      auto of = ir::as<ir::ArrayType>(hint_of(unary->operand));
      if (of != nullptr && of->fixed()) {
        std::vector<std::int64_t> found;
        for (auto along : of->shape)
          if (along)
            found.push_back(std::int64_t(*along));
        return found;
      }
      diags.emit(D::LANG_SHAPE_NOT_WRITTEN_DOWN, expr->span);
      return std::nullopt;
    }
    std::vector<const ast::Expr*> written;
    if (auto tuple = ast::as<ast::TupleLit>(expr))
      written.assign(tuple->members.begin(), tuple->members.end());
    else
      written.push_back(expr);
    std::vector<std::int64_t> found;
    for (auto one : written) {
      auto along = constant_number(one);
      if (along.refused)
        return std::nullopt;
      if (! along.value) [[unlikely]] {
        diags.emit(D::LANG_SHAPE_NOT_WRITTEN_DOWN, one->span);
        return std::nullopt;
      }
      if (*along.value <= 0) [[unlikely]] {
        diags.emit(D::LANG_SHAPE_NOT_A_COUNT, one->span, { { "found", decimal(*along.value) } });
        return std::nullopt;
      }
      found.push_back(std::int64_t(*along.value));
    }
    return found;
  }

  ir::Value* Checker::lower_failure(ir::Builder& builder, const ast::Failure* expr, const ir::Type* expected)
  {
    // What is wanted as it stands, and not what a value of the answer type
    // would be aimed at.
    auto wanted = ir::as<ir::ResultType>(expected);
    if (wanted == nullptr) [[unlikely]] {
      diags.emit(D::LANG_FAILURE_NEEDS_A_RESULT, expr->span,
                 { { "found", expected == nullptr ? std::string("nothing in particular")
                                                  : std::format("'{}'", expected->written()) } });
      return fresh_undef(error_ty());
    }
    ir::Value* carried_value = nullptr;
    if (wanted->err == nullptr) {
      if (expr->value != nullptr) [[unlikely]] {
        diags.emit(D::LANG_FAILURE_CARRIES_NOTHING, expr->value->span, { { "found", wanted->written() } });
        return fresh_undef(error_ty());
      }
    } else {
      if (expr->value == nullptr) [[unlikely]] {
        diags.emit(D::LANG_FAILURE_NEEDS_A_VALUE, expr->span,
                   { { "found", wanted->written() }, { "carried", wanted->err->written() } });
        return fresh_undef(error_ty());
      }
      carried_value = lower_into(builder, expr->value, wanted->err, expr->value->span);
      if (value_type_of(carried_value) == error_ty())
        return fresh_undef(error_ty());
    }
    // The answer half is a value nothing may read.
    auto nothing = fresh_undef(wanted->ok);
    auto bad = builder.bool_const(true);
    return builder.wrap(nothing, bad, wanted, expr->span, carried_value);
  }

  ir::Value* Checker::lower_divides(ir::Builder& builder, const ast::Expr* expr, const ast::Expr* written,
                                    const ast::Expr* over, std::string_view name, bool negated,
                                    const ir::Type* expected)
  {
    ir::Value* left = nullptr;
    ir::Value* right = nullptr;
    const ir::Type* first = nullptr;
    auto outer = operand_of;
    operand_of = std::string(name);
    auto was_listing = listing;
    listing = true;
    try {
      if (written != nullptr) {
        // What is wanted of each side is what an array of them would be an
        // array of.
        left = lower_expr(builder, written, scalar_of(hint_of(over)));
        first = scalar_of(value_type_of(left));
        if (first != nullptr && first != error_ty() && ! ir::is<ir::IntType>(first)) [[unlikely]] {
          diags.emit(D::LANG_DIVIDES_IS_FOR_INTEGERS, written->span, { { "operator", name }, { "found", first->written() } });
          first = error_ty();
        }
      }
      right = lower_expr(builder, over, first != nullptr && first != error_ty() ? first : nullptr);
    } catch (...) {
      operand_of = outer;
      listing = was_listing;
      throw;
    }
    operand_of = outer;
    listing = was_listing;
    auto ty_found = scalar_of(value_type_of(right));
    if (first == error_ty() || ty_found == nullptr || ty_found == error_ty())
      return fresh_undef(error_ty());
    auto ty = ir::as<ir::IntType>(ty_found);
    if (ty == nullptr) [[unlikely]] {
      diags.emit(D::LANG_DIVIDES_IS_FOR_INTEGERS, over->span, { { "operator", name }, { "found", ty_found->written() } });
      return fresh_undef(error_ty());
    }
    if (left == nullptr) {
      if (! ty->holds(2)) [[unlikely]] {
        diags.emit(D::LANG_DIVIDES_NEEDS_A_TWO, expr->span, { { "operator", name }, { "found", ty->written() } });
        return fresh_undef(error_ty());
      }
      left = const_cast<ir::Value*>(static_cast<const ir::Value*>(module.constant<ir::IntConst>(ty, 2)));
    } else if (first != ty) [[unlikely]] {
      diags.emit(D::LANG_TYPE_OPERAND_MISMATCH, over->span,
                 { { "operator", name }, { "expected", first->written() }, { "found", ty->written() } });
      return fresh_undef(error_ty());
    }
    std::vector<std::tuple<std::string, ir::Value*>> given;
    if (written != nullptr)
      given = { { "left", left }, { "right", right } };
    else
      given = { { "operand", right } };
    auto walked = walk_operands(builder, expr, given, expected);
    if (walked != nullptr)
      return walked;
    // What it answers depends on whether the divisor can be seen.
    auto ready = ast::as<ast::Ready>(written);
    bool settled = written == nullptr || ast::is<ast::IntLit>(written)
                   || (ready != nullptr && ready->value->vkind == ir::ValueKind::int_const);
    const ir::Type* answer = settled ? bool_ty() : ir::result_type(bool_ty(), ty);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return fresh_undef(error_ty());
    }
    if (settled) {
      if (static_cast<ir::IntConst*>(left)->value == 0) [[unlikely]] {
        diags.emit(D::LANG_DIVIDES_BY_A_WRITTEN_ZERO, expr->span, { { "operator", name } });
        return fresh_undef(error_ty());
      }
      auto found = divides(builder, left, right, ty, expr->span);
      return negated ? negate(builder, found, expr->span) : found;
    }
    return divides_or_not(builder, left, right, ty, answer, negated, expr->span);
  }

  ir::Value* Checker::divides_or_not(ir::Builder& builder, ir::Value* left, ir::Value* right, const ir::IntType* ty,
                                     const ir::Type* answer, bool negated, Span span)
  {
    auto remainder = ir::result_type(ty);
    auto left_over = builder.binary(ty->is_signed ? ir::BinOp::srem : ir::BinOp::urem, right, left, span, remainder);
    auto unwrapped = builder.unwrap(left_over, ty, span);
    auto nothing = builder.int_const(ty, 0);
    auto evenly = builder.compare(ir::CmpPred::eq, unwrapped, nothing, span);
    if (negated)
      evenly = negate(builder, evenly, span);
    auto failed = builder.failed(left_over, span);
    return builder.wrap(evenly, failed, answer, span, right);
  }

  ir::Value* Checker::divides(ir::Builder& builder, ir::Value* left, ir::Value* right, const ir::IntType* ty, Span span)
  {
    auto nothing = builder.int_const(ty, 0);
    auto remainder = ir::result_type(ty);
    auto taking = ty->is_signed ? ir::BinOp::srem : ir::BinOp::urem;
    auto evenly = [&] {
      auto left_over = builder.binary(taking, right, left, span, remainder);
      auto unwrapped = builder.unwrap(left_over, ty, span);
      return builder.compare(ir::CmpPred::eq, unwrapped, nothing, span);
    };
    if (left->vkind == ir::ValueKind::int_const) {
      // The divisor is written down, so which of the two cases this is, is
      // written down with it.
      if (static_cast<ir::IntConst*>(left)->value == 0)
        return builder.compare(ir::CmpPred::eq, right, nothing, span);
      return evenly();
    }
    auto by_zero = builder.new_block("divides.by.nothing");
    auto ordinary = builder.new_block("divides");
    auto done = builder.new_block("divided");
    builder.condbr(builder.compare(ir::CmpPred::eq, left, nothing, span), by_zero, ordinary, {}, {}, span);
    builder.position_at(by_zero);
    builder.br(done, std::vector<ir::Value*>{ builder.compare(ir::CmpPred::eq, right, nothing, span) }, span);
    builder.position_at(ordinary);
    builder.br(done, std::vector<ir::Value*>{ evenly() }, span);
    builder.position_at(done);
    return done->add_param(bool_ty(), "divides");
  }

  ir::Value* Checker::lower_power(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    auto [base, ty, times, count] = sides_of_a_power(builder, expr, expr->left, expr->right, expected);
    if (ty == nullptr || count == nullptr)
      return fresh_undef(error_ty());
    auto answer = ir::result_type(ty);
    auto walked = walk_operands(builder, expr,
                                std::vector<std::tuple<std::string, ir::Value*>>{ { "left", base }, { "right", times } },
                                expected);
    if (walked != nullptr)
      return walked;
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return fresh_undef(error_ty());
    }
    if (times->vkind == ir::ValueKind::int_const) {
      // Written down after all, which settles which of the two it is.
      auto written = static_cast<ir::IntConst*>(times)->value;
      if (written < 0)
        return reciprocal(builder, base, ty, -written, answer, expr->span);
      if (base->vkind == ir::ValueKind::int_const
          && power_is_already_known(expr, ty, static_cast<ir::IntConst*>(base), written))
        return fresh_undef(error_ty());
      auto raised = raised_by(builder, base, ty, written, expr->span);
      return builder.wrap(raised, builder.bool_const(false), answer, expr->span);
    }
    return raised_over(builder, base, ty, times, count, answer, expr->span);
  }

  ir::Value* Checker::lower_raised(ir::Builder& builder, const ast::Raised* expr, const ir::Type* expected)
  {
    auto wanted = expected;
    // A raised exponent beyond 127 bits is no exponent anyone writes; it is
    // read as the largest there is, of its sign.
    __int128 exponent = expr->exponent.value;
    if (exponent < 0 && ir::is<ir::ResultType>(aiming_at(expected)))
      wanted = ir::as<ir::ResultType>(aiming_at(expected))->ok;
    auto [base, ty, _, _] = sides_of_a_power(builder, expr, expr->base, nullptr, wanted);
    if (ty == nullptr)
      return fresh_undef(error_ty());
    const ir::Type* answer = exponent < 0 ? ir::result_type(ty) : ty;
    auto walked = walk_operands(builder, expr, std::vector<std::tuple<std::string, ir::Value*>>{ { "base", base } },
                                expected);
    if (walked != nullptr)
      return walked;
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return fresh_undef(error_ty());
    }
    if (exponent < 0)
      return reciprocal(builder, base, ty, -exponent, answer, expr->span);
    if (base->vkind == ir::ValueKind::int_const
        && power_is_already_known(expr, ty, static_cast<ir::IntConst*>(base), exponent))
      return fresh_undef(error_ty());
    return raised_by(builder, base, ty, exponent, expr->span);
  }

  std::tuple<ir::Value*, const ir::Type*, ir::Value*, const ir::IntType*>
  Checker::sides_of_a_power(ir::Builder& builder, const ast::Expr* expr, const ast::Expr* written,
                            const ast::Expr* exponent, const ir::Type* expected)
  {
    auto context = expected != nullptr ? scalar_of(aiming_at(expected)) : hint_of(written);
    auto outer = operand_of;
    operand_of = std::string(describe(ast::BinaryOp::power));
    auto was_listing = listing;
    listing = true;
    ir::Value* base = nullptr;
    try {
      base = lower_expr(builder, written, context);
    } catch (...) {
      operand_of = outer;
      listing = was_listing;
      throw;
    }
    operand_of = outer;
    listing = was_listing;
    auto ty = scalar_of(value_type_of(base));
    if (ty != nullptr && ty != error_ty() && ! ir::is<ir::IntType>(ty) && ! ir::is<ir::FloatType>(ty)) [[unlikely]] {
      diags.emit(D::LANG_POWER_BASE_NOT_A_NUMBER, written->span, { { "found", ty->written() } });
      ty = error_ty();
    }
    if (exponent == nullptr)
      return { base, ty == error_ty() ? nullptr : ty, fresh_undef(error_ty()), nullptr };
    // A count with nothing to say what width it is, is the widest there is.
    auto lit = ast::as<ast::IntLit>(exponent);
    auto times = lower_expr(builder, exponent, lit != nullptr && ! lit->type_name ? ir::u64() : nullptr);
    auto count_ty = value_type_of(times);
    auto count = ir::as<ir::IntType>(count_ty);
    if (count == nullptr) {
      if (count_ty != error_ty()) [[unlikely]]
        diags.emit(D::LANG_POWER_EXPONENT_NOT_A_COUNT, exponent->span, { { "found", count_ty->written() } });
      return { base, nullptr, times, nullptr };
    }
    if (ty == error_ty() || ty == nullptr)
      return { base, nullptr, times, count };
    return { base, ty, times, count };
  }

  ir::Value* Checker::reciprocal(ir::Builder& builder, ir::Value* base, const ir::Type* ty, __int128 times,
                                 const ir::Type* answer, Span span)
  {
    auto raised = raised_by(builder, base, ty, times, span);
    return reciprocal_of(builder, raised, ty, answer, span);
  }

  ir::Value* Checker::reciprocal_of(ir::Builder& builder, ir::Value* raised, const ir::Type* ty, const ir::Type* answer,
                                    Span span)
  {
    if (ir::is<ir::FloatType>(ty))
      return builder.binary(ir::BinOp::fdiv, unity(builder, ty, span), raised, span, answer);
    auto integer = ir::as<ir::IntType>(ty);
    return builder.binary(integer->is_signed ? ir::BinOp::sdiv : ir::BinOp::udiv, unity(builder, ty, span), raised,
                          span, answer);
  }

  bool Checker::power_is_already_known(const ast::Expr* expr, const ir::Type* ty, const ir::IntConst* base,
                                       __int128 times)
  {
    if (wrapping)
      return false;
    auto integer = ir::as<ir::IntType>(ty);
    auto answer = power_of(base->value, times);
    if (answer && integer->holds(*answer))
      return false;
    auto text = answer ? decimal(*answer) : power_decimal(base->value, times);
    diags.emit(D::LANG_TYPE_ANSWER_DOES_NOT_FIT, expr->span, { { "value", text }, { "type", ty->written() } });
    return true;
  }

  ir::Value* Checker::unity(ir::Builder& builder, const ir::Type* ty, Span span)
  {
    (void) span;
    if (ir::is<ir::FloatType>(ty))
      return builder.float_const(ty, 1.0);
    return builder.int_const(ty, 1);
  }

  ir::Value* Checker::raised_by(ir::Builder& builder, ir::Value* base, const ir::Type* ty, __int128 times, Span span)
  {
    if (times == 0)
      return unity(builder, ty, span);
    ir::Value* found = nullptr;
    auto power = base;
    auto left = times;
    for (;;) {
      if ((left & 1) != 0)
        found = found == nullptr ? power : builder.binary(wrapped(ir::BinOp::mul), found, power, span);
      left >>= 1;
      if (left == 0)
        return found;
      power = builder.binary(wrapped(ir::BinOp::mul), power, power, span);
    }
  }

  ir::Value* Checker::raised_over(ir::Builder& builder, ir::Value* base, const ir::Type* ty, ir::Value* times,
                                  const ir::IntType* count, const ir::Type* answer, Span span)
  {
    if (! count->is_signed) {
      // No sign to look at: there is no answer it has not got.
      auto raised = power_loop(builder, base, ty, times, count, span);
      return builder.wrap(raised, builder.bool_const(false), answer, span);
    }
    auto negative = builder.compare(ir::CmpPred::slt, times, builder.int_const(count, 0), span);
    auto flip = builder.new_block("power.flip");
    auto keep = builder.new_block("power.keep");
    auto sized = builder.new_block("power.by");
    builder.condbr(negative, flip, keep, {}, {}, span);
    builder.position_at(flip);
    builder.br(sized, std::vector<ir::Value*>{ builder.binary(ir::BinOp::wrap_sub, builder.int_const(count, 0), times, span) },
               span);
    builder.position_at(keep);
    builder.br(sized, std::vector<ir::Value*>{ times }, span);
    builder.position_at(sized);
    auto magnitude = sized->add_param(count, "times");
    auto raised = power_loop(builder, base, ty, magnitude, count, span);
    auto over = builder.new_block("power.over");
    auto plain = builder.new_block("power.plain");
    auto finish = builder.new_block("power.answered");
    builder.condbr(negative, over, plain, {}, {}, span);
    builder.position_at(over);
    // One divided by the power, which is what a negative exponent means.
    auto divided = reciprocal_of(builder, raised, ty, answer, span);
    auto value_part = builder.unwrap(divided, ty, span);
    auto failed_part = builder.failed(divided, span);
    builder.br(finish, std::vector<ir::Value*>{ value_part, failed_part }, span);
    builder.position_at(plain);
    builder.br(finish, std::vector<ir::Value*>{ raised, builder.bool_const(false) }, span);
    builder.position_at(finish);
    // The two halves rather than the whole.
    auto value = finish->add_param(ty, "raised");
    auto bad = finish->add_param(bool_ty(), "failed");
    return builder.wrap(value, bad, answer, span);
  }

  ir::Value* Checker::power_loop(ir::Builder& builder, ir::Value* base, const ir::Type* ty, ir::Value* times,
                                 const ir::IntType* count, Span span)
  {
    auto one = unity(builder, ty, span);
    auto start = builder.new_block("raise");
    auto header = builder.new_block("raising");
    auto folding = builder.new_block("fold");
    auto skipping = builder.new_block("skip");
    auto after = builder.new_block("folded");
    auto again = builder.new_block("square");
    auto last = builder.new_block("raised.last");
    auto none = builder.new_block("raised.none");
    auto done = builder.new_block("raised");
    // Raised to no power at all is one, and it is the only turn count the loop
    // below cannot take.
    auto zero = builder.int_const(count, 0);
    builder.condbr(builder.compare(ir::CmpPred::eq, times, zero, span), none, start, {}, {}, span);
    builder.position_at(none);
    builder.br(done, std::vector<ir::Value*>{ one }, span);
    builder.position_at(start);
    builder.br(header, std::vector<ir::Value*>{ one, base, times }, span);
    builder.position_at(header);
    // The answer so far, the power of the base this bit stands for, and the
    // bits left to look at.
    auto found = header->add_param(ty, "found");
    auto power = header->add_param(ty, "power");
    auto left = header->add_param(count, "left");
    auto low = builder.binary(ir::BinOp::and_, left, builder.int_const(count, 1), span);
    auto set = builder.compare(ir::CmpPred::ne, low, builder.int_const(count, 0), span);
    builder.condbr(set, folding, skipping, {}, {}, span);
    builder.position_at(folding);
    builder.br(after, std::vector<ir::Value*>{ builder.binary(wrapped(ir::BinOp::mul), found, power, span) }, span);
    builder.position_at(skipping);
    builder.br(after, std::vector<ir::Value*>{ found }, span);
    builder.position_at(after);
    auto carried_found = after->add_param(ty, "found");
    auto rest = builder.binary(ir::BinOp::wrap_lshr, left, builder.int_const(count, 1), span);
    auto ended = builder.compare(ir::CmpPred::eq, rest, builder.int_const(count, 0), span);
    builder.condbr(ended, last, again, {}, {}, span);
    builder.position_at(last);
    builder.br(done, std::vector<ir::Value*>{ carried_found }, span);
    builder.position_at(again);
    auto squared = builder.binary(wrapped(ir::BinOp::mul), power, power, span);
    builder.br(header, std::vector<ir::Value*>{ carried_found, squared, rest }, span);
    builder.position_at(done);
    return done->add_param(ty, "raised");
  }

  ir::Value* Checker::lower_concat(ir::Builder& builder, const ast::Binary* expr, const ir::Type* expected)
  {
    auto left = lower_expr(builder, expr->left, nullptr);
    // What is wanted of the right is what the left turned out to be, where
    // that type says nothing about how many there are.
    auto held = value_type_of(left);
    auto want = held == ir::str_type() || (ir::is<ir::ListType>(held) && ! may_be_an_array(expr->right)) ? held
                                                                                                    : nullptr;
    auto right = lower_expr(builder, expr->right, want);
    if (value_type_of(left) == ir::str_type() || value_type_of(right) == ir::str_type())
      return joined_text(builder, expr, left, right, expected);
    if (ir::is<ir::ListType>(value_type_of(left)) || ir::is<ir::ListType>(value_type_of(right)))
      return joined_list(builder, expr, left, right, expected);
    auto joined_ty = joined_type(expr, left, right);
    if (joined_ty == nullptr)
      return fresh_undef(error_ty());
    auto place = builder.frame(joined_ty, expr->span);
    std::int64_t at = 0;
    for (auto side : { left, right }) {
      auto ty = ir::as<ir::ArrayType>(value_type_of(side));
      copied(builder, side, ty, place, joined_ty->element, at, expr->span);
      at += std::int64_t(*ty->count());
    }
    auto found = builder.cast(ir::CastKind::bitcast, place, joined_ty, expr->span);
    if (! accepts(expected, joined_ty)) [[unlikely]] {
      report_mismatch(expr->span, joined_ty, expected);
      return fresh_undef(error_ty());
    }
    return found;
  }

  ir::Value* Checker::joined_list(ir::Builder& builder, const ast::Binary* expr, ir::Value* left, ir::Value* right,
                                  const ir::Type* expected)
  {
    if (ir::is<ir::ArrayType>(value_type_of(left)) || ir::is<ir::ArrayType>(value_type_of(right)))
      return joined_with_a_view(builder, expr, left, right, expected);
    const ir::Type* holds = nullptr;
    for (auto [side, value] : { std::pair{ expr->left, left }, std::pair{ expr->right, right } }) {
      auto found = value_type_of(value);
      auto list = ir::as<ir::ListType>(found);
      if (list == nullptr) {
        if (found != error_ty()) [[unlikely]]
          diags.emit(D::LANG_CONCAT_NEEDS_AN_ARRAY, side->span, { { "found", found->written() } });
        return fresh_undef(error_ty());
      }
      if (holds == nullptr)
        holds = list->element;
      else if (list->element != holds) [[unlikely]] {
        diags.emit(D::LANG_CONCAT_ELEMENTS_DIFFER, expr->span,
                   { { "left", value_type_of(left)->written() }, { "right", value_type_of(right)->written() } });
        return fresh_undef(error_ty());
      }
    }
    auto answer = ir::list_type(holds);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return fresh_undef(error_ty());
    }
    // What the elements of either side point into goes where the answer is
    // made.
    auto owner = owner_of(out_of_arenas);
    std::vector<ir::Value*> spare;
    KeySet made_in;
    if (owner && owned::points(holds)) {
      // A side whose elements the answer may not share is copied first, and a
      // run only the join reads is given back after it.
      std::vector<ir::Value*> sides;
      for (auto [written, value] : { std::pair{ expr->left, left }, std::pair{ expr->right, right } }) {
        auto [one, added, moved] = stored(builder, written, value, answer, *owner, std::int64_t(scopes.size()), true,
                                          written->span);
        if (value_type_of(one) == error_ty())
          return fresh_undef(error_ty());
        sides.push_back(one);
        made_in.insert(added.begin(), added.end());
        if (one != value) {
          spare.push_back(one);
          if (moved != nullptr)
            spare.push_back(moved);
        } else if (*owner == heap_key && freshly_heap(written))
          spare.push_back(one);
      }
      left = sides[0];
      right = sides[1];
    }
    KeySet from = out_of_arenas;
    from.insert(made_in.begin(), made_in.end());
    made_from[id_of(expr)] = { expr, from };
    auto stride = builder.int_const(ir::u64(), __int128(ir::stride_of(holds, layout())));
    auto pointer = ir::parts_of(answer)[0];
    auto bytes = ir::ptr_type(ir::u8(), true);
    std::vector<ir::Value*> counts;
    counts.push_back(builder.extract(left, 1, ir::u64(), expr->span));
    counts.push_back(builder.extract(right, 1, ir::u64(), expr->span));
    auto join = lists::join_function(module);
    std::vector<ir::Value*> args;
    args.push_back(out_of(builder, expr->span));
    args.push_back(builder.cast(ir::CastKind::bitcast, builder.extract(left, 0, pointer, expr->span), bytes, expr->span));
    args.push_back(counts[0]);
    args.push_back(builder.cast(ir::CastKind::bitcast, builder.extract(right, 0, pointer, expr->span), bytes, expr->span));
    args.push_back(counts[1]);
    args.push_back(stride);
    auto made = builder.call(join, args, bytes, expr->span);
    for (auto one : spare)
      owned::give_back_storage(builder, one, answer, owner && *owner == heap_key ? "heap" : "pool");
    std::vector<ir::Value*> parts;
    parts.push_back(builder.cast(ir::CastKind::bitcast, made, pointer, expr->span));
    parts.push_back(builder.binary(ir::BinOp::wrap_add, counts[0], counts[1], expr->span));
    parts.push_back(out_of(builder, expr->span));
    return builder.make_tuple(parts, answer, expr->span);
  }


  ir::Value* Checker::joined_with_a_view(ir::Builder& builder, const ast::Binary* expr, ir::Value* left,
                                         ir::Value* right, const ir::Type* expected)
  {
    auto list = ir::as<ir::ListType>(value_type_of(left));
    if (list == nullptr)
      list = ir::as<ir::ListType>(value_type_of(right));
    auto holds = list->element;
    auto wanted = ir::array_type(holds, { std::nullopt });
    auto span = expr->span;
    std::vector<ir::Value*> views;
    for (auto [written, value] : { std::pair{ expr->left, left }, std::pair{ expr->right, right } }) {
      auto found = value_type_of(value);
      if (found == error_ty())
        return fresh_undef(error_ty());
      bool runs = ir::is<ir::ArrayType>(found) || ir::is<ir::ListType>(found);
      auto view = runs ? as_a_view(builder, written, value, wanted, written->span) : nullptr;
      if (view == nullptr) [[unlikely]] {
        if (runs)
          diags.emit(D::LANG_CONCAT_ELEMENTS_DIFFER, span,
                     { { "left", value_type_of(left)->written() }, { "right", value_type_of(right)->written() } });
        else
          diags.emit(D::LANG_CONCAT_NEEDS_AN_ARRAY, written->span, { { "found", found->written() } });
        return fresh_undef(error_ty());
      }
      views.push_back(view);
    }
    if (! view_copied(holds, expr)) [[unlikely]]
      return fresh_undef(error_ty());
    auto answer = ir::list_type(holds);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(span, answer, expected);
      return fresh_undef(error_ty());
    }
    made_from[id_of(expr)] = { expr, out_of_arenas };
    std::vector<ir::Value*> counts;
    for (auto one : views)
      counts.push_back(builder.extract(one, 1, ir::u64(), span));
    auto total = builder.binary(ir::BinOp::wrap_add, counts[0], counts[1], span);
    auto where = out_of(builder, span);
    auto room_function = lists::room_function(module);
    auto stride = builder.int_const(ir::u64(), __int128(ir::stride_of(holds, layout())));
    auto room = builder.call(room_function, std::vector<ir::Value*>{ where, total, stride }, ir::ptr_type(ir::u8(), true),
                             span);
    joined_on(builder, room, builder.int_const(ir::u64(), 0), views[0], holds, span);
    joined_on(builder, room, counts[0], views[1], holds, span);
    auto start = builder.cast(ir::CastKind::bitcast, room, ir::parts_of(answer)[0], span);
    return builder.make_tuple(std::vector<ir::Value*>{ start, total, where }, answer, span);
  }

} // namespace pl4g::sema
