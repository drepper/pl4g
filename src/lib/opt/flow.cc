// What the passes ask about a function's graph and a module's calls.  A port
// of pypl4g's opt/flow.py and ir/callgraph.py.
#include "opt/flow.hh"

#include <algorithm>


namespace pl4g::opt {

  using namespace ir;

  // -- what an instruction does ---------------------------------------------

  bool has_effects(const Instruction* inst)
  {
    switch (inst->kind) {
    case InstKind::CallInst: {
      // What the callee says; a call through a value says nothing, and what it
      // does not say has to be assumed.
      if (inst->callee == nullptr)
        return true;
      const auto& attrs = inst->callee->attrs;
      return attrs.answer_in_storage || attrs.impure || attrs.opaque;
    }
    case InstKind::SyscallInst:
    case InstKind::AssertInst:
    case InstKind::StoreInst:
      return true;
    case InstKind::LoadInst:
      return inst->ordering != Ordering::plain;
    default:
      return inst->is_terminator();
    }
  }


  Function* named_by(const Instruction* inst)
  {
    if (inst->kind == InstKind::CallInst || inst->kind == InstKind::CodeInst)
      return inst->callee;
    return nullptr;
  }


  std::vector<Function*> roots(const Module& module)
  {
    std::vector<Function*> found;
    if (module.startup != nullptr)
      found.push_back(module.startup);
    if (module.environ != nullptr)
      if (auto made = module.function("__pl4g_environ_make"))
        found.push_back(made);
    if (module.auxv != nullptr)
      if (auto made = module.function("__pl4g_auxv_make"))
        found.push_back(made);
    found.insert(found.end(), module.ctors.begin(), module.ctors.end());
    found.insert(found.end(), module.dtors.begin(), module.dtors.end());
    if (! module.test_plan.empty())
      found.insert(found.end(), module.test_plan.begin(), module.test_plan.end());
    else
      for (auto one : module.tests)
        if (one->attrs.special == SpecialKind::test_always)
          found.push_back(one);
    for (const auto& [_, func] : module.functions)
      if (func->linkage == Linkage::visible && ! func->is_declaration())
        found.push_back(func.get());
    return found;
  }


  // -- the graph ----------------------------------------------------------------

  std::vector<BasicBlock*> successors(const BasicBlock* block)
  {
    std::vector<BasicBlock*> found;
    if (auto last = block->terminator())
      for (const auto& target : last->targets)
        found.push_back(target.block);
    return found;
  }


  std::unordered_map<const BasicBlock*, std::vector<BasicBlock*>> predecessors(const Function& func)
  {
    std::unordered_map<const BasicBlock*, std::vector<BasicBlock*>> found;
    for (const auto& block : func.blocks)
      found[block.get()];
    for (const auto& block : func.blocks) {
      std::vector<BasicBlock*> once;
      for (auto one : successors(block.get()))
        if (std::ranges::find(once, one) == once.end())
          once.push_back(one);
      for (auto one : once)
        found[one].push_back(block.get());
    }
    return found;
  }


  std::vector<BasicBlock*> reverse_postorder(const Function& func)
  {
    std::vector<BasicBlock*> order;
    std::unordered_set<const BasicBlock*> seen;
    std::vector<std::pair<BasicBlock*, std::size_t>> stack{ { func.blocks.front().get(), 0 } };
    seen.insert(func.blocks.front().get());
    while (! stack.empty()) {
      auto [block, at] = stack.back();
      stack.pop_back();
      auto following = successors(block);
      if (at < following.size()) {
        stack.emplace_back(block, at + 1);
        auto one = following[at];
        if (seen.insert(one).second)
          stack.emplace_back(one, 0);
      } else
        order.push_back(block);
    }
    std::ranges::reverse(order);
    return order;
  }


  bool Dominators::dominates(const BasicBlock* one, const BasicBlock* other) const
  {
    auto a = entered.find(one), b = entered.find(other);
    if (a == entered.end() || b == entered.end())
      return false;
    return a->second <= b->second && left.at(other) <= left.at(one);
  }


  Dominators dominators(const Function& func)
  {
    Dominators tree;
    tree.order = reverse_postorder(func);
    std::unordered_map<const BasicBlock*, std::size_t> number;
    for (std::size_t at = 0; at < tree.order.size(); ++at)
      number[tree.order[at]] = at;
    auto preds = predecessors(func);
    auto entry = tree.order.front();
    std::unordered_map<const BasicBlock*, BasicBlock*> idom{ { entry, entry } };
    auto meet = [&](BasicBlock* one, BasicBlock* other) {
      while (one != other) {
        while (number[one] > number[other])
          one = idom.at(one);
        while (number[other] > number[one])
          other = idom.at(other);
      }
      return one;
    };
    bool changed = true;
    while (changed) {
      changed = false;
      for (std::size_t at = 1; at < tree.order.size(); ++at) {
        auto block = tree.order[at];
        BasicBlock* made = nullptr;
        for (auto pred : preds[block]) {
          if (! idom.contains(pred))
            continue;
          made = made == nullptr ? pred : meet(pred, made);
        }
        if (made != nullptr) {
          auto it = idom.find(block);
          if (it == idom.end() || it->second != made) {
            idom[block] = made;
            changed = true;
          }
        }
      }
    }
    idom[entry] = nullptr;
    tree.idom = idom;
    for (auto block : tree.order) {
      tree.children[block];
      if (auto parent = tree.idom_of(block))
        tree.children[parent].push_back(block);
    }
    int clock = 0;
    std::vector<std::pair<BasicBlock*, bool>> stack{ { entry, false } };
    while (! stack.empty()) {
      auto [block, done] = stack.back();
      stack.pop_back();
      if (done) {
        tree.left[block] = clock++;
        continue;
      }
      tree.entered[block] = clock++;
      stack.emplace_back(block, true);
      const auto& below = tree.children[block];
      for (auto it = below.rbegin(); it != below.rend(); ++it)
        stack.emplace_back(*it, false);
    }
    return tree;
  }


  void replace_uses(Function& func, const std::unordered_map<const Value*, Value*>& instead,
                    const std::unordered_set<const Value*>& dropped)
  {
    auto settled = [&](Value* value) {
      for (auto it = instead.find(value); it != instead.end(); it = instead.find(value))
        value = it->second;
      return value;
    };
    for (auto& block : func.blocks) {
      for (auto inst : block->insts) {
        for (auto& operand : inst->operands)
          if (instead.contains(operand))
            operand = settled(operand);
        if (inst->is_terminator())
          for (auto& target : inst->targets)
            for (auto& arg : target.args)
              if (instead.contains(arg))
                arg = settled(arg);
      }
      std::erase_if(block->insts, [&](const Instruction* one) {
        return instead.contains(one) || dropped.contains(one);
      });
    }
  }


  void retire_block(Function& func, const BasicBlock* block)
  {
    auto it = std::ranges::find_if(func.blocks, [&](const auto& one) { return one.get() == block; });
    if (it == func.blocks.end())
      return;
    func.retired_blocks.push_back(std::move(*it));
    func.blocks.erase(it);
  }


  // -- calls ----------------------------------------------------------------------

  std::vector<Function*> callees_of(const Function& func)
  {
    std::vector<Function*> found;
    std::unordered_set<const Function*> seen;
    for (const auto& block : func.blocks)
      for (auto inst : block->insts)
        if (auto named = named_by(inst); named != nullptr && seen.insert(named).second)
          found.push_back(named);
    return found;
  }


  std::vector<Function*> in_call_order(const Module& module)
  {
    std::vector<Function*> order;
    std::unordered_set<const Function*> done;
    std::unordered_set<const Function*> open;
    for (const auto& [_, root] : module.functions) {
      if (done.contains(root.get()))
        continue;
      std::vector<std::pair<Function*, std::vector<Function*>>> stack;
      stack.emplace_back(root.get(), callees_of(*root));
      open.insert(root.get());
      while (! stack.empty()) {
        auto& [func, waiting] = stack.back();
        if (waiting.empty()) {
          auto finished = func;
          stack.pop_back();
          open.erase(finished);
          if (done.insert(finished).second)
            order.push_back(finished);
          continue;
        }
        auto callee = waiting.back();
        waiting.pop_back();
        if (done.contains(callee) || open.contains(callee))
          continue;
        open.insert(callee);
        auto more = callees_of(*callee);
        stack.emplace_back(callee, std::move(more));
      }
    }
    return order;
  }


  std::unordered_set<const Function*> in_a_cycle(const Module& module)
  {
    std::unordered_map<const Function*, std::unordered_set<const Function*>> reaching;
    for (const auto& [_, func] : module.functions) {
      auto& reached = reaching[func.get()];
      for (auto one : callees_of(*func))
        reached.insert(one);
    }
    bool changed = true;
    while (changed) {
      changed = false;
      for (auto& [at, reached] : reaching) {
        auto grown = reached;
        for (auto other : reached)
          if (auto it = reaching.find(other); it != reaching.end())
            grown.insert(it->second.begin(), it->second.end());
        if (grown.size() != reached.size()) {
          reached = std::move(grown);
          changed = true;
        }
      }
    }
    std::unordered_set<const Function*> found;
    for (const auto& [at, reached] : reaching)
      if (reached.contains(at))
        found.insert(at);
    return found;
  }


  std::unordered_map<const Function*, std::int64_t> called_by_count(const Module& module)
  {
    std::unordered_map<const Function*, std::int64_t> counted;
    for (const auto& [_, func] : module.functions)
      for (const auto& block : func->blocks)
        for (auto inst : block->insts)
          if (auto named = named_by(inst))
            ++counted[named];
    return counted;
  }


  std::int64_t cost_of(const Instruction* inst)
  {
    switch (inst->kind) {
    case InstKind::MemStartInst:
    case InstKind::TupleInst:
    case InstKind::ExtractInst:
    case InstKind::WrapInst:
    case InstKind::UnwrapInst:
    case InstKind::ErrorInst:
    case InstKind::FailedInst:
    case InstKind::SizeOfInst:
    case InstKind::UnreachableInst:
      return 0;
    case InstKind::CastInst:
      return inst->cast == CastKind::bitcast ? 0 : 1;
    case InstKind::BinaryInst:
      switch (inst->bin) {
      case BinOp::add: case BinOp::sub: return 6;
      case BinOp::mul: return 8;
      case BinOp::sdiv: case BinOp::udiv: case BinOp::srem: case BinOp::urem: return 5;
      case BinOp::sat_add: case BinOp::sat_sub: return 2;
      case BinOp::sat_mul: return 3;
      case BinOp::smax: case BinOp::umax: case BinOp::smin: case BinOp::umin: return 2;
      default: return 1;
      }
    case InstKind::CallInst:
    case InstKind::SyscallInst:
      return 4;
    case InstKind::AssertInst:
      return 8;
    case InstKind::AddressInst:
    case InstKind::SumMakeInst:
      return 2;
    case InstKind::SwitchInst:
      return 1 + 2 * std::int64_t(inst->cases.size());
    default:
      return 1;
    }
  }


  std::int64_t cost_of_body(const Function& func)
  {
    std::int64_t total = 0;
    for (const auto& block : func.blocks)
      for (auto inst : block->insts)
        total += cost_of(inst);
    return total;
  }


  bool is_leaf(const Function& func)
  {
    for (const auto& block : func.blocks)
      for (auto inst : block->insts)
        if (inst->kind == InstKind::CallInst || inst->kind == InstKind::SyscallInst)
          return false;
    return true;
  }

} // namespace pl4g::opt
