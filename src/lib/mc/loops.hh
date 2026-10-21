// Which blocks are in a loop, and how deeply.  A port of pypl4g/mc/loops.py.
//
// An edge from u to v where v dominates u is a back edge, and the loop it
// closes is v with every block that reaches u without passing through v.  A
// block's depth is how many such loops hold it; one the entry cannot reach is
// at depth nought.
#pragma once

#include <cstddef>
#include <set>
#include <utility>
#include <vector>

#include "mc/machine.hh"


namespace pl4g::mc {

  // The edges of a graph: for each block, the blocks it reaches.
  using Edges = std::vector<std::vector<std::size_t>>;

  // Which blocks every path from the entry to each block passes through.
  std::vector<std::set<std::size_t>> dominators(const Edges& edges);
  // Every edge that goes back to a block it is inside, as (latch, header).
  std::vector<std::pair<std::size_t, std::size_t>> back_edges(const Edges& edges);
  // The blocks of the loop the back edge from `latch` to `header` closes.
  std::set<std::size_t> natural_loop(const Edges& edges, std::size_t latch, std::size_t header);
  // How many loops hold each block of `function`, in layout order.
  std::vector<int> loop_depths(const MachineFunction& function);

  // How many times a loop is taken to turn for each time it is entered.
  inline constexpr int turns = 10;

  // How often each block of `function` is taken to run for each call of it,
  // in layout order: ten turns of every loop and either way of each branch
  // alike, a way out of a loop taken once whichever turn takes it.  pypl4g's
  // `block_frequencies`.
  std::vector<double> block_frequencies(const MachineFunction& function);

} // namespace pl4g::mc
