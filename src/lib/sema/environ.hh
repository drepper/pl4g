// The environment, and the table the compiler builds it into.
//
// A port of pypl4g's sema/environ.py.  `⎕environ` is a variable of the image
// holding one table of text standing for text, filled by a function generated
// here, which the entry point calls with the strings the runtime read off the
// stack before anything of the program runs.  Made on first ask.
#pragma once

#include <cstdint>

#include "ir/ir.hh"
#include "sema/tables.hh"


namespace pl4g::sema::environ {

  // How far into a variable's two strings what its name stands for begins.
  inline constexpr std::int64_t value_at = 3 * tables::word;

  // What the table holds: text standing for text.
  tables::Shape shape();
  // The type of the name: a dictionary nothing may put anything in.
  const ir::DictType* held_type(ir::Module& module);
  // The variable the table waits in, made once for the program.
  ir::GlobalVar* variable(ir::Module& module);
  // `__pl4g_environ_make(at, many)`: the table the strings come to.
  ir::Function* make_function(ir::Module& module, ir::GlobalVar* heap);

} // namespace pl4g::sema::environ
