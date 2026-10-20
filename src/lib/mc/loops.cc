// Which blocks are in a loop, and how deeply.
#include "mc/loops.hh"

#include <optional>


namespace pl4g::mc {

  namespace {

    // Every block the entry reaches.
    std::set<std::size_t> reachable(const Edges& edges)
    {
      std::set<std::size_t> found;
      if (edges.empty())
        return found;
      found.insert(0);
      std::vector<std::size_t> stack{ 0 };
      while (! stack.empty()) {
        auto at = stack.back();
        stack.pop_back();
        for (auto reached : edges[at])
          if (found.insert(reached).second)
            stack.push_back(reached);
      }
      return found;
    }

    // For each block, the blocks that reach it in one step.
    std::vector<std::vector<std::size_t>> predecessors(const Edges& edges)
    {
      std::vector<std::vector<std::size_t>> found(edges.size());
      for (std::size_t at = 0; at < edges.size(); ++at)
        for (auto reached : edges[at])
          found[reached].push_back(at);
      return found;
    }

  } // anonymous namespace


  std::vector<std::set<std::size_t>> dominators(const Edges& edges)
  {
    auto reached = reachable(edges);
    auto before = predecessors(edges);
    std::vector<std::set<std::size_t>> doms(edges.size());
    for (std::size_t at = 0; at < edges.size(); ++at)
      if (reached.contains(at))
        doms[at] = reached;
    if (! reached.empty())
      doms[0] = { 0 };
    bool changed = true;
    while (changed) {
      changed = false;
      for (std::size_t at = 1; at < edges.size(); ++at) {
        if (! reached.contains(at))
          continue;
        std::optional<std::set<std::size_t>> common;
        for (auto one : before[at]) {
          if (! reached.contains(one))
            continue;
          if (! common)
            common = doms[one];
          else {
            std::set<std::size_t> both;
            for (auto x : *common)
              if (doms[one].contains(x))
                both.insert(x);
            common = std::move(both);
          }
        }
        auto found = common ? *common : std::set<std::size_t>{};
        found.insert(at);
        if (found != doms[at]) {
          doms[at] = std::move(found);
          changed = true;
        }
      }
    }
    return doms;
  }


  std::vector<std::pair<std::size_t, std::size_t>> back_edges(const Edges& edges)
  {
    auto doms = dominators(edges);
    std::vector<std::pair<std::size_t, std::size_t>> found;
    for (std::size_t at = 0; at < edges.size(); ++at)
      for (auto reached : edges[at])
        if (doms[at].contains(reached))
          found.emplace_back(at, reached);
    return found;
  }


  std::set<std::size_t> natural_loop(const Edges& edges, std::size_t latch, std::size_t header)
  {
    auto before = predecessors(edges);
    std::set<std::size_t> body{ header };
    std::vector<std::size_t> stack;
    if (latch != header) {
      body.insert(latch);
      stack.push_back(latch);
    }
    while (! stack.empty()) {
      auto at = stack.back();
      stack.pop_back();
      for (auto one : before[at])
        if (body.insert(one).second)
          stack.push_back(one);
    }
    return body;
  }


  std::vector<int> loop_depths(const MachineFunction& function)
  {
    auto edges = function.successor_indices();
    std::vector<int> depths(edges.size(), 0);
    for (auto [latch, header] : back_edges(edges))
      for (auto at : natural_loop(edges, latch, header))
        ++depths[at];
    return depths;
  }

} // namespace pl4g::mc
