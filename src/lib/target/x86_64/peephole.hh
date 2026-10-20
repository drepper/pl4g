// Peephole rewrites over the machine form: pypl4g's target/x86_64/peephole.py.
//
// These run on a machine function before it is streamed; each states the
// condition under which it is valid and checks it rather than assuming it.
#pragma once

#include <memory>
#include <vector>

#include "mc/asmbuilder.hh"
#include "mc/desc.hh"


namespace pl4g::target::x86_64 {

  // Whether the flags are dead immediately after instruction `index` of
  // `block`: the first later instruction reading them makes them live, the
  // first writing them dead, and the end of the block is conclusive only where
  // control leaves the function there.
  bool flags_dead_after(const mc::MachineBasicBlock& block, std::size_t index, bool leaves_function);

  // Rewrites `mov r32, 0` as `xor r32, r32`, three bytes shorter, where the
  // flags the exclusive-or writes are dead.
  class ClearRegisterWithXor final : public mc::MachinePass {
  public:
    explicit ClearRegisterWithXor(const mc::InstrTable& table) : table(table) {}
    std::string_view name() const override { return "x86.clear-with-xor"; }
    bool run(mc::MachineFunction& function) override;

  private:
    const mc::InstrTable& table;
  };

  // Removes a move of a register to the register it is already in, which the
  // allocator's hints make common.
  class DropMovesToItself final : public mc::MachinePass {
  public:
    std::string_view name() const override { return "x86.drop-moves-to-itself"; }
    bool run(mc::MachineFunction& function) override;
  };

  // The machine passes to run at optimization level `opt_level`.
  std::vector<std::unique_ptr<mc::MachinePass>> passes_for(const mc::InstrTable& table, int opt_level);

} // namespace pl4g::target::x86_64
