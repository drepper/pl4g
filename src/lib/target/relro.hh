// Making the `@[relro]` variables read-only once the constructors have run
// (pypl4g's target/relro.py).
//
// They are in a section of their own, which the image writer puts in a segment
// of its own and gives the bounds of to two symbols; the entry point calls the
// constructors and then asks for `mprotect(start, end - start, PROT_READ)`.
// What the kernel answers changes nothing: a page left writable is what there
// was, and the checker's rule is what keeps the program from writing it.
#pragma once

#include <string_view>

#include "ir/ir.hh"
#include "mc/asmbuilder.hh"
#include "target/allocator.hh"


namespace pl4g::target {

  // Where the variables the constructors alone write are put: not
  // `.data.rel.ro`, which is what the loader writes and PT_GNU_RELRO protects
  // before any constructor runs.
  inline constexpr std::string_view relro_section = ".pl4g.relro";

  // Whether `module` has a `@[relro]` variable, which is what asks for this.
  [[nodiscard]] bool relro_wanted(const ir::Module& module);

  // Emit, into the entry point, the call that makes the pages read-only: after
  // the constructors, before anything else.  The registers are the system
  // call's own, which nothing the entry point keeps across a call is in.
  void emit_protect_relro(mc::Assembler& asm_, const SyscallABI& abi);

} // namespace pl4g::target
