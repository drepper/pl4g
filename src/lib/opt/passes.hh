// The passes over the IR.
//
// The ones every build runs, whatever it asked for, because they are not
// optimizations but the shape the back ends and the interpreter need: calls
// whose answer nothing wants left out, functions nothing reaches dropped, a
// function calling itself last made a jump, an answer too large for registers
// put where the caller says, generators split at their stops, a text's
// allocator not carried where it is known, and blocks put on the edges a
// branch hands values over.  Ports of pypl4g's opt/passes, pass for pass.
#pragma once

#include "ir/ir.hh"


namespace pl4g::opt {

  bool drop_ignored_calls(ir::Module& module);
  bool drop_unreached(ir::Module& module);
  bool tail_calls(ir::Module& module);
  bool large_answers(ir::Module& module);
  bool coroutines(ir::Module& module);
  bool lean_values(ir::Module& module);
  bool split_edges(ir::Module& module);

  // Every pass a build at optimization level `level` runs, in order.  Only
  // the ones of level nought are here: the rest are optimizations, which an
  // interpreter has no use for.
  void run_pipeline(ir::Module& module, int level = 0);

} // namespace pl4g::opt
