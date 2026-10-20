// Placing the program's variables in the image: a variable that may be
// assigned to in a section that may be written, one that may not in one that
// may not.  A port of pypl4g/target/globals.py.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

#include "ir/ir.hh"
#include "ir/layout.hh"
#include "mc/asmbuilder.hh"


namespace pl4g::target {

  inline constexpr std::string_view data_section = ".data";
  inline constexpr std::string_view rodata_section = ".rodata";

  // An address a variable's initial bytes hold: where in the variable, whose
  // address goes there, and how far into it.
  struct HeldAddress {
    std::uint64_t at = 0;
    const ir::Value* target = nullptr;
    std::int64_t inside = 0;
  };

  // Emit every variable of `module` into the section its type calls for, the
  // addresses of the program's ring settled first.
  void emit_globals(mc::Assembler& asm_, ir::Module& module, const ir::DataLayout& layout);

  // How `var` is laid out: the system's way where something outside the image
  // reads it.
  ir::DataLayout layout_for(const ir::GlobalVar* var, const ir::DataLayout& layout);

  // The name a variable is known by in the image.
  std::string symbol_of(const ir::GlobalVar* var);

  // The bytes a variable starts out holding; where a word in them is the
  // address of another variable, the word is nought and `addresses` is told.
  std::vector<std::uint8_t> initial_bytes(const ir::GlobalVar* var, const ir::DataLayout& layout,
                                          std::vector<HeldAddress>* addresses = nullptr);

} // namespace pl4g::target
