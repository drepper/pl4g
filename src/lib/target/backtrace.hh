// Walking the stack, so that a fault says who called and not only where it
// was: written once for every architecture over the table frames.hh builds,
// what differs being which registers a system call uses and which instruction
// enters the kernel.  A port of pypl4g/target/backtrace.py.
#pragma once

#include <cstdint>
#include <functional>
#include <span>
#include <string_view>
#include <vector>

#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // What the helper is called, where what a stopping program says goes, and
  // how many frames are written before the walk gives up.
  inline constexpr std::string_view backtrace_symbol = "__pl4g_backtrace";
  inline constexpr std::int64_t standard_error = 2;
  inline constexpr std::int64_t walk_limit = 64;

  // How one architecture writes, and which registers the walk may keep.
  struct WalkABI {
    // The number of the write system call.
    std::int64_t write = 0;
    // Where the number of the call goes, and where its arguments go.
    mc::Reg number;
    std::vector<mc::Reg> arguments;
    // The instruction that enters the kernel.
    std::function<void(mc::Assembler&)> enter;
    // Four registers the system call leaves alone, for where the program was,
    // where its stack is, which row is looked at and how many frames were
    // written; and two for a moment's use.
    std::vector<mc::Reg> kept;
    std::vector<mc::Reg> scratch;
  };

  // Emit the helper that walks the stack and writes a line per frame;
  // `arguments` is where its own two arrive.
  void emit_backtrace(mc::Assembler& asm_, const WalkABI& abi, std::span<const mc::Reg> arguments);

} // namespace pl4g::target
