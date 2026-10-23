// What a position-independent image does before anything else: add where it
// was loaded to every eight-byte word that holds an address.
//
// The image is linked at address nought, so an address in it is how far into
// the image the thing is; the kernel puts the image anywhere, and the words the
// table names are made true by adding where that was.  The table is ELF's
// SHT_RELR encoding (elf/relr.hh), which the image writer makes and gives the
// symbols below the addresses of.  Nothing else is relocated: the code of every
// back end reaches what it reaches relative to where it is.
#pragma once

#include <span>
#include <string_view>

#include "mc/asmbuilder.hh"
#include "target/allocator.hh"


namespace pl4g::target {

  // The function that applies the table, and the symbols the image writer
  // defines for it: the image's first byte, and where the table begins and ends.
  inline constexpr std::string_view relocate_symbol = "__pl4g_relocate";
  inline constexpr std::string_view image_start_symbol = "__pl4g_image_start";
  inline constexpr std::string_view relr_start_symbol = "__pl4g_relr_start";
  inline constexpr std::string_view relr_end_symbol = "__pl4g_relr_end";
  // And the pages that are read-only but for being relocated, which are made
  // read-only again once they are: nothing where both are the same.
  inline constexpr std::string_view relro_start_symbol = "__pl4g_relro_start";
  inline constexpr std::string_view relro_end_symbol = "__pl4g_relro_end";

  // How many registers a target names for `emit_relocate`, all of them free
  // to destroy where the entry point calls it: before it has done anything.
  inline constexpr std::size_t relocate_scratch = 7;

  // Emit the function that walks the table and adds the load address to each
  // word it names, and then, where `protects` says, asks the kernel, through
  // `abi`, to make what was read-only but for that read-only again -- which
  // the entry point does instead, after the constructors, where the same
  // pages hold what they write (target/relro.hh).  What it works in is the
  // allocator's, the width of the first of `scratch`, which are the registers
  // it may have.
  void emit_relocate(mc::Assembler& asm_, std::span<const mc::Reg> scratch, const SyscallABI& abi,
                     bool protects = true);

} // namespace pl4g::target
