// The IR verifier.
#include "ir/verify.hh"

#include <algorithm>
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "diag/engine.hh"
#include "ir/builder.hh"
#include "ir/printer.hh"


namespace pl4g::ir {

  namespace {

    // Whether a value of `ty` is an address as far as a register is concerned:
    // a pointer, a collection, or anything held in memory.
    bool is_an_address(const Type* ty)
    {
      return is<PtrType>(ty) || is<SetType>(ty) || is<DictType>(ty) || held_in_memory(ty);
    }


    // Whether one of these is held as the other, where the bits are the same
    // bits: a code point or an enumeration and its holder, an address and
    // `u64`, and two function types differing only in what they promise.
    bool held_as(const Type* one, const Type* other)
    {
      auto f1 = as<FuncType>(one);
      auto f2 = as<FuncType>(other);
      if (f1 != nullptr && f2 != nullptr)
        return std::ranges::equal(f1->params, f2->params, same) && f1->ret == f2->ret;
      if (is<PtrType>(one) && other == u64())
        return true;
      if (is<PtrType>(other) && one == u64())
        return true;
      auto holder = [](const Type* ty) -> const Type* {
        if (auto c = as<CharType>(ty))
          return c->holder();
        if (auto e = as<EnumType>(ty))
          return e->holder;
        return nullptr;
      };
      auto h1 = holder(one);
      auto h2 = holder(other);
      return (h1 != nullptr && other == h1) || (h2 != nullptr && one == h2);
    }


    // Whether a value of `ty` is a whole number as far as a register is
    // concerned, which is what a widening or a narrowing is between.
    bool counts(const Type* ty)
    {
      return is<IntType>(ty) || is<BoolType>(ty) || is<CharType>(ty) || is<EnumType>(ty);
    }


    // Whether `parts_of(ty)` is `(ty,)`: a type not made of parts.
    bool not_of_parts(const std::vector<const Type*>& pieces, const Type* ty)
    {
      return pieces.size() == 1 && same(pieces[0], ty);
    }


    class Verifier {
    public:
      explicit Verifier(const Module& module) : module(module) {}

      void run(std::optional<std::span<Function* const>> functions)
      {
        check_module();
        if (functions) {
          std::unordered_set<const Function*> present;
          for (const auto& [key, func] : module.functions)
            present.insert(func.get());
          for (auto func : *functions)
            if (present.contains(func) && ! func->is_declaration())
              check_function(*func);
        } else
          for (const auto& [key, func] : module.functions)
            if (! func->is_declaration())
              check_function(*func);
        if (! problems.empty()) [[unlikely]] {
          std::string all;
          for (const auto& one : problems) {
            if (! all.empty())
              all += "; ";
            all += one;
          }
          throw InternalError(all);
        }
      }

    private:
      // Record one violated invariant.
      void fail(const std::string& where, const std::string& detail) { problems.push_back(where + ": " + detail); }

      void check_module()
      {
        check_symbols_are_distinct();
        check_special_caches();
      }

      // No two functions end up under one symbol: only a defect in the
      // mangling could make them.
      void check_symbols_are_distinct()
      {
        std::unordered_map<std::string, const Function*> seen;
        for (const auto& [key, func] : module.functions) {
          auto symbol = symbol_name(*func);
          auto previous = seen.find(symbol);
          if (previous != seen.end()) {
            const Function* before = previous->second;
            if (((before->attrs.external && func->attrs.external) || (before->attrs.host && func->attrs.host))
                && before->blocks.empty() && func->blocks.empty())
              // One function of the runtime, or of the host, declared by two
              // files.
              continue;
            fail("module", "'" + before->name + "' and '" + func->name + "' are both known by the symbol '" + symbol
                 + "'");
            continue;
          }
          seen.emplace(symbol, func.get());
        }
      }

      // The special-function caches agree with the attributes.
      void check_special_caches()
      {
        std::vector<const Function*> startups;
        std::unordered_set<const Function*> ctors;
        std::unordered_set<const Function*> dtors;
        for (const auto& [key, func] : module.functions) {
          if (func->attrs.special == SpecialKind::startup)
            startups.push_back(func.get());
          if (func->attrs.special == SpecialKind::constructor)
            ctors.insert(func.get());
          if (func->attrs.special == SpecialKind::destructor)
            dtors.insert(func.get());
        }
        if (startups.size() > 1)
          fail("module", "more than one function is marked startup");
        if (module.startup != nullptr && std::ranges::find(startups, module.startup) == startups.end())
          fail("module", "the cached startup function is not marked startup");
        if (! startups.empty() && module.startup == nullptr)
          fail("module", "a startup function exists but was not cached");
        if (ctors != std::unordered_set<const Function*>(module.ctors.begin(), module.ctors.end()))
          fail("module", "the constructor cache disagrees with the attributes");
        if (dtors != std::unordered_set<const Function*>(module.dtors.begin(), module.dtors.end()))
          fail("module", "the destructor cache disagrees with the attributes");
      }

      // One function's blocks, types and dominance.
      void check_function(const Function& func)
      {
        auto where = "function @" + func.name;
        auto entry = func.entry();
        if (entry == nullptr) {
          fail(where, "a definition with no entry block");
          return;
        }
        if (! std::ranges::equal(entry->params, func.fty->params,
                                 [](const auto& p, const Type* t) { return same(p->ty, t); }))
          fail(where, "entry block parameters do not match the function type");
        std::unordered_set<std::string> labels;
        for (const auto& block : func.blocks) {
          if (! labels.insert(block->label).second)
            fail(where, "duplicate block label '" + block->label + "'");
          check_block(func, *block);
        }
        check_dominance(func);
      }

      // A block is terminated exactly once and typed correctly.
      void check_block(const Function& func, const BasicBlock& block)
      {
        auto where = "function @" + func.name + ", block " + block.label;
        if (block.insts.empty()) {
          fail(where, "empty block");
          return;
        }
        for (std::size_t i = 0; i + 1 < block.insts.size(); ++i)
          if (block.insts[i]->is_terminator())
            fail(where, "terminator '" + block.insts[i]->opcode() + "' is not the last instruction");
        auto last = block.insts.back();
        if (! last->is_terminator())
          fail(where, "block ends with '" + last->opcode() + "', which is not a terminator");
        for (auto inst : block.insts)
          check_inst(where, func, *inst);
      }

      // The types of one instruction.
      void check_inst(const std::string& where, const Function& func, const Instruction& inst)
      {
        for (auto operand : inst.operands) {
          if (operand->vkind == ValueKind::int_const)
            if (auto ty = as<IntType>(operand->ty)) {
              auto value = static_cast<const IntConst*>(operand)->value;
              if (! ty->holds(value))
                fail(where, "constant " + decimal(value) + " does not fit in " + ty->render());
            }
          if (operand->ty == void_type())
            // Only a call answering nothing has this type, and its answer is
            // not a value; the front end reports one named, and this catches a
            // pass that builds one by mistake.
            fail(where, "'" + inst.opcode() + "' is given something that has no value");
        }
        auto render = [](const Value* v) { return v->ty->render(); };
        const auto& ops = inst.operands;
        switch (inst.kind) {
        case InstKind::RetInst: {
          auto expected = func.fty->ret;
          if (expected == void_type()) {
            if (! ops.empty())
              fail(where, "returning a value from a void function");
          } else if (ops.empty())
            fail(where, "returning no value from a non-void function");
          else if (! same(ops[0]->ty, expected))
            fail(where, "returning " + render(ops[0]) + " from a function returning " + expected->render());
          break;
        }
        case InstKind::BinaryInst:
          if (is<PtrType>(inst.ty)) {
            // Arithmetic on an address: the place so many bytes along.
            if (inst.bin != BinOp::add && inst.bin != BinOp::sub)
              fail(where, "'" + inst.opcode() + "' applied to an address");
            else if (! same(ops[0]->ty, inst.ty))
              fail(where, "'" + inst.opcode() + "' answering with " + inst.ty->render() + " from " + render(ops[0]));
            else if (! is<IntType>(ops[1]->ty))
              fail(where, "moving an address by " + render(ops[1]));
            break;
          }
          // Up to units, which the bits know nothing about.
          if (! same(without_units(ops[0]->ty), without_units(ops[1]->ty)))
            fail(where, "'" + inst.opcode() + "' applied to operands of different types");
          {
            // An operation that may have no answer says so in its type.
            auto result = as<ResultType>(inst.ty);
            auto answered = result != nullptr ? result->ok : inst.ty;
            if (! same(without_units(answered), without_units(ops[0]->ty)))
              fail(where, "'" + inst.opcode() + "' result type differs from its operands");
          }
          break;
        case InstKind::CastInst:
          if (inst.cast == CastKind::zext || inst.cast == CastKind::sext || inst.cast == CastKind::trunc) {
            if (! (counts(inst.ty) && counts(ops[0]->ty)))
              fail(where, "'" + inst.opcode() + "' between " + render(ops[0]) + " and " + inst.ty->render());
          } else if (inst.cast == CastKind::bitcast) {
            if (! (is_an_address(inst.ty) && is_an_address(ops[0]->ty)) && ! held_as(inst.ty, ops[0]->ty)
                && without_units(inst.ty) != without_units(ops[0]->ty))
              fail(where, "reading " + render(ops[0]) + " as " + inst.ty->render()
                   + ", which is not the same kind of thing");
          }
          break;
        case InstKind::FrameInst:
          if (! same(inst.ty, ptr_type(inst.held, true)))
            fail(where, "room for a " + inst.held->render() + " read as " + inst.ty->render());
          break;
        case InstKind::AssertInst:
          if (ops[0]->ty != bool_type())
            fail(where, "a check of " + render(ops[0]) + ", which is not a truth value");
          break;
        case InstKind::SyscallInst:
          // Everything the kernel is given is a machine word.
          if (! is<IntType>(inst.ty))
            fail(where, "a request to the kernel answering '" + inst.ty->render() + "'");
          for (auto one : ops)
            if (! is<IntType>(one->ty) && ! is<PtrType>(one->ty))
              fail(where, "a request to the kernel given '" + render(one) + "'");
          break;
        case InstKind::AddressInst:
          if (! is<PtrType>(ops[0]->ty))
            fail(where, "the address of something that is not a place");
          else if (! same(inst.ty, ops[0]->ty))
            fail(where, "the address of a " + render(ops[0]) + " read as " + inst.ty->render());
          break;
        case InstKind::TupleInst: {
          // Anything of several parts, and not only a tuple.
          auto pieces = parts_of(inst.ty);
          if (not_of_parts(pieces, inst.ty))
            fail(where, "making a " + inst.ty->render() + ", which is not made of parts");
          else if (ops.size() != pieces.size())
            fail(where, "making a " + inst.ty->render() + " out of " + std::to_string(ops.size()) + " values");
          else
            for (std::size_t index = 0; index < ops.size(); ++index)
              if (! same(ops[index]->ty, pieces[index]))
                fail(where, "the value at " + std::to_string(index) + " of a " + inst.ty->render() + " is "
                     + render(ops[index]));
          break;
        }
        case InstKind::ExtractInst: {
          auto inner = parts_of(ops[0]->ty);
          if (not_of_parts(inner, ops[0]->ty))
            fail(where, "taking a value out of " + render(ops[0]) + ", which is not made of parts");
          else if (inst.index < 0 || std::size_t(inst.index) >= inner.size())
            fail(where, render(ops[0]) + " has no value at " + std::to_string(inst.index));
          else if (! same(inst.ty, inner[inst.index]))
            fail(where, "taking " + inst.ty->render() + " out of " + render(ops[0]));
          break;
        }
        case InstKind::WrapInst: {
          auto result = as<ResultType>(inst.ty);
          if (result == nullptr)
            fail(where, "making something that is not a result");
          else if (! same(ops[0]->ty, result->ok))
            fail(where, "making a " + inst.ty->render() + " out of " + render(ops[0]));
          else if (ops[1]->ty != bool_type())
            fail(where, "whether a result failed is not a truth value");
          else if ((ops.size() > 2) != (result->err != nullptr))
            fail(where, "making a " + inst.ty->render()
                 + (ops.size() > 2 ? " with an error value" : " without the value its error carries"));
          else if (ops.size() > 2 && ! same(ops[2]->ty, result->err))
            fail(where, "making a " + inst.ty->render() + " whose error carries " + render(ops[2]));
          break;
        }
        case InstKind::UnwrapInst:
        case InstKind::FailedInst:
        case InstKind::ErrorInst: {
          auto inner = as<ResultType>(ops[0]->ty);
          if (inner == nullptr)
            fail(where, "'" + inst.opcode() + "' applied to something that is not a result");
          else if (inst.kind == InstKind::UnwrapInst && ! same(inst.ty, inner->ok))
            fail(where, "reading " + inst.ty->render() + " out of " + inner->render());
          else if (inst.kind == InstKind::ErrorInst && ! same(inst.ty, inner->err))
            fail(where, "reading the error of " + inner->render() + " as " + inst.ty->render());
          break;
        }
        case InstKind::UnaryInst:
          if (inst.un == UnOp::count_ones || inst.un == UnOp::count_leading) {
            // A count of bits is a count, whatever was counted.
            if (! is<IntType>(ops[0]->ty))
              fail(where, "'" + inst.opcode() + "' counting something that is not a whole number");
            else if (! is<IntType>(inst.ty))
              fail(where, "'" + inst.opcode() + "' answering with something that is not a whole number");
          } else if (! same(inst.ty, ops[0]->ty))
            fail(where, "'" + inst.opcode() + "' result type differs from its operand");
          break;
        case InstKind::CmpInst:
          if (! same(ops[0]->ty, ops[1]->ty))
            fail(where, "comparison of operands of different types");
          else if (auto lanes = as<VecType>(inst.ty)) {
            // One truth value per lane.
            auto compared = as<VecType>(ops[0]->ty);
            if (! (compared != nullptr && compared->lanes == lanes->lanes && lanes->element == bool_type()))
              fail(where, "comparing " + render(ops[0]) + " answering " + inst.ty->render());
          } else if (inst.ty != bool_type())
            fail(where, "a comparison answering " + inst.ty->render());
          break;
        case InstKind::SelectInst: {
          auto chooser = ops[0];
          auto first = ops[1];
          auto second = ops[2];
          if (chooser->ty != bool_type())
            fail(where, "a choice made by " + render(chooser) + ", which is not a truth value");
          else if (! same(first->ty, inst.ty) || ! same(second->ty, inst.ty))
            fail(where, "a choice of " + render(first) + " and " + render(second) + " answering " + inst.ty->render());
          else if (! (is<IntType>(inst.ty) || is<BoolType>(inst.ty) || is<CharType>(inst.ty) || is<FloatType>(inst.ty)
                      || is<PtrType>(inst.ty)))
            fail(where, "a choice of " + inst.ty->render() + ", which is not one register's worth");
          break;
        }
        case InstKind::SplatInst: {
          auto lanes = as<VecType>(inst.ty);
          if (lanes == nullptr)
            fail(where, "spreading a value over " + inst.ty->render() + ", which has no lanes");
          else if (! same(ops[0]->ty, lanes->element))
            fail(where, "spreading " + render(ops[0]) + " over " + inst.ty->render());
          break;
        }
        case InstKind::AnyLaneInst: {
          auto asked = as<VecType>(ops[0]->ty);
          if (! (asked != nullptr && asked->element == bool_type()))
            fail(where, "asking whether any lane of " + render(ops[0]) + " is true, which holds no truth values");
          else if (inst.ty != bool_type())
            fail(where, "whether any lane is true read as " + inst.ty->render());
          break;
        }
        case InstKind::StoreInst:
          if (inst.ordering == Ordering::acquire)
            // A write can carry the ordering that says what came before it is
            // seen, and not the one a read carries.
            fail(where, "a store cannot acquire");
          if (ops.size() != 3)
            fail(where, "a store takes a token, an address and a value");
          else if (ops[0]->ty != mem_type())
            fail(where, "a store's first operand is not a memory token");
          else {
            auto target = ops[1];
            auto stored = ops[2]->ty;
            auto ptr = as<PtrType>(target->ty);
            if (ptr == nullptr)
              fail(where, "a store's address is not a pointer");
            else if (! same(ptr->pointee, stored))
              fail(where, "storing " + stored->render() + " through a pointer to " + ptr->pointee->render());
            if (ptr != nullptr && ! ptr->is_mutable) {
              std::string named = target->vkind == ValueKind::global ? static_cast<const GlobalVar*>(target)->name
                                                                     : "a place";
              fail(where, "storing into '" + named + "', through a pointer that does not allow it");
            }
          }
          break;
        case InstKind::LoadInst:
          if (inst.ordering == Ordering::release)
            fail(where, "a load cannot release");
          if (ops.size() != 2)
            fail(where, "a load takes a memory token and an address");
          else if (ops[0]->ty != mem_type())
            fail(where, "a load's first operand is not a memory token");
          else {
            auto address = as<PtrType>(ops[1]->ty);
            if (address == nullptr)
              fail(where, "a load's address is not a pointer");
            else if (! same(address->pointee, inst.ty))
              fail(where, "loading " + inst.ty->render() + " through a pointer to " + address->pointee->render());
          }
          break;
        default:
          break;
        }
        if (inst.is_terminator())
          for (const auto& target : inst.targets)
            check_target(where, func, target);
      }

      // A branch supplies the arguments its destination declares.
      void check_target(const std::string& where, const Function& func, const BlockTarget& target)
      {
        auto block = target.block;
        if (block == nullptr || block->parent != &func) {
          fail(where, "branch to a block of another function");
          return;
        }
        if (target.args.size() != block->params.size()) {
          fail(where, "branch to " + block->label + " supplies " + std::to_string(target.args.size())
               + " arguments but the block declares " + std::to_string(block->params.size()));
          return;
        }
        for (std::size_t i = 0; i < target.args.size(); ++i)
          if (! same(target.args[i]->ty, block->params[i]->ty))
            fail(where, "branch to " + block->label + " supplies " + target.args[i]->ty->render() + " where "
                 + block->params[i]->ty->render() + " is declared");
      }

      // For each block, the blocks that dominate it, by index.
      std::vector<std::vector<bool>> dominators(const Function& func)
      {
        auto count = func.blocks.size();
        std::unordered_map<const BasicBlock*, std::size_t> number;
        for (std::size_t i = 0; i < count; ++i)
          number.emplace(func.blocks[i].get(), i);
        std::vector<std::vector<std::size_t>> preds(count);
        for (std::size_t i = 0; i < count; ++i)
          if (auto terminator = func.blocks[i]->terminator())
            for (const auto& target : terminator->targets)
              if (auto found = number.find(target.block); found != number.end())
                preds[found->second].push_back(i);
        std::vector<std::vector<bool>> doms(count, std::vector<bool>(count, true));
        doms[0].assign(count, false);
        doms[0][0] = true;
        for (bool changed = true; changed;) {
          changed = false;
          for (std::size_t b = 1; b < count; ++b) {
            std::vector<bool> made(count, preds[b].empty() ? false : true);
            for (auto p : preds[b])
              for (std::size_t i = 0; i < count; ++i)
                made[i] = made[i] && doms[p][i];
            made[b] = true;
            if (made != doms[b]) {
              doms[b] = std::move(made);
              changed = true;
            }
          }
        }
        return doms;
      }

      // Every use of a value is dominated by its definition.
      void check_dominance(const Function& func)
      {
        auto where = "function @" + func.name;
        auto doms = dominators(func);
        std::unordered_map<const Value*, std::pair<std::size_t, long>> defined;
        for (std::size_t b = 0; b < func.blocks.size(); ++b) {
          for (const auto& param : func.blocks[b]->params)
            defined[param.get()] = { b, -1 };
          for (std::size_t i = 0; i < func.blocks[b]->insts.size(); ++i)
            defined[func.blocks[b]->insts[i]] = { b, long(i) };
        }
        auto check_use = [&](std::size_t user, long index, const Value* value) {
          // A constant, a global and a function are available everywhere.
          if (value->is_const() || value->vkind == ValueKind::global || value->vkind == ValueKind::function)
            return;
          auto home = defined.find(value);
          if (home == defined.end()) {
            fail(where, "use of a value that no block defines");
            return;
          }
          if (home->second.first == user) {
            if (home->second.second >= index && index >= 0)
              fail(where, "use of a value before its definition");
            return;
          }
          if (! doms[user][home->second.first])
            fail(where, "use of a value that does not dominate the use");
        };
        for (std::size_t b = 0; b < func.blocks.size(); ++b)
          for (std::size_t i = 0; i < func.blocks[b]->insts.size(); ++i) {
            auto inst = func.blocks[b]->insts[i];
            for (auto operand : inst->operands)
              check_use(b, long(i), operand);
            if (inst->is_terminator())
              for (const auto& target : inst->targets)
                for (auto arg : target.args)
                  check_use(b, long(i), arg);
          }
      }

      const Module& module;
      std::vector<std::string> problems;
    };

  } // anonymous namespace


  void verify(const Module& module, std::optional<std::span<Function* const>> functions)
  {
    Verifier(module).run(functions);
  }


  bool verifying_asked() noexcept
  {
    const char* asked = std::getenv(std::string(verify_variable).c_str());
    return asked != nullptr && std::string_view(asked) != "" && std::string_view(asked) != "0";
  }

} // namespace pl4g::ir
