// The stack a program runs on, and the guard below it: asked of the system by
// the entry point before anything of the program runs, with a handler that
// reports running off the bottom as a status of its own.  Written once for
// every architecture.  A port of pypl4g/target/stack.py.
#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <string_view>
#include <vector>

#include "mc/asmbuilder.hh"
#include "target/allocator.hh"


namespace pl4g::target {

  // How much room the handler runs in.
  inline constexpr std::int64_t alt_stack = 16 * 1024;
  // How far below the kernel's stack the program's own is asked for, and the
  // grain the address is brought down to.
  inline constexpr std::int64_t stack_gap = 2 << 20;
  inline constexpr std::int64_t hint_grain = 2 << 20;
  inline constexpr std::int64_t prot_none = 0;
  inline constexpr std::int64_t map_private_anon_noreserve = 0x4022;
  inline constexpr std::int64_t sigsegv = 11;
  inline constexpr std::int64_t sa_siginfo = 0x00000004;
  inline constexpr std::int64_t sa_onstack = 0x08000000;
  inline constexpr std::int64_t sa_restorer = 0x04000000;
  inline constexpr std::int64_t sigset_size = 8;
  inline constexpr std::int64_t si_addr = 16;

  // Where things are in the one object holding the handler's state and the
  // structures the calls are handed.
  inline constexpr std::int64_t state_low = 0;
  inline constexpr std::int64_t state_high = word;
  inline constexpr std::int64_t state_action = 2 * word;
  inline constexpr std::int64_t state_plain = 6 * word;
  inline constexpr std::int64_t action_handler = 0;
  inline constexpr std::int64_t action_flags = word;
  inline constexpr std::int64_t action_restorer = 2 * word;
  inline constexpr std::int64_t state_alt = 10 * word;
  inline constexpr std::int64_t alt_sp = 0;
  inline constexpr std::int64_t alt_flags = word;
  inline constexpr std::int64_t alt_size = 2 * word;
  inline constexpr std::int64_t state_size = 13 * word;

  // What the program says when it runs out.
  inline constexpr std::string_view stack_message = "pl4g: the stack ran out\n";

  inline constexpr std::string_view stack_state_symbol = "__pl4g_stack_state";
  inline constexpr std::string_view stack_handler_symbol = "__pl4g_stack_fault";
  inline constexpr std::string_view stack_restorer_symbol = "__pl4g_stack_return";
  inline constexpr std::string_view stack_message_symbol = ".Lstack.message";
  inline constexpr std::string_view stack_state_section = ".data";
  inline constexpr std::string_view stack_message_section = ".rodata";

  // What one architecture needs beyond what the allocator's record says.
  struct StackABI {
    std::int64_t mprotect = 0;
    std::int64_t sigaltstack = 0;
    std::int64_t rt_sigaction = 0;
    std::int64_t write = 0;
    std::int64_t exit_group = 0;
    // The largest page this architecture may be configured with.
    std::int64_t page = 0;
    // Three registers a system call leaves alone and the entry point is not
    // otherwise using, and three the handler may destroy.
    std::vector<mc::Reg> kept;
    std::vector<mc::Reg> scratch;
    // Reading and writing the stack pointer.
    std::function<void(mc::Assembler&, mc::Reg)> read_sp;
    std::function<void(mc::Assembler&, mc::Reg)> write_sp;
    // The call that returns from a handler, for the kernel that wants one.
    std::optional<std::int64_t> rt_sigreturn;
  };

  // `size` brought up to a whole number of `grain`.
  inline std::int64_t rounded(std::int64_t size, std::int64_t grain)
  {
    return (size + grain - 1) & ~(grain - 1);
  }

  // Emit, into the entry point, the making of the stack and the switch to it.
  void emit_make(mc::Assembler& asm_, const SyscallABI& calls, const StackABI& abi, std::int64_t stack_size,
                 std::int64_t guard_size);

  // Emit what the kernel calls when the program follows a bad address.
  void emit_handler(mc::Assembler& asm_, const SyscallABI& calls, const StackABI& abi);

  // Put the state and the message into the image.
  void emit_state(mc::Assembler& asm_, const StackABI& abi);

} // namespace pl4g::target
