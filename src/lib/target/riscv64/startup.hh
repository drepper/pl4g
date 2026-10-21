// The program's entry point on RISC-V, and the helpers a fault and a failing
// test leave the program through: pypl4g's target/riscv64/startup.py.
//
// Built through the ordinary builder API.  The binary depends on nothing from
// the system: this is the first instruction the kernel runs, and the process
// leaves through an environment call rather than by returning to anything.
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
#include "target/host.hh"
#include "target/stack.hh"
#include "target/tests.hh"


namespace pl4g::target::riscv64 {

  // The numbers of the Linux system calls the back end makes itself.
  inline constexpr std::int64_t nr_exit_group = 94;
  inline constexpr std::int64_t nr_write = 64;
  inline constexpr std::int64_t nr_mmap = 222;
  inline constexpr std::int64_t nr_munmap = 215;
  inline constexpr std::int64_t nr_mprotect = 226;
  inline constexpr std::int64_t nr_rt_sigaction = 134;
  inline constexpr std::int64_t nr_sigaltstack = 132;

  // What a fault writes to.
  inline constexpr std::int64_t standard_error = 2;

  inline constexpr std::string_view entry_symbol = "_start";
  inline constexpr std::string_view abort_symbol = "__pl4g_abort";
  inline constexpr std::string_view report_symbol = "__pl4g_report";

  // How a system call is made here, what the allocator, the stack and the walk
  // of the stack are written with.
  const SyscallABI& syscalls();
  const AllocatorRegs& allocator_regs();
  const StackABI& stack_abi();
  const WalkABI& walk_abi();
  // The registers a stub for what the host defines works in.
  const host::HostABI& host_abi();

  // The entry point for `module`.
  void emit_start(mc::Assembler& asm_, const ir::Module& module, const CallConvDesc& cconv,
                  const std::unordered_map<const ir::Function*, Failure>& failures, const ir::DataLayout& layout,
                  bool relocates = false);

  // The registers the table of a position-independent image is applied with.
  std::span<const mc::Reg> relocate_regs();

  // The helper that reports a fault and stops the program, and the one a
  // failing test says so through and comes back.
  void emit_abort(mc::Assembler& asm_, const CallConvDesc& cconv, bool walk = false);
  void emit_report(mc::Assembler& asm_, const CallConvDesc& cconv);

} // namespace pl4g::target::riscv64
