// Branching on each part of a condition rather than on the truth value they
// make.  A port of pypl4g's opt/passes/conditions.py.
//
// A branch on `a ∧ b`, `a ∨ b` or `¬a` read by nothing else becomes a branch
// on `a` into a block of its own that branches on `b`, what works `b` out
// moving into that block where nothing else reads it; and a block that is
// nothing but a branch on its own parameter -- where the two ways of a
// short-circuit meet -- is gone through rather than to, as is a block that is
// nothing but a jump.  The blocks left behind nothing reaches are taken out.
#include "opt/passes.hh"

#include <algorithm>
#include <optional>
#include <unordered_map>
#include <unordered_set>

#include "ir/builder.hh"
#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // How many times the two rewrites are taken in turn over one function.
    constexpr int rounds = 100;

    using Counts = std::unordered_map<const Value*, int>;

    // How many times each value is read, a branch's arguments counted.
    Counts uses_of(const Function& func)
    {
      Counts counted;
      for (const auto& block : func.blocks)
        for (auto inst : block->insts) {
          for (auto operand : inst->operands)
            ++counted[operand];
          if (inst->is_terminator())
            for (const auto& target : inst->targets)
              for (auto arg : target.args)
                ++counted[arg];
        }
      return counted;
    }

    int count_of(const Counts& counted, const Value* value)
    {
      auto it = counted.find(value);
      return it == counted.end() ? 0 : it->second;
    }

    enum class Joined { and_, or_, not_ };

    // What a truth value is made of, where it is made with `∧`, `∨` or `¬`.
    struct Parts {
      Joined kind;
      Value* first;
      Value* second;
    };

    bool is_true(const Value* value)
    {
      return value->vkind == ValueKind::bool_const && static_cast<const BoolConst*>(value)->value;
    }

    std::optional<Parts> parts_of(Value* cond)
    {
      if (cond->vkind != ValueKind::instruction || cond->ty != bool_type())
        return std::nullopt;
      auto inst = static_cast<Instruction*>(cond);
      if (inst->kind == InstKind::BinaryInst) {
        auto lhs = inst->operands[0];
        auto rhs = inst->operands[1];
        if (inst->bin == BinOp::and_)
          return Parts{ Joined::and_, lhs, rhs };
        if (inst->bin == BinOp::or_)
          return Parts{ Joined::or_, lhs, rhs };
        if (inst->bin == BinOp::xor_ && is_true(rhs))
          return Parts{ Joined::not_, lhs, nullptr };
        if (inst->bin == BinOp::xor_ && is_true(lhs))
          return Parts{ Joined::not_, rhs, nullptr };
      }
      if (inst->kind == InstKind::UnaryInst && inst->un == UnOp::not_)
        return Parts{ Joined::not_, inst->operands[0], nullptr };
      return std::nullopt;
    }

    // Whether `inst` may be worked out later than where it stands.
    bool movable(const Instruction* inst)
    {
      if (has_effects(inst) || inst->kind == InstKind::LoadInst || inst->is_terminator() || inst->ty == mem_type())
        return false;
      return std::ranges::none_of(inst->operands, [](const Value* one) { return one->ty == mem_type(); });
    }

    // Move what works `value` out from `block` into `into`, where nothing else
    // reads it, in the order the instructions stood.
    void sink(BasicBlock* block, BasicBlock* into, Value* value, const Counts& counted)
    {
      std::unordered_set<const Value*> moving;
      std::vector<Value*> pending{ value };
      while (! pending.empty()) {
        auto one = pending.back();
        pending.pop_back();
        if (one->vkind != ValueKind::instruction)
          continue;
        auto inst = static_cast<Instruction*>(one);
        if (inst->parent != block || moving.contains(inst) || ! movable(inst) || count_of(counted, inst) != 1)
          continue;
        moving.insert(inst);
        for (auto operand : inst->operands)
          pending.push_back(operand);
      }
      if (moving.empty())
        return;
      std::vector<Instruction*> kept;
      std::vector<Instruction*> moved;
      for (auto inst : block->insts)
        (moving.contains(inst) ? moved : kept).push_back(inst);
      block->insts = std::move(kept);
      for (auto inst : moved)
        inst->parent = into;
      into->insts.insert(into->insts.begin(), moved.begin(), moved.end());
    }

    Instruction* conditional(Function& func, Value* cond, BlockTarget when_true, BlockTarget when_false, Span span)
    {
      auto made = func.make(InstKind::CondBrInst, void_type());
      made->operands.push_back(cond);
      made->targets.push_back(std::move(when_true));
      made->targets.push_back(std::move(when_false));
      made->span = span;
      return made;
    }

    // Branch on the parts of every condition made with `∧`, `∨` or `¬`.
    bool split(Function& func)
    {
      bool changed = false;
      auto counted = uses_of(func);
      for (std::size_t at = 0; at < func.blocks.size();) {
        auto block = func.blocks[at].get();
        auto branch = block->terminator();
        ++at;
        if (branch == nullptr || branch->kind != InstKind::CondBrInst)
          continue;
        auto cond = branch->operands[0];
        auto made = parts_of(cond);
        if (! made || static_cast<Instruction*>(cond)->parent != block || count_of(counted, cond) != 1)
          continue;
        auto joined = static_cast<Instruction*>(cond);
        std::erase(block->insts, joined);
        changed = true;
        // A constant operand of `¬` written as an exclusive or is read no more.
        for (auto operand : joined->operands)
          if (operand != made->first && operand != made->second)
            --counted[operand];
        branch->operands[0] = made->first;
        if (made->kind == Joined::not_) {
          std::swap(branch->targets[0], branch->targets[1]);
          // The same block again: what it branches on now may be made of parts.
          --at;
          continue;
        }
        auto rest = func.detached_block(func.unique_label(block->label + (made->kind == Joined::and_ ? ".then" : ".else")));
        auto into = rest.get();
        func.blocks.insert(func.blocks.begin() + std::ptrdiff_t(at), std::move(rest));
        // The way both branches share is carried by both, so what it carries
        // is read once more.
        const auto& shared = branch->targets[made->kind == Joined::and_ ? 1 : 0];
        for (auto arg : shared.args)
          ++counted[arg];
        auto second = conditional(func, made->second, branch->targets[0], branch->targets[1], branch->span);
        second->parent = into;
        into->insts.push_back(second);
        branch->targets[made->kind == Joined::and_ ? 0 : 1] = BlockTarget{ into, {} };
        sink(block, into, made->second, counted);
        // This block again, and the new one after it.
        --at;
      }
      return changed;
    }

    // A branch to where `target` goes, its parameters of `block` read as
    // `args`.
    BlockTarget substituted(const BlockTarget& target, const BasicBlock* block, const std::vector<Value*>& args)
    {
      std::unordered_map<const Value*, Value*> given;
      for (std::size_t at = 0; at < block->params.size() && at < args.size(); ++at)
        given[block->params[at].get()] = args[at];
      BlockTarget made{ target.block, {} };
      for (auto arg : target.args) {
        auto it = given.find(arg);
        made.args.push_back(it == given.end() ? arg : it->second);
      }
      return made;
    }

    // Have every jump into `block` -- nothing but a branch on one of its
    // parameters -- branch itself, and every branch into it carrying a
    // constant go where that constant goes.
    bool through_branch(Function& func, BasicBlock* block, Instruction* branch)
    {
      auto cond = branch->operands[0];
      if (cond->vkind != ValueKind::block_param || static_cast<BlockParam*>(cond)->block != block
          || cond->ty != bool_type())
        return false;
      auto counted = uses_of(func);
      Counts inside;
      ++inside[cond];
      for (const auto& target : branch->targets)
        for (auto arg : target.args)
          ++inside[arg];
      for (const auto& param : block->params)
        if (count_of(counted, param.get()) != count_of(inside, param.get()))
          return false;
      if (branch->targets[0].block == block || branch->targets[1].block == block)
        return false;
      auto index = static_cast<BlockParam*>(cond)->index;
      bool changed = false;
      auto edges = incoming_edges(func);
      for (auto [source, edge] : edges[block]) {
        if (edge->block != block)
          continue;
        auto known = edge->args[index];
        auto last = source->insts.back();
        if (known->vkind == ValueKind::bool_const) {
          auto made = substituted(branch->targets[static_cast<BoolConst*>(known)->value ? 0 : 1], block, edge->args);
          *edge = std::move(made);
          changed = true;
          continue;
        }
        if (last->kind != InstKind::BrInst || &last->targets[0] != edge)
          continue;
        auto args = edge->args;
        auto replaced = conditional(func, known, substituted(branch->targets[0], block, args),
                                    substituted(branch->targets[1], block, args), last->span);
        replaced->parent = source;
        source->insts.back() = replaced;
        changed = true;
      }
      return changed;
    }

    // Have every branch into `block` -- nothing but a jump, taking nothing --
    // go where the jump goes instead.
    bool through_jump(Function& func, BasicBlock* block, Instruction* jump)
    {
      if (jump->targets[0].block == block)
        return false;
      // A branch of two ways carrying values would only be given a block of
      // its own again, to make the moves in: that is this block.
      bool carries = ! carried_values(jump->targets[0]).empty();
      bool changed = false;
      auto edges = incoming_edges(func);
      for (auto [source, edge] : edges[block]) {
        if (edge->block != block)
          continue;
        if (carries && source->insts.back()->kind != InstKind::BrInst)
          continue;
        *edge = BlockTarget{ jump->targets[0].block, jump->targets[0].args };
        changed = true;
      }
      return changed;
    }

    // Go through every block that only branches on its parameter, or only
    // jumps, rather than into it.
    bool thread(Function& func)
    {
      bool changed = false;
      std::vector<BasicBlock*> blocks;
      for (const auto& block : func.blocks)
        blocks.push_back(block.get());
      for (auto block : blocks) {
        if (block == func.entry() || block->insts.size() != 1)
          continue;
        auto only = block->insts[0];
        if (only->kind == InstKind::CondBrInst)
          changed |= through_branch(func, block, only);
        else if (only->kind == InstKind::BrInst && block->params.empty())
          changed |= through_jump(func, block, only);
      }
      return changed;
    }

    // Take out the blocks nothing reaches any more: the ones gone through.
    void prune(Function& func)
    {
      auto entry = func.entry();
      if (entry == nullptr)
        return;
      std::unordered_set<const BasicBlock*> seen{ entry };
      std::vector<BasicBlock*> queue{ entry };
      while (! queue.empty()) {
        auto block = queue.back();
        queue.pop_back();
        if (block->terminator() == nullptr)
          continue;
        for (auto one : successors(block))
          if (one != nullptr && seen.insert(one).second)
            queue.push_back(one);
      }
      std::vector<const BasicBlock*> gone;
      for (const auto& block : func.blocks)
        if (! seen.contains(block.get()))
          gone.push_back(block.get());
      for (auto block : gone)
        retire_block(func, block);
    }

  } // anonymous namespace


  bool branch_conditions(Module& module)
  {
    bool touched = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      bool changed = false;
      for (int round = 0; round < rounds; ++round) {
        bool one = thread(*func);
        one |= split(*func);
        changed |= one;
        if (! one)
          break;
      }
      if (changed)
        prune(*func);
      touched |= changed;
    }
    return touched;
  }

} // namespace pl4g::opt
