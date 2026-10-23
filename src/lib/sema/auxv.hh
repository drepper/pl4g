// The auxiliary vector, and the table the entry point reads it into.
//
// A port of pypl4g's sema/auxv.py.  `⎕auxv(TYPE)` reads a variable of the image
// holding a word for every type the kernel defines, the word of `AT_NULL`
// holding a bit for each type the kernel gave; a function generated here, which
// the entry point calls with the stack the process was started on before the
// constructors, fills it.  Made on first ask.
#pragma once

#include <cstdint>

#include "ir/builder.hh"
#include "ir/ir.hh"


namespace pl4g::sema::auxv {

  // The largest type the kernel defines, `AT_MINSIGSTKSZ`, and so how many
  // words the table has: one for every type up to it, nought among them.
  inline constexpr std::int64_t largest = 51;
  inline constexpr std::int64_t slots = largest + 1;

  // How far apart two words of the table are, and two of the vector's pairs.
  inline constexpr std::int64_t word = 8;
  inline constexpr std::int64_t pair = 2 * word;

  // The type of the table: a word for every type, the presence bits first.
  const ir::ArrayType* held_type();
  // The variable the table waits in, made once for the program.
  ir::GlobalVar* variable(ir::Module& module);
  // Where the table's first word is, as a place of one word.
  ir::Value* words_of(ir::Builder& builder, ir::GlobalVar* table);
  // `__pl4g_auxv_make(at)`: the vector, read off the stack into the table.
  ir::Function* make_function(ir::Module& module, ir::GlobalVar* table);

} // namespace pl4g::sema::auxv
