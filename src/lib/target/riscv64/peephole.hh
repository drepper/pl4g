// Rewrites over the machine form of a RISC-V function: pypl4g's
// target/riscv64/peephole.py.
//
// One today, about the vector extension: every group of vector instructions
// the selector writes begins by saying how wide an element is and how many
// there are, and within one block what says again what was just said goes.
#pragma once

#include "mc/asmbuilder.hh"


namespace pl4g::target::riscv64 {

  // Drops a `vsetivli` that says what the one before it in its block said,
  // forgetting at the start of every block, at a call and at a request to the
  // kernel -- after which nothing is promised about the vector state.
  class SaidOnce final : public mc::MachinePass {
  public:
    std::string_view name() const override { return "riscv.said-once"; }
    // Drop what is said twice, answering whether anything went.
    bool run(mc::MachineFunction& function) override;
  };

} // namespace pl4g::target::riscv64
