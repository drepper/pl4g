// Dead-code elimination.  A port of pypl4g's opt/passes/dce.py.
//
// What nothing uses and what does nothing goes.  One effect is not the
// instruction's own to answer for: a call answering through room its caller
// made writes only that room, so where nothing else reads the room the call
// changes nothing anyone can see and goes, and the room after it.
#include "opt/passes.hh"

#include <unordered_map>
#include <unordered_set>

#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // Whether the only memory a call changes is the place it was handed.
    bool writes_only_its_place(const Instruction* inst)
    {
      if (inst->callee == nullptr)
        return false;
      const auto& attrs = inst->callee->attrs;
      return attrs.answer_in_storage && ! attrs.impure && ! attrs.opaque;
    }

    // For each value, the instructions of `func` that name it.
    std::unordered_map<const Value*, std::vector<const Instruction*>> readers_in(const Function& func)
    {
      std::unordered_map<const Value*, std::vector<const Instruction*>> found;
      for (const auto& block : func.blocks)
        for (auto inst : block->insts) {
          for (auto value : inst->operands)
            found[value].push_back(inst);
          if (inst->is_terminator())
            for (const auto& target : inst->targets)
              for (auto value : target.args)
                found[value].push_back(inst);
        }
      return found;
    }

    // Whether `inst` has to be there even though nothing reads it.
    bool matters(const Instruction* inst, const std::unordered_map<const Value*, std::vector<const Instruction*>>& readers)
    {
      if (inst->kind != InstKind::CallInst || ! writes_only_its_place(inst))
        return has_effects(inst);
      // Last, or first where the system's convention puts it there.
      const Value* place = inst->operands.empty()          ? nullptr
                           : inst->callee->attrs.answer_first ? inst->operands.front()
                                                              : inst->operands.back();
      if (place == nullptr || place->vkind != ValueKind::instruction
          || static_cast<const Instruction*>(place)->kind != InstKind::FrameInst)
        // Room this function did not make, which something else may read.
        return true;
      auto it = readers.find(place);
      if (it == readers.end())
        return false;
      for (auto one : it->second)
        if (one != inst)
          return true;
      return false;
    }

    // Drop what is dead now, and say whether anything went.
    bool sweep(Module& module, Function& func)
    {
      std::unordered_set<const Value*> used;
      for (const auto& block : func.blocks)
        for (auto inst : block->insts) {
          used.insert(inst->operands.begin(), inst->operands.end());
          if (inst->is_terminator())
            for (const auto& target : inst->targets)
              used.insert(target.args.begin(), target.args.end());
        }
      auto readers = readers_in(func);
      bool removed = false;
      for (auto& block : func.blocks) {
        std::vector<Instruction*> kept;
        for (auto inst : block->insts)
          if (matters(inst, readers) || used.contains(inst))
            kept.push_back(inst);
        if (kept.size() == block->insts.size())
          continue;
        std::unordered_set<const Instruction*> surviving(kept.begin(), kept.end());
        for (auto inst : block->insts) {
          if (surviving.contains(inst))
            continue;
          if (inst->kind == InstKind::CallInst)
            module.record("drop-call", inst->callee != nullptr ? inst->callee->name : "a call",
                          "nothing reads what it answers with and it changes nothing that outlives the call, so "
                            + func.name + " does not make it",
                          inst->span);
          else if (inst->name_hint)
            module.record("drop-local", *inst->name_hint,
                          "nothing reads it, and computing it does nothing else, so it is not in " + func.name,
                          inst->name_span.is_valid() ? inst->name_span : inst->span);
        }
        block->insts = std::move(kept);
        removed = true;
      }
      return removed;
    }

  } // anonymous namespace


  bool dead_code(Module& module)
  {
    bool changed = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      while (sweep(module, *func))
        changed = true;
    }
    return changed;
  }

} // namespace pl4g::opt
