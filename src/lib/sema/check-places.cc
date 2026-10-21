// The checker, part of the way through "a generic function checked where it is
// written" up to naming a loop and leaving or repeating it: how long what a
// reference reaches lasts, the places a reference is taken of, sets and
// dictionaries written down, lists and the cursors over them, `if`, and the
// loops.  A port of pypl4g's sema/check.py, lines 15128 to 18052.
#include "sema/check.hh"

#include <algorithm>
#include <format>

#include "diag/ids.hh"
#include "front/token.hh"
#include "sema/lists.hh"
#include "sema/owned.hh"
#include "sema/strings.hh"
#include "sema/tables.hh"
#include "target/statuses.hh"


namespace pl4g::sema {

  namespace {

    // The names the language gives the compiler's own (front/token.py).
    constexpr std::string_view heap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";
    constexpr std::string_view is_record_name = "\N{APL FUNCTIONAL SYMBOL QUAD}isrecord";
    constexpr std::string_view typeof_name = "\N{APL FUNCTIONAL SYMBOL QUAD}typeof";
    constexpr std::string_view reserve_name = "\N{APL FUNCTIONAL SYMBOL QUAD}reserve";
    constexpr std::string_view iter_name = "\N{APL FUNCTIONAL SYMBOL QUAD}iter";
    constexpr std::string_view enumerate_name = "\N{APL FUNCTIONAL SYMBOL QUAD}enumerate";
    constexpr std::string_view wildcard_name = "_";

    // Python's `UndefConst(ty)`, as a value an instruction may take.
    ir::Value* undef_of(ir::Module& module, const ir::Type* ty)
    {
      return const_cast<ir::UndefConst*>(module.undef(ty));
    }

    // What a construct that was refused comes to: `UndefConst(ERROR)`.
    ir::Value* undef_error(ir::Module& module)
    {
      return undef_of(module, ir::error_type());
    }

    // `value` as an instruction of shape `kind`, or nothing.
    ir::Instruction* inst_of(ir::Value* value, ir::InstKind kind) noexcept
    {
      if (value == nullptr || value->vkind != ir::ValueKind::instruction)
        return nullptr;
      auto inst = static_cast<ir::Instruction*>(value);
      return inst->kind == kind ? inst : nullptr;
    }

    // `value` as an instruction of any shape, or nothing.
    ir::Instruction* any_inst(ir::Value* value) noexcept
    {
      return value != nullptr && value->vkind == ir::ValueKind::instruction ? static_cast<ir::Instruction*>(value)
                                                                            : nullptr;
    }

    // `value` as a variable of the module, or nothing.
    ir::GlobalVar* global_of(ir::Value* value) noexcept
    {
      return value != nullptr && value->vkind == ir::ValueKind::global ? static_cast<ir::GlobalVar*>(value) : nullptr;
    }

    // Whether `inst` is a cast, an extract, or an addition or subtraction:
    // what keeps a reference pointing into the place its first operand does.
    bool keeps_pointing(ir::Instruction* inst, bool tuples = false) noexcept
    {
      if (inst == nullptr)
        return false;
      if (inst->kind == ir::InstKind::CastInst || inst->kind == ir::InstKind::ExtractInst)
        return true;
      if (tuples && inst->kind == ir::InstKind::TupleInst)
        return true;
      return inst->kind == ir::InstKind::BinaryInst && (inst->bin == ir::BinOp::add || inst->bin == ir::BinOp::sub);
    }

    // `FuncType.borrowed()`: the borrows as a table.
    Dict<std::int64_t, ir::Borrowed> borrowed_table(const ir::FuncType* ty)
    {
      Dict<std::int64_t, ir::Borrowed> table;
      if (ty != nullptr)
        for (const auto& [leaf, one] : ty->borrows)
          table[leaf] = one;
      return table;
    }

    // `Borrowed.leaves_of`: the parts of parameter `param` the name stands on,
    // or nothing where it stands on the whole of it.
    std::optional<std::vector<int>> leaves_borrowed(const ir::Borrowed& borrowed, std::int64_t param)
    {
      for (std::size_t at = 0; at < borrowed.params.size(); ++at)
        if (borrowed.params[at] == param)
          return at < borrowed.leaves.size() ? borrowed.leaves[at] : std::nullopt;
      return std::nullopt;
    }

    // The name a lifetime is written with.
    std::string lifetime_written(std::string_view name)
    {
      return std::string("\N{WHITE HOURGLASS}") + std::string(name);
    }

  } // anonymous namespace


  bool Checker::lasting(ir::Value* value)
  {
    ir::Value* seen = value;
    while (true) {
      if (global_of(seen) != nullptr)
        return true;
      if (auto inst = inst_of(seen, ir::InstKind::AddressInst)) {
        seen = inst->operands[0];
        continue;
      }
      if (auto inst = any_inst(seen); keeps_pointing(inst)) {
        seen = inst->operands[0];
        continue;
      }
      auto ptr = ir::as<ir::PtrType>(seen->ty);
      return ptr != nullptr && ptr->lasting;
    }
  }


  void Checker::answers_from(ir::Function* func, std::string_view name, std::span<ir::Value* const> sources,
                             const Dict<std::int64_t, KeySet>& only, const KeySet& stored)
  {
    // Which branches reach each block, so that the walk can go through a
    // block's parameter.
    auto incoming = incoming_of(func);
    auto outer = lifetime_outer();
    for (const auto& block : func->blocks)
      for (auto inst : block->insts) {
        if (inst->kind != ir::InstKind::RetInst || inst->operands.empty())
          continue;
        ir::Value* value = inst->operands[0];
        if (lasting(value) || reached_from(value, sources, &incoming, nullptr, &only, &outer, std::nullopt, stored))
          continue;
        diags.emit(D::LANG_BORROW_NOT_FROM_IT, inst->span, { { "name", name } });
      }
  }


  void Checker::answered_as_said(ir::Function* func, const Dict<std::int64_t, ir::Borrowed>& table,
                                 std::span<const std::string> names, std::span<ir::Value* const> arriving)
  {
    if (auto whole = table.get(-1)) {
      auto [called, sources, only, stored] = sources_for(*whole, names, arriving);
      answers_from(func, called, sources, only, stored);
    }
    Dict<std::int64_t, std::tuple<std::string, std::vector<ir::Value*>, Dict<std::int64_t, KeySet>, KeySet>> members;
    for (const auto& [leaf, one] : table)
      if (leaf >= 0)
        members[leaf] = sources_for(one, names, arriving);
    if (! members.empty())
      members_answered_from(func, members);
  }


  std::tuple<std::string, std::vector<ir::Value*>, Dict<std::int64_t, KeySet>, KeySet>
  Checker::sources_for(const ir::Borrowed& borrowed, std::span<const std::string> names,
                       std::span<ir::Value* const> arriving)
  {
    std::vector<ir::Value*> sources;
    Dict<std::int64_t, KeySet> only;
    KeySet stored;
    std::vector<std::string> called;
    for (auto at : borrowed.params) {
      if (std::size_t(at) >= arriving.size() || std::size_t(at) >= names.size())
        continue;
      called.push_back(names[at]);
      std::vector<ir::Value*> values{ arriving[at] };
      Local* local = find_local(names[at]);
      if (local != nullptr && local->placed) {
        values.push_back(local->value);
        stored.insert(id_of(local->value));
      }
      auto leaves = leaves_borrowed(borrowed, at);
      for (auto one : values) {
        sources.push_back(one);
        if (leaves)
          only[id_of(one)] = KeySet(leaves->begin(), leaves->end());
      }
    }
    for (const auto& [key, name] : borrowed.outer)
      if (auto entries = lifetime_sources.get({ key, name }))
        for (const auto& [value, leaf, named, kept] : *entries) {
          sources.push_back(value);
          if (kept)
            stored.insert(id_of(value));
          if (std::ranges::find(called, named) == called.end())
            called.push_back(named);
          if (leaf)
            only[id_of(value)].insert(*leaf);
        }
    if (called.empty())
      // A name of the signature around that nothing brought in stands for:
      // there is nothing here to call it but the name.
      for (const auto& [_, one] : borrowed.outer)
        called.push_back(lifetime_written(one));
    std::string joined;
    for (const auto& one : called) {
      if (! joined.empty())
        joined += ", ";
      joined += one;
    }
    return { std::move(joined), std::move(sources), std::move(only), std::move(stored) };
  }


  Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>> Checker::lifetime_outer()
  {
    Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>> outer;
    for (const auto& [pair, entries] : lifetime_sources) {
      auto& values = outer[{ std::get<0>(pair), std::get<1>(pair) }];
      for (const auto& entry : entries)
        values.push_back(std::get<0>(entry));
    }
    return outer;
  }


  Checker::LifetimesSaved Checker::enter_lifetimes(std::string_view key, std::span<const ast::Param* const> params,
                                                   std::span<const ir::Type* const> types,
                                                   std::span<ir::Value* const> arriving,
                                                   std::span<const std::tuple<Local*, Local*, bool>> brought)
  {
    LifetimesSaved saved{ lifetime_scopes, lifetime_sources };
    NameSet names;
    for (auto one : params) {
      auto more = lifetimes_of(one->type);
      names.insert(more.begin(), more.end());
    }
    lifetime_scopes.emplace_back(std::string(key), std::move(names));
    Dict<std::tuple<std::string, std::string>, std::vector<std::tuple<ir::Value*, std::optional<std::int64_t>, std::string, bool>>>
      found;
    for (const auto& [outside, inside, by_reference] : brought)
      for (const auto& [pair, entries] : saved.sources)
        for (const auto& [value, leaf, _, kept] : entries)
          if (value == outside->value)
            found[pair].emplace_back(inside->value, leaf, inside->name, kept || by_reference);
    for (std::size_t at = 0; at < params.size() && at < types.size() && at < arriving.size(); ++at) {
      const ast::Param* param = params[at];
      ir::Value* value = arriving[at];
      Local* local = find_local(param->name);
      std::vector<std::pair<ir::Value*, bool>> values{ { value, false } };
      if (local != nullptr && local->placed && local->value != value)
        values.emplace_back(local->value, true);
      for (const auto& name : lifetimes_of(param->type)) {
        auto leaves = leaves_named(param->type, types[at], NameSet{ name });
        auto& entries = found[{ std::string(key), name }];
        for (const auto& [one, kept] : values)
          if (! leaves)
            entries.emplace_back(one, std::nullopt, std::string(param->name), kept);
          else
            for (auto leaf : *leaves)
              entries.emplace_back(one, leaf, std::string(param->name), kept);
      }
    }
    lifetime_sources = std::move(found);
    return saved;
  }


  void Checker::members_answered_from(
    ir::Function* func,
    const Dict<std::int64_t, std::tuple<std::string, std::vector<ir::Value*>, Dict<std::int64_t, KeySet>, KeySet>>&
      sources)
  {
    auto incoming = incoming_of(func);
    auto outer = lifetime_outer();
    for (const auto& block : func->blocks)
      for (auto inst : block->insts) {
        if (inst->kind != ir::InstKind::RetInst || inst->operands.empty())
          continue;
        for (const auto& [leaf, entry] : sources) {
          const auto& [name, given, only, stored] = entry;
          KeySet visited;
          if (leaf_reached(inst->operands[0], leaf, given, incoming, visited, only, outer, stored))
            continue;
          diags.emit(D::LANG_BORROW_NOT_FROM_IT, inst->span, { { "name", name } });
        }
      }
  }


  bool Checker::leaf_reached(ir::Value* value, std::int64_t leaf, std::span<ir::Value* const> sources,
                             const Incoming& incoming, KeySet& visited, const Dict<std::int64_t, KeySet>& only,
                             const Dict<std::pair<std::string, std::string>, std::vector<ir::Value*>>& outer,
                             const KeySet& stored)
  {
    if (auto tuple = inst_of(value, ir::InstKind::TupleInst)) {
      ir::Value* part = tuple->operands[leaf];
      return lasting(part) || reached_from(part, sources, &incoming, nullptr, &only, &outer, std::nullopt, stored);
    }
    if (value->vkind == ir::ValueKind::block_param) {
      auto param = static_cast<ir::BlockParam*>(value);
      if (visited.contains(id_of(value)))
        return true;
      visited.insert(id_of(value));
      auto found = incoming.find(param->block);
      if (found == incoming.end() || found->second.empty())
        return false;
      for (auto arm : found->second)
        if (! (param->index < arm->args.size()
               && leaf_reached(arm->args[param->index], leaf, sources, incoming, visited, only, outer, stored)))
          return false;
      return true;
    }
    if (inst_of(value, ir::InstKind::CallInst) != nullptr)
      return reached_from(value, sources, &incoming, nullptr, &only, &outer, leaf, stored);
    if (auto cast = inst_of(value, ir::InstKind::CastInst))
      return leaf_reached(cast->operands[0], leaf, sources, incoming, visited, only, outer, stored);
    return false;
  }


  ir::Value* Checker::as_long_as_given(ir::Builder& builder, ir::Function* func, std::span<ir::Value* const> args,
                                       ir::Value* answer, Span span)
  {
    return lasting_as_given(builder, func->name, func->param_names, borrowed_table(func->fty), args, answer, span);
  }


  ir::Value* Checker::lasting_as_given(ir::Builder& builder, std::string_view name,
                                       std::span<const std::string> param_names,
                                       const Dict<std::int64_t, ir::Borrowed>& table, std::span<ir::Value* const> args,
                                       ir::Value* answer, Span span)
  {
    if (ir::is<ir::TupleType>(answer->ty)
        && std::ranges::any_of(table, [](const auto& entry) { return entry.first >= 0; }))
      return members_as_long_as_given(builder, name, param_names, table, args, answer, span);
    const ir::Borrowed* borrowed = table.get(-1);
    auto ptr = ir::as<ir::PtrType>(answer->ty);
    if (borrowed == nullptr || ptr == nullptr)
      return answer;
    auto parts = borrowed_parts(args, *borrowed);
    if (parts.empty() && borrowed->outer.empty())
      return answer;
    bool lasts = borrowed->outer.empty() && std::ranges::all_of(parts, [this](const auto& part) {
      return ! std::get<1>(part).has_value() && lasting(std::get<0>(part));
    });
    module.record("lifetime", std::string(name), how_long(param_names, *borrowed, parts, lasts), span);
    if (ptr->lasting || ! lasts)
      return answer;
    return builder.cast(ir::CastKind::bitcast, answer, ir::ptr_type(ptr->pointee, ptr->is_mutable, true), span);
  }


  ir::Value* Checker::members_as_long_as_given(ir::Builder& builder, std::string_view name,
                                               std::span<const std::string> param_names,
                                               const Dict<std::int64_t, ir::Borrowed>& table,
                                               std::span<ir::Value* const> args, ir::Value* answer, Span span)
  {
    auto ty = ir::as<ir::TupleType>(answer->ty);
    auto pieces = ir::parts_of(ty);
    std::vector<ir::Value*> leaves;
    for (std::size_t at = 0; at < pieces.size(); ++at)
      leaves.push_back(builder.extract(answer, at, pieces[at], span));
    bool changed = false;
    std::vector<std::pair<std::int64_t, std::int64_t>> member_of;
    {
      std::int64_t at = 0;
      for (const auto& [first, _] : ir::parts_within(ty))
        member_of.emplace_back(first, at++);
    }
    auto member_at = [&](std::int64_t leaf) {
      std::int64_t found = leaf;
      for (const auto& [first, at] : member_of)
        if (first == leaf)
          found = at;
      return found;
    };
    for (const auto& [leaf, borrowed] : table) {
      if (leaf < 0 || std::size_t(leaf) >= pieces.size() || ! ir::is<ir::PtrType>(pieces[leaf]))
        continue;
      auto parts = borrowed_parts(args, borrowed);
      if (parts.empty() && borrowed.outer.empty())
        continue;
      bool lasts = borrowed.outer.empty() && std::ranges::all_of(parts, [this](const auto& part) {
        return ! std::get<1>(part).has_value() && lasting(std::get<0>(part));
      });
      module.record("lifetime", std::string(name),
                    std::format("part {} of the answer {}", member_at(leaf) + 1,
                                how_long(param_names, borrowed, parts, lasts)),
                    span);
      auto piece = ir::as<ir::PtrType>(pieces[leaf]);
      if (lasts && ! piece->lasting) {
        leaves[leaf] = builder.cast(ir::CastKind::bitcast, leaves[leaf],
                                    ir::ptr_type(piece->pointee, piece->is_mutable, true), span);
        changed = true;
      }
    }
    if (! changed)
      return answer;
    std::vector<const ir::Type*> members = ty->members;
    // Python's dict comprehension keeps the last leaf for a member listed twice.
    Dict<std::int64_t, std::int64_t> firsts;
    for (const auto& [first, at] : member_of)
      firsts[first] = at;
    for (const auto& [leaf, at] : firsts)
      if (ir::is<ir::PtrType>(members[at]) && leaves[leaf]->ty != members[at])
        members[at] = leaves[leaf]->ty;
    return builder.make_tuple(leaves, ir::tuple_type(std::move(members)), span);
  }


  std::string Checker::how_long(std::span<const std::string> param_names, const ir::Borrowed& borrowed,
                                std::span<const std::tuple<ir::Value*, std::optional<std::int64_t>>> parts,
                                bool lasts)
  {
    bool many = borrowed.params.size() > 1;
    std::string one, several, every;
    if (std::ranges::all_of(borrowed.params, [&](int at) {
          return std::size_t(at) < param_names.size() && ! param_names[at].empty();
        })) {
      std::vector<std::string> those;
      for (auto at : borrowed.params)
        those.push_back(param_names[at]);
      std::string named = and_then(those);
      one = "the argument for " + named;
      several = "the arguments for " + named;
      every = "every argument for " + named;
    } else {
      // Called through a value, whose type names no parameter: they are told
      // apart by where they stand.
      std::vector<std::string> numbers;
      for (auto at : borrowed.params)
        numbers.push_back(std::to_string(at + 1));
      std::string named;
      if (many) {
        for (std::size_t i = 0; i + 1 < numbers.size(); ++i) {
          if (i > 0)
            named += ", ";
          named += numbers[i];
        }
        named += " and " + numbers.back();
      } else
        for (const auto& n : numbers)
          named += n;
      one = several = every = std::string(many ? "arguments" : "argument") + " " + named;
    }
    if (borrowed.params.empty()) {
      std::vector<std::string> outer;
      for (const auto& [_, name] : borrowed.outer)
        outer.push_back(lifetime_written(name));
      return "answers with a reference that lives as long as what " + and_then(outer)
             + " stands for where the lambda was written";
    }
    if (lasts)
      return "answers with a reference that lasts as long as the program, because " + (many ? every : one) + " does";
    std::vector<Local*> found;
    for (const auto& [value, leaf] : parts)
      if (Local* place = named_place_of(value, leaf); place != nullptr)
        found.push_back(place);
    if (found.empty())
      return "answers with a reference that lives as long as this call, which is all " + (many ? several : one)
             + " said";
    // Every one of the shortest-lived, not the first of them.
    std::int64_t deepest_at = 0;
    for (auto place : found)
      deepest_at = std::max(deepest_at, place->depth);
    std::vector<std::string> shortest;
    for (auto place : found)
      if (place->depth == deepest_at)
        shortest.push_back(place->name);
    return "answers with a reference that lives as long as " + and_then(shortest) + ", "
           + (many ? "the shortest-lived of what " + several : "which is what " + one) + " named";
  }


  Local* Checker::named_place_of(ir::Value* value, std::optional<std::int64_t> leaf)
  {
    ir::Value* seen = value;
    while (true) {
      if (auto found = places.get(id_of(seen)))
        return *found;
      if (auto tuple = inst_of(seen, ir::InstKind::TupleInst)) {
        if (leaf && std::size_t(*leaf) < tuple->operands.size()) {
          seen = tuple->operands[*leaf];
          leaf.reset();
          continue;
        }
        std::vector<Local*> each;
        for (auto one : tuple->operands)
          each.push_back(named_place_of(one));
        return deepest(each);
      }
      if (auto call = inst_of(seen, ir::InstKind::CallInst)) {
        auto [given, table] = call_borrows(call);
        std::vector<ir::Borrowed> borrowed;
        if (leaf) {
          if (auto one = table.get(*leaf))
            borrowed.push_back(*one);
        } else if (auto one = table.get(-1))
          borrowed.push_back(*one);
        else
          for (const auto& [_, one] : table)
            borrowed.push_back(one);
        std::vector<Local*> each;
        for (const auto& one : borrowed)
          for (const auto& [part, at] : borrowed_parts(given, one))
            each.push_back(named_place_of(part, at));
        return deepest(each);
      }
      if (auto extract = inst_of(seen, ir::InstKind::ExtractInst);
          extract != nullptr && ir::is<ir::TupleType>(extract->operands[0]->ty)) {
        seen = extract->operands[0];
        leaf = extract->index;
        continue;
      }
      if (auto inst = any_inst(seen); keeps_pointing(inst)) {
        seen = inst->operands[0];
        continue;
      }
      return nullptr;
    }
  }


  std::vector<ir::Value*> Checker::parameter_sources(std::int64_t at)
  {
    auto node = ast::as<ast::FuncDef>(std::get<0>(demanding));
    if (node == nullptr || std::size_t(at) >= node->params.size())
      return {};
    std::string_view name = node->params[at]->name;
    std::vector<ir::Value*> found;
    if (auto entry = at_entry.get(std::string(name)))
      found.push_back(*entry);
    Local* local = find_local(name);
    if (local != nullptr && local->placed)
      found.push_back(local->value);
    return found;
  }


  bool Checker::said_alike(ir::Value* place, ir::Value* value)
  {
    auto node = ast::as<ast::FuncDef>(std::get<0>(demanding));
    if (node == nullptr)
      return false;
    for (const auto& [slot, kept] : kept_pairs(node))
      if (reached_from(place, parameter_sources(slot)) && reached_from(value, parameter_sources(kept)))
        return true;
    return false;
  }


  std::optional<Dict<std::int64_t, ir::Borrowed>> Checker::adopted_borrows(const ast::Lambda* expr,
                                                                           const ir::Type* answer,
                                                                           const ir::Type* expected)
  {
    auto wanted = ir::as<ir::FuncType>(expected);
    auto ptr = ir::as<ir::PtrType>(answer);
    auto ref = ast::as<ast::RefTypeRef>(expr->ret_type);
    if (! calling_key || wanted == nullptr || ptr == nullptr || ptr->lasting || ref == nullptr || ref->lifetime)
      return std::nullopt;
    auto table = borrowed_table(wanted);
    const ir::Borrowed* said = table.get(-1);
    if (said == nullptr || ! said->params.empty()
        || std::ranges::none_of(said->outer, [this](const auto& pair) { return pair.first == *calling_key; }))
      return std::nullopt;
    Dict<std::int64_t, ir::Borrowed> adopted;
    adopted[-1] = *said;
    return adopted;
  }


  Checker::Lifetime Checker::lifetime_of_value(ir::Value* value)
  {
    if (Local* named = named_place_of(value); named != nullptr)
      return { "local", named, {} };
    if (lasting(value))
      return { "lasting", nullptr, {} };
    std::set<std::pair<std::string, std::string>> names;
    for (const auto& [pair, sources] : lifetime_outer())
      if (reached_from(value, sources))
        names.insert(pair);
    if (! names.empty())
      return { "names", nullptr, std::move(names) };
    return { "unknown", nullptr, {} };
  }


  bool Checker::lasts_as_long(const Lifetime& answer, const Lifetime& given)
  {
    if (answer.kind == "lasting")
      return true;
    if (answer.kind == "unknown" || given.kind == "lasting" || given.kind == "unknown")
      return false;
    if (given.kind == "local")
      return answer.kind == "names" || (answer.local != nullptr && answer.local->depth <= given.local->depth);
    return answer.kind == "names" && ! answer.names.empty()
           && std::ranges::includes(given.names, answer.names);
  }


  void Checker::answers_as_long(ir::Function* func, std::span<ir::Value* const> args,
                                const Dict<std::int64_t, std::vector<std::tuple<bool, Local*>>>& notes,
                                Span span)
  {
    Dict<std::string, std::vector<int>> carriers;
    for (const auto& [name, at] : func->lifetimes)
      carriers[name] = at;
    for (std::size_t at = 0; at < func->fty->params.size() && at < args.size(); ++at) {
      auto param = ir::as<ir::FuncType>(func->fty->params[at]);
      ir::Value* value = args[at];
      auto had = ir::as<ir::FuncType>(promised_as.get_or(id_of(value), value->ty));
      if (param == nullptr || had == nullptr)
        continue;
      for (const auto& [leaf, said] : param->borrows) {
        std::vector<std::string> free;
        for (const auto& [key, name] : said.outer)
          if (key == func->lifetime_key)
            free.push_back(name);
        if (free.empty())
          continue;
        std::vector<Lifetime> given;
        for (const auto& name : free)
          if (auto those = carriers.get(name))
            for (auto one : *those)
              if (std::size_t(one) < args.size())
                given.push_back(lifetime_of_value(args[one]));
        auto table = borrowed_table(had);
        const ir::Borrowed* found = table.get(leaf);
        if (found == nullptr)
          continue;
        std::vector<Lifetime> answers;
        if (auto noted = notes.get(at)) {
          for (const auto& [by_reference, local] : *noted)
            answers.push_back(by_reference ? Lifetime{ "local", local, {} } : lifetime_of_value(local->value));
        } else if (! found->outer.empty() && found->params.empty())
          answers.push_back({ "names", nullptr, { found->outer.begin(), found->outer.end() } });
        else
          continue;
        bool holds = true;
        for (const auto& one : answers)
          for (const auto& other : given)
            if (! lasts_as_long(one, other))
              holds = false;
        if (! holds) {
          std::string named;
          for (const auto& name : free)
            if (auto those = carriers.get(name))
              for (auto one : *those)
                if (std::size_t(one) < func->param_names.size()) {
                  if (! named.empty())
                    named += ", ";
                  named += "'" + func->param_names[one] + "'";
                }
          diags.emit(D::LANG_LAMBDA_ANSWERS_SHORTER, span,
                     { { "name", func->name }, { "at", std::to_string(at + 1) }, { "given", named } });
        }
      }
    }
  }


  void Checker::kept_alike(ir::Function* func, std::span<ir::Value* const> args, Span span)
  {
    for (const auto& [slot, kept] : func->keeps) {
      if (std::size_t(std::max(slot, kept)) >= args.size())
        continue;
      ir::Value* place = args[slot];
      ir::Value* value = args[kept];
      if (Local* named = named_place_of(place); named != nullptr)
        outlives_it(value, named->depth, named->name, span);
      else if (! lasting(value) && ! said_alike(place, value))
        diags.emit(D::LANG_REF_WRITTEN_INTO_A_PLACE_FROM_OUTSIDE, span);
    }
  }


  bool Checker::outlives_it(ir::Value* value, std::int64_t depth, std::string_view where, Span span)
  {
    Local* named = named_place_of(value);
    if (named == nullptr || named->depth <= depth)
      return false;
    diags.emit(D::LANG_REF_OUTLIVES_PLACE, span, { { "name", where }, { "place", named->name } })
      .note(D::LANG_VARDEF_DEFINED_HERE, named->span, { { "name", named->name } });
    return true;
  }


  std::optional<Checker::PlaceTaken> Checker::place_of_a_name(ir::Builder& builder, const ast::NameRef* expr)
  {
    if (Local* local = find_local(expr->name); local != nullptr) {
      local->read = true;
      if (notes != nullptr && local->held != nullptr)
        note(expr->span, local->is_parameter ? note_kind::parameter : note_kind::variable, expr->name,
             local->held->written(), local->span);
      if (! local->placed) [[unlikely]] {
        // Every name a reference is taken of anywhere in the body was given
        // storage before the body was walked, so a name that has none is one
        // whose type a place cannot hold.
        diags.emit(D::LANG_REF_TYPE_NOT_ALLOWED, expr->span, { { "found", held_by(local)->written() } });
        return std::nullopt;
      }
      // A name inside a call is gone when the call is, whatever it holds.
      return PlaceTaken{ local->value, local->held, local->is_mutable, std::string(expr->name), false };
    }
    Held found = provided(expr->name);
    if (auto var = found.get<ir::GlobalVar>()) {
      if (var->value_type == ir::error_type())
        return std::nullopt;
      // A variable at the top level is there for as long as the program is.
      return PlaceTaken{ builder.address(var, expr->span), var->value_type, var->is_mutable(),
                         std::string(expr->name), true };
    }
    diags.emit(D::LANG_FILESTRUCT_UNDEFINED_NAME, expr->span, { { "name", expr->name } });
    return std::nullopt;
  }


  ir::GlobalVar* Checker::exported_variable(const ast::Member* expr)
  {
    if (auto single = member_named(expr).get<ir::GlobalVar>())
      // A singleton, `T.name` or `m.T.name`: the one object the type has.
      return single;
    LoadedModule* held = module_at(expr->base);
    if (held == nullptr)
      return nullptr;
    const Held* found = held->exports.get(std::string(expr->name));
    return found != nullptr ? found->get<ir::GlobalVar>() : nullptr;
  }


  std::optional<Checker::PlaceTaken> Checker::place_of_a_field(ir::Builder& builder, const ast::Member* expr,
                                                               bool writing)
  {
    if (ir::GlobalVar* shared = exported_variable(expr); shared != nullptr)
      // The variable itself and not a field of one.
      return PlaceTaken{ builder.address(shared, expr->span), shared->value_type, shared->is_mutable(),
                         std::string(expr->name), true };
    auto found = record_at(builder, expr->base, writing);
    if (! found) [[unlikely]] {
      diags.emit(D::LANG_REF_NOT_A_PLACE, expr->span);
      return std::nullopt;
    }
    auto [where, ty] = *found;
    auto at = field_index(ty, expr->name);
    if (! at) [[unlikely]] {
      diags.emit(D::LANG_PRODUCT_NO_SUCH_FIELD, expr->name_span, { { "type", ty->written() }, { "name", expr->name } });
      return std::nullopt;
    }
    // A field lives as long as the record does, and may be written where the
    // record may.
    ir::Value* place = field_place(builder, where, ty, *at, expr->span);
    bool writable = lifted(expr->base, expr->name, ty) || may_write(expr->base);
    return PlaceTaken{ place, ty->fields[*at].second, writable, path_of(expr), lasting(where) };
  }


  std::optional<Checker::PlaceTaken> Checker::place_of_an_element(ir::Builder& builder, const ast::Element* expr,
                                                                  bool of_a_list)
  {
    ir::Value* base = lower_expr(builder, expr->base, nullptr);
    const ir::Type* ty = value_type_of(base);
    if (ty == ir::error_type())
      return std::nullopt;
    if (auto list = ir::as<ir::ListType>(ty);
        of_a_list && list != nullptr && expr->indices.size() == 1 && ! ast::is<ast::Range>(expr->indices[0])) {
      // A record that is an element is read a field at a time, so `l⟦i⟧.name`
      // asks where the element is.  No reference is taken this way -- the run
      // moves when the list grows -- and no field is written.
      ir::Value* at = index_into(builder, expr->indices[0], expr->span);
      if (at == nullptr)
        return std::nullopt;
      ir::Value* count = builder.extract(base, 1, ir::u64(), expr->span);
      builder.check(builder.compare(ir::CmpPred::ult, at, count, expr->span), "an index outside its list",
                    target::statuses::OUT_OF_RANGE, expr->span);
      ir::Value* start = builder.extract(base, 0, ir::parts_of(list)[0], expr->span);
      return PlaceTaken{ element_place(builder, start, list->element, at, expr->span), list->element, false,
                         "an element", false };
    }
    auto array = ir::as<ir::ArrayType>(ty);
    if (array == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, expr->base->span, { { "found", ty->written() } });
      return std::nullopt;
    }
    if (expr->indices.size() != array->rank()) [[unlikely]] {
      // One element, so every index: a run of them is several places and a
      // reference names one.
      diags.emit(D::LANG_ARRAY_WRONG_RANK, expr->span,
                 { { "given", expr->indices.size() }, { "wanted", array->rank() } });
      return std::nullopt;
    }
    auto [start, lengths] = shape_of(builder, base, array, expr->span);
    auto strides = strides_of(builder, base, array, expr->span);
    ir::Value* offset = offset_of(builder, expr->indices, array, lengths, strides, expr->span);
    if (offset == nullptr)
      return std::nullopt;
    ir::Value* place = element_place(builder, start, array->element, offset, expr->span);
    // An element lives as long as the array does; one in a part of the image
    // nothing may write may not be written through a reference either.
    bool writable = may_write(expr->base) && ! read_only(base);
    return PlaceTaken{ place, array->element, writable, "an element", lasting(base) };
  }


  bool Checker::lifted(const ast::Expr* base, std::string_view name, const ir::ProductType* ty)
  {
    if (std::ranges::find(ty->mutable_fields, name) != ty->mutable_fields.end())
      return ! behind_a_reader(base);
    if (auto member = ast::as<ast::Member>(base))
      if (auto above = ir::as<ir::ProductType>(record_type_of(member->base)))
        return lifted(member->base, member->name, above);
    return false;
  }


  bool Checker::behind_a_reader(const ast::Expr* expr)
  {
    while (true)
      if (auto member = ast::as<ast::Member>(expr))
        expr = member->base;
      else if (auto element = ast::as<ast::Element>(expr))
        expr = element->base;
      else if (auto index = ast::as<ast::Index>(expr))
        expr = index->base;
      else if (auto deref = ast::as<ast::Deref>(expr)) {
        auto held = ir::as<ir::PtrType>(record_type_of(deref->operand));
        if (held != nullptr && ! held->is_mutable)
          return true;
        expr = deref->operand;
      } else
        return false;
  }


  const ir::Type* Checker::record_type_of(const ast::Expr* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr)) {
      if (Local* local = find_local(name->name); local != nullptr)
        return held_by(local);
      auto var = provided(name->name).get<ir::GlobalVar>();
      return var != nullptr ? var->value_type : nullptr;
    }
    if (auto member = ast::as<ast::Member>(expr)) {
      if (ir::GlobalVar* shared = exported_variable(member); shared != nullptr)
        return shared->value_type;
      if (auto base = ir::as<ir::ProductType>(record_type_of(member->base))) {
        auto at = field_index(base, member->name);
        return at ? base->fields[*at].second : nullptr;
      }
      return nullptr;
    }
    if (auto deref = ast::as<ast::Deref>(expr)) {
      auto held = ir::as<ir::PtrType>(record_type_of(deref->operand));
      return held != nullptr ? held->pointee : nullptr;
    }
    return nullptr;
  }


  bool Checker::may_write(const ast::Expr* expr)
  {
    if (auto member = ast::as<ast::Member>(expr)) {
      if (ir::GlobalVar* shared = exported_variable(member); shared != nullptr)
        return exported_variable(member)->is_mutable();
      return may_write(member->base);
    }
    if (auto element = ast::as<ast::Element>(expr))
      return may_write(element->base);
    if (auto deref = ast::as<ast::Deref>(expr)) {
      if (auto name = ast::as<ast::NameRef>(deref->operand)) {
        Local* held = find_local(name->name);
        auto ty = ir::as<ir::PtrType>(held != nullptr ? held_by(held) : nullptr);
        if (ty != nullptr)
          return ty->is_mutable;
      }
      return true;
    }
    if (auto name = ast::as<ast::NameRef>(expr)) {
      if (Local* local = find_local(name->name); local != nullptr)
        return local->is_mutable;
      auto var = provided(name->name).get<ir::GlobalVar>();
      return var == nullptr || var->is_mutable();
    }
    if (ast::is<ast::ArrayLit>(expr))
      return true;
    return ! in_the_image(expr);
  }


  bool Checker::read_only(ir::Value* value)
  {
    ir::Value* seen = value;
    while (true) {
      if (auto var = global_of(seen))
        return ! var->is_mutable();
      if (auto inst = inst_of(seen, ir::InstKind::AddressInst)) {
        seen = inst->operands[0];
        continue;
      }
      if (auto inst = any_inst(seen); keeps_pointing(inst, true)) {
        seen = inst->operands[0];
        continue;
      }
      return false;
    }
  }

  ir::Value* Checker::lower_collection(ir::Builder& builder, const ast::Expr* expr, const ir::Type* expected)
  {
    if (auto set = ast::as<ast::SetLit>(expr); set != nullptr && set->spread && ir::is<ir::DictType>(aiming_at(expected))) {
      const ast::DictLit* paired_up = spread_paired(set);
      if (paired_up == nullptr)
        return undef_error(module);
      expr = paired_up;
    }
    if (in_the_image(expr))
      return lower_static(builder, expr, expr, expected);
    auto set = ast::as<ast::SetLit>(expr);
    auto dict = ast::as<ast::DictLit>(expr);
    bool empty = (set != nullptr && set->elements.empty()) || (dict != nullptr && dict->entries.empty());
    // Nothing written is nothing to work out, and a table with nothing in it
    // is still a table.
    Entries ready;
    const ir::Type* aim = aiming_at(expected);
    const ir::Type* ty = nullptr;
    if (empty) {
      if (! ir::is<ir::SetType>(aim) && ! ir::is<ir::DictType>(aim)) [[unlikely]] {
        diags.emit(D::LANG_COLLECTION_EMPTY_UNKNOWN, expr->span);
        return undef_error(module);
      }
      ty = aim;
    } else {
      std::vector<std::tuple<const ast::Expr*, const ast::Expr*>> written;
      if (set != nullptr)
        for (auto one : set->elements)
          written.emplace_back(one, nullptr);
      else
        for (const auto& entry : dict->entries)
          written.emplace_back(entry.key, entry.value);
      ready = entries_written(builder, written, expected);
      std::vector<Span> key_spans;
      for (const auto& [key, _] : written)
        key_spans.push_back(key->span);
      const ir::Type* keys = same_type(ready.keys, key_spans, ready.wanted_key);
      if (keys == ir::error_type())
        return undef_error(module);
      if (! can_be_a_key(keys)) [[unlikely]] {
        diags.emit(D::LANG_COLLECTION_KEY_NOT_HASHABLE, std::get<0>(written[0])->span, { { "found", keys->written() } });
        return undef_error(module);
      }
      // What is written down is fresh and belongs to whoever is given it, so
      // it is the writable type.
      if (set != nullptr)
        ty = ir::set_type(keys, true);
      else {
        std::vector<Span> value_spans;
        for (const auto& [_, value] : written)
          if (value != nullptr)
            value_spans.push_back(value->span);
        const ir::Type* held = same_type(ready.values, value_spans, ready.wanted_value);
        if (held == ir::error_type())
          return undef_error(module);
        ty = ir::dict_type(keys, held, true);
      }
    }
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return undef_error(module);
    }
    if (auto table = ir::as<ir::DictType>(ty); table != nullptr && ! can_be_a_value(table->value)) [[unlikely]] {
      diags.emit(D::LANG_COLLECTION_VALUE_TOO_LARGE, expr->span, { { "found", table->value->written() } });
      return undef_error(module);
    }
    // Making one takes room out of an arena, which changes nothing anybody
    // else can see.
    const ast::Expr* arena = set != nullptr ? set->arena : dict->arena;
    ir::Value* named = arena_named(builder, arena);
    return build_collection(builder, expr, ty, named, std::move(ready));
  }


  ir::Value* Checker::arena_named(ir::Builder& builder, const ast::Expr* written)
  {
    if (written == nullptr)
      return nullptr;
    if (auto member = ast::as<ast::Member>(written)) {
      // An arena another module exports, reached by the path every other name
      // of a module is reached by.
      ir::GlobalVar* shared = exported_variable(member);
      if (shared != nullptr && shared->value_type == ir::arena_type())
        return builder.address(shared, written->span);
    }
    auto name = ast::as<ast::NameRef>(written);
    if (name == nullptr) [[unlikely]] {
      diags.emit(D::LANG_NOT_AN_ARENA, written->span, { { "name", about_the_shape(written) } });
      return nullptr;
    }
    if (Local* local = find_local(name->name); local != nullptr) {
      const ir::Type* held = held_by(local);
      if (local->placed && held == ir::arena_type()) {
        local->read = true;
        local->written = true;
        return local->value;
      }
      if (auto ptr = ir::as<ir::PtrType>(held); ptr != nullptr && ptr->pointee == ir::arena_type()) {
        local->read = true;
        return local->value;
      }
      diags.emit(D::LANG_NOT_AN_ARENA, written->span, { { "name", name->name } });
      return nullptr;
    }
    auto var = provided(name->name).get<ir::GlobalVar>();
    if (! (var != nullptr && var->value_type == ir::arena_type())) [[unlikely]] {
      diags.emit(D::LANG_NOT_AN_ARENA, written->span, { { "name", name->name } });
      return nullptr;
    }
    return builder.address(var, written->span);
  }


  ir::Value* Checker::build_collection(ir::Builder& builder, const ast::Expr* expr, const ir::Type* ty,
                                       ir::Value* arena, Entries ready)
  {
    ir::Value* table = new_table(builder, ty, expr->span, arena);
    tables::Shape shape = table_shape(ty);
    auto dict = ir::as<ir::DictType>(ty);
    std::size_t next = 0;
    for (auto key : ready.keys) {
      ir::Value* place = put_key(builder, shape, table, key, expr->span);
      if (dict != nullptr) {
        ir::Value* where = value_place(builder, shape, place, dict->value);
        builder.store(where, ready.values[next++], expr->span);
      }
    }
    return table;
  }


  tables::Shape Checker::table_shape(const ir::Type* ty)
  {
    if (auto set = ir::as<ir::SetType>(ty))
      return tables::Shape{ set->element };
    auto dict = ir::as<ir::DictType>(ty);
    return tables::Shape{ dict->key, dict->value };
  }


  ir::Value* Checker::new_table(ir::Builder& builder, const ir::Type* ty, Span span, ir::Value* arena,
                                ir::Value* comes_from)
  {
    tables::Shape shape = table_shape(ty);
    tables::ensure_runtime(module, shape);
    ir::Value* place = nullptr;
    if (comes_from != nullptr)
      place = tables::arena_of(builder, comes_from);
    else if (arena != nullptr)
      place = arena;
    else
      place = builder.address(provided(heap_name).get<ir::GlobalVar>(), span);
    ir::Value* made = builder.call(module.function(tables::new_symbol),
                                   std::vector<ir::Value*>{ place, builder.int_const(ir::u64(), shape.stride()) },
                                   tables::table_type(module), span);
    return builder.cast(ir::CastKind::bitcast, made, ty, span);
  }


  ir::Value* Checker::as_held(ir::Builder& builder, const tables::Shape& shape, ir::Value* key, Span span)
  {
    if (tables::key_ir_type(shape) != ir::u64())
      return key;
    if (value_type_of(key) == ir::u64())
      return key;
    return builder.cast(ir::CastKind::zext, key, ir::u64(), span);
  }


  ir::Value* Checker::put_key(ir::Builder& builder, const tables::Shape& shape, ir::Value* table, ir::Value* key,
                              Span span)
  {
    tables::ensure_runtime(module, shape);
    ir::Function* callee = module.function(tables::named(tables::put_symbol, shape));
    ir::Value* handle = builder.cast(ir::CastKind::bitcast, table, tables::table_type(module), span);
    ir::Value* held = as_held(builder, shape, key, span);
    return builder.call(callee, std::vector<ir::Value*>{ handle, held }, tables::table_type(module), span);
  }


  ir::Value* Checker::find_key(ir::Builder& builder, const tables::Shape& shape, ir::Value* table, ir::Value* key,
                               Span span)
  {
    tables::ensure_runtime(module, shape);
    ir::Function* callee = module.function(tables::named(tables::slot_symbol, shape));
    ir::Value* handle = builder.cast(ir::CastKind::bitcast, table, tables::table_type(module), span);
    ir::Value* held = as_held(builder, shape, key, span);
    return builder.call(callee, std::vector<ir::Value*>{ handle, held }, tables::table_type(module), span);
  }


  ir::Value* Checker::is_live(ir::Builder& builder, ir::Value* place, Span span)
  {
    ir::Value* state = builder.load(place, span);
    return builder.compare(ir::CmpPred::eq, state, builder.int_const(ir::u64(), tables::live), span);
  }


  ir::Value* Checker::value_place(ir::Builder& builder, const tables::Shape& shape, ir::Value* place,
                                  const ir::Type* ty)
  {
    ir::Value* word = builder.binary(ir::BinOp::add, place, builder.int_const(ir::u64(), shape.value_at()));
    return builder.cast(ir::CastKind::bitcast, word, ir::ptr_type(ty, true));
  }


  const ir::Type* Checker::one_type(ir::Builder& builder, std::span<const ast::Expr* const> written,
                                    const ir::Type* wanted, std::vector<ir::Value*>* into)
  {
    const ir::Type* aim = wanted != nullptr ? wanted : said_by(written);
    std::vector<ir::Value*> values;
    {
      // Nothing is expected of an entry (`_nothing_expected`).
      auto outer = std::pair{ initializing, assigning };
      initializing.reset();
      assigning.reset();
      for (auto entry : written)
        values.push_back(lower_expr(builder, entry, taken_from(entry, aim)));
      std::tie(initializing, assigning) = outer;
    }
    if (into != nullptr)
      into->insert(into->end(), values.begin(), values.end());
    std::vector<Span> spans;
    for (auto entry : written)
      spans.push_back(entry->span);
    return same_type(values, spans, wanted);
  }


  const ir::Type* Checker::said_by(std::span<const ast::Expr* const> written)
  {
    for (auto one : written) {
      const ir::Type* found = nullptr;
      if (auto array = ast::as<ast::ArrayLit>(one))
        found = said_by(array->elements);
      else if (auto list = ast::as<ast::ListLit>(one))
        found = said_by(list->elements);
      else if (says_its_type(one))
        found = type_of(one);
      if (found != nullptr)
        return found;
    }
    return nullptr;
  }


  std::vector<const ast::Expr*> Checker::nothing_expected(std::span<const ast::Expr* const> written)
  {
    // Python's generator puts the context aside while it is walked; its one
    // caller, `one_type`, does that itself around the walk.
    return { written.begin(), written.end() };
  }


  const ir::Type* Checker::same_type(std::span<ir::Value* const> values, std::span<const Span> spans,
                                     const ir::Type* wanted)
  {
    const ir::Type* found = wanted;
    bool spoiled = false;
    for (std::size_t at = 0; at < values.size() && at < spans.size(); ++at) {
      const ir::Type* ty = value_type_of(values[at]);
      if (ty == ir::error_type()) {
        spoiled = true;
        continue;
      }
      if (found == nullptr)
        found = ty;
      else if (ty != found) {
        diags.emit(D::LANG_COLLECTION_MIXED_ENTRIES, spans[at], { { "found", ty->written() }, { "expected", found->written() } });
        spoiled = true;
      }
    }
    return spoiled || found == nullptr ? ir::error_type() : found;
  }


  Entries Checker::entries_written(ir::Builder& builder,
                                   std::span<const std::tuple<const ast::Expr*, const ast::Expr*>> written,
                                   const ir::Type* expected)
  {
    const ir::Type* aim = aiming_at(expected);
    const ir::Type* wanted_key = nullptr;
    const ir::Type* wanted_value = nullptr;
    if (auto set = ir::as<ir::SetType>(aim))
      wanted_key = set->element;
    else if (auto dict = ir::as<ir::DictType>(aim)) {
      wanted_key = dict->key;
      wanted_value = dict->value;
    }
    // What one entry says is what they all are.
    std::vector<const ast::Expr*> written_keys, written_values;
    for (const auto& [key, value] : written) {
      written_keys.push_back(key);
      if (value != nullptr)
        written_values.push_back(value);
    }
    const ir::Type* said_key = said_by(written_keys);
    if (said_key == nullptr)
      said_key = wanted_key;
    const ir::Type* said_value = said_by(written_values);
    if (said_value == nullptr)
      said_value = wanted_value;
    Entries ready;
    auto outer = std::pair{ initializing, assigning };
    initializing.reset();
    assigning.reset();
    for (const auto& [key, value] : written) {
      ready.keys.push_back(lower_expr(builder, key, taken_from(key, said_key)));
      if (value != nullptr)
        ready.values.push_back(lower_expr(builder, value, taken_from(value, said_value)));
    }
    std::tie(initializing, assigning) = outer;
    ready.wanted_key = wanted_key;
    ready.wanted_value = wanted_value;
    return ready;
  }


  ir::Value* Checker::lower_index(ir::Builder& builder, const ast::Index* expr, const ir::Type* expected)
  {
    if (ir::Value* found = paired(builder, expr->base, table_pair, std::vector<const ast::Expr*>{ expr->key },
                                  expr->span, expected))
      return found;
    ir::Value* base = lower_expr(builder, expr->base, nullptr);
    const ir::Type* ty = value_type_of(base);
    if (ty == ir::error_type())
      return undef_error(module);
    auto set = ir::as<ir::SetType>(ty);
    auto dict = ir::as<ir::DictType>(ty);
    if (set == nullptr && dict == nullptr) [[unlikely]] {
      diags.emit(D::LANG_INDEX_NOT_A_COLLECTION, expr->base->span, { { "found", ty->written() } });
      return undef_error(module);
    }
    ir::Value* key = lower_expr(builder, expr->key, set != nullptr ? set->element : dict->key);
    if (value_type_of(key) == ir::error_type())
      return undef_error(module);
    const ir::Type* answer = set != nullptr ? static_cast<const ir::Type*>(ir::bool_type()) : ir::result_type(dict->value);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return undef_error(module);
    }
    tables::Shape shape = table_shape(ty);
    ir::Value* place = find_key(builder, shape, base, key, expr->span);
    if (set != nullptr)
      return is_live(builder, place, expr->span);
    // The value is read whether the entry holds a key or not.
    ir::Value* state = builder.load(place, expr->span);
    ir::Value* missing = builder.compare(ir::CmpPred::ne, state, builder.int_const(ir::u64(), tables::live), expr->span);
    ir::Value* where = value_place(builder, shape, place, dict->value);
    ir::Value* value = builder.load(where, expr->span);
    return builder.wrap(value, missing, answer, expr->span);
  }


  ir::Value* Checker::lower_take(ir::Builder& builder, const ast::Take* expr, const ir::Type* expected)
  {
    ir::Value* base = lower_expr(builder, expr->base, nullptr);
    const ir::Type* ty = value_type_of(base);
    if (ty == ir::error_type())
      return undef_error(module);
    auto set = ir::as<ir::SetType>(ty);
    auto dict = ir::as<ir::DictType>(ty);
    if (set == nullptr && dict == nullptr) [[unlikely]] {
      diags.emit(D::LANG_INDEX_NOT_A_COLLECTION, expr->base->span, { { "found", ty->written() } });
      return undef_error(module);
    }
    if (! (set != nullptr ? set->is_mutable : dict->is_mutable)) [[unlikely]] {
      diags.emit(D::LANG_COLLECTION_NOT_WRITABLE, expr->base->span, { { "found", ty->written() } });
      return undef_error(module);
    }
    ir::Value* key = lower_expr(builder, expr->key, set != nullptr ? set->element : dict->key);
    if (value_type_of(key) == ir::error_type())
      return undef_error(module);
    const ir::Type* answer = set != nullptr ? static_cast<const ir::Type*>(ir::bool_type()) : ir::result_type(dict->value);
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(expr->span, answer, expected);
      return undef_error(module);
    }
    written_table(base, expr->span);
    tables::Shape shape = table_shape(ty);
    tables::ensure_take(module, shape);
    ir::Value* place = find_key(builder, shape, base, key, expr->span);
    ir::Value* held = builder.load(place, expr->span);
    // What was there is read before the entry is given up.
    ir::Value* found = nullptr;
    if (dict != nullptr) {
      ir::Value* where = value_place(builder, shape, place, dict->value);
      found = builder.load(where, expr->span);
    }
    ir::Value* handle = builder.cast(ir::CastKind::bitcast, base, tables::table_type(module), expr->span);
    builder.call(module.function(tables::give_up_symbol), std::vector<ir::Value*>{ handle, place }, ir::void_type(),
                 expr->span);
    if (found == nullptr)
      return builder.compare(ir::CmpPred::eq, held, builder.int_const(ir::u64(), tables::live), expr->span);
    ir::Value* missing = builder.compare(ir::CmpPred::ne, held, builder.int_const(ir::u64(), tables::live), expr->span);
    return builder.wrap(found, missing, answer, expr->span);
  }

  // -- lists, and the cursors that walk them ----------------------------------

  std::optional<std::tuple<ir::Value*, const ir::ListType*>>
  Checker::list_place(ir::Builder& builder, const ast::Expr* expr, Span span, std::optional<std::string> changing)
  {
    auto refuse = [&](const ir::Type* found) {
      if (! changing)
        diags.emit(D::LANG_TAKE_NOT_A_CURSOR, span, { { "found", found->written() } });
      else
        diags.emit(D::LANG_LIST_CHANGED_NOT_A_LIST, span, { { "what", *changing }, { "found", found->written() } });
    };
    if (! names_a_place(expr)) [[unlikely]] {
      if (! changing)
        diags.emit(D::LANG_TAKE_NOT_A_PLACE, span);
      else
        diags.emit(D::LANG_LIST_CHANGED_NOT_A_PLACE, span, { { "what", *changing } });
      return std::nullopt;
    }
    if (const ir::Type* said = said_to_be(expr); said != nullptr && ! ir::is<ir::ListType>(said)) [[unlikely]] {
      // Said before the place is asked for, which has rules of its own about
      // what may be in one: what is wrong is what it is.
      refuse(said);
      return std::nullopt;
    }
    auto found = place_written(builder, expr, through_a_list(expr));
    if (! found)
      return std::nullopt;
    auto [address, held, may_change, what, _] = *found;
    if (held == ir::error_type())
      return std::nullopt;
    auto list = ir::as<ir::ListType>(held);
    if (list == nullptr) [[unlikely]] {
      refuse(held);
      return std::nullopt;
    }
    if (! may_change) [[unlikely]] {
      diags.emit(D::LANG_VARDEF_NOT_MUTABLE, span, { { "name", what } });
      return std::nullopt;
    }
    // A view of its elements may be out, which changing it in place could
    // leave pointing at room given back: the list is lent for as long as one
    // is kept (`as_a_view`).
    if (auto borrowed = borrowed_name(expr)) {
      const auto& [name, path] = *borrowed;
      if (lent_out(name, span, true, path)) [[unlikely]]
        return std::nullopt;
    }
    return std::tuple{ builder.cast(ir::CastKind::bitcast, address, ir::ptr_type(list, true), span), list };
  }


  std::tuple<ir::Value*, ir::Value*, ir::Value*> Checker::list_at(ir::Builder& builder, ir::Value* place,
                                                                 const ir::ListType* ty, Span span)
  {
    ir::Value* held = builder.load(place, span);
    ir::Value* elements = builder.extract(held, 0, ir::parts_of(ty)[0], span);
    ir::Value* count = builder.extract(held, 1, ir::u64(), span);
    return { elements, count, held };
  }


  void Checker::erased(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, ir::Value* elements,
                       ir::Value* count, ir::Value* at, Span span)
  {
    auto stride = std::int64_t(ir::stride_of(ty->element, layout()));
    ir::Value* bytes = builder.cast(ir::CastKind::bitcast, elements, ir::ptr_type(ir::u8(), true), span);
    builder.call(lists::erase_function(module),
                 std::vector<ir::Value*>{ bytes, builder.int_const(ir::u64(), stride), count, at }, ir::void_type(),
                 span);
    ir::Value* held = builder.load(place, span);
    ir::Value* fewer = builder.binary(ir::BinOp::wrap_sub, count, builder.int_const(ir::u64(), 1), span);
    ir::Value* from = builder.extract(held, 2, ir::parts_of(ty)[2], span);
    builder.store(place, builder.make_tuple(std::vector<ir::Value*>{ elements, fewer, from }, ty, span), span);
  }


  ir::Value* Checker::element_of(ir::Builder& builder, ir::Value* elements, const ir::ListType* ty, ir::Value* at,
                                 Span span, const ast::Expr* expr)
  {
    auto stride = std::int64_t(ir::stride_of(ty->element, layout()));
    ir::Value* offset = builder.binary(ir::BinOp::wrap_mul, at, builder.int_const(ir::u64(), stride), span);
    ir::Value* address = builder.binary(ir::BinOp::add, elements, offset, span);
    if (expr == nullptr)
      return builder.load(address, span);
    return loaded_or_in_place(builder, address, expr, span);
  }


  ir::Value* Checker::lower_take_at(ir::Builder& builder, const ast::TakeAt* expr, const ir::Type* expected)
  {
    auto found = list_place(builder, expr->base, expr->base->span);
    if (! found)
      return undef_error(module);
    auto [place, ty] = *found;
    ir::Value* at = index_into(builder, expr->index, expr->span);
    if (at == nullptr)
      return undef_error(module);
    if (! accepts(expected, ty->element)) [[unlikely]] {
      report_mismatch(expr->span, ty->element, expected);
      return undef_error(module);
    }
    if (! made_here(place))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, expr->span);
    made_writable(builder, place, ty, expr->span, expr->base);
    auto [elements, count, _] = list_at(builder, place, ty, expr->span);
    builder.check(builder.compare(ir::CmpPred::ult, at, count, expr->span), "an index outside its list",
                  target::statuses::OUT_OF_RANGE, expr->span);
    ir::Value* gone = element_of(builder, elements, ty, at, expr->span);
    erased(builder, place, ty, elements, count, at, expr->span);
    return gone;
  }


  Checker::Room Checker::room_made(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, ir::Value* more,
                                   Span span)
  {
    const ir::PtrType* bytes = ir::ptr_type(ir::u8(), true);
    auto [elements, count, held] = list_at(builder, place, ty, span);
    ir::Value* had = builder.extract(held, 2, ir::parts_of(ty)[2], span);
    ir::Value* arena = out_of(builder, span);
    ir::Value* into = builder.call(lists::allocator_function(module), std::vector<ir::Value*>{ had, arena },
                                   ir::parts_of(ty)[2], span);
    ir::Value* old = builder.cast(ir::CastKind::bitcast, elements, bytes, span);
    auto stride = std::int64_t(ir::stride_of(ty->element, layout()));
    ir::Value* grown = builder.call(
      lists::grow_function(module),
      std::vector<ir::Value*>{ old, count, had, into, more, builder.int_const(ir::u64(), stride) }, bytes, span);
    return { old, grown, count, had, into };
  }


  void Checker::room_kept(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, const Room& made,
                          ir::Value* count, Span span)
  {
    auto stride = std::int64_t(ir::stride_of(ty->element, layout()));
    builder.call(lists::release_function(module),
                 std::vector<ir::Value*>{ made.had, made.old, made.grown, builder.int_const(ir::u64(), stride) },
                 ir::void_type(), span);
    ir::Value* elements = builder.cast(ir::CastKind::bitcast, made.grown, ir::parts_of(ty)[0], span);
    builder.store(place, builder.make_tuple(std::vector<ir::Value*>{ elements, count, made.into }, ty, span), span);
  }


  void Checker::made_writable(ir::Builder& builder, ir::Value* place, const ir::ListType* ty, Span span,
                              const ast::Expr* written)
  {
    if (auto name = ast::as<ast::NameRef>(written))
      if (Local* local = find_local(name->name); local != nullptr && ! local->is_parameter) {
        KeySet arenas = arenas_in(written);
        if (! arenas.empty() && ! arenas.contains(image_key))
          return;
      }
    Room made = room_made(builder, place, ty, builder.int_const(ir::u64(), 0), span);
    room_kept(builder, place, ty, made, made.count, span);
  }


  void Checker::copied_where_shared(const ast::Expr* written, bool changes)
  {
    const ir::Type* ty = said_to_be(written);
    if (ty == nullptr || ! holds_a_list(ty) || ! owned::can_own(ty)
        || ! (ast::is<ast::NameRef>(written) || ast::is<ast::Member>(written) || ast::is<ast::Element>(written)
              || ast::is<ast::Deref>(written)))
      return;
    const ast::Expr* root = written;
    bool through_a_reference = false;
    while (true)
      if (auto member = ast::as<ast::Member>(root))
        root = member->base;
      else if (auto element = ast::as<ast::Element>(root))
        root = element->base;
      else if (auto deref = ast::as<ast::Deref>(root)) {
        through_a_reference = true;
        root = deref->operand;
      } else
        break;
    auto name = ast::as<ast::NameRef>(root);
    Local* local = name != nullptr ? find_local(name->name) : nullptr;
    if (local != nullptr && ast::is<ast::NameRef>(written) && ! local->is_parameter && ! used_after(local))
      return;
    bool may_change = through_a_reference || local == nullptr || local->is_mutable;
    if (changes || may_change)
      copying.insert(id_of(written));
  }


  std::vector<ir::Value*> Checker::owned_arguments(ir::Builder& builder, const ast::Call* expr, ir::Function* func,
                                                   std::span<ir::Value* const> args)
  {
    std::vector<ir::Value*> given(args.begin(), args.end());
    if (func->takes_owned.empty())
      return given;
    auto written = written_for(expr, func);
    for (auto at : func->takes_owned) {
      if (std::size_t(at) >= given.size())
        continue;
      const ast::Expr* one = written.get_or(at, nullptr);
      if (one != nullptr && movable(one, &builder.function())) {
        module.record("copy-into-allocator", func->name,
                      "'" + written_as(one) + "' is handed to '" + func->name
                        + "' as it is: nothing reads it again, and the callee changes it in place",
                      one->span);
        continue;
      }
      given[at] = copy_into_heap(builder, given[at], expr->span);
    }
    return given;
  }


  bool Checker::movable(const ast::Expr* written, ir::Function* current)
  {
    auto name = ast::as<ast::NameRef>(written);
    if (name == nullptr)
      return false;
    Local* local = find_local(name->name);
    const ast::Stmt* stmt = current_stmt;
    if (local == nullptr || ! local->is_mutable || (local->named_parameter && ! owned_parameter(written, current))
        || stmt == nullptr || mentions(stmt, name->name) != 1)
      return false;
    if (addressed.contains(name->name)) {
      // A place: of its own, for joining onto it in place, or one a second
      // way reaches, which nothing handed over can be.
      auto node = node_of_func.get(id_of(current));
      if (node == nullptr || *node == nullptr || (*node)->body == nullptr || referenced((*node)->body, name->name))
        return false;
    }
    if (auto assign = ast::as<ast::AssignStmt>(stmt); assign != nullptr && assign->name == name->name
                                                      && assign->more.empty())
      return true;
    return ! used_after(local);
  }


  bool Checker::owned_parameter(const ast::Expr* written, ir::Function* func)
  {
    auto name = ast::as<ast::NameRef>(written);
    if (name == nullptr)
      return false;
    Local* local = find_local(name->name);
    if (local == nullptr || ! local->named_parameter)
      return false;
    auto at = std::ranges::find(func->param_names, name->name);
    if (at == func->param_names.end())
      return false;
    auto index = int(at - func->param_names.begin());
    return std::ranges::find(func->takes_owned, index) != func->takes_owned.end();
  }


  ir::Value* Checker::heaps_or_copied(ir::Builder& builder, ir::Value* value, const ir::ListType* ty, Span span)
  {
    ir::Value* heap = owner_place(builder, heap_key, span);
    ir::Value* held = builder.extract(value, 2, ir::parts_of(ty)[2], span);
    ir::Value* left = builder.cast(ir::CastKind::bitcast, held, ir::u64(), span);
    ir::Value* right = builder.cast(ir::CastKind::bitcast, heap, ir::u64(), span);
    ir::Value* same = builder.compare(ir::CmpPred::eq, left, right, span);
    ir::BasicBlock* kept_block = builder.new_block("owned.heaps");
    ir::BasicBlock* copying_block = builder.new_block("owned.copy");
    ir::BasicBlock* done = builder.new_block("owned");
    ir::Value* before = builder.memory();
    builder.condbr(same, kept_block, copying_block, {}, {}, span);
    builder.position_at(kept_block);
    builder.br(done, std::vector<ir::Value*>{ value, before }, span);
    builder.position_at(copying_block);
    builder.set_memory(before);
    ir::Value* copied = builder.call(owned::own_function(module, ty), std::vector<ir::Value*>{ heap, value }, ty, span);
    builder.br(done, std::vector<ir::Value*>{ copied, builder.memory() }, span);
    builder.position_at(done);
    ir::Value* answer = done->add_param(ty, "answer");
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    return answer;
  }


  ir::Value* Checker::copy_into_heap(ir::Builder& builder, ir::Value* value, Span span)
  {
    const ir::Type* ty = value_type_of(value);
    if (! holds_a_list(ty) || ! owned::can_own(ty))
      return value;
    auto heap = provided(heap_name).get<ir::GlobalVar>();
    ir::Value* where = builder.address(heap, span);
    return builder.call(owned::own_function(module, ty), std::vector<ir::Value*>{ where, value }, ty, span);
  }


  ir::Value* Checker::own_when_changed(ir::Builder& builder, const ast::Param* param, ir::Value* value,
                                       const ast::Node* body)
  {
    if (! param->mutable_ || ! holds_a_list(value_type_of(value)) || ! changed_in_place(param->name, body))
      return value;
    return copy_of(builder, value, param->span);
  }


  bool Checker::used_after(Local* local)
  {
    const ast::Stmt* stmt = current_stmt;
    const KeySet* after = stmt != nullptr ? live_out.get(id_of(stmt)) : nullptr;
    return after == nullptr || after->contains(local->span.start);
  }


  ir::Value* Checker::copy_of(ir::Builder& builder, ir::Value* value, Span span)
  {
    const ir::Type* ty = value_type_of(value);
    if (! holds_a_list(ty) || ! owned::can_own(ty))
      return value;
    ir::Value* arena = out_of(builder, span);
    return builder.call(owned::own_function(module, ty), std::vector<ir::Value*>{ arena, value }, ty, span);
  }


  void Checker::lower_join_assign(ir::Builder& builder, const ast::JoinAssign* stmt)
  {
    auto found = list_place(builder, stmt->target, stmt->target->span,
                            std::string("\N{DOUBLE PLUS}") + "\N{LEFTWARDS ARROW}");
    if (! found)
      return;
    auto [place, ty] = *found;
    const ast::Expr* written = stmt->value;
    if (auto array = ast::as<ast::ArrayLit>(written)) {
      auto list = syntax.make<ast::ListLit>(array->span);
      list->elements = array->elements;
      written = list;
    }
    if (! made_here(place))
      // A list is a value, so one in this function's own room is changed for
      // nobody else; one reached through a reference is somebody's.
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    auto stride = std::int64_t(ir::stride_of(ty->element, layout()));
    Span span = stmt->span;
    if (may_be_an_array(written)) {
      // An array of the list's elements, of one dimension: one growth to fit,
      // and the elements copied in as `joined_on` copies.
      ir::Value* given = lower_expr(builder, written, nullptr);
      const ir::Type* held = value_type_of(given);
      if (held == ir::error_type())
        return;
      ir::Value* view = nullptr;
      if (ir::is<ir::ArrayType>(held))
        view = as_a_view(builder, written, given, ir::array_type(ty->element, { std::nullopt }), written->span);
      if (view == nullptr) [[unlikely]] {
        report_mismatch(written->span, held, ty);
        return;
      }
      if (! view_copied(ty->element, written)) [[unlikely]]
        return;
      ir::Value* count = builder.extract(view, 1, ir::u64(), span);
      Room made = room_made(builder, place, ty, count, span);
      joined_on(builder, made.grown, made.count, view, ty->element, span);
      room_kept(builder, place, ty, made, builder.binary(ir::BinOp::wrap_add, made.count, count, span), span);
      return;
    }
    if (auto list = ast::as<ast::ListLit>(written);
        list != nullptr && ! list->elements.empty() && ! owned::points(ty->element)) {
      std::vector<ir::Value*> values;
      for (auto one : list->elements)
        values.push_back(lower_into(builder, one, ty->element, one->span));
      if (std::ranges::any_of(values, [this](ir::Value* one) { return value_type_of(one) == ir::error_type(); }))
        return;
      Room made = room_made(builder, place, ty, builder.int_const(ir::u64(), values.size()), span);
      ir::Value* at = builder.cast(ir::CastKind::bitcast, made.grown, ir::ptr_type(ty->element, true), span);
      for (std::size_t index = 0; index < values.size(); ++index) {
        ir::Value* offset = builder.binary(ir::BinOp::wrap_add, made.count, builder.int_const(ir::u64(), index), span);
        builder.store(element_place(builder, at, ty->element, offset, span), values[index], span);
      }
      ir::Value* total = builder.binary(ir::BinOp::wrap_add, made.count, builder.int_const(ir::u64(), values.size()),
                                        span);
      room_kept(builder, place, ty, made, total, span);
      return;
    }
    ir::Value* more = lower_into(builder, written, ty, written->span);
    if (value_type_of(more) == ir::error_type())
      return;
    ir::Value* joined = builder.extract(more, 1, ir::u64(), span);
    Room made = room_made(builder, place, ty, joined, span);
    ir::Value* source = more;
    if (owned::points(ty->element))
      // Into the list's own allocator, which is known only while running.
      source = builder.call(owned::own_function(module, ty), std::vector<ir::Value*>{ made.into, more }, ty, span);
    const ir::PtrType* bytes = ir::ptr_type(ir::u8(), true);
    ir::Value* size = builder.int_const(ir::u64(), stride);
    ir::Value* offset = builder.binary(ir::BinOp::wrap_mul, made.count, size, span);
    ir::Value* first = builder.extract(source, 0, ir::parts_of(ty)[0], span);
    ir::Value* from = builder.cast(ir::CastKind::bitcast, first, bytes, span);
    ir::Value* length = builder.binary(ir::BinOp::wrap_mul, joined, size, span);
    strings::copy_bytes(builder, made.grown, offset, from, length);
    if (owned::points(ty->element))
      // Its elements are the list's now, and the run they were in is spare.
      owned::give_back_storage(builder, source, ty, std::nullopt);
    else if (is_temporary(written))
      owned::give_back_storage(builder, more, ty,
                               freshly_heap(written) ? std::optional<std::string_view>("heap") : std::nullopt);
    room_kept(builder, place, ty, made, builder.binary(ir::BinOp::wrap_add, made.count, joined, span), span);
  }


  bool Checker::may_be_an_array(const ast::Expr* written)
  {
    if (ir::is<ir::ArrayType>(said_to_be(written)))
      return true;
    if (auto element = ast::as<ast::Element>(written))
      return std::ranges::any_of(element->indices, [](const ast::Expr* one) { return ast::is<ast::Range>(one); });
    if (auto call = ast::as<ast::Call>(written))
      if (auto name = ast::as<ast::NameRef>(call->callee))
        return name->name == names::bytes_name || name->name == names::span_name
               || name->name == names::one_run_name;
    return false;
  }


  bool Checker::view_copied(const ir::Type* element, const ast::Expr* written)
  {
    auto why = not_copied_because(element);
    if (! why.has_value())
      return true;
    diags.emit(D::LANG_ARRAY_NOT_COPIED, written->span, { { "type", element->written() }, { "why", *why } });
    return false;
  }


  void Checker::joined_on(ir::Builder& builder, ir::Value* into, ir::Value* after, ir::Value* view,
                          const ir::Type* element, Span span)
  {
    auto stride = std::int64_t(ir::stride_of(element, layout()));
    const ir::PtrType* bytes = ir::ptr_type(ir::u8(), true);
    const ir::PtrType* pointer = ir::ptr_type(element, true);
    ir::Value* start = builder.extract(view, 0, pointer, span);
    ir::Value* count = builder.extract(view, 1, ir::u64(), span);
    ir::Value* apart = builder.extract(view, 2, ir::u64(), span);
    ir::Value* size = builder.int_const(ir::u64(), stride);
    ir::Value* offset = builder.binary(ir::BinOp::wrap_mul, after, size, span);
    ir::Value* place = builder.binary(ir::BinOp::add, into, offset, span);
    auto one_run = builder.new_block("join.run");
    auto stepped = builder.new_block("join.stepped");
    auto done = builder.new_block("joined.view");
    ir::Value* before = builder.memory();
    builder.condbr(builder.compare(ir::CmpPred::eq, apart, builder.int_const(ir::u64(), 1), span), one_run, stepped,
                   {}, {}, span);
    builder.position_at(one_run);
    builder.set_memory(before);
    ir::Value* from = builder.cast(ir::CastKind::bitcast, start, bytes, span);
    ir::Value* length = builder.binary(ir::BinOp::wrap_mul, count, size, span);
    strings::moved(builder, place, from, length, span);
    builder.br(done, std::vector<ir::Value*>{ builder.memory() }, span);
    builder.position_at(stepped);
    builder.set_memory(before);
    ir::Value* one = builder.int_const(ir::u64(), 1);
    ir::Value* to = builder.cast(ir::CastKind::bitcast, place, pointer, span);
    std::vector<ir::Value*> counts{ count };
    std::vector<ir::Value*> dense{ one };
    std::vector<ir::Value*> strides{ apart };
    element_by_element(builder, Laid{ to, counts, dense, true }, Laid{ start, counts, strides, false }, element, count,
                       false, span);
    builder.br(done, std::vector<ir::Value*>{ builder.memory() }, span);
    builder.position_at(done);
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
  }


  ir::Value* Checker::list_element(ir::Builder& builder, const ast::Element* expr, ir::Value* base,
                                   const ir::ListType* ty, const ir::Type* expected)
  {
    if (expr->indices.size() == 1 && ast::is<ast::Range>(expr->indices[0])) {
      // Some of its elements: the list read as a view of them, lent as long as
      // the view is kept, and that view sliced as an array's is.
      auto view = as_a_view(builder, expr->base, base, ir::array_type(ty->element, { std::nullopt }), expr->base->span);
      contract_assert(view != nullptr);
      list_views[id_of(expr)] = ty;
      return lower_slice(builder, expr, view, ir::as<ir::ArrayType>(value_type_of(view)), expected);
    }
    if (expr->indices.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, expr->base->span, { { "found", ty->written() } });
      return undef_error(module);
    }
    ir::Value* at = index_into(builder, expr->indices[0], expr->span);
    if (at == nullptr)
      return undef_error(module);
    if (! accepts(expected, ty->element)) [[unlikely]] {
      report_mismatch(expr->span, ty->element, expected);
      return undef_error(module);
    }
    ir::Value* count = builder.extract(base, 1, ir::u64(), expr->span);
    builder.check(builder.compare(ir::CmpPred::ult, at, count, expr->span), "an index outside its list",
                  target::statuses::OUT_OF_RANGE, expr->span);
    ir::Value* elements = builder.extract(base, 0, ir::parts_of(ty)[0], expr->span);
    return element_of(builder, elements, ty, at, expr->span, expr);
  }


  void Checker::lower_list_element_assign(ir::Builder& builder, const ast::ElementAssign* stmt)
  {
    if (stmt->indices.size() != 1 || ast::is<ast::Range>(stmt->indices[0])) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_NOT_AN_ARRAY, stmt->base->span, { { "found", "a list" } });
      return;
    }
    auto found = list_place(builder, stmt->base, stmt->base->span, "an element written");
    if (! found)
      return;
    auto [place, ty] = *found;
    ir::Value* at = index_into(builder, stmt->indices[0], stmt->span);
    if (at == nullptr)
      return;
    ir::Value* value = lower_into(builder, stmt->value, ty->element, stmt->value->span);
    if (value_type_of(value) == ir::error_type())
      return;
    if (! made_here(place))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, stmt->span);
    Span span = stmt->span;
    made_writable(builder, place, ty, span, stmt->base);
    auto [elements, count, held] = list_at(builder, place, ty, span);
    builder.check(builder.compare(ir::CmpPred::ult, at, count, span), "an index outside its list",
                  target::statuses::OUT_OF_RANGE, span);
    auto stride = std::int64_t(ir::stride_of(ty->element, layout()));
    ir::Value* offset = builder.binary(ir::BinOp::wrap_mul, at, builder.int_const(ir::u64(), stride), span);
    ir::Value* where = builder.binary(ir::BinOp::add, elements, offset, span);
    if (owned::points(ty->element) && owned::can_own(ty->element)) {
      ir::Value* replaced = builder.load(where, span);
      ir::Value* from = builder.extract(held, 2, ir::parts_of(ty)[2], span);
      value = builder.call(owned::own_function(module, ty->element), std::vector<ir::Value*>{ from, value },
                           ty->element, span);
      builder.store(where, value, span);
      owned::give_back(builder, replaced, ty->element, std::nullopt);
      return;
    }
    builder.store(where, value, span);
  }


  ir::Value* Checker::lower_reserve(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 2
        || std::ranges::any_of(expr->args, [](auto one) { return ast::is<ast::Named>(one) || ast::is<ast::Spread>(one); }))
      [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", reserve_name }, { "expected", 2 }, { "found", expr->args.size() } });
      return undef_error(module);
    }
    auto found = list_place(builder, expr->args[0], expr->args[0]->span, std::string(reserve_name));
    if (! found)
      return undef_error(module);
    auto [place, ty] = *found;
    ir::Value* more = index_into(builder, expr->args[1], expr->args[1]->span);
    if (more == nullptr)
      return undef_error(module);
    if (! accepts(expected, ir::void_type())) [[unlikely]] {
      report_mismatch(expr->span, ir::void_type(), expected);
      return undef_error(module);
    }
    if (! made_here(place))
      an_effect(D::LANG_PURE_WRITES_ELSEWHERE, expr->span);
    Room made = room_made(builder, place, ty, more, expr->span);
    room_kept(builder, place, ty, made, made.count, expr->span);
    return undef_of(module, ir::void_type());
  }


  ir::Value* Checker::index_into(ir::Builder& builder, const ast::Expr* written, Span span)
  {
    ir::Value* index = on_its_own(builder, written);
    const ir::Type* found = value_type_of(index);
    if (found == ir::error_type())
      return nullptr;
    auto number = ir::as<ir::IntType>(found);
    if (number == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ARRAY_INDEX_NOT_A_NUMBER, written->span, { { "found", found->written() } });
      return nullptr;
    }
    if (! ast::is<ast::IntLit>(written) && ! stands_for(number->unit, idx_unit())) [[unlikely]] {
      diags.emit(D::LANG_UNIT_INDEX, written->span, { { "found", found->written() } });
      return nullptr;
    }
    return as_count(builder, index, number, span);
  }


  std::tuple<ir::Value*, ir::Value*, const ir::ListType*> Checker::cursor_parts(ir::Builder& builder,
                                                                               ir::Value* value,
                                                                               const ir::CursorType* ty, Span span)
  {
    const ir::ListType* held = ir::list_type(ty->element);
    ir::Value* place = builder.extract(value, 0, ir::ptr_type(held, true), span);
    ir::Value* at = builder.extract(value, 1, ir::u64(), span);
    return { place, at, held };
  }


  ir::Value* Checker::lower_iter(ir::Builder& builder, const ast::Call* expr, const ir::Type* expected)
  {
    if (expr->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, expr->span,
                 { { "name", iter_name }, { "expected", 1 }, { "found", expr->args.size() } });
      return undef_error(module);
    }
    if (! names_a_place(expr->args[0])) [[unlikely]] {
      diags.emit(D::LANG_TAKE_NOT_A_PLACE, expr->args[0]->span);
      return undef_error(module);
    }
    auto found = place_written(builder, expr->args[0], through_a_list(expr->args[0]));
    if (! found)
      return undef_error(module);
    auto [address, held, may_change, what, _] = *found;
    if (held == ir::error_type())
      return undef_error(module);
    auto list = ir::as<ir::ListType>(held);
    if (list == nullptr) [[unlikely]] {
      diags.emit(D::LANG_CURSOR_NOT_A_LIST, expr->args[0]->span, { { "found", held->written() } });
      return undef_error(module);
    }
    if (! may_change) [[unlikely]] {
      diags.emit(D::LANG_VARDEF_NOT_MUTABLE, expr->args[0]->span, { { "name", what } });
      return undef_error(module);
    }
    const ir::CursorType* ty = ir::cursor_type(list->element);
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return undef_error(module);
    }
    ir::Value* where = builder.cast(ir::CastKind::bitcast, address, ir::ptr_type(list, true), expr->span);
    return builder.make_tuple(std::vector<ir::Value*>{ where, builder.int_const(ir::u64(), 0) }, ty, expr->span);
  }


  ir::Value* Checker::lower_step(ir::Builder& builder, const ast::Unary* expr, ir::Value* value,
                                 const ir::CursorType* ty, const ir::Type* expected)
  {
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return undef_error(module);
    }
    auto [place, at, held] = cursor_parts(builder, value, ty, expr->span);
    bool onward = expr->op == ast::UnaryOp::next;
    if (onward) {
      auto [_, count, __] = list_at(builder, place, held, expr->span);
      builder.check(builder.compare(ir::CmpPred::ult, at, count, expr->span), "a walk moved past the end of its list",
                    target::statuses::WALK_ENDED, expr->span);
    } else
      builder.check(builder.compare(ir::CmpPred::ne, at, builder.int_const(ir::u64(), 0), expr->span),
                    "a walk moved before the start of its list", target::statuses::WALK_ENDED, expr->span);
    ir::Value* moved_on = builder.binary(onward ? ir::BinOp::wrap_add : ir::BinOp::wrap_sub, at,
                                         builder.int_const(ir::u64(), 1), expr->span);
    return builder.make_tuple(std::vector<ir::Value*>{ place, moved_on }, ty, expr->span);
  }


  ir::Value* Checker::lower_take_through(ir::Builder& builder, const ast::TakeThrough* expr, const ir::Type* expected)
  {
    ir::Value* value = lower_expr(builder, expr->operand, nullptr);
    const ir::Type* found = value_type_of(value);
    if (found == ir::error_type())
      return undef_error(module);
    auto ty = ir::as<ir::CursorType>(found);
    if (ty == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TAKE_NOT_A_CURSOR, expr->operand->span, { { "found", found->written() } });
      return undef_error(module);
    }
    if (! accepts(expected, ty)) [[unlikely]] {
      report_mismatch(expr->span, ty, expected);
      return undef_error(module);
    }
    an_effect(D::LANG_PURE_WRITES_ELSEWHERE, expr->span);
    auto [place, at, held] = cursor_parts(builder, value, ty, expr->span);
    auto [elements, count, _] = list_at(builder, place, held, expr->span);
    builder.check(builder.compare(ir::CmpPred::ult, at, count, expr->span),
                  "taking an element out through a walk that is over", target::statuses::WALK_ENDED, expr->span);
    erased(builder, place, held, elements, count, at, expr->span);
    return value;
  }


  ir::Value* Checker::through_a_cursor(ir::Builder& builder, ir::Value* value, const ir::CursorType* ty, Span span)
  {
    auto [place, at, held] = cursor_parts(builder, value, ty, span);
    auto [elements, count, _] = list_at(builder, place, held, span);
    builder.check(builder.compare(ir::CmpPred::ult, at, count, span), "reading through a walk that is over",
                  target::statuses::WALK_ENDED, span);
    return element_of(builder, elements, held, at, span);
  }


  ir::Value* Checker::asked(ir::Builder& builder, ir::Value* value, Span span)
  {
    auto ty = ir::as<ir::CursorType>(value_type_of(value));
    if (ty == nullptr)
      return value;
    auto [place, at, held] = cursor_parts(builder, value, ty, span);
    auto [_, count, __] = list_at(builder, place, held, span);
    return builder.compare(ir::CmpPred::uge, at, count, span);
  }


  void Checker::lower_entry_assign(ir::Builder& builder, const ast::EntryAssign* stmt)
  {
    ir::Value* base = lower_expr(builder, stmt->base, nullptr);
    const ir::Type* found = value_type_of(base);
    if (found == ir::error_type())
      return;
    auto ty = ir::as<ir::DictType>(found);
    if (ty == nullptr) [[unlikely]] {
      diags.emit(D::LANG_ENTRY_ASSIGN_NOT_A_DICT, stmt->base->span, { { "found", found->written() } });
      return;
    }
    if (! ty->is_mutable) [[unlikely]] {
      diags.emit(D::LANG_COLLECTION_NOT_WRITABLE, stmt->base->span, { { "found", found->written() } });
      return;
    }
    ir::Value* key = lower_expr(builder, stmt->key, ty->key);
    ir::Value* value = lower_into(builder, stmt->value, ty->value, stmt->span);
    if (value_type_of(key) == ir::error_type() || value_type_of(value) == ir::error_type())
      return;
    written_table(base, stmt->span);
    tables::Shape shape = table_shape(ty);
    ir::Value* place = put_key(builder, shape, base, key, stmt->span);
    builder.store(value_place(builder, shape, place, ty->value), value, stmt->span);
  }

  // -- if ---------------------------------------------------------------------

  std::optional<bool> Checker::settled(const ast::Expr* condition)
  {
    if (auto literal = ast::as<ast::BoolLit>(condition))
      return literal->value;
    if (auto unary = ast::as<ast::Unary>(condition); unary != nullptr && unary->op == ast::UnaryOp::logic_not) {
      auto found = settled(unary->operand);
      return found ? std::optional<bool>(! *found) : std::nullopt;
    }
    if (auto binary = ast::as<ast::Binary>(condition)) {
      switch (binary->op) {
      case ast::BinaryOp::logic_and:
      case ast::BinaryOp::short_and:
        return both_settled(binary, [](std::span<const bool> sides) { return std::ranges::all_of(sides, std::identity{}); });
      case ast::BinaryOp::logic_or:
      case ast::BinaryOp::short_or:
        return both_settled(binary, [](std::span<const bool> sides) { return std::ranges::any_of(sides, std::identity{}); });
      case ast::BinaryOp::equal:
      case ast::BinaryOp::not_equal: {
        if (auto kind = settled_kind(binary))
          return binary->op == ast::BinaryOp::equal ? *kind : ! *kind;
        const ir::Type* left = type_stood_for(binary->left);
        const ir::Type* right = type_stood_for(binary->right);
        if (left == nullptr || right == nullptr)
          return std::nullopt;
        bool alike = left == right;
        return binary->op == ast::BinaryOp::equal ? alike : ! alike;
      }
      default:
        return std::nullopt;
      }
    }
    if (auto call = ast::as<ast::Call>(condition))
      if (auto callee = ast::as<ast::NameRef>(call->callee); callee != nullptr && callee->name == is_record_name)
        return settled_is_record(call);
    return std::nullopt;
  }


  namespace {

    // What `⎕kindof` answers, for each kind of type there is, in the words the
    // specification uses; "other" is everything a program cannot hold as a
    // value of its own.
    std::span<const std::string_view> kinds()
  {
    static constexpr std::string_view every[] = { "integer", "float", "bool",       "char",   "str",    "enumeration",
                                                  "record",  "sum",   "tuple",      "array",  "list",   "set",
                                                  "dictionary", "result", "reference", "function", "other" };
    return every;
  }


    // The kind `⎕kindof` names for `ty`.
    std::string_view kind_of(const ir::Type* ty)
  {
    if (ir::is<ir::IntType>(ty))
      return "integer";
    if (ir::is<ir::FloatType>(ty))
      return "float";
    if (ty == ir::bool_type())
      return "bool";
    if (ty == ir::char_type())
      return "char";
    if (ty == ir::str_type())
      return "str";
    if (ir::is<ir::EnumType>(ty))
      return "enumeration";
    if (ir::is<ir::ProductType>(ty))
      return "record";
    if (ir::is<ir::SumType>(ty))
      return "sum";
    if (ir::is<ir::TupleType>(ty))
      return "tuple";
    if (ir::is<ir::ArrayType>(ty))
      return "array";
    if (ir::is<ir::ListType>(ty))
      return "list";
    if (ir::is<ir::SetType>(ty))
      return "set";
    if (ir::is<ir::DictType>(ty))
      return "dictionary";
    if (ir::is<ir::ResultType>(ty))
      return "result";
    if (ir::is<ir::PtrType>(ty))
      return "reference";
    if (ir::is<ir::FuncType>(ty))
      return "function";
    return "other";
  }

  } // namespace


  std::optional<bool> Checker::settled_kind(const ast::Binary* condition)
  {
    const ast::Expr* asked = condition->left;
    const ast::Expr* said = condition->right;
    if (ast::is<ast::StringLit>(asked))
      std::swap(asked, said);
    auto call = ast::as<ast::Call>(asked);
    auto callee = call != nullptr ? ast::as<ast::NameRef>(call->callee) : nullptr;
    auto literal = ast::as<ast::StringLit>(said);
    if (callee == nullptr || callee->name != names::kind_of_name || literal == nullptr)
      return std::nullopt;
    if (call->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, call->span,
                 { { "name", names::kind_of_name }, { "expected", 1 }, { "found", call->args.size() } });
      return false;
    }
    if (std::ranges::find(kinds(), literal->value) == kinds().end()) [[unlikely]] {
      std::string every;
      for (auto one : kinds()) {
        if (! every.empty())
          every += ", ";
        every += one;
      }
      diags.emit(D::LANG_KIND_UNKNOWN, literal->span, { { "found", std::string(literal->value) }, { "kinds", every } });
      return false;
    }
    const ir::Type* found = type_stood_for(call->args[0]);
    if (found == nullptr)
      return std::nullopt;
    return kind_of(found) == literal->value;
  }


  std::optional<bool> Checker::settled_is_record(const ast::Call* call)
  {
    if (call->args.size() != 1) [[unlikely]] {
      diags.emit(D::LANG_CALL_WRONG_ARGUMENT_COUNT, call->span,
                 { { "name", is_record_name }, { "expected", 1 }, { "found", call->args.size() } });
      return std::nullopt;
    }
    const ir::Type* found = type_stood_for(call->args[0]);
    if (found == nullptr)
      return std::nullopt;
    return ir::is<ir::ProductType>(found);
  }


  std::optional<bool> Checker::both_settled(const ast::Binary* condition,
                                            const std::function<bool(std::span<const bool>)>& joined)
  {
    auto left = settled(condition->left);
    auto right = settled(condition->right);
    if (! left || ! right)
      return std::nullopt;
    const bool sides[] = { *left, *right };
    return joined(sides);
  }


  const ir::Type* Checker::type_stood_for(const ast::Expr* expr)
  {
    if (auto call = ast::as<ast::Call>(expr)) {
      auto callee = ast::as<ast::NameRef>(call->callee);
      if (callee == nullptr || callee->name != typeof_name)
        return nullptr;
      if (call->args.size() != 1)
        return nullptr;
      return type_of_a_lift(call->args[0]);
    }
    if (auto lift = ast::as<ast::Lifted>(expr))
      return lifted_type(lift);
    if (ast::is<ast::Element>(expr))
      return field_type_named(expr);
    return nullptr;
  }


  const ir::Type* Checker::lifted_type(const ast::Lifted* expr)
  {
    if (expr->written == nullptr)
      return nullptr;
    if (auto ref = ast::as<ast::TypeRef>(expr->written);
        ref != nullptr && ! ref->module && ! ref->result && find_local(ref->name) != nullptr)
      // A local by that name, so what stands there is the value and not a
      // type of the same spelling.
      return nullptr;
    const ir::Type* found = resolve_type(expr->written);
    return found == ir::error_type() ? nullptr : found;
  }


  const ir::Type* Checker::type_of_a_lift(const ast::Expr* asked)
  {
    auto lift = ast::as<ast::Lifted>(asked);
    if (lift == nullptr) [[unlikely]] {
      diags.emit(D::LANG_TYPEOF_TAKES_A_LIFT, asked->span);
      return nullptr;
    }
    if (const ir::Type* lifted = lifted_type(lift); lifted != nullptr) [[unlikely]] {
      // A type was lifted, so its own type is being asked for, and a type is
      // not a thing this language has a type of.
      diags.emit(D::LANG_TYPEOF_OF_A_TYPE, asked->span, { { "found", lifted->written() } });
      return nullptr;
    }
    const ast::Expr* written = lift->value;
    if (written == nullptr)
      if (auto ref = ast::as<ast::TypeRef>(lift->written)) {
        auto name = syntax.make<ast::NameRef>(asked->span);
        name->name = ref->name;
        written = name;
      }
    if (written == nullptr)
      return nullptr;
    if (auto name = ast::as<ast::NameRef>(written))
      // Asking what a name is of is reading it, as far as a reader is concerned.
      if (Local* local = find_local(name->name); local != nullptr)
        local->read = true;
    const ir::Type* found = hint_of(written);
    return found == ir::error_type() ? nullptr : found;
  }


  std::optional<std::vector<const ast::IfArm*>> Checker::chosen_arms(const ast::If* stmt)
  {
    std::vector<const ast::IfArm*> found;
    for (auto arm : stmt->arms) {
      if (! arm->comptime || arm->condition == nullptr) {
        found.push_back(arm);
        continue;
      }
      auto holds = settled(arm->condition);
      if (! holds) [[unlikely]] {
        diags.emit(D::LANG_COMPTIME_NOT_SETTLED, arm->condition->span);
        return std::nullopt;
      }
      if (*holds) {
        // The arm is taken, so what it does is what the whole `if` does.
        auto taken = syntax.copy(*arm);
        taken->condition = nullptr;
        taken->comptime = false;
        found.push_back(taken);
        break;
      }
    }
    return found;
  }


  ir::Value* Checker::lower_scoped(ir::Builder& builder, const ast::Scoped* expr, const ir::Type* wanted)
  {
    push_scope();
    ir::Value* found = lower_block(builder, expr->body, &builder.function(), false, wanted, true);
    pop_scope(! builder.is_terminated() ? std::optional<Span>(expr->span) : std::nullopt);
    return found != nullptr ? found : undef_error(module);
  }


  ir::Value* Checker::lower_if(ir::Builder& builder, const ast::If* stmt, ir::Function* func, const ir::Type* wanted,
                               bool produces)
  {
    if (std::ranges::any_of(stmt->arms, [](auto arm) { return arm->comptime; })) {
      if (abstract != nullptr) {
        // It asks what the types are, which a body checked where it is written
        // does not know: its arms are checked where a call says.
        abstract->deferred.push_back(stmt->span);
        return undef_error(module);
      }
      auto arms = chosen_arms(stmt);
      if (! arms)
        return undef_error(module);
      if (arms->empty())
        // Every arm the compiler settled was false and there was no `else`.
        return produces ? undef_error(module) : undef_of(module, ir::void_type());
      const ast::If* written = stmt;
      if (arms->size() == 1 && (*arms)[0]->condition == nullptr) {
        ir::Value* found = lower_block(builder, (*arms)[0]->body, func, produces, wanted, produces);
        // What the `if` comes to is what the arm taken comes to.
        auto chosen = (*arms)[0]->body->stmts();
        KeySet made;
        if (! chosen.empty())
          if (auto last = ast::as<ast::ExprStmt>(chosen.back()))
            made = arenas_in(last->value);
        made_from[id_of(written)] = { written, std::move(made) };
        return found != nullptr ? found : undef_of(module, ir::void_type());
      }
      auto narrowed = syntax.copy(*stmt);
      narrowed->arms = syntax.list(*arms);
      stmt = narrowed;
      // What the arms left come to, which the lowering below records for the
      // `if` it lowers.
      made_alias[id_of(written)] = { written, stmt };
    }
    const ast::IfArm* last = stmt->arms.back();
    bool has_else = last->condition == nullptr;
    if (produces && ! has_else) [[unlikely]] {
      diags.emit(D::LANG_IF_NEEDS_AN_ELSE, stmt->span);
      return undef_error(module);
    }
    std::vector<const ast::IfArm*> asked_arms;
    for (auto arm : stmt->arms)
      if (arm->condition != nullptr)
        asked_arms.push_back(arm);
    std::vector<ir::BasicBlock*> blocks;
    for (std::size_t i = 0; i < asked_arms.size(); ++i)
      blocks.push_back(builder.new_block("then"));
    // One per condition: the token current where that condition branched.
    std::vector<ir::Value*> asked_memory;
    ir::BasicBlock* otherwise = has_else ? builder.new_block("otherwise") : nullptr;
    bool spoiled = false;
    for (std::size_t index = 0; index < asked_arms.size(); ++index) {
      const ast::IfArm* arm = asked_arms[index];
      // Lowered with nothing expected of it, so that a condition of the wrong
      // type is reported once, as a condition.
      ir::Value* condition = asked(builder, lower_expr(builder, arm->condition, nullptr), arm->condition->span);
      const ir::Type* found = value_type_of(condition);
      if (found != ir::bool_type() && found != ir::error_type()) {
        diags.emit(D::LANG_IF_CONDITION_NOT_BOOLEAN, arm->condition->span, { { "found", found->written() } });
        spoiled = true;
        condition = undef_of(module, ir::bool_type());
      } else if (found == ir::error_type()) {
        spoiled = true;
        condition = undef_of(module, ir::bool_type());
      }
      ir::BasicBlock* following = index + 1 < asked_arms.size() ? builder.new_block("elif") : otherwise;
      if (following == nullptr) {
        // Nothing left to ask and no `else`: what the last condition falls
        // through to is the place the arms join.
        following = builder.new_block("otherwise");
        otherwise = following;
      }
      builder.condbr(condition, blocks[index], following, {}, {}, arm->span);
      asked_memory.push_back(builder.memory());
      builder.position_at(following);
    }
    if (spoiled)
      return undef_error(module);
    std::vector<ArmPlan> plan;
    for (std::size_t i = 0; i < asked_arms.size(); ++i)
      plan.push_back(ArmPlan{ asked_arms[i]->body, blocks[i], std::nullopt, asked_memory[i] });
    // What falls past every condition begins with the last one's token.
    ir::Value* after = ! asked_memory.empty() ? asked_memory.back() : builder.memory();
    if (has_else) {
      plan.push_back(ArmPlan{ last->body, otherwise, std::nullopt, after });
      return run_arms(builder, stmt, func, plan, wanted, produces);
    }
    return run_arms(builder, stmt, func, plan, wanted, produces, otherwise, after);
  }

  // -- loops ------------------------------------------------------------------

  ir::Value* Checker::lower_while(ir::Builder& builder, const ast::While* stmt, ir::Function* func,
                                  const ir::Type* expected, bool produces)
  {
    if (builder.block() == nullptr)
      return undef_error(module);
    const ast::Label* label = label_of(stmt->label);
    const ir::Type* handing = wanted_of_a_loop(stmt, expected, produces);
    std::vector<Local*> carried_locals = loop_locals(stmt->body, stmt->alternative);
    ir::BasicBlock* header = builder.new_block("loop");
    ir::BasicBlock* body = builder.new_block("body");
    // A loop with a name is a loop something may leave, and where the exit
    // takes what a jump hands over it has to take it before there is a jump.
    bool leaves = label != nullptr || stmt->alternative != nullptr || produces;
    ir::BasicBlock* leave = leaves ? builder.new_block("leave") : nullptr;
    ir::BasicBlock* after = builder.new_block("done");
    {
      std::vector<ir::Value*> handed;
      for (auto local : carried_locals)
        handed.push_back(local->value);
      handed.push_back(builder.memory());
      builder.br(header, handed, stmt->span);
    }
    builder.position_at(header);
    std::vector<ir::Value*> params;
    for (auto local : carried_locals)
      params.push_back(header->add_param(value_type_of(local->value), local->name));
    ir::Value* token = header->add_param(ir::mem_type(), "mem");
    std::optional<std::vector<ir::Value*>> ways;
    ir::Value* exit_token = token;
    if (leaves)
      std::tie(ways, exit_token) = exit_params(after, carried_locals, params);
    for (std::size_t i = 0; i < carried_locals.size(); ++i) {
      carried_locals[i]->value = params[i];
      carried_locals[i]->value_span = stmt->span;
      carried_locals[i]->read = false;
    }
    builder.set_memory(token);
    // Lowered with nothing expected of it, so that a condition of the wrong
    // type is reported once, as a condition.
    ir::Value* condition = asked(builder, lower_expr(builder, stmt->condition, nullptr), stmt->condition->span);
    const ir::Type* found = value_type_of(condition);
    if (found != ir::bool_type()) {
      if (found != ir::error_type())
        diags.emit(D::LANG_LOOP_CONDITION_NOT_BOOLEAN, stmt->condition->span, { { "found", found->written() } });
      condition = undef_of(module, ir::bool_type());
    } else if (stmt->until)
      // `unless` is this loop with its condition read the other way round.
      condition = negate(builder, condition, stmt->span);
    builder.condbr(condition, body, leave != nullptr ? leave : after, {}, {}, stmt->span);
    ir::Value* ran_out = builder.memory();
    KeySet outer_carried = carried;
    // A name the loop carries is read by the next turn, so replacing the value
    // it stands for is not throwing that value away.
    for (auto local : carried_locals)
      carried.insert(id_of(local));
    builder.position_at(body);
    push_scope();
    Loop* one = begin_loop(label, header, after, carried_locals, {}, nullptr, produces,
                           produces && stmt->alternative == nullptr, handing);
    Provenance head = a_turn_begins(stmt->body);
    if (one != nullptr)
      one->turn_began = head;
    lower_block(builder, stmt->body, func, false);
    std::optional<Provenance> end;
    if (! builder.is_terminated())
      end = provenance();
    end_loop(label);
    pop_scope(! builder.is_terminated() ? std::optional<Span>(stmt->body->span) : std::nullopt);
    auto literal = ast::as<ast::BoolLit>(stmt->condition);
    after_the_turns(head, end, one, true, ! (literal != nullptr && literal->value));
    carried = outer_carried;
    if (! builder.is_terminated() && builder.block() != nullptr) {
      std::vector<ir::Value*> handed;
      for (auto local : carried_locals)
        handed.push_back(local->value);
      handed.push_back(builder.memory());
      builder.br(header, handed, stmt->span);
      // The branch backwards reads every value it hands over.
      for (auto local : carried_locals)
        local->read = true;
    }
    return loop_answer(builder, stmt, func, one, leave, after, carried_locals, params, token, exit_token, ran_out,
                       ways ? &*ways : nullptr, expected, produces);
  }


  ir::Value* Checker::lower_foreach(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func,
                                    const ir::Type* expected, bool produces)
  {
    if (builder.block() == nullptr)
      return undef_error(module);
    if (! stmt->comptime && ! members.empty()) {
      auto changed = syntax.copy(*stmt);
      changed->iterable = called_each(builder, stmt->iterable);
      stmt = changed;
      auto written = ast::as<ast::Call>(stmt->iterable);
      if (written != nullptr && ast::is<ast::Member>(written->callee) && generator_named(written->callee) == nullptr) {
        // `v.g(…)` as the call it stands for, `T.g(v, …)`, so that the loop
        // sees the generator it walks where `g` is one.
        Held through = through_a_value(builder, written);
        if (through.is_refused())
          return undef_error(module);
        if (auto call = through.get<ast::Call>()) {
          auto again = syntax.copy(*stmt);
          again->iterable = call;
          stmt = again;
        }
      }
    }
    if (! stmt->marks.empty() && (stmt->comptime || written_in(stmt))) {
      // What these give is made for the turn and is nowhere to refer to.  The
      // names are bound as copies, so nothing more is said.
      std::vector<std::pair<std::string_view, Span>> names{ { stmt->name, stmt->name_span } };
      for (const auto& more : stmt->more)
        names.emplace_back(more.name, more.span);
      for (std::size_t i = 0; i < names.size() && i < stmt->marks.size(); ++i)
        if (! stmt->marks[i].empty())
          diags.emit(D::LANG_FOREACH_NOT_A_PLACE, names[i].second,
                     { { "name", names[i].first },
                       { "mark", stmt->marks[i] },
                       { "what", "what a turn gives it is made for the turn, not somewhere a reference could name" } });
      auto unmarked = syntax.copy(*stmt);
      unmarked->marks = {};
      stmt = unmarked;
    }
    if (stmt->comptime) {
      if (abstract != nullptr) {
        // What it walks is what the types are: checked where a call says.
        abstract->deferred.push_back(stmt->span);
        return undef_error(module);
      }
      return written_out(builder, stmt, func, expected, produces);
    }
    if (auto inside = written_in(stmt))
      return lower_written_in(builder, stmt, func, expected, produces, std::get<0>(*inside), std::get<1>(*inside));
    auto found = iteration_over(builder, stmt);
    if (! found) {
      // Bound to nothing that means anything, so that a later mention of the
      // name reports nothing of its own.
      push_scope();
      if (stmt->name != wildcard_name)
        bind_local(stmt->name, undef_error(module), stmt->name_span);
      for (const auto& more : stmt->more)
        if (more.name != wildcard_name)
          bind_local(more.name, undef_error(module), more.span);
      lower_block(builder, stmt->body, func, false);
      pop_scope();
      return undef_error(module);
    }
    if (found->finish) {
      // Whichever way the loop is left, what it walks is told to finish.
      auto finish = found->finish;
      auto& run = syntax.hold<std::function<void(ir::Builder&)>>(finish);
      auto emit = syntax.make<ast::Emit>(stmt->span);
      emit->run = &run;
      auto expr_stmt = syntax.make<ast::ExprStmt>(stmt->span);
      expr_stmt->value = emit;
      auto defer = syntax.make<ast::Defer>(stmt->span);
      defer->stmt = expr_stmt;
      Deferred* entry = make<Deferred>();
      entry->node = defer;
      entry->depth = std::int64_t(scopes.size());
      deferred.push_back({ entry });
      emptied.emplace_back();
      Iteration rest = *found;
      rest.finish = nullptr;
      ir::Value* answer = lower_walk(builder, stmt, func, expected, produces, std::move(rest));
      deferred.pop_back();
      emptied.pop_back();
      if (! builder.is_terminated() && builder.block() != nullptr)
        finish(builder);
      return answer;
    }
    return lower_walk(builder, stmt, func, expected, produces, std::move(*found));
  }


  ir::Value* Checker::lower_walk(ir::Builder& builder, const ast::ForEach* stmt, ir::Function* func,
                                 const ir::Type* expected, bool produces, Iteration found)
  {
    const ast::Label* label = label_of(stmt->label);
    const ir::Type* handing = wanted_of_a_loop(stmt, expected, produces);
    std::vector<Local*> carried_locals = loop_locals(stmt->body, stmt->alternative);
    ir::BasicBlock* header = builder.new_block("loop");
    // A place asked whether it is picked is asked before its turn, so it is
    // laid out before the body.
    ir::BasicBlock* picks = found.wanted ? builder.new_block("pick") : nullptr;
    ir::BasicBlock* body = builder.new_block("body");
    bool leaves = label != nullptr || stmt->alternative != nullptr || produces;
    ir::BasicBlock* leave = leaves ? builder.new_block("leave") : nullptr;
    ir::BasicBlock* after = builder.new_block("done");
    {
      std::vector<ir::Value*> handed(found.start.begin(), found.start.end());
      for (auto local : carried_locals)
        handed.push_back(local->value);
      handed.push_back(builder.memory());
      builder.br(header, handed, stmt->span);
    }
    builder.position_at(header);
    LoopState state;
    for (auto one : found.start)
      state.push_back(header->add_param(value_type_of(one), std::string(stmt->name)));
    std::vector<ir::Value*> params;
    for (auto local : carried_locals)
      params.push_back(header->add_param(value_type_of(local->value), local->name));
    ir::Value* token = header->add_param(ir::mem_type(), "mem");
    std::optional<std::vector<ir::Value*>> ways;
    ir::Value* exit_token = token;
    if (leaves)
      std::tie(ways, exit_token) = exit_params(after, carried_locals, params);
    for (std::size_t i = 0; i < carried_locals.size(); ++i) {
      carried_locals[i]->value = params[i];
      carried_locals[i]->value_span = stmt->span;
      carried_locals[i]->read = false;
    }
    builder.set_memory(token);
    ir::BasicBlock* asks = picks != nullptr ? picks : body;
    ir::Value* more = found.more(builder, state);
    builder.condbr(more, asks, leave != nullptr ? leave : after, {}, {}, stmt->span);
    ir::Value* ran_out = builder.memory();
    if (found.wanted) {
      // A place that is not picked goes straight back to the test with
      // everything as it was but where the walk is.
      ir::BasicBlock* skip = builder.new_block("skip");
      builder.position_at(asks);
      ir::Value* picked = found.wanted(builder, state);
      builder.condbr(picked, body, skip, {}, {}, stmt->span);
      builder.position_at(skip);
      std::vector<ir::Value*> handed = found.passed ? found.passed(builder, state) : found.step(builder, state);
      handed.insert(handed.end(), params.begin(), params.end());
      handed.push_back(builder.memory());
      builder.br(header, handed, stmt->span);
    }
    KeySet outer_carried = carried;
    for (auto local : carried_locals)
      carried.insert(id_of(local));
    builder.position_at(body);
    push_scope();
    bind_turn(builder, stmt, found, state);
    {
      std::vector<std::string> names{ std::string(stmt->name) };
      for (const auto& one : stmt->more)
        names.emplace_back(one.name);
      taken_out_of(stmt->iterable, names);
    }
    Loop* one = begin_loop(label, header, after, carried_locals, state, found.step, produces,
                           produces && stmt->alternative == nullptr, handing);
    Provenance head = a_turn_begins(stmt->body);
    if (one != nullptr && ! one_turn(stmt->iterable))
      one->turn_began = head;
    lower_block(builder, stmt->body, func, false);
    std::optional<Provenance> end;
    if (! builder.is_terminated())
      end = provenance();
    end_loop(label);
    pop_scope(! builder.is_terminated() ? std::optional<Span>(stmt->body->span) : std::nullopt);
    after_the_turns(head, end, one, ! one_turn(stmt->iterable));
    carried = outer_carried;
    if (! builder.is_terminated() && builder.block() != nullptr) {
      std::vector<ir::Value*> handed = found.step(builder, state);
      for (auto local : carried_locals)
        handed.push_back(local->value);
      handed.push_back(builder.memory());
      builder.br(header, handed, stmt->span);
      for (auto local : carried_locals)
        local->read = true;
    }
    return loop_answer(builder, stmt, func, one, leave, after, carried_locals, params, token, exit_token, ran_out,
                       ways ? &*ways : nullptr, expected, produces);
  }


  // -- naming a loop, and leaving or repeating it -----------------------------

  const ast::Label* Checker::label_of(const ast::Label* label)
  {
    if (label == nullptr)
      return nullptr;
    if (std::ranges::any_of(loops, [&](Loop* one) { return one->label == label->name; })) [[unlikely]] {
      diags.emit(D::LANG_LOOP_LABEL_REUSED, label->span, { { "name", label->name } });
      return nullptr;
    }
    return label;
  }


  Loop* Checker::begin_loop(const ast::Label* label, ir::BasicBlock* header, ir::BasicBlock* after,
                            std::span<Local* const> carried_locals, std::span<ir::Value* const> state,
                            const Step& step, bool answers, bool wraps, const ir::Type* handing)
  {
    if (label == nullptr)
      return nullptr;
    Loop* one = make<Loop>();
    one->label = label->name;
    one->span = label->span;
    one->header = header;
    one->after = after;
    one->carried.assign(carried_locals.begin(), carried_locals.end());
    one->state.assign(state.begin(), state.end());
    one->step = step;
    one->answers = answers;
    one->wraps = wraps;
    one->handing = handing;
    one->frame = std::int64_t(deferred.size());
    one->scope_at = std::int64_t(scopes.size()) - 1;
    loops.push_back(one);
    return one;
  }


  Loop* Checker::end_loop(const ast::Label* label)
  {
    if (label == nullptr)
      return nullptr;
    Loop* one = loops.back();
    loops.pop_back();
    if (! one->named)
      diags.emit(D::LANG_LOOP_LABEL_UNUSED, one->span, { { "name", one->label } });
    return one;
  }


  std::tuple<std::vector<ir::Value*>, ir::Value*> Checker::exit_params(ir::BasicBlock* after,
                                                                       std::span<Local* const> carried_locals,
                                                                       std::span<ir::Value* const> params)
  {
    std::vector<ir::Value*> ways;
    for (std::size_t i = 0; i < carried_locals.size() && i < params.size(); ++i)
      ways.push_back(after->add_param(value_type_of(params[i]), carried_locals[i]->name));
    return { std::move(ways), after->add_param(ir::mem_type(), "mem") };
  }


  Loop* Checker::find_loop(const ast::Label* label)
  {
    for (auto it = loops.rbegin(); it != loops.rend(); ++it)
      if ((*it)->label == label->name) {
        (*it)->named = true;
        return *it;
      }
    diags.emit(D::LANG_LOOP_LABEL_UNKNOWN, label->span, { { "name", label->name } });
    return nullptr;
  }


  void Checker::lower_break(ir::Builder& builder, const ast::Break* stmt)
  {
    Loop* found = find_loop(stmt->label);
    if (found == nullptr || builder.block() == nullptr)
      return;
    ir::Value* handed = handed_over(builder, stmt, found);
    // Leaving every block opened since the loop began, so what they put off is
    // done now -- after what is handed over was worked out, as a return's is.
    auto kept = on_the_way_out();
    run_deferred(builder, std::get<1>(demanding), found->frame);
    all_closed(std::span(scopes).subspan(std::min<std::size_t>(found->scope_at, scopes.size())), stmt->span);
    // What holds here holds after the loop, which this way reaches.
    found->left_with.push_back(provenance());
    left(kept);
    if (builder.is_terminated())
      return;
    std::vector<ir::Value*> carried_values;
    for (auto local : found->carried)
      carried_values.push_back(local->value);
    carried_values.push_back(builder.memory());
    if (handed != nullptr)
      carried_values.push_back(handed);
    builder.br(found->after, carried_values, stmt->span);
    for (auto local : found->carried)
      local->read = true;
  }


  ir::Value* Checker::handed_over(ir::Builder& builder, const ast::Break* stmt, Loop* found)
  {
    if (stmt->value == nullptr) {
      if (! found->bare_at)
        found->bare_at = stmt->span;
      if (found->answers)
        diags.emit(D::LANG_LOOP_BREAK_HANDS_NOTHING, stmt->span);
      return nullptr;
    }
    if (! found->answers) [[unlikely]] {
      // The loop stands where nothing reads what it comes to, so there is
      // nowhere for this to go.
      diags.emit(D::LANG_LOOP_VALUE_UNUSED, stmt->value->span);
      return nullptr;
    }
    if (! found->handed_at)
      found->handed_at = stmt->span;
    LoopContext outer = as_the_loops_value();
    ir::Value* value = found->handing != nullptr
                         ? lower_into(builder, stmt->value, found->handing, stmt->value->span)
                         : lower_expr(builder, stmt->value, nullptr);
    const ir::Type* ty = value_type_of(value);
    bool refused = false;
    if (ty == ir::error_type())
      refused = true;
    else if (found->handing == nullptr)
      found->handing = ty;
    else if (ty != found->handing) {
      report_mismatch(stmt->value->span, ty, found->handing);
      refused = true;
    }
    as_it_was(outer);
    if (refused)
      return nullptr;
    if (! found->wraps)
      return value;
    return builder.wrap(value, builder.bool_const(false), ir::result_type(found->handing), stmt->span);
  }


  void Checker::lower_continue(ir::Builder& builder, const ast::Continue* stmt)
  {
    Loop* found = find_loop(stmt->label);
    if (found == nullptr || builder.block() == nullptr)
      return;
    // The next turn begins with the body left, which runs what it put off.
    auto kept = on_the_way_out();
    run_deferred(builder, std::get<1>(demanding), found->frame);
    all_closed(std::span(scopes).subspan(std::min<std::size_t>(found->scope_at, scopes.size())), stmt->span);
    if (found->turn_began)
      goes_round(*found->turn_began, stmt->span);
    left(kept);
    if (builder.is_terminated())
      return;
    std::vector<ir::Value*> handed = found->step ? found->step(builder, found->state) : found->state;
    for (auto local : found->carried)
      handed.push_back(local->value);
    handed.push_back(builder.memory());
    builder.br(found->header, handed, stmt->span);
    for (auto local : found->carried)
      local->read = true;
  }


  LoopContext Checker::as_the_loops_value()
  {
    LoopContext outer{ operand_of, initializing, assigning, handing_over, leaving };
    operand_of.reset();
    initializing.reset();
    assigning.reset();
    handing_over.reset();
    leaving = true;
    return outer;
  }


  void Checker::as_it_was(const LoopContext& outer)
  {
    operand_of = outer.operand_of;
    initializing = outer.initializing;
    assigning = outer.assigning;
    handing_over = outer.handing_over;
    leaving = outer.leaving;
  }


  const ir::Type* Checker::wanted_of_a_loop(const ast::Expr* stmt, const ir::Type* expected, bool produces)
  {
    return produces ? aiming_at(expected) : nullptr;
  }


  ir::Value* Checker::loop_answer(ir::Builder& builder, const ast::Expr* stmt, ir::Function* func, Loop* one,
                                  ir::BasicBlock* leave, ir::BasicBlock* after, std::span<Local* const> carried_locals,
                                  std::span<ir::Value* const> params, ir::Value* token, ir::Value* exit_token,
                                  ir::Value* ran_out, const std::vector<ir::Value*>* ways, const ir::Type* expected,
                                  bool produces)
  {
    const ast::Block* alternative = nullptr;
    if (auto loop = ast::as<ast::While>(stmt))
      alternative = loop->alternative;
    else if (auto walk = ast::as<ast::ForEach>(stmt))
      alternative = walk->alternative;
    const ir::Type* handing = one != nullptr ? one->handing : nullptr;
    ir::Value* otherwise = nullptr;
    if (leave != nullptr) {
      builder.position_at(leave);
      builder.set_memory(ran_out);
      for (std::size_t i = 0; i < carried_locals.size() && i < params.size(); ++i) {
        carried_locals[i]->value = params[i];
        carried_locals[i]->value_span = stmt->span;
      }
      if (alternative != nullptr) {
        push_scope();
        LoopContext outer = as_the_loops_value();
        otherwise = lower_block(builder, alternative, func, false, handing, produces);
        as_it_was(outer);
        pop_scope(! builder.is_terminated() ? std::optional<Span>(alternative->span) : std::nullopt);
        if (otherwise != nullptr) {
          const ir::Type* found = value_type_of(otherwise);
          if (found == ir::error_type())
            otherwise = nullptr;
          else if (handing == nullptr)
            handing = found;
          else if (found != handing) {
            report_mismatch(alternative->span, found, handing);
            otherwise = nullptr;
          }
        }
      }
    }
    if (produces && handing == nullptr) [[unlikely]] {
      // Nothing hands anything over and no `else` arm gives anything.
      diags.emit(D::LANG_LOOP_COMES_TO_NOTHING, stmt->span);
      settle_after(builder, carried_locals, ways, params, after, exit_token, token, stmt, leave, nullptr);
      return undef_error(module);
    }
    const ir::Type* answer = nullptr;
    if (produces)
      answer = alternative != nullptr ? handing : ir::result_type(handing);
    ir::Value* given = nullptr;
    if (answer != nullptr) {
      if (alternative != nullptr)
        given = otherwise;
      else {
        auto result = ir::as<ir::ResultType>(answer);
        ir::Value* nothing = undef_of(module, handing);
        ir::Value* failed = builder.bool_const(true);
        ir::Value* error = result->err == nullptr ? nullptr : undef_of(module, result->err);
        given = builder.wrap(nothing, failed, answer, stmt->span, error);
      }
      if (given == nullptr)
        given = undef_of(module, answer);
    }
    settle_after(builder, carried_locals, ways, params, after, exit_token, token, stmt, leave, given);
    if (answer == nullptr)
      return undef_of(module, ir::void_type());
    ir::Value* value = after->add_param(answer, "answer");
    if (! accepts(expected, answer)) [[unlikely]] {
      report_mismatch(stmt->span, answer, expected);
      return undef_error(module);
    }
    return value;
  }


  void Checker::settle_after(ir::Builder& builder, std::span<Local* const> carried_locals,
                             const std::vector<ir::Value*>* ways, std::span<ir::Value* const> params,
                             ir::BasicBlock* after, ir::Value* exit_token, ir::Value* token, const ast::Expr* stmt,
                             ir::BasicBlock* leave, ir::Value* given)
  {
    if (leave != nullptr) {
      std::vector<ir::Value*> handed;
      for (auto local : carried_locals)
        handed.push_back(local->value);
      handed.push_back(builder.memory());
      if (given != nullptr)
        handed.push_back(given);
      builder.br(after, handed, stmt->span);
    }
    builder.position_at(after);
    std::span<ir::Value* const> now = ways != nullptr ? std::span<ir::Value* const>(*ways) : params;
    for (std::size_t i = 0; i < carried_locals.size() && i < now.size(); ++i) {
      carried_locals[i]->value = now[i];
      carried_locals[i]->value_span = stmt->span;
    }
    builder.set_memory(ways != nullptr ? exit_token : token);
  }


  void Checker::bind_turn(ir::Builder& builder, const ast::ForEach* stmt, const Iteration& found,
                          std::span<ir::Value* const> state)
  {
    std::vector<std::tuple<std::string, Span>> names{ { std::string(stmt->name), stmt->name_span } };
    for (const auto& more : stmt->more)
      names.emplace_back(std::string(more.name), more.span);
    if (! stmt->marks.empty()) {
      bind_by_reference(builder, stmt, found, state, names);
      return;
    }
    std::vector<const ir::Type*> members_of;
    if (names.size() == 1)
      members_of.push_back(found.element);
    else if (auto tuple = ir::as<ir::TupleType>(found.element); tuple != nullptr && tuple->members.size() == names.size())
      members_of = tuple->members;
    for (std::size_t i = 0; i < names.size() && i < members_of.size(); ++i) {
      const auto& [name, span] = names[i];
      if (name != wildcard_name && never_copied(members_of[i], name, span)) {
        for (const auto& [one, where] : names)
          if (one != wildcard_name)
            bind_local(one, undef_error(module), where);
        return;
      }
    }
    ir::Value* value = found.take(builder, state);
    if (! stmt->more.empty()) {
      name_value(value, stmt->name, stmt->name_span);
      bind_apart(builder, stmt, value);
      report_inferred(names, stmt->type);
      return;
    }
    if (stmt->name == wildcard_name)
      // The name that is not a name, as it is in a `match` arm.
      return;
    name_value(value, stmt->name, stmt->name_span);
    bind_local(stmt->name, value, stmt->name_span, false, stmt->iterable->span, false, &builder);
    report_inferred(std::vector<std::tuple<std::string, Span>>{ { std::string(stmt->name), stmt->name_span } },
                    stmt->type);
  }


  bool Checker::never_copied(const ir::Type* ty, std::string_view name, Span span)
  {
    auto product = ir::as<ir::ProductType>(ty);
    if (product == nullptr || ! product->unique)
      return false;
    diags.emit(D::LANG_UNIQUE_COPIED, span,
               { { "type", ty->written() },
                 { "what", std::format("binding each one to '{0}' would be a copy of it; '&{0}' refers to it where it is",
                                       name) } });
    return true;
  }


  void Checker::bind_by_reference(ir::Builder& builder, const ast::ForEach* stmt, const Iteration& found,
                                  std::span<ir::Value* const> state,
                                  std::span<const std::tuple<std::string, Span>> names)
  {
    auto marks = stmt->marks;
    std::vector<std::pair<Ask, Ask>> getters;
    std::vector<const ir::Type*> types;
    if (names.size() == 1) {
      getters.emplace_back(found.take, found.place);
      types.push_back(found.element);
    } else if (found.members && found.members->size() == names.size()) {
      getters = *found.members;
      types = ir::as<ir::TupleType>(found.element)->members;
    } else {
      getters.resize(names.size());
      types.assign(names.size(), ir::error_type());
    }
    const ast::Expr* walked = walked_expr(stmt->iterable);
    bool writes = false;
    for (std::size_t i = 0; i < names.size() && i < marks.size(); ++i) {
      const auto& [name, span] = names[i];
      std::string_view mark = marks[i];
      const auto& [get, where] = getters[i];
      const ir::Type* ty = types[i];
      if (name == wildcard_name)
        continue;
      ir::Value* bound = undef_error(module);
      if (! mark.empty()) {
        if (! where)
          diags.emit(D::LANG_FOREACH_NOT_A_PLACE, span,
                     { { "name", name },
                       { "mark", mark },
                       { "what", "what a turn gives it is made for the turn, not somewhere a reference could name" } });
        else if (! can_be_referred_to(ty))
          diags.emit(D::LANG_REF_TYPE_NOT_ALLOWED, span, { { "found", ty->written() } });
        else if (mark == "&mut" && ! walked_may_change(walked))
          diags.emit(D::LANG_REF_PLACE_NOT_MUTABLE, span,
                     { { "name", ast::is<ast::NameRef>(walked) || ast::is<ast::Member>(walked) ? path_text(walked)
                                                                                                : "what the loop walks" } });
        else {
          writes = writes || mark == "&mut";
          ir::Value* place = where(builder, state);
          bound = builder.cast(ir::CastKind::bitcast, place, ir::ptr_type(ty, mark == "&mut"), span);
        }
      } else if (get && ty != ir::error_type() && ! never_copied(ty, name, span))
        bound = get(builder, state);
      bind_local(name, bound, span, false, stmt->iterable->span, false, &builder);
    }
    report_inferred(names, stmt->type);
    if (auto lent = walked_name(walked)) {
      // Lent for the loop: the references live as long as its body does.
      std::size_t before = borrows.size();
      lend(*lent, writes, walked->span);
      const std::tuple<std::string, Span>* marked = nullptr;
      for (std::size_t i = 0; i < names.size() && i < marks.size(); ++i)
        if (! marks[i].empty() && std::get<0>(names[i]) != wildcard_name) {
          marked = &names[i];
          break;
        }
      for (std::size_t at = before; at < borrows.size(); ++at) {
        Borrow* one = borrows[at];
        one->depth = std::int64_t(scopes.size()) - 1;
        if (marked != nullptr) {
          // Kept by the turn's reference, until its last use in the turn.
          one->keeper = std::pair<std::int64_t, std::int64_t>{ std::get<1>(*marked).start, id_of(stmt) };
          one->keeper_name = std::get<0>(*marked);
        }
      }
    }
  }


  const ast::Expr* Checker::walked_expr(const ast::Expr* expr)
  {
    if (auto call = ast::as<ast::Call>(expr))
      if (auto callee = ast::as<ast::NameRef>(call->callee);
          callee != nullptr && callee->name == enumerate_name && ! call->args.empty())
        return call->args[0];
    return expr;
  }


  std::optional<std::string> Checker::walked_name(const ast::Expr* expr)
  {
    while (auto member = ast::as<ast::Member>(expr))
      expr = member->base;
    if (auto name = ast::as<ast::NameRef>(expr); name != nullptr && find_local(name->name) != nullptr)
      return std::string(name->name);
    return std::nullopt;
  }


  bool Checker::walked_may_change(const ast::Expr* expr)
  {
    if (auto name = ast::as<ast::NameRef>(expr)) {
      if (Local* local = find_local(name->name); local != nullptr)
        return local->is_mutable;
      auto var = provided(name->name).get<ir::GlobalVar>();
      return var != nullptr && var->is_mutable();
    }
    return false;
  }

} // namespace pl4g::sema
