// What a pure function's purity buys besides dropping a call nothing reads.  A
// port of pypl4g's opt/passes/purecalls.py.
//
// A pure function reading no memory but its own frame answers what its
// arguments say, so a call made twice with the same arguments is worked out
// once, one whose arguments do not change round a loop is moved out of it,
// and one whose arguments are all constants is worked out while compiling --
// by the machine that runs the macros -- where it answers a value.
#include "opt/passes.hh"

#include <algorithm>
#include <unordered_map>
#include <unordered_set>

#include "front/interpret.hh"
#include "ir/builder.hh"
#include "opt/flow.hh"
#include "util/ordered.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // How many instructions working out one call while compiling may take.
    constexpr std::int64_t steps = 5'000;

    using Doms = std::unordered_map<const BasicBlock*, std::unordered_set<const BasicBlock*>>;

    const Instruction* inst_of(const Value* value)
    {
      return value->vkind == ValueKind::instruction ? static_cast<const Instruction*>(value) : nullptr;
    }

    // Whether an answer of `ty` is a value rather than something somewhere.
    bool a_value(const Type* ty)
    {
      return is<IntType>(ty) || is<BoolType>(ty) || is<CharType>(ty) || is<FloatType>(ty);
    }

    // Whether `address` is, or is worked out from, a frame of the function.
    bool in_its_frame(const Value* address)
    {
      auto seen = address;
      while (true) {
        auto inst = inst_of(seen);
        if (inst == nullptr)
          return false;
        if (inst->kind == InstKind::FrameInst)
          return true;
        if (inst->kind == InstKind::CastInst || inst->kind == InstKind::ExtractInst
            || (inst->kind == InstKind::BinaryInst && (inst->bin == BinOp::add || inst->bin == BinOp::sub))) {
          seen = inst->operands[0];
          continue;
        }
        return false;
      }
    }

    // Arithmetic that cannot stop the program.
    bool plain(BinOp op)
    {
      switch (op) {
      case BinOp::wrap_add: case BinOp::wrap_sub: case BinOp::wrap_mul: case BinOp::and_: case BinOp::or_:
      case BinOp::xor_: case BinOp::smax: case BinOp::smin: case BinOp::umax: case BinOp::umin:
        return true;
      default:
        return false;
      }
    }

    // What may stand in a loop's head before a call moved out of it.
    bool harmless(const Instruction* inst)
    {
      switch (inst->kind) {
      case InstKind::CastInst: case InstKind::ExtractInst: case InstKind::TupleInst: case InstKind::CmpInst:
      case InstKind::AddressInst: case InstKind::FrameInst:
        return true;
      case InstKind::BinaryInst:
        return plain(inst->bin);
      default:
        return false;
      }
    }

    // For each block, the blocks that dominate it, by the plain dataflow.
    Doms dominators_of(const Function& func)
    {
      std::unordered_set<const BasicBlock*> every;
      std::unordered_map<const BasicBlock*, std::vector<const BasicBlock*>> preds;
      for (const auto& block : func.blocks) {
        every.insert(block.get());
        preds[block.get()];
      }
      for (const auto& block : func.blocks)
        for (auto one : successors(block.get()))
          preds[one].push_back(block.get());
      Doms found;
      for (const auto& block : func.blocks)
        found[block.get()] = every;
      auto first = func.blocks.front().get();
      found[first] = { first };
      bool changed = true;
      while (changed) {
        changed = false;
        for (std::size_t i = 1; i < func.blocks.size(); ++i) {
          auto block = func.blocks[i].get();
          auto made = every;
          for (auto pred : preds[block])
            std::erase_if(made, [&](const BasicBlock* one) { return ! found[pred].contains(one); });
          made.insert(block);
          if (made != found[block]) {
            found[block] = std::move(made);
            changed = true;
          }
        }
      }
      return found;
    }

    // Each natural loop: its head, and its blocks.
    std::vector<std::pair<BasicBlock*, std::unordered_set<const BasicBlock*>>> loops_of(const Function& func,
                                                                                        Doms& doms)
    {
      std::unordered_map<const BasicBlock*, std::vector<BasicBlock*>> preds;
      for (const auto& block : func.blocks)
        preds[block.get()];
      for (const auto& block : func.blocks)
        for (auto one : successors(block.get()))
          preds[one].push_back(block.get());
      OrderedMap<const BasicBlock*, std::pair<BasicBlock*, std::unordered_set<const BasicBlock*>>> found;
      for (const auto& block : func.blocks)
        for (auto head : successors(block.get())) {
          if (! doms[block.get()].contains(head))
            continue;
          auto& [_, body] = found.setdefault(head, { head, { head } });
          std::vector<BasicBlock*> waiting{ block.get() };
          while (! waiting.empty()) {
            auto one = waiting.back();
            waiting.pop_back();
            if (! body.insert(one).second)
              continue;
            for (auto pred : preds[one])
              waiting.push_back(pred);
          }
        }
      std::vector<std::pair<BasicBlock*, std::unordered_set<const BasicBlock*>>> loops;
      for (auto& [_, one] : found)
        loops.push_back(one);
      return loops;
    }

    // The one block outside the loop that goes to its head, and nowhere else.
    BasicBlock* entering(const Function& func, const BasicBlock* head, const std::unordered_set<const BasicBlock*>& body)
    {
      std::vector<BasicBlock*> outside;
      for (const auto& block : func.blocks) {
        if (body.contains(block.get()))
          continue;
        auto next = successors(block.get());
        if (std::ranges::find(next, head) != next.end())
          outside.push_back(block.get());
      }
      if (outside.size() != 1)
        return nullptr;
      auto block = outside.front();
      auto last = block->insts.empty() ? nullptr : block->insts.back();
      return last != nullptr && last->kind == InstKind::BrInst ? block : nullptr;
    }

    // Whether `value` is worked out, or arrives, in one of the loop's blocks.
    bool inside(const Value* value, const std::unordered_set<const BasicBlock*>& body)
    {
      if (value->vkind == ValueKind::block_param)
        return body.contains(static_cast<const BlockParam*>(value)->block);
      auto inst = inst_of(value);
      return inst != nullptr && inst->parent != nullptr && body.contains(inst->parent);
    }

    Span span_of(const Instruction* inst, const Function& func)
    {
      return inst->span.is_valid() ? inst->span : func.span;
    }


    class Pure {
    public:
      explicit Pure(Module& module) : module(module) {}

      bool run()
      {
        bool changed = false;
        std::vector<Function*> all;
        for (auto& [_, func] : module.functions)
          all.push_back(func.get());
        for (auto func : all) {
          if (func->blocks.empty())
            continue;
          changed |= fold(*func);
          std::size_t calls = 0;
          for (auto& block : func->blocks)
            for (auto inst : block->insts)
              if (candidate(inst) != nullptr)
                ++calls;
          if (calls == 0)
            continue;
          auto doms = dominators_of(*func);
          if (calls > 1) {
            changed |= reuse(*func, doms);
            doms = dominators_of(*func);
          }
          changed |= hoist(*func, doms);
        }
        return changed;
      }

    private:
      // The callee of a call whose answer depends on its arguments alone.
      Function* candidate(const Instruction* inst)
      {
        if (inst->kind != InstKind::CallInst || has_effects(inst))
          return nullptr;
        auto callee = inst->callee;
        if (callee == nullptr || callee->blocks.empty() || ! a_value(inst->ty))
          return nullptr;
        std::unordered_set<const Function*> seen;
        return reads_nothing(callee, seen) ? callee : nullptr;
      }

      bool reads_nothing(const Function* func, std::unordered_set<const Function*>& seen)
      {
        if (auto it = free.find(func); it != free.end())
          return it->second;
        if (seen.contains(func))
          // A call of itself: as free as the rest of it turns out to be.
          return true;
        seen.insert(func);
        bool found = true;
        for (const auto& block : func->blocks) {
          for (auto inst : block->insts) {
            if (inst->kind == InstKind::LoadInst || inst->kind == InstKind::StoreInst) {
              if (! in_its_frame(inst->operands[1]))
                found = false;
            } else if (inst->kind == InstKind::AddressInst) {
              auto var = inst->operands[0];
              if (var->vkind == ValueKind::global && static_cast<const GlobalVar*>(var)->is_mutable())
                found = false;
            } else if (inst->kind == InstKind::CallInst) {
              auto callee = inst->callee;
              if (callee == nullptr || callee->blocks.empty() || has_effects(inst) || ! reads_nothing(callee, seen))
                found = false;
            }
            if (! found)
              break;
          }
          if (! found)
            break;
        }
        seen.erase(func);
        free[func] = found;
        return found;
      }

      bool uses_floats(const Function* func, std::unordered_set<const Function*>& seen)
      {
        if (auto it = floats.find(func); it != floats.end())
          return it->second;
        if (seen.contains(func))
          return false;
        seen.insert(func);
        bool found = false;
        for (const auto& block : func->blocks)
          for (const auto& param : block->params)
            found |= is<FloatType>(param->ty);
        if (! found)
          for (const auto& block : func->blocks) {
            for (auto inst : block->insts) {
              bool here = is<FloatType>(inst->ty)
                          || std::ranges::any_of(inst->operands, [](const Value* one) { return is<FloatType>(one->ty); })
                          || (inst->kind == InstKind::CallInst && inst->callee != nullptr
                              && uses_floats(inst->callee, seen));
              if (here) {
                found = true;
                break;
              }
            }
            if (found)
              break;
          }
        floats[func] = found;
        return found;
      }

      // What a call answers, worked out by the macros' machine, or nothing
      // where it stops or will not run what it was given.
      Value* worked_out(const Function* callee, const Instruction* call)
      {
        macro::Machine machine({}, steps);
        std::vector<macro::MValue> args;
        for (auto one : call->operands)
          switch (one->vkind) {
          case ValueKind::int_const: args.emplace_back(static_cast<const IntConst*>(one)->value); break;
          case ValueKind::bool_const: args.emplace_back(static_cast<const BoolConst*>(one)->value); break;
          default: args.emplace_back(__int128(static_cast<const CharConst*>(one)->value)); break;
          }
        macro::MValue found;
        try {
          found = machine.call(callee, args);
        } catch (const macro::Refused&) {
          return nullptr;
        } catch (const macro::Stopped&) {
          return nullptr;
        }
        auto number = found.number();
        if (number == nullptr)
          return nullptr;
        auto ty = call->ty;
        if (is<BoolType>(ty))
          return const_cast<BoolConst*>(module.bool_const(*number != 0));
        if (is<CharType>(ty))
          return const_cast<CharConst*>(module.char_const(std::uint32_t(*number)));
        if (auto integer = as<IntType>(ty)) {
          auto bits = static_cast<unsigned __int128>(*number);
          if (integer->bits < 128)
            bits &= (static_cast<unsigned __int128>(1) << integer->bits) - 1;
          auto value = static_cast<__int128>(bits);
          if (integer->is_signed && (bits >> (integer->bits - 1)) != 0)
            value -= static_cast<__int128>(static_cast<unsigned __int128>(1) << integer->bits);
          return const_cast<IntConst*>(module.int_const(integer, value));
        }
        return nullptr;
      }

      bool fold(Function& func)
      {
        std::unordered_map<const Value*, Value*> instead;
        for (auto& block : func.blocks) {
          auto insts = block->insts;
          for (auto inst : insts) {
            auto callee = candidate(inst);
            if (callee == nullptr)
              continue;
            if (! std::ranges::all_of(inst->operands, [](const Value* one) {
                  return one->vkind == ValueKind::int_const || one->vkind == ValueKind::bool_const
                         || one->vkind == ValueKind::char_const;
                }))
              continue;
            std::unordered_set<const Function*> seen;
            if (uses_floats(callee, seen))
              // The machine's numbers are not the target's floating point.
              continue;
            auto answer = worked_out(callee, inst);
            if (answer == nullptr)
              continue;
            instead[inst] = answer;
            std::erase(block->insts, inst);
            module.record("pure-call-folded", func.name,
                          "the call of '" + callee->name
                            + "' is worked out while compiling: it is pure, reads no memory, and every argument is a "
                              "constant",
                          span_of(inst, func));
          }
        }
        if (instead.empty())
          return false;
        all_stand_for(func, instead);
        return true;
      }

      bool reuse(Function& func, Doms& doms)
      {
        std::vector<BasicBlock*> order;
        for (auto& block : func.blocks)
          order.push_back(block.get());
        std::ranges::stable_sort(order, [&](const BasicBlock* a, const BasicBlock* b) {
          return doms[a].size() < doms[b].size();
        });
        struct Made {
          BasicBlock* where;
          std::vector<const void*> key;
          Instruction* call;
        };
        std::vector<Made> made;
        std::unordered_map<const Value*, Value*> instead;
        for (auto block : order) {
          auto insts = block->insts;
          for (auto inst : insts) {
            if (candidate(inst) == nullptr)
              continue;
            std::vector<const void*> key{ inst->callee };
            for (auto one : inst->operands) {
              auto it = instead.find(one);
              key.push_back(it == instead.end() ? one : it->second);
            }
            auto index_of = [](const BasicBlock* in, const Instruction* one) {
              return std::ranges::find(in->insts, one) - in->insts.begin();
            };
            Instruction* earlier = nullptr;
            for (const auto& one : made)
              if (one.key == key && doms[block].contains(one.where)
                  && (one.where != block || index_of(block, one.call) < index_of(block, inst))) {
                earlier = one.call;
                break;
              }
            if (earlier == nullptr) {
              made.push_back({ block, std::move(key), inst });
              continue;
            }
            instead[inst] = earlier;
            std::erase(block->insts, inst);
            module.record("pure-call-reused", func.name,
                          "this call of '" + inst->callee->name
                            + "' is the one made earlier with the same arguments: it is pure and reads no memory, so "
                              "it is worked out once",
                          span_of(inst, func));
          }
        }
        if (instead.empty())
          return false;
        all_stand_for(func, instead);
        return true;
      }

      bool hoist(Function& func, Doms& doms)
      {
        bool changed = false;
        for (auto& [head, body] : loops_of(func, doms)) {
          auto into = entering(func, head, body);
          if (into == nullptr)
            continue;
          auto insts = head->insts;
          for (auto inst : insts) {
            if (harmless(inst))
              continue;
            if (candidate(inst) == nullptr
                || std::ranges::any_of(inst->operands, [&](const Value* one) { return inside(one, body); }))
              // Anything else may stop the program, and a call after it has to
              // stay after it.
              break;
            std::erase(head->insts, inst);
            into->insts.insert(into->insts.end() - 1, inst);
            inst->parent = into;
            changed = true;
            module.record("pure-call-hoisted", func.name,
                          "the call of '" + inst->callee->name
                            + "' is moved out of the loop: it is pure, reads no memory, and nothing it is handed "
                              "changes round the loop",
                          span_of(inst, func));
          }
        }
        return changed;
      }

      Module& module;
      std::unordered_map<const Function*, bool> free;
      std::unordered_map<const Function*, bool> floats;
    };

  } // anonymous namespace


  bool pure_calls(Module& module)
  {
    return Pure(module).run();
  }

} // namespace pl4g::opt
