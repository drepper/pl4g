// Which blocks are in a loop, and how deeply.
#include "mc/loops.hh"

#include <algorithm>
#include <map>
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


  namespace {

    // The blocks the entry reaches, each after every block with a forward
    // edge to it: the reverse of the order a walk along forward edges leaves
    // them in.
    std::vector<std::size_t> forward_order(const Edges& edges,
                                           const std::set<std::pair<std::size_t, std::size_t>>& backward,
                                           const std::set<std::size_t>& reached)
    {
      std::vector<std::size_t> order;
      if (edges.empty())
        return order;
      std::set<std::size_t> seen{ 0 };
      std::vector<std::pair<std::size_t, std::size_t>> stack{ { 0, 0 } };
      while (! stack.empty()) {
        auto [at, next] = stack.back();
        std::vector<std::size_t> going;
        for (auto target : edges[at])
          if (! backward.contains({ at, target }))
            going.push_back(target);
        if (next < going.size()) {
          stack.back().second = next + 1;
          auto target = going[next];
          if (seen.insert(target).second)
            stack.emplace_back(target, 0);
          continue;
        }
        stack.pop_back();
        order.push_back(at);
      }
      std::ranges::reverse(order);
      std::erase_if(order, [&](std::size_t at) { return ! reached.contains(at); });
      return order;
    }

  } // anonymous namespace


  std::vector<double> block_frequencies(const MachineFunction& function)
  {
    auto edges = function.successor_indices();
    auto count = edges.size();
    if (count == 0)
      return {};
    auto reached = reachable(edges);
    auto latches = back_edges(edges);
    std::set<std::pair<std::size_t, std::size_t>> backward(latches.begin(), latches.end());
    // Every loop, its blocks, by its header, in the order the back edges come.
    std::vector<std::size_t> headers;
    std::map<std::size_t, std::set<std::size_t>> bodies;
    for (auto [latch, header] : latches) {
      if (! bodies.contains(header))
        headers.push_back(header);
      auto body = natural_loop(edges, latch, header);
      bodies[header].insert(body.begin(), body.end());
    }
    std::vector<std::vector<std::size_t>> holding(count);
    for (auto header : headers)
      for (auto at : bodies[header])
        holding[at].push_back(header);
    std::map<std::size_t, int> exits;
    for (auto header : headers) {
      int found = 0;
      for (auto at : bodies[header])
        for (auto target : edges[at])
          if (! bodies[header].contains(target))
            ++found;
      exits[header] = found;
    }
    std::vector<std::vector<std::size_t>> arriving(count);
    for (std::size_t at = 0; at < count; ++at)
      for (auto target : edges[at])
        if (! backward.contains({ at, target }))
          arriving[target].push_back(at);
    std::vector<double> share(count, 0.0);
    for (auto at : forward_order(edges, backward, reached)) {
      if (at == 0) {
        share[at] = 1.0;
        continue;
      }
      double total = 0.0;
      for (auto source : arriving[at]) {
        if (! reached.contains(source))
          continue;
        // Out of every loop the edge leaves: the outermost of them is entered
        // as often as its header's share says.
        std::optional<std::size_t> outer;
        for (auto header : holding[source])
          if (! bodies[header].contains(at) && (! outer || bodies[header].size() > bodies[*outer].size()))
            outer = header;
        if (outer) {
          total += share[*outer] / exits[*outer];
          continue;
        }
        int staying = 0;
        for (auto target : edges[source])
          if (std::ranges::all_of(holding[source],
                                  [&](std::size_t header) { return bodies[header].contains(target); }))
            ++staying;
        total += share[source] / staying;
      }
      share[at] = total;
    }
    std::vector<double> found(count);
    for (std::size_t at = 0; at < count; ++at) {
      double scale = 1.0;
      for (std::size_t d = 0; d < holding[at].size(); ++d)
        scale *= turns;
      found[at] = share[at] * scale;
    }
    return found;
  }

} // namespace pl4g::mc
