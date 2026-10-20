// What the passes ask about a function's graph and a module's calls.
//
// A port of pypl4g's opt/flow.py -- which blocks follow which, which dominate
// which -- and of ir/callgraph.py, who calls whom and in what order, beside the
// questions every pass asks of an instruction.
#pragma once

#include <cstdint>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "ir/ir.hh"


namespace pl4g::opt {

  // -- what an instruction does ---------------------------------------------

  // Whether removing `inst` would change what the program does: pypl4g's
  // `Instruction.has_effects`, shape by shape.
  bool has_effects(const ir::Instruction* inst);

  // The function an instruction names: what it calls directly, or whose code
  // it takes.  pypl4g's `references`.
  ir::Function* named_by(const ir::Instruction* inst);

  // The functions reachable whatever the program does: pypl4g's
  // `DropUnreached._roots`.
  std::vector<ir::Function*> roots(const ir::Module& module);


  // -- the graph ----------------------------------------------------------------

  // The blocks `block` may go to next, in the order its terminator names them.
  std::vector<ir::BasicBlock*> successors(const ir::BasicBlock* block);

  // For each block, the blocks that may go to it, once each.
  std::unordered_map<const ir::BasicBlock*, std::vector<ir::BasicBlock*>> predecessors(const ir::Function& func);

  // The blocks reachable from the entry, each before everything it reaches
  // that does not reach back to it.
  std::vector<ir::BasicBlock*> reverse_postorder(const ir::Function& func);

  // Which block dominates which, for the blocks reachable from the entry.
  struct Dominators {
    std::vector<ir::BasicBlock*> order;
    std::unordered_map<const ir::BasicBlock*, ir::BasicBlock*> idom;
    std::unordered_map<const ir::BasicBlock*, std::vector<ir::BasicBlock*>> children;
    std::unordered_map<const ir::BasicBlock*, int> entered;
    std::unordered_map<const ir::BasicBlock*, int> left;

    // Whether every way to `other` goes through `one`.
    bool dominates(const ir::BasicBlock* one, const ir::BasicBlock* other) const;
    // The immediate dominator of `block`, or nothing for the entry and for a
    // block nothing reaches.
    ir::BasicBlock* idom_of(const ir::BasicBlock* block) const
    {
      auto it = idom.find(block);
      return it == idom.end() ? nullptr : it->second;
    }
  };

  // Cooper, Harvey and Kennedy's dominators of `func`.
  Dominators dominators(const ir::Function& func);

  // Point every use of a value in `instead` at what it stands for -- the end of
  // the chain, where what it stands for stands for something too -- and drop
  // those instructions and the ones in `dropped`.
  void replace_uses(ir::Function& func, const std::unordered_map<const ir::Value*, ir::Value*>& instead,
                    const std::unordered_set<const ir::Value*>& dropped = {});

  // Take `block` out of `func`, keeping it alive for whatever still names it.
  void retire_block(ir::Function& func, const ir::BasicBlock* block);


  // -- calls ----------------------------------------------------------------------

  // Every function `func` names, in the order it names them, once each.
  std::vector<ir::Function*> callees_of(const ir::Function& func);

  // Every function of `module`, callees before callers.
  std::vector<ir::Function*> in_call_order(const ir::Module& module);

  // The functions a call can come back round to.
  std::unordered_set<const ir::Function*> in_a_cycle(const ir::Module& module);

  // How many instructions anywhere in the program name each function.
  std::unordered_map<const ir::Function*, std::int64_t> called_by_count(const ir::Module& module);

  // What `inst`, and a copy of `func`, come to where generated, in machine
  // instructions: what an inliner weighs.
  std::int64_t cost_of(const ir::Instruction* inst);
  std::int64_t cost_of_body(const ir::Function& func);

} // namespace pl4g::opt
