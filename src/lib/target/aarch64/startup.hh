// The program's entry point on AArch64, and the helpers a fault leaves the
// program through.  A port of pypl4g/target/aarch64/startup.py.
//
// Built through the ordinary builder API, so it is itself a test of the
// assembler and appears in the debugging dump like any other function.  The
// binary depends on nothing from the system: this is the first instruction
// the kernel runs, and the process leaves through a supervisor call.
#pragma once

#include <span>

#include <cstdint>
#include <string_view>
#include <unordered_map>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"
#include "target/allocator.hh"
#include "target/backtrace.hh"
#include "target/callconv.hh"
#include "target/stack.hh"
#include "target/tests.hh"


namespace pl4g::target::aarch64 {

  // The Linux system calls the back end makes, by this architecture's numbers.
  inline constexpr std::int64_t nr_exit_group = 94;
  inline constexpr std::int64_t nr_write = 64;
  inline constexpr std::int64_t nr_mmap = 222;
  inline constexpr std::int64_t nr_munmap = 215;
  inline constexpr std::int64_t nr_mprotect = 226;
  inline constexpr std::int64_t nr_rt_sigaction = 134;
  inline constexpr std::int64_t nr_sigaltstack = 132;

  // What a fault writes to: standard error, by the number every system gives it.
  inline constexpr std::int64_t standard_error = 2;

  // The name the entry point is given in the image, and those of the helpers a
  // fault leaves the program through and a failing test says so through.
  inline constexpr std::string_view entry_symbol = "_start";
  inline constexpr std::string_view abort_symbol = "__pl4g_abort";
  inline constexpr std::string_view report_symbol = "__pl4g_report";

  // What a system call looks like here, what the allocator is handed, how the
  // stack is made, and how the walk of the stack writes.
  const SyscallABI& syscalls();
  const AllocatorRegs& allocator_regs();
  const StackABI& stack_abi();
  const WalkABI& walk_abi();

  // The entry point for `module`.
  void emit_start(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                  const std::unordered_map<const ir::Function*, Failure>& failures, const ir::DataLayout& layout,
                  bool relocates = false);

  // The registers the table of a position-independent image is applied with.
  std::span<const mc::Reg> relocate_regs();
  // The helper that reports a fault and stops the program; with `walk`, it
  // says where the program was on the way.
  void emit_abort(mc::Assembler& asm_, const CallConvDesc& cconv, bool walk = false);
  // The helper a failing test says so through.
  void emit_report(mc::Assembler& asm_, const CallConvDesc& cconv);

} // namespace pl4g::target::aarch64
