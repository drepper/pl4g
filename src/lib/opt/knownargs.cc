// What every caller hands a function, written into the function.  A port of
// pypl4g's opt/passes/knownargs.py.
//
// A function nothing outside the program can name has all its callers in
// view; where every one hands a parameter -- or one part of a parameter that
// travels as several -- the same constant, that is what the parameter is.  A
// caller's own parameter counts where what is known of it is, so the facts are
// worked out over every function in turn until none changes.
#include "opt/passes.hh"

#include <map>
#include <optional>
#include <set>
#include <tuple>
#include <unordered_map>
#include <unordered_set>

#include "ir/builder.hh"
#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // How many times every function is looked at at most.
    constexpr int rounds = 8;

    // Which whole parameter, or which part of one, a fact is about.
    constexpr std::int64_t whole = -1;

    using Key = std::tuple<const Function*, std::size_t, std::int64_t>;
    using Known = std::map<Key, Value*>;
    using Calls = std::vector<std::pair<Function*, Instruction*>>;

    const Instruction* tuple_of(const Value* value)
    {
      if (value->vkind != ValueKind::instruction)
        return nullptr;
      auto inst = static_cast<const Instruction*>(value);
      return inst->kind == InstKind::TupleInst ? inst : nullptr;
    }

    // How many parts the tuples handed for parameter `at` have, where every
    // call hands one made where it is called or a parameter; nought otherwise.
    std::int64_t parts(const Calls& calls, std::size_t at)
    {
      std::set<std::size_t> counts;
      bool every = true;
      for (const auto& [_, call] : calls) {
        auto arg = call->arguments()[at];
        if (auto made = tuple_of(arg))
          counts.insert(made->operands.size());
        else if (arg->vkind != ValueKind::block_param)
          every = false;
      }
      if (counts.size() != 1 || ! every)
        return 0;
      return std::int64_t(*counts.begin());
    }

    Value* of_a_parameter(const Function* caller, const Value* value, std::int64_t part, const Known& facts)
    {
      auto entry = caller->entry();
      if (value->vkind != ValueKind::block_param || entry == nullptr
          || static_cast<const BlockParam*>(value)->block != entry)
        return nullptr;
      for (std::size_t at = 0; at < entry->params.size(); ++at)
        if (entry->params[at].get() == value) {
          auto it = facts.find({ caller, at, part });
          return it == facts.end() ? nullptr : it->second;
        }
      return nullptr;
    }

    // The constant `value` -- or its part `part` -- is, where that is known in
    // `caller`.
    Value* known(const Function* caller, Value* value, std::int64_t part, const Known& facts)
    {
      if (part != whole) {
        if (auto made = tuple_of(value)) {
          if (part >= std::int64_t(made->operands.size()))
            return nullptr;
          return known(caller, made->operands[std::size_t(part)], whole, facts);
        }
        return of_a_parameter(caller, value, part, facts);
      }
      if (value->vkind == ValueKind::int_const || value->vkind == ValueKind::bool_const)
        return value;
      return of_a_parameter(caller, value, whole, facts);
    }

    // The one constant every call handed, where every one handed the same.
    Value* alike(const std::vector<Value*>& found)
    {
      if (found.empty() || std::ranges::any_of(found, [](Value* one) { return one == nullptr; }))
        return nullptr;
      auto first = found.front();
      auto value_of = [](const Value* one) -> __int128 {
        return one->vkind == ValueKind::int_const ? static_cast<const IntConst*>(one)->value
                                                  : static_cast<const BoolConst*>(one)->value ? 1 : 0;
      };
      for (std::size_t i = 1; i < found.size(); ++i) {
        auto one = found[i];
        if (one->vkind != first->vkind || ! same(one->ty, first->ty) || value_of(one) != value_of(first))
          return nullptr;
      }
      return first;
    }

    bool learn(Function* func, const Calls& calls, Known& facts)
    {
      const auto& params = func->entry()->params;
      for (const auto& [_, call] : calls)
        if (call->arguments().size() != params.size())
          return false;
      bool grew = false;
      for (std::size_t at = 0; at < params.size(); ++at) {
        std::vector<std::int64_t> wanted{ whole };
        for (std::int64_t i = 0, n = parts(calls, at); i < n; ++i)
          wanted.push_back(i);
        for (auto part : wanted) {
          Key key{ func, at, part };
          if (facts.contains(key))
            continue;
          std::vector<Value*> found;
          for (const auto& [caller, call] : calls)
            found.push_back(known(caller, call->arguments()[at], part, facts));
          auto made = alike(found);
          if (made != nullptr && (part != whole || same(made->ty, params[at]->ty))) {
            facts[key] = made;
            grew = true;
          }
        }
      }
      return grew;
    }

    bool apply(Function* func, const Known& facts)
    {
      const auto& params = func->entry()->params;
      std::unordered_map<const Value*, std::size_t> index;
      for (std::size_t at = 0; at < params.size(); ++at)
        index[params[at].get()] = at;
      std::unordered_map<const Value*, Value*> instead;
      for (std::size_t at = 0; at < params.size(); ++at)
        if (auto it = facts.find({ func, at, whole }); it != facts.end())
          instead[params[at].get()] = it->second;
      for (auto& block : func->blocks)
        for (auto inst : block->insts) {
          if (inst->kind != InstKind::ExtractInst)
            continue;
          auto at = index.find(inst->operands[0]);
          if (at == index.end())
            continue;
          if (auto it = facts.find({ func, at->second, inst->index }); it != facts.end() && same(it->second->ty, inst->ty))
            instead[inst] = it->second;
        }
      all_stand_for(*func, instead);
      return ! instead.empty();
    }

  } // anonymous namespace


  bool known_arguments(Module& module)
  {
    std::unordered_map<const Function*, Calls> calls;
    std::unordered_set<const Function*> taken;
    for (auto& [_, func] : module.functions)
      for (auto& block : func->blocks)
        for (auto inst : block->insts) {
          auto named = named_by(inst);
          if (named == nullptr)
            continue;
          if (inst->kind == InstKind::CallInst)
            calls[named].emplace_back(func.get(), inst);
          else
            // Named as a value, and so called from where nothing here sees.
            taken.insert(named);
        }
    std::unordered_set<const Function*> rooted;
    for (auto one : roots(module))
      rooted.insert(one);
    std::vector<Function*> closed;
    for (auto& [_, func] : module.functions)
      if (! func->is_declaration() && func->entry() != nullptr && func->linkage == Linkage::internal
          && ! rooted.contains(func.get()) && ! taken.contains(func.get()) && calls.contains(func.get()))
        closed.push_back(func.get());
    Known facts;
    for (int round = 0; round < rounds; ++round) {
      bool grew = false;
      for (auto func : closed)
        grew |= learn(func, calls[func], facts);
      if (! grew)
        break;
    }
    bool changed = false;
    for (auto func : closed)
      changed |= apply(func, facts);
    return changed;
  }

} // namespace pl4g::opt
