// Peephole rewrites over the machine form.
#include "target/x86_64/peephole.hh"

#include <algorithm>

#include "target/x86_64/regs.hh"


namespace pl4g::target::x86_64 {

  namespace {

    bool defines_flags(const mc::MCInst& inst)
    {
      return std::ranges::contains(inst.desc->implicit_defs, registers().eflags);
    }

    bool uses_flags(const mc::MCInst& inst)
    {
      return std::ranges::contains(inst.desc->implicit_uses, registers().eflags);
    }

    // Whether this instruction puts a register where it already is.
    bool moves_to_itself(const mc::MCInst& inst)
    {
      if ((inst.mnemonic() != "mov" && inst.mnemonic() != "movdqu") || inst.operands.size() != 2)
        return false;
      auto dst = std::get_if<mc::MCReg>(&inst.operands[0]);
      auto src = std::get_if<mc::MCReg>(&inst.operands[1]);
      return dst != nullptr && src != nullptr && dst->reg.is_physical() && src->reg.is_physical()
             && dst->reg.phys()->unit == src->reg.phys()->unit && dst->width() == src->width()
             && dst->reg.phys()->byte_off == src->reg.phys()->byte_off;
    }

  } // anonymous namespace


  bool flags_dead_after(const mc::MachineBasicBlock& block, std::size_t index, bool leaves_function)
  {
    for (std::size_t at = index + 1; at < block.insts.size(); ++at) {
      if (uses_flags(*block.insts[at]))
        return false;
      if (defines_flags(*block.insts[at]))
        return true;
    }
    return leaves_function;
  }


  bool ClearRegisterWithXor::run(mc::MachineFunction& function)
  {
    bool changed = false;
    for (auto& block : function.blocks) {
      bool leaves_function = block->successors.empty();
      for (std::size_t index = 0; index < block->insts.size(); ++index) {
        const auto& inst = *block->insts[index];
        if (inst.mnemonic() != "mov" || inst.operands.size() != 2)
          continue;
        auto dst = std::get_if<mc::MCReg>(&inst.operands[0]);
        auto src = std::get_if<mc::MCImm>(&inst.operands[1]);
        if (dst == nullptr || src == nullptr)
          continue;
        if (src->value != 0 || ! dst->reg.is_physical() || dst->reg.phys()->bits != 32)
          continue;
        if (! flags_dead_after(*block, index, leaves_function))
          continue;
        std::vector<mc::MCOperand> operands{ *dst, *dst };
        auto desc = table.select("xor", operands);
        block->insts[index] = mc::make_inst(desc, std::move(operands), inst.span);
        changed = true;
      }
    }
    return changed;
  }


  bool DropMovesToItself::run(mc::MachineFunction& function)
  {
    bool changed = false;
    for (auto& block : function.blocks) {
      auto before = block->insts.size();
      std::erase_if(block->insts, [](const mc::InstRef& inst) { return moves_to_itself(*inst); });
      if (block->insts.size() != before)
        changed = true;
    }
    return changed;
  }


  std::vector<std::unique_ptr<mc::MachinePass>> passes_for(const mc::InstrTable& table, int opt_level)
  {
    std::vector<std::unique_ptr<mc::MachinePass>> made;
    if (opt_level <= 0)
      return made;
    made.push_back(std::make_unique<ClearRegisterWithXor>(table));
    made.push_back(std::make_unique<DropMovesToItself>());
    return made;
  }

} // namespace pl4g::target::x86_64
