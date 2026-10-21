// The program's entry point and the helpers it reports through: pypl4g's
// target/x86_64/startup.py.
//
// `_start` is built through the ordinary builder, so it is itself a test of the
// assembler and appears in the dump like any other function.  The binary
// depends on nothing from the system: this is the first instruction the
// kernel runs, and the process leaves through a system call.
#pragma once

#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"
#include "target/allocator.hh"
#include "target/backtrace.hh"
#include "target/callconv.hh"
#include "target/host.hh"
#include "target/stack.hh"
#include "target/tests.hh"


namespace pl4g::target::x86_64 {

  // The name the entry point is given in the image, and the helpers a fault
  // leaves the program through and a failing test says so through.
  inline constexpr std::string_view entry_symbol = "_start";
  inline constexpr std::string_view abort_symbol = "__pl4g_abort";
  inline constexpr std::string_view report_symbol = "__pl4g_report";

  // The Linux system calls these make.
  inline constexpr std::int64_t nr_exit_group = 231;
  inline constexpr std::int64_t nr_write = 1;
  inline constexpr std::int64_t nr_mmap = 9;
  inline constexpr std::int64_t nr_munmap = 11;
  inline constexpr std::int64_t nr_mprotect = 10;
  inline constexpr std::int64_t nr_rt_sigreturn = 15;
  inline constexpr std::int64_t nr_rt_sigaction = 13;
  inline constexpr std::int64_t nr_sigaltstack = 131;
  // What a fault writes to.
  inline constexpr std::int64_t standard_error = 2;

  // How a system call is made here, where the allocator's arguments arrive,
  // how the stack is made and how the walk of it writes.
  const SyscallABI& syscalls();
  const AllocatorRegs& allocator_regs();
  const StackABI& stack_abi();
  const WalkABI& walk_abi();
  // The registers a stub for what the host defines works in.
  const host::HostABI& host_abi();

  // The entry point for `module`.  A program built for more than the oldest
  // level asks the processor first whether it can run at all; `refused` is
  // where the message it then says was put, and nothing is asked without one.
  void emit_start(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv, std::string_view level,
                  const std::optional<std::string>& refused, const std::optional<std::string>& turned_off,
                  const std::unordered_map<const ir::Function*, Failure>& failures, const ir::DataLayout& layout,
                  bool relocates = false);

  // The registers the table of a position-independent image is applied with.
  std::span<const mc::Reg> relocate_regs();

  // The helper that reports a fault and stops the program, walking the stack
  // first where `walk` says.
  void emit_abort(mc::Assembler& asm_, const CallConvDesc& cconv, bool walk = false);

  // The helper a failing test says so through, which comes back.
  void emit_report(mc::Assembler& asm_, const CallConvDesc& cconv);

} // namespace pl4g::target::x86_64
