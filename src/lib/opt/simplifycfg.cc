// Control-flow simplification.  A port of pypl4g's opt/passes/simplifycfg.py.
//
// A branch whose answer is known becomes the jump it would take; blocks no
// branch reaches go; a parameter every way in supplies alike is that value;
// and a block with one way in, from a block with one way out, is written into
// that block.  Each leaves work for the next, so the four run until none
// changes anything.  Nothing is reordered, the order of the blocks being the
// order the back ends walk them in -- except where a block is written into a
// predecessor standing after it, and the blocks it dominated, which may read
// what it computed, follow it down to just after that predecessor.
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

    // How many times the four are run before it is taken to be a defect.
    constexpr int rounds = 100;

    // Which way a branch on `value` goes, where that is already settled.
    std::optional<bool> known_condition(const Value* value)
    {
      if (value->vkind == ValueKind::bool_const)
        return static_cast<const BoolConst*>(value)->value;
      if (value->vkind == ValueKind::int_const)
        return static_cast<const IntConst*>(value)->value != 0;
      return std::nullopt;
    }

    // What a value is, where that is already settled and it is a number.
    std::optional<__int128> known_number(const Value* value)
    {
      if (value->vkind == ValueKind::enum_const)
        return static_cast<const EnumConst*>(value)->number();
      if (value->vkind == ValueKind::int_const)
        return static_cast<const IntConst*>(value)->value;
      return std::nullopt;
    }

    bool settle_branches(Function& func)
    {
      bool changed = false;
      for (auto& block : func.blocks) {
        auto terminator = block->terminator();
        if (terminator == nullptr)
          continue;
        std::optional<BlockTarget> target;
        if (terminator->kind == InstKind::CondBrInst) {
          if (auto taken = known_condition(terminator->operands[0]))
            target = terminator->targets[*taken ? 0 : 1];
        } else if (terminator->kind == InstKind::SwitchInst) {
          if (auto number = known_number(terminator->operands[0])) {
            target = terminator->targets.back();
            for (std::size_t at = 0; at < terminator->cases.size(); ++at)
              if (terminator->cases[at] == *number) {
                target = terminator->targets[at];
                break;
              }
          }
        }
        if (! target)
          continue;
        auto jump = func.make(InstKind::BrInst, void_type());
        jump->span = terminator->span;
        jump->targets.push_back(std::move(*target));
        jump->parent = block.get();
        block->insts.back() = jump;
        changed = true;
      }
      return changed;
    }

    bool prune(Function& func)
    {
      auto entry = func.entry();
      std::unordered_set<const BasicBlock*> seen;
      if (entry != nullptr) {
        seen.insert(entry);
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
      }
      std::vector<const BasicBlock*> gone;
      for (const auto& block : func.blocks)
        if (! seen.contains(block.get()))
          gone.push_back(block.get());
      for (auto block : gone)
        retire_block(func, block);
      return ! gone.empty();
    }

    bool settle_params(Function& func)
    {
      bool changed = false;
      auto edges = incoming_edges(func);
      // What each parameter dropped stands for, written into the function once
      // at the end: a branch's arguments read before then are read through what
      // has been dropped so far, which is what they would have said had every
      // replacement been made as it was decided.
      std::unordered_map<const Value*, Value*> instead;
      auto now = [&instead](Value* value) {
        for (auto it = instead.find(value); it != instead.end(); it = instead.find(value))
          value = it->second;
        return value;
      };
      for (auto& block : func.blocks) {
        if (block.get() == func.entry() || block->params.empty())
          continue;
        std::vector<BlockTarget*> arriving;
        for (const auto& [_, target] : edges[block.get()])
          arriving.push_back(target);
        if (arriving.empty())
          continue;
        for (std::size_t at = block->params.size(); at-- > 0;) {
          auto param = block->params[at].get();
          std::vector<Value*> supplied;
          for (auto one : arriving) {
            auto arg = now(one->args[at]);
            if (arg != param && std::ranges::find(supplied, arg) == supplied.end())
              supplied.push_back(arg);
          }
          if (supplied.size() != 1)
            continue;
          instead[param] = supplied.front();
          for (auto one : arriving)
            one->args.erase(one->args.begin() + std::ptrdiff_t(at));
          func.retired.push_back(std::move(block->params[at]));
          block->params.erase(block->params.begin() + std::ptrdiff_t(at));
          changed = true;
        }
        for (std::size_t index = 0; index < block->params.size(); ++index)
          block->params[index]->index = index;
      }
      std::unordered_map<const Value*, Value*> settled;
      for (const auto& [gone, stands] : instead)
        settled[gone] = now(stands);
      all_stand_for(func, settled);
      return changed;
    }

    // Move everything of `target` into `block` and take it out.
    // Where `block` stands among the blocks of `func`.
    std::size_t position_of(const Function& func, const BasicBlock* block)
    {
      auto it = std::ranges::find_if(func.blocks, [&](const auto& one) { return one.get() == block; });
      contract_assert(it != func.blocks.end());
      return std::size_t(it - func.blocks.begin());
    }


    // Move the blocks from `first` up to `block` that `block` now dominates to
    // just after it, in the order they stand in: what was written into `block`
    // reached them, and they may read what it computed.
    void follow(Function& func, BasicBlock* block, std::size_t first)
    {
      auto tree = dominators(func);
      auto at = position_of(func, block);
      std::vector<std::unique_ptr<BasicBlock>> moved;
      std::vector<std::unique_ptr<BasicBlock>> rest;
      for (std::size_t index = 0; index < func.blocks.size(); ++index)
        if (index >= first && index < at && tree.dominates(block, func.blocks[index].get()))
          moved.push_back(std::move(func.blocks[index]));
        else
          rest.push_back(std::move(func.blocks[index]));
      if (moved.empty()) {
        func.blocks = std::move(rest);
        return;
      }
      func.blocks.clear();
      for (auto& one : rest) {
        bool here = one.get() == block;
        func.blocks.push_back(std::move(one));
        if (here)
          for (auto& each : moved)
            func.blocks.push_back(std::move(each));
      }
    }


    void absorb(Function& func, BasicBlock* block, BasicBlock* target, Instruction* branch)
    {
      auto first = position_of(func, target);
      bool ahead = first < position_of(func, block);
      auto args = branch->targets[0].args;
      auto handed_on = std::ranges::any_of(args, [target](const Value* arg) {
        return std::ranges::any_of(target->params, [arg](const auto& param) { return param.get() == arg; });
      });
      if (handed_on)
        // A parameter handed to another of the same block, which one
        // replacement after another reads differently from all at once.
        for (std::size_t at = 0; at < target->params.size() && at < args.size(); ++at)
          stands_for(func, target->params[at].get(), args[at]);
      else {
        std::unordered_map<const Value*, Value*> instead;
        for (std::size_t at = 0; at < target->params.size() && at < args.size(); ++at)
          instead.emplace(target->params[at].get(), args[at]);
        all_stand_for(func, instead);
      }
      block->insts.pop_back();
      for (auto one : target->insts) {
        one->parent = block;
        block->insts.push_back(one);
      }
      for (auto& param : target->params)
        func.retired.push_back(std::move(param));
      target->params.clear();
      target->insts.clear();
      retire_block(func, target);
      if (ahead)
        follow(func, block, first);
    }

    // How many ways arrive at each block is asked once: writing a block into
    // its one way in changes that for no block that stays, and makes no block
    // before it worth joining, so the walk goes on from where it joined -- the
    // hundred joins a call may make kept.
    bool merge_blocks(Function& func)
    {
      bool changed = false;
      std::unordered_map<const BasicBlock*, std::size_t> arriving;
      for (const auto& [block, found] : incoming_edges(func))
        arriving[block] = found.size();
      int joined = 0;
      for (std::size_t at = 0; at < func.blocks.size() && joined < rounds;) {
        auto block = func.blocks[at++].get();
        auto terminator = block->terminator();
        if (terminator == nullptr || terminator->kind != InstKind::BrInst)
          continue;
        auto target = terminator->targets[0].block;
        if (target == nullptr || target == func.entry())
          continue;
        // A loop of one would be written into itself twice.
        if (target == block || target->terminator() == nullptr)
          continue;
        if (auto it = arriving.find(target); it == arriving.end() || it->second != 1)
          continue;
        absorb(func, block, target, terminator);
        changed = true;
        ++joined;
        // The block again, from where it now stands: what it ends with is what
        // the block written into it ended with.
        at = position_of(func, block);
      }
      return changed;
    }

  } // anonymous namespace


  bool simplify_cfg(Module& module)
  {
    bool changed = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      for (int round = 0; round < rounds; ++round) {
        bool one = settle_branches(*func);
        one |= prune(*func);
        one |= settle_params(*func);
        one |= merge_blocks(*func);
        changed |= one;
        if (! one)
          break;
      }
    }
    return changed;
  }

} // namespace pl4g::opt
