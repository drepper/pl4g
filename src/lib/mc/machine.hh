// The machine-level function: what instruction selection produces and the
// register allocator and the peephole passes work on.  A port of
// pypl4g/mc/machine.py.
#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

#include "mc/inst.hh"


namespace pl4g::mc {

  // A function whose recorded edges do not describe where control goes.
  struct MalformedGraph : std::runtime_error {
    using std::runtime_error::runtime_error;
  };


  // A straight-line run of machine instructions.
  struct MachineBasicBlock {
    std::string label;
    Insts insts;
    // Labels of the blocks control can reach from here.
    std::vector<std::string> successors;

    explicit MachineBasicBlock(std::string label) : label(std::move(label)) {}
    // Append an instruction to this block.
    const InstRef& append(InstRef inst)
    {
      insts.push_back(std::move(inst));
      return insts.back();
    }
  };


  // The stack a function uses for what it could not keep in registers, and
  // for what it keeps of its own.
  struct FrameInfo {
    // How wide one slot is, which is also what it is aligned to.
    std::int64_t slot_size = 8;
    // What the stack pointer must be a multiple of.
    std::int64_t alignment = 16;
    // How much of the frame has been given out, in bytes.
    std::int64_t taken = 0;
    // How many one-register slots were given out.
    std::int64_t slots = 0;

    // Take `size` bytes of the frame, aligned, and answer where they begin.
    std::int64_t reserve(std::int64_t size, std::int64_t align);
    // Take a slot for one register of `size` bytes (a word where none).
    std::int64_t allocate(std::optional<std::int64_t> size = std::nullopt);
    // How far the stack pointer moves; nothing where nothing was taken.
    std::int64_t size() const noexcept;
  };


  // One function, as machine instructions.
  struct MachineFunction {
    std::string name;
    std::vector<std::unique_ptr<MachineBasicBlock>> blocks;
    FrameInfo frame;
    // Whether this function's symbol is visible outside the image.
    bool exported = false;
    // Reserved growth slack to emit after the function.
    std::int64_t padding = 0;
    // The units this function hands back as it found them.
    std::set<const RegUnit*> preserved;
    // Where its return address is once its frame stands, from the stack
    // pointer; nothing where it is still in the register a call left it in.
    std::optional<std::int64_t> return_at;
    // And how much further up the caller's stack pointer is.
    std::int64_t caller_at = 0;

    explicit MachineFunction(std::string name) : name(std::move(name)) {}

    // Append a new block and return it.
    MachineBasicBlock* add_block(std::optional<std::string> label = std::nullopt);
    // Every instruction of the function, in layout order.
    Insts instructions() const;
    // Where the block called `label` is laid out.
    std::optional<std::size_t> index_of(std::string_view label) const;
    // For each block, the blocks control can reach from it.
    std::vector<std::vector<std::size_t>> successor_indices() const;
    // What is wrong with the recorded edges, or nothing where all is well.
    std::optional<std::string> edges_are_complete() const;
    // Every virtual register the function still mentions, first seen first.
    std::vector<const VirtReg*> virtual_registers() const;
  };

  // Every unit a finished function writes and does not put back.
  std::set<const RegUnit*> clobbered_units(const MachineFunction& function);
  // Whether every register in `registers` has been assigned.
  bool physical_only(std::span<const Reg> registers) noexcept;

} // namespace pl4g::mc
