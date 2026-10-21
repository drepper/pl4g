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

#include <span>
#include <vector>
#include <string_view>

#include "ir/ir.hh"


namespace pl4g::opt {

  bool drop_ignored_calls(ir::Module& module);
  bool drop_unreached(ir::Module& module);
  bool tail_calls(ir::Module& module);
  bool large_answers(ir::Module& module);
  bool coroutines(ir::Module& module);
  bool lean_values(ir::Module& module);
  bool split_edges(ir::Module& module);

  // The optimizations, each a port of the pypl4g pass of the same name.
  bool inlining(ir::Module& module);
  bool constant_folding(ir::Module& module);
  bool same_values(ir::Module& module);
  bool pure_calls(ir::Module& module);
  bool simplify_cfg(ir::Module& module);
  bool facts(ir::Module& module);
  bool branch_conditions(ir::Module& module);
  bool known_arguments(ir::Module& module);
  bool dead_code(ir::Module& module);
  // Folding, simplifying and knowing the arguments in turn until none of
  // them changes anything: each opens work for the others.
  bool settle(ir::Module& module);

  // The names of the passes optimization level `level` runs, in order: what
  // pypl4g's `pipeline_for` answers.
  std::span<const std::string_view> pipeline_for(int level);

  // Every pass a build at optimization level `level` runs, in order, the
  // module verified after each that changed it where PL4G_VERIFY asks.
  void run_pipeline(ir::Module& module, int level = 0);

  // How long one pass took, as the driver's `--time-report` shows it.
  struct PassTiming {
    std::string_view name;
    double seconds = 0;
    bool changed = false;
  };

  // The same, verifying where `verifying` says -- what `--verify` asks for,
  // beside the environment -- and timing each pass into `timings`.
  void run_pipeline(ir::Module& module, int level, bool verifying, std::vector<PassTiming>* timings);

} // namespace pl4g::opt
