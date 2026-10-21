// What a shared object has that an executable has not.  A port of
// pypl4g/target/shared.py.
//
// It has no entry point: the program that loads it calls what it offers, which
// is its visible functions and variables.  Its constructors are run when it is
// loaded and its destructors when it is unloaded, by the loader, from a table of
// each -- the destructors' read from its end, so that they run in the order the
// program gives them, which is the opposite of its constructors'.  Each was
// given the system's convention for that, the loader calling the way the system
// calls.  And it reaches what the host defines through stubs that ask `dlsym`
// (host.hh), which is what it needs the C library for.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "source/manager.hh"
#include "target/callconv.hh"
#include "target/faults.hh"
#include "target/host.hh"


namespace pl4g::target::shared {

  // The library `dlsym` is in, which is the one a shared object depends on.
  inline constexpr std::string_view libc = "libc.so.6";

  // The tables of constructors and destructors.
  inline constexpr std::string_view init_array = ".init_array";
  inline constexpr std::string_view fini_array = ".fini_array";

  // Emit the stubs for what the host defines, and the two tables.
  void emit(mc::Assembler& asm_, const ir::Module& module, const host::HostABI& abi, const CallConvDesc& cconv,
            Messages& messages, const SourceManager* sources, std::uint64_t text_alignment, std::string_view abort);

  // What a shared object offers: its visible functions, then its variables.
  std::vector<std::string> exports(const ir::Module& module);

} // namespace pl4g::target::shared
