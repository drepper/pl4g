// The allocator, and the arenas it hands storage out of: GNU's obstacks, a
// bump pointer over a list of chunks, written once for every architecture.
// What differs between them is a small record each back end fills in.  A port
// of pypl4g/target/allocator.py.
#pragma once

#include <cstdint>
#include <functional>
#include <string_view>
#include <vector>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // How wide a word is here.
  inline constexpr std::int64_t word = 8;

  // Where the three words of an arena are, and how much room one takes.
  inline constexpr std::int64_t arena_next = 0;
  inline constexpr std::int64_t arena_limit = word;
  inline constexpr std::int64_t arena_chunk = 2 * word;
  inline constexpr std::int64_t arena_size = 3 * word;

  // The two words at the front of a chunk, and where its body begins.
  inline constexpr std::int64_t chunk_next = 0;
  inline constexpr std::int64_t chunk_bytes = word;
  inline constexpr std::int64_t chunk_body = 2 * word;

  // Every allocation starts on this many bytes, and the smallest a chunk is
  // asked for.
  inline constexpr std::int64_t grain = 16;
  inline constexpr std::int64_t chunk_minimum = 64 * 1024;

  // PROT_READ | PROT_WRITE, and MAP_PRIVATE | MAP_ANONYMOUS.
  inline constexpr std::int64_t prot_read_write = 3;
  inline constexpr std::int64_t map_private_anonymous = 0x22;
  // The answers of a system call that failed.
  inline constexpr std::int64_t error_window = 4096;

  // The names the three the back end provides are known by.
  inline constexpr std::string_view alloc_symbol = "__pl4g_alloc";
  inline constexpr std::string_view grow_symbol = "__pl4g_grow";
  inline constexpr std::string_view release_symbol = "__pl4g_release";

  // What is said where the system will give no more memory.
  inline constexpr std::string_view out_of_memory = "pl4g: out of memory\n";

  // Whether anything in `module` calls into the allocator.
  bool allocator_wanted_by(const ir::Module& module);

  // How one architecture asks the kernel for something.
  struct SyscallABI {
    // The numbers of the two calls the allocator makes.
    std::int64_t mmap = 0;
    std::int64_t munmap = 0;
    // Where the number of the call goes, where the arguments go, and where the
    // answer comes back.
    mc::Reg number;
    std::vector<mc::Reg> arguments;
    mc::Reg answer;
    // The instruction that enters the kernel.
    std::function<void(mc::Assembler&)> enter;
    // What the kernel does not give back, beyond the register it answers in.
    std::vector<mc::Reg> clobbers;
  };

  // The registers the allocator's own code works in: where its arguments
  // arrive, where its answer goes, and registers a caller does not expect back.
  struct AllocatorRegs {
    mc::Reg arena;
    mc::Reg size;
    mc::Reg answer;
    std::vector<mc::Reg> scratch;
  };

  // Emit the three the back end provides, in the order they call each other.
  void emit_allocator(mc::Assembler& asm_, const SyscallABI& abi, const AllocatorRegs& regs,
                      std::string_view abort_symbol, std::string_view message_symbol);

} // namespace pl4g::target
