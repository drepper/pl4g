// Control-flow simplification.  A port of pypl4g's opt/passes/simplifycfg.py.
//
// A branch whose answer is known becomes the jump it would take; blocks no
// branch reaches go; a parameter every way in supplies alike is that value;
// and a block with one way in, from a block with one way out, is written into
// that block.  Each leaves work for the next, so the four run until none
// changes anything.  Nothing is reordered: the order of the blocks is the
// order the back ends walk them in.
#include "opt/passes.hh"

#include <algorithm>
#include <optional>
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
            auto arg = one->args[at];
            if (arg != param && std::ranges::find(supplied, arg) == supplied.end())
              supplied.push_back(arg);
          }
          if (supplied.size() != 1)
            continue;
          stands_for(func, param, supplied.front());
          for (auto one : arriving)
            one->args.erase(one->args.begin() + std::ptrdiff_t(at));
          func.retired.push_back(std::move(block->params[at]));
          block->params.erase(block->params.begin() + std::ptrdiff_t(at));
          changed = true;
        }
        for (std::size_t index = 0; index < block->params.size(); ++index)
          block->params[index]->index = index;
      }
      return changed;
    }

    // Move everything of `target` into `block` and take it out.
    void absorb(Function& func, BasicBlock* block, BasicBlock* target, Instruction* branch)
    {
      auto args = branch->targets[0].args;
      for (std::size_t at = 0; at < target->params.size() && at < args.size(); ++at)
        stands_for(func, target->params[at].get(), args[at]);
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
    }

    bool merge_blocks(Function& func)
    {
      bool changed = false;
      for (int round = 0; round < rounds; ++round) {
        auto edges = incoming_edges(func);
        bool joined = false;
        std::vector<BasicBlock*> all;
        for (auto& block : func.blocks)
          all.push_back(block.get());
        for (auto block : all) {
          auto terminator = block->terminator();
          if (terminator == nullptr || terminator->kind != InstKind::BrInst)
            continue;
          auto target = terminator->targets[0].block;
          if (target == nullptr || target == func.entry())
            continue;
          // A loop of one would be written into itself twice.
          if (target == block || target->terminator() == nullptr)
            continue;
          if (edges[target].size() != 1)
            continue;
          absorb(func, block, target, terminator);
          joined = changed = true;
          break;
        }
        if (! joined)
          break;
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
