// Putting a callee's body where the call was.  A port of pypl4g's
// opt/passes/inline.py.
//
// Functions are walked callees first, so a callee reached here is final.
// Inlined is what the program said to inline, what the whole program calls
// once, and what is small enough; never a function that can reach itself, one
// of another convention, or one marked `@[inline(never)]`.  The block holding
// the call is cut in two, the first half branching into a copy of the callee
// whose every `ret` branches to the second, which takes the answer and the
// token the body ended at.
#include "opt/passes.hh"

#include <algorithm>
#include <unordered_map>
#include <unordered_set>

#include "ir/builder.hh"
#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // What a function may come to where generated and still be copied where
    // it is called, more for a leaf, whose caller keeps every register the
    // leaf does not write; and how much a caller may grow in total, a copy of
    // a function called once or of one no larger than a call not counting.
    constexpr std::int64_t small = 16;
    constexpr std::int64_t small_leaf = 32;
    constexpr std::int64_t budget = 300;
    constexpr std::int64_t call_cost = 4;

    // How large `callee` may be and still be small.
    std::int64_t limit_of(const Function& callee) { return is_leaf(callee) ? small_leaf : small; }

    Span where_of(const Function* func) { return func->name_span.is_valid() ? func->name_span : func->span; }

    // A copy of `one`, kept by `func`, in no block.
    Instruction* clone(Function& func, const Instruction* one)
    {
      auto made = func.make(one->kind, one->ty);
      made->name_hint = one->name_hint;
      made->name_span = one->name_span;
      made->operands = one->operands;
      made->span = one->span;
      made->bin = one->bin;
      made->un = one->un;
      made->pred = one->pred;
      made->cast = one->cast;
      made->ordering = one->ordering;
      made->index = one->index;
      made->callee = one->callee;
      made->cconv = one->cconv;
      made->held = one->held;
      made->what = one->what;
      made->status = one->status;
      made->observing = one->observing;
      made->targets = one->targets;
      made->cases = one->cases;
      made->delegates = one->delegates;
      return made;
    }

    Instruction* branch(Function& func, BasicBlock* to, std::vector<Value*> args, Span span)
    {
      auto made = func.make(InstKind::BrInst, void_type());
      made->targets.push_back({ to, std::move(args) });
      made->span = span;
      return made;
    }

    void append(BasicBlock* block, Instruction* inst)
    {
      inst->parent = block;
      block->insts.push_back(inst);
    }

    Value* mapped(Value* value, const std::unordered_map<const Value*, Value*>& values)
    {
      auto it = values.find(value);
      return it == values.end() ? value : it->second;
    }

    // The token in force just before instruction `at` of `block`, or up the
    // dominator tree where the block says neither; nothing where the token
    // comes from the function's start.
    Value* last_token(BasicBlock* block, std::size_t at, const Dominators* tree)
    {
      auto current = block;
      auto end = at;
      while (true) {
        for (std::size_t i = end; i-- > 0;)
          if (current->insts[i]->ty == mem_type())
            return current->insts[i];
        for (auto& param : current->params)
          if (param->ty == mem_type())
            return param.get();
        auto above = tree != nullptr ? tree->idom_of(current) : nullptr;
        if (above == nullptr)
          return nullptr;
        current = above;
        end = above->insts.size();
      }
    }

    // The memory token in force just before instruction `at` of `block`.
    Value* token_before(Function& func, BasicBlock* block, std::size_t at)
    {
      auto tree = dominators(func);
      if (auto found = last_token(block, at, &tree))
        return found;
      auto entry = func.entry();
      if (! entry->insts.empty() && entry->insts.front()->kind == InstKind::MemStartInst)
        return entry->insts.front();
      auto start = func.make(InstKind::MemStartInst, mem_type());
      start->parent = entry;
      entry->insts.insert(entry->insts.begin(), start);
      return start;
    }

    // Have what follows an inlined body read the token the body ended at.
    void resume_chain(Function& func, BasicBlock* after, Value* token, Value* resumed)
    {
      auto found = dominators(func);
      for (auto& block : func.blocks) {
        if (! found.dominates(after, block.get()))
          continue;
        for (auto inst : block->insts) {
          for (auto& one : inst->operands)
            if (one == token)
              one = resumed;
          if (inst->is_terminator())
            for (auto& target : inst->targets)
              for (auto& one : target.args)
                if (one == token)
                  one = resumed;
        }
      }
    }

    class Inliner {
    public:
      Inliner(Module& module) : module(module), cyclic(in_a_cycle(module)), counted(called_by_count(module)) {}

      bool run()
      {
        bool changed = false;
        for (auto func : in_call_order(module)) {
          if (func->blocks.empty() || cyclic.contains(func))
            continue;
          changed |= into(*func);
        }
        return changed;
      }

    private:
      bool worth_it(const Function& caller, const Function* callee, std::int64_t grown)
      {
        if (callee->blocks.empty() || callee == &caller || cyclic.contains(callee))
          return false;
        if (callee->cconv != default_cconv || callee->attrs.special)
          return false;
        if (callee->attrs.inline_ == InlineHint::never || callee->attrs.opaque)
          return false;
        if (callee->attrs.inline_ == InlineHint::always)
          return true;
        // The copy of a function called once is the only one there is, and
        // one no larger than the call costs nothing: the budget is for neither.
        if (called_once(callee))
          return true;
        auto cost = cost_of_body(*callee);
        if (cost <= call_cost)
          return true;
        if (grown >= budget)
          return false;
        return cost <= limit_of(*callee);
      }

      // Whether `callee` is named by one call of the whole program and can be
      // reached from nowhere outside it.
      bool called_once(const Function* callee)
      {
        auto it = counted.find(callee);
        return it != counted.end() && it->second == 1 && callee->linkage != Linkage::visible;
      }

      // What a copy of `callee` adds to the program, in the budget's measure.
      std::int64_t growth(const Function* callee)
      {
        auto cost = cost_of_body(*callee);
        if (callee->attrs.inline_ != InlineHint::always && (called_once(callee) || cost <= call_cost))
          return 0;
        return cost;
      }

      std::string because(const Function* callee)
      {
        if (callee->attrs.inline_ == InlineHint::always)
          return "which the program asked for";
        if (auto cost = cost_of_body(*callee); cost <= limit_of(*callee))
          return "coming to about " + std::to_string(cost) + " machine instructions";
        return "the whole program calling it once";
      }

      bool into(Function& caller)
      {
        std::int64_t grown = 0;
        bool changed = false;
        for (std::size_t at_block = 0; at_block < caller.blocks.size(); ++at_block) {
          auto block = caller.blocks[at_block].get();
          for (std::size_t at = 0; at < block->insts.size(); ++at) {
            auto inst = block->insts[at];
            auto callee = inst->kind == InstKind::CallInst ? inst->callee : nullptr;
            if (callee == nullptr || ! worth_it(caller, callee, grown))
              continue;
            grown += growth(callee);
            module.record("inline", callee->name, "put where " + caller.name + " calls it, " + because(callee),
                          where_of(callee));
            splice(caller, block, at, *callee);
            changed = true;
            // The block is now what stood before the call; what followed it is
            // a block of its own further down.
            break;
          }
        }
        return changed;
      }

      void splice(Function& caller, BasicBlock* block, std::size_t at, Function& callee)
      {
        auto call = block->insts[at];
        // The token first, and then where the call is again: finding the
        // token may put the start of a chain at the top of the entry block.
        auto token = token_before(caller, block, at);
        at = std::size_t(std::ranges::find(block->insts, call) - block->insts.begin());
        auto after = caller.add_block("inlined.after");
        after->insts.assign(block->insts.begin() + std::ptrdiff_t(at) + 1, block->insts.end());
        for (auto one : after->insts)
          one->parent = after;
        block->insts.erase(block->insts.begin() + std::ptrdiff_t(at), block->insts.end());
        BlockParam* answer = call->ty != void_type() ? after->add_param(call->ty, "answer") : nullptr;
        BlockParam* resumed = after->add_param(mem_type(), "memory");

        std::unordered_map<const BasicBlock*, BasicBlock*> blocks;
        std::unordered_map<const Value*, Value*> values;
        auto tree = dominators(callee);
        for (auto& original : callee.blocks) {
          auto made = caller.add_block("inlined." + original->label);
          blocks[original.get()] = made;
          for (auto& param : original->params) {
            auto copy = param->name_hint ? made->add_param(param->ty, *param->name_hint) : made->add_param(param->ty);
            values[param.get()] = copy;
          }
        }
        std::vector<Instruction*> copies;
        for (auto& original : callee.blocks) {
          auto made = blocks[original.get()];
          for (std::size_t i = 0; i < original->insts.size(); ++i) {
            auto one = original->insts[i];
            if (one->kind == InstKind::MemStartInst) {
              values[one] = token;
              continue;
            }
            if (one->kind == InstKind::RetInst) {
              auto ended = last_token(original.get(), i, &tree);
              Value* handed = ended != nullptr ? ended : token;
              std::vector<Value*> args;
              if (answer == nullptr)
                args = { handed };
              else
                args = { one->operands[0], handed };
              append(made, branch(caller, after, std::move(args), one->span));
              continue;
            }
            auto copy = clone(caller, one);
            values[one] = copy;
            append(made, copy);
            copies.push_back(copy);
          }
        }
        for (auto copy : copies) {
          for (auto& operand : copy->operands)
            operand = mapped(operand, values);
          if (copy->kind == InstKind::BrInst || copy->kind == InstKind::CondBrInst
              || copy->kind == InstKind::SwitchInst)
            for (auto& target : copy->targets) {
              target.block = blocks.at(target.block);
              for (auto& arg : target.args)
                arg = mapped(arg, values);
            }
        }
        for (auto& original : callee.blocks) {
          auto last = blocks[original.get()]->insts.back();
          if (last->kind == InstKind::BrInst && last->targets[0].block == after)
            for (auto& arg : last->targets[0].args)
              arg = mapped(arg, values);
        }
        // Into the copy, with the arguments where its parameters are.
        std::vector<Value*> args;
        for (auto one : call->operands)
          args.push_back(mapped(one, values));
        append(block, branch(caller, blocks[callee.blocks.front().get()], std::move(args), call->span));
        if (answer != nullptr)
          stands_for(caller, call, answer);
        // Straight after the block that branches into it, with what follows the
        // call last: the back ends walk the blocks in this order.
        std::vector<BasicBlock*> made;
        for (auto& original : callee.blocks)
          made.push_back(blocks[original.get()]);
        made.push_back(after);
        std::unordered_set<const BasicBlock*> fresh(made.begin(), made.end());
        std::unordered_map<const BasicBlock*, std::unique_ptr<BasicBlock>> owned;
        std::vector<std::unique_ptr<BasicBlock>> rest;
        for (auto& one : caller.blocks)
          if (fresh.contains(one.get()))
            owned[one.get()] = std::move(one);
          else
            rest.push_back(std::move(one));
        std::vector<std::unique_ptr<BasicBlock>> order;
        for (auto& one : rest) {
          bool here = one.get() == block;
          order.push_back(std::move(one));
          if (here)
            for (auto fresh_one : made)
              order.push_back(std::move(owned[fresh_one]));
        }
        caller.blocks = std::move(order);
        // Where every way out of the body hands on one token, that is what
        // follows the call reads, and it needs no parameter.
        std::vector<BlockTarget*> endings;
        for (std::size_t i = 0; i + 1 < made.size(); ++i) {
          auto last = made[i]->insts.empty() ? nullptr : made[i]->insts.back();
          if (last != nullptr && last->kind == InstKind::BrInst && last->targets[0].block == after)
            endings.push_back(&last->targets[0]);
        }
        std::unordered_set<const Value*> handed;
        for (auto target : endings)
          handed.insert(target->args.back());
        if (handed.size() == 1) {
          auto one = endings.front()->args.back();
          auto it = std::ranges::find_if(after->params, [&](const auto& p) { return p.get() == resumed; });
          caller.retired.push_back(std::move(*it));
          after->params.erase(it);
          for (auto target : endings)
            target->args.pop_back();
          if (one != token)
            resume_chain(caller, after, token, one);
        } else
          resume_chain(caller, after, token, resumed);
      }

      Module& module;
      std::unordered_set<const Function*> cyclic;
      std::unordered_map<const Function*, std::int64_t> counted;
    };

  } // anonymous namespace


  bool inlining(Module& module)
  {
    return Inliner(module).run();
  }

} // namespace pl4g::opt
