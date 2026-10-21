// Rewrites over the machine form of a RISC-V function.
#include "target/riscv64/peephole.hh"

#include <optional>
#include <vector>

#include "mc/desc.hh"
#include "mc/machine.hh"


namespace pl4g::target::riscv64 {

  bool SaidOnce::run(mc::MachineFunction& function)
  {
    bool changed = false;
    for (auto& block : function.blocks) {
      std::optional<std::vector<mc::MCOperand>> said;
      mc::Insts kept;
      for (auto& inst : block->insts) {
        if (inst->mnemonic() == "vsetivli") {
          if (said.has_value() && inst->operands == *said) {
            changed = true;
            continue;
          }
          said = inst->operands;
        } else if (mc::has(inst->desc->flags, mc::InstFlags::call) || inst->mnemonic() == "ecall")
          // A function called may say what it likes, and the kernel leaves
          // the vector state as it pleases.
          said.reset();
        kept.push_back(inst);
      }
      block->insts = std::move(kept);
    }
    return changed;
  }

} // namespace pl4g::target::riscv64
