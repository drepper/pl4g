// Finding what the host defines, the first time it is called.  A port of
// pypl4g/target/host.py.
//
// A function the host defines is called through a stub of this image's own, one
// per symbol.  The stub reads a slot that holds where the function is; where the
// slot holds something it goes there, the arguments and the return address being
// just as the caller left them.  The first time the slot holds nothing, and the
// stub asks the finder, which asks `dlsym` and fills the slot in.
//
// `dlsym` is reached through a slot the loader fills, the one relocation of a
// shared object that names a symbol rather than adding where the object was
// put.  It is asked with `RTLD_DEFAULT`, which is nought, so what is searched is
// what the program loaded globally.
//
// The finder is written once for the three architectures, from what each one's
// convention says its arguments are passed in.  It keeps every one of those
// across the call, the floating-point ones whole, and the slot it was handed:
// what it hands back is in the register the name came in.  A stub whose symbol
// the host does not define stops the program, saying which, with a status of
// its own.
#pragma once

#include <cstdint>
#include <optional>
#include <string_view>
#include <vector>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "source/manager.hh"
#include "target/callconv.hh"
#include "target/faults.hh"


namespace pl4g::target::host {

  // The finder every stub calls.
  inline constexpr std::string_view find_symbol = "__pl4g_host_find";

  // The slot the loader puts `dlsym`'s address in, and what it is asked for.
  inline constexpr std::string_view dlsym_slot = "__pl4g_dlsym";
  inline constexpr std::string_view dlsym = "dlsym";

  // What the slot a stub reads is called, and the symbol's name it asks for,
  // before the host's symbol.
  inline constexpr std::string_view slot_prefix = "__pl4g_host_slot.";
  inline constexpr std::string_view name_prefix = "__pl4g_host_name.";

  // What the declaration that takes C's text in place of a `str` is called,
  // after the name the program gave the function: the checker makes it, and a
  // message about it is about the function the program wrote.
  inline constexpr std::string_view crossing_suffix = ".host";

  // Where the loader's slots go, which is where every linker puts them.
  inline constexpr std::string_view got_section = ".got";

  // The registers a stub and the finder work in, which no argument is in.
  struct HostABI {
    // Where a stub hands the finder its slot.
    mc::Reg slot;
    // Where it hands the name, and where the finder answers.
    mc::Reg name;
    // What the finder calls `dlsym` through.
    mc::Reg through;
    // What a stub keeps across its call to the finder, where a call writes the
    // return address to a register: nothing where it pushes it.
    std::optional<mc::Reg> link;
    // How much room a stub makes around its call to the finder: enough to keep
    // the link register in, and to keep the stack as aligned at the call as
    // the convention has it.
    std::int64_t room = 16;
  };

  // The functions the host defines that the program calls, one per symbol, in
  // the order the module holds them.  A declaration nothing calls has no stub.
  std::vector<const ir::Function*> hosted(const ir::Module& module);

  // Emit the slots, the names, a stub per symbol and the finder.  `cconv` is the
  // system's convention, whose arguments the finder keeps; `abort` is the helper
  // a stub stops the program through.  Answers whether there was anything to
  // emit, which is whether the image needs `dlsym`.
  bool emit(mc::Assembler& asm_, const ir::Module& module, const HostABI& abi, const CallConvDesc& cconv,
            Messages& messages, const SourceManager* sources, std::uint64_t text_alignment, std::string_view abort);

} // namespace pl4g::target::host
