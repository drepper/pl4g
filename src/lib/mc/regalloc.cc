// Giving every value a register.  See regalloc.hh, and pypl4g/mc/regalloc.py
// for the reasoning behind each choice, which this follows step for step.
#include "mc/regalloc.hh"

#include <algorithm>
#include <limits>
#include <set>

#include "mc/loops.hh"


namespace pl4g::mc {

  RegisterPressureError::RegisterPressureError(std::string function, std::int64_t wanted, std::int64_t available)
    : std::runtime_error("a function needing " + std::to_string(wanted)
                         + " values at once, which is more than the " + std::to_string(available)
                         + " registers this compiler has to give, and which spilling cannot help with"),
      function(std::move(function)),
      wanted(wanted),
      available(available)
  {
  }


  Insts SpillSelector::select_move(Reg, const MCOperand&, Span)
  {
    throw std::logic_error("this selector has no select_move");
  }


  AllocationOrder AllocationOrder::single(std::vector<const RegUnit*> units)
  {
    AllocationOrder made;
    made.classes[""] = std::move(units);
    return made;
  }


  AllocationOrder AllocationOrder::per_class(OrderedMap<std::string, std::vector<const RegUnit*>> classes)
  {
    AllocationOrder made;
    made.classes = std::move(classes);
    made.by_class = true;
    return made;
  }


  std::vector<const RegUnit*> AllocationOrder::preferred() const
  {
    std::vector<const RegUnit*> all;
    for (const auto& [_, units] : classes)
      all.insert(all.end(), units.begin(), units.end());
    return all;
  }


  namespace {

    // The stretches of a range, or its hull where none were worked out.
    std::vector<std::pair<std::int64_t, std::int64_t>> parts(const LiveRange& range)
    {
      if (range.stretches.empty())
        return { { range.start, range.end } };
      return range.stretches;
    }

    // Python's `_victim_order` compared: the price -- cost per point -- and
    // where two are worth the same, the one that reaches further first.
    bool cheaper(const LiveRange& a, const LiveRange& b) noexcept
    {
      __int128 left = __int128(a.cost) * b.length();
      __int128 right = __int128(b.cost) * a.length();
      if (left != right)
        return left < right;
      return -a.end < -b.end;
    }

    // Stretches in order, with those that meet or touch made one.
    std::vector<std::pair<std::int64_t, std::int64_t>> merged(std::vector<std::pair<std::int64_t, std::int64_t>> given)
    {
      std::ranges::sort(given);
      std::vector<std::pair<std::int64_t, std::int64_t>> out;
      for (auto [a, b] : given)
        if (! out.empty() && a <= out.back().second + 1)
          out.back().second = std::max(out.back().second, b);
        else
          out.emplace_back(a, b);
      return out;
    }

    std::int64_t power_of_ten(int depth)
    {
      std::int64_t value = 1;
      for (int i = 0; i < depth; ++i)
        value *= 10;
      return value;
    }

  } // anonymous namespace


  bool LiveRange::covers(const LiveRange& other) const noexcept
  {
    if (! (start <= other.end && other.start <= end))
      return false;
    auto mine = parts(*this);
    auto theirs = parts(other);
    std::size_t i = 0;
    std::size_t j = 0;
    while (i < mine.size() && j < theirs.size()) {
      auto [a, b] = mine[i];
      auto [c, d] = theirs[j];
      if (a <= d && c <= b)
        return true;
      if (b < d)
        ++i;
      else
        ++j;
    }
    return false;
  }


  std::int64_t LiveRange::length() const noexcept
  {
    std::int64_t total = 0;
    for (auto [a, b] : parts(*this))
      total += b - a + 1;
    return total;
  }


  // One block's place in the layout and in the graph, and what lives there.
  struct LinearScan::BlockFlow {
    std::int64_t first = 0;
    std::int64_t last = 0;
    std::vector<std::size_t> successors;
    std::set<Key> uses;
    std::set<Key> kills;
    std::set<Key> live_in;
    std::set<Key> live_out;

    bool empty() const noexcept { return last < first; }
    std::int64_t entry_point() const noexcept { return first * 2; }
    std::int64_t exit_point() const noexcept { return last * 2 + 1; }
  };


  std::vector<std::pair<Reg, bool>> registers_of(const MCOperand& operand)
  {
    if (auto reg = std::get_if<MCReg>(&operand))
      return { { reg->reg, true } };
    if (auto mem = std::get_if<MCMem>(&operand)) {
      std::vector<std::pair<Reg, bool>> found;
      if (mem->base)
        found.emplace_back(mem->base, false);
      if (mem->index)
        found.emplace_back(mem->index, false);
      return found;
    }
    return {};
  }


  std::pair<std::vector<Reg>, std::vector<Reg>> defs_and_uses(const MCInst& inst)
  {
    std::vector<Reg> defs;
    std::vector<Reg> uses;
    for (auto reg : inst.desc->implicit_defs)
      defs.emplace_back(reg);
    defs.insert(defs.end(), inst.clobbers.begin(), inst.clobbers.end());
    for (auto reg : inst.desc->implicit_uses)
      uses.emplace_back(reg);
    uses.insert(uses.end(), inst.reads.begin(), inst.reads.end());
    for (std::size_t index = 0; index < inst.operands.size(); ++index) {
      auto role = inst.desc->role_of(index);
      for (auto [reg, writable] : registers_of(inst.operands[index])) {
        if (writable && role != OperandRole::use)
          defs.push_back(reg);
        if (! writable || role != OperandRole::def)
          uses.push_back(reg);
      }
    }
    return { std::move(defs), std::move(uses) };
  }


  LinearScan::LinearScan(RegisterInfo& registers, const AllocationOrder& order, SpillSelector& selector)
    : registers(registers), selector(selector), orders(order)
  {
  }


  const std::vector<const RegUnit*>& LinearScan::order_for(Reg reg) const
  {
    static const std::vector<const RegUnit*> none;
    if (orders.classes.size() == 1 && orders.classes.contains(""))
      return orders.classes.begin()->second;
    auto found = orders.classes.get(reg.cls()->name);
    return found == nullptr ? none : *found;
  }


  std::size_t LinearScan::widest_order() const noexcept
  {
    std::size_t widest = 0;
    for (const auto& [_, units] : orders.classes)
      widest = std::max(widest, units.size());
    return widest;
  }


  Assignment LinearScan::run(MachineFunction& function)
  {
    Assignment assignment;
    for (std::size_t round = 0; round < widest_order() + 1; ++round) {
      auto [ranges, blocked] = live_ranges(function);
      assignment.ranges = ranges;
      auto crowded = assign(ranges, blocked, assignment);
      if (crowded.empty()) {
        forward_reloads(function, assignment);
        rewrite(function, assignment);
        assignment.coalesced = drop_identity_moves(function);
        return assignment;
      }
      spill(function, crowded, assignment);
    }
    throw RegisterPressureError(function.name, std::int64_t(assignment.spilled.size()) + 1,
                                std::int64_t(widest_order()));
  }


  // -- liveness ---------------------------------------------------------------

  LinearScan::Key LinearScan::key(Reg reg) noexcept
  {
    return reg.is_virtual() ? static_cast<Key>(reg.virt()) : static_cast<Key>(reg.phys()->unit);
  }


  std::pair<std::vector<LinearScan::BlockFlow>, OrderedMap<LinearScan::Key, Reg>>
  LinearScan::flow(const MachineFunction& function)
  {
    auto reachable = function.successor_indices();
    std::vector<BlockFlow> flows;
    OrderedMap<Key, Reg> seen;
    std::int64_t position = 0;
    for (std::size_t b = 0; b < function.blocks.size(); ++b) {
      const auto& block = *function.blocks[b];
      BlockFlow made;
      made.first = position;
      for (const auto& inst : block.insts) {
        auto [defs, used] = defs_and_uses(*inst);
        for (auto reg : used) {
          auto k = key(reg);
          seen.emplace(k, reg);
          if (! made.kills.contains(k))
            made.uses.insert(k);
        }
        for (auto reg : defs) {
          auto k = key(reg);
          seen.emplace(k, reg);
          made.kills.insert(k);
        }
        ++position;
      }
      made.last = position - 1;
      made.successors = reachable[b];
      flows.push_back(std::move(made));
    }
    return { std::move(flows), std::move(seen) };
  }


  namespace {

    // Settle what is live where each block begins and ends.
    template<typename Flow>
    void propagate(std::vector<Flow>& flows)
    {
      bool changed = true;
      while (changed) {
        changed = false;
        for (auto flow = flows.rbegin(); flow != flows.rend(); ++flow) {
          std::set<const void*> leaving;
          for (auto at : flow->successors)
            leaving.insert(flows[at].live_in.begin(), flows[at].live_in.end());
          if (leaving != flow->live_out) {
            flow->live_out = leaving;
            changed = true;
          }
          auto arriving = flow->uses;
          for (auto k : leaving)
            if (! flow->kills.contains(k))
              arriving.insert(k);
          if (arriving != flow->live_in) {
            flow->live_in = std::move(arriving);
            changed = true;
          }
        }
      }
    }

  } // anonymous namespace


  std::pair<std::vector<LiveRange>, std::vector<LiveRange>> LinearScan::live_ranges(MachineFunction& function)
  {
    auto [flows, seen] = flow(function);
    propagate(flows);
    auto weight_of = weights(function, flows);
    std::unordered_map<Key, std::int64_t> first;
    std::unordered_map<Key, std::int64_t> last;
    std::unordered_map<Key, std::int64_t> cost;

    auto touch = [&](Key k, std::int64_t point) {
      if (auto it = first.find(k); it == first.end() || point < it->second)
        first[k] = point;
      if (auto it = last.find(k); it == last.end() || point > it->second)
        last[k] = point;
    };

    std::int64_t position = 0;
    for (const auto& block : function.blocks)
      for (const auto& inst : block->insts) {
        auto [defs, uses] = defs_and_uses(*inst);
        auto weight = weight_of[std::size_t(position)];
        for (auto reg : uses) {
          auto k = key(reg);
          touch(k, position * 2);
          cost[k] += weight;
        }
        for (auto reg : defs) {
          auto k = key(reg);
          touch(k, position * 2 + 1);
          cost[k] += weight;
        }
        ++position;
      }
    for (const auto& one : flows) {
      if (one.empty())
        continue;
      for (auto k : one.live_in)
        touch(k, one.entry_point());
      for (auto k : one.live_out)
        touch(k, one.exit_point());
    }
    auto stretched = stretches(function, flows, seen, true);
    std::vector<LiveRange> virtual_;
    for (const auto& [k, reg] : seen)
      if (reg.is_virtual()) {
        LiveRange range;
        range.reg = reg;
        range.start = first.at(k);
        range.end = last.at(k);
        if (auto it = cost.find(k); it != cost.end())
          range.cost = it->second;
        if (auto it = stretched.find(k); it != stretched.end())
          range.stretches = merged(it->second);
        virtual_.push_back(std::move(range));
      }
    std::vector<LiveRange> physical_;
    for (const auto& [k, found] : stretches(function, flows, seen, false))
      for (auto [a, b] : found) {
        LiveRange range;
        range.reg = seen.get(k) == nullptr ? Reg() : *seen.get(k);
        range.start = a;
        range.end = b;
        physical_.push_back(std::move(range));
      }
    std::ranges::stable_sort(virtual_, [](const LiveRange& a, const LiveRange& b) {
      return std::pair(a.start, a.end) < std::pair(b.start, b.end);
    });
    return { std::move(virtual_), std::move(physical_) };
  }


  std::vector<std::int64_t> LinearScan::weights(const MachineFunction& function, std::span<const BlockFlow> flows)
  {
    auto depths = loop_depths(function);
    std::vector<std::int64_t> found;
    for (std::size_t i = 0; i < flows.size() && i < depths.size(); ++i) {
      if (flows[i].empty())
        continue;
      found.insert(found.end(), std::size_t(flows[i].last - flows[i].first + 1), power_of_ten(depths[i]));
    }
    return found;
  }


  std::unordered_map<LinearScan::Key, std::vector<std::pair<std::int64_t, std::int64_t>>>
  LinearScan::stretches(const MachineFunction& function, std::span<const BlockFlow> flows,
                        const OrderedMap<Key, Reg>& seen, bool virtual_)
  {
    std::set<Key> wanted;
    for (const auto& [k, reg] : seen)
      if (reg.is_virtual() == virtual_)
        wanted.insert(k);
    std::unordered_map<Key, std::vector<std::pair<std::int64_t, std::int64_t>>> found;
    for (std::size_t b = 0; b < function.blocks.size() && b < flows.size(); ++b) {
      const auto& block = *function.blocks[b];
      const auto& one = flows[b];
      if (one.empty())
        continue;
      OrderedMap<Key, std::int64_t> start;
      std::unordered_map<Key, std::int64_t> reached;
      for (auto k : one.live_in)
        if (wanted.contains(k)) {
          start[k] = one.entry_point();
          reached[k] = one.entry_point();
        }
      for (std::size_t at = 0; at < block.insts.size(); ++at) {
        auto position = one.first + std::int64_t(at);
        auto [defs, uses] = defs_and_uses(*block.insts[at]);
        std::set<Key> read;
        for (auto reg : uses)
          if (reg.is_virtual() == virtual_)
            read.insert(key(reg));
        std::set<Key> written;
        for (auto reg : defs)
          if (reg.is_virtual() == virtual_)
            written.insert(key(reg));
        for (auto k : read) {
          start.setdefault(k, position * 2);
          reached[k] = position * 2;
        }
        for (auto k : written) {
          if (! read.contains(k)) {
            // What it held before this is gone, and what it holds from here
            // is another value.
            if (auto began = start.get(k))
              found[k].emplace_back(*began, reached[k]);
            start[k] = position * 2 + 1;
          }
          reached[k] = position * 2 + 1;
        }
      }
      for (const auto& [k, began] : start) {
        auto end = one.live_out.contains(k) ? one.exit_point() : reached[k];
        found[k].emplace_back(began, end);
      }
    }
    return found;
  }


  // -- assignment -------------------------------------------------------------

  std::vector<const VirtReg*> LinearScan::assign(std::span<const LiveRange> ranges, std::span<const LiveRange> blocked,
                                                 Assignment& assignment)
  {
    std::vector<std::pair<const LiveRange*, const RegUnit*>> active;
    std::vector<const VirtReg*> crowded;
    for (const auto& found : ranges) {
      std::erase_if(active, [&](const auto& pair) { return pair.first->end < found.start; });
      std::vector<std::pair<const LiveRange*, const RegUnit*>> meeting;
      for (const auto& pair : active)
        if (pair.first->covers(found))
          meeting.push_back(pair);
      auto fixed = blocked_by(found, blocked);
      std::set<const RegUnit*> taken = fixed;
      for (const auto& [_, unit] : meeting)
        taken.insert(unit);
      auto unit = choose(found, taken);
      auto reg = found.reg.virt();
      if (unit != nullptr) {
        assignment.units[reg->ident] = unit;
        active.emplace_back(&found, unit);
        continue;
      }
      // Only a value that may be spilled can be the one given up, and giving
      // it up has to leave its unit free for this one.
      std::unordered_map<const RegUnit*, int> holders;
      for (const auto& [_, held] : meeting)
        ++holders[held];
      const auto& usable_list = order_for(found.reg);
      std::set<const RegUnit*> usable(usable_list.begin(), usable_list.end());
      const std::pair<const LiveRange*, const RegUnit*>* cheapest = nullptr;
      for (const auto& pair : meeting)
        if (pair.first->reg.is_virtual() && pair.first->reg.virt()->spillable && holders[pair.second] == 1
            && ! fixed.contains(pair.second) && usable.contains(pair.second)
            && (cheapest == nullptr || cheaper(*pair.first, *cheapest->first)))
          cheapest = &pair;
      if (cheapest == nullptr) {
        crowded.push_back(reg);
        continue;
      }
      if (reg->spillable && cheaper(found, *cheapest->first)) {
        crowded.push_back(reg);
        continue;
      }
      auto [victim, freed] = *cheapest;
      crowded.push_back(victim->reg.virt());
      assignment.units.erase(victim->reg.virt()->ident);
      std::erase_if(active, [&](const auto& pair) { return pair.first == victim && pair.second == freed; });
      assignment.units[reg->ident] = freed;
      active.emplace_back(&found, freed);
    }
    return crowded;
  }


  std::set<const RegUnit*> LinearScan::blocked_by(const LiveRange& found, std::span<const LiveRange> blocked) const
  {
    std::set<const RegUnit*> units;
    for (const auto& other : blocked)
      if (other.covers(found) && other.reg.is_physical())
        units.insert(other.reg.phys()->unit);
    return units;
  }


  const RegUnit* LinearScan::choose(const LiveRange& found, const std::set<const RegUnit*>& taken) const
  {
    const auto& order = order_for(found.reg);
    if (found.reg.is_virtual() && found.reg.virt()->hint != nullptr) {
      auto hinted = found.reg.virt()->hint->unit;
      if (! taken.contains(hinted) && std::ranges::find(order, hinted) != order.end())
        return hinted;
    }
    for (auto unit : order)
      if (! taken.contains(unit))
        return unit;
    return nullptr;
  }


  // -- spilling ---------------------------------------------------------------

  void LinearScan::spill(MachineFunction& function, std::span<const VirtReg* const> victims, Assignment& assignment)
  {
    OrderedMap<int, std::int64_t> slots;
    for (auto victim : victims) {
      if (! victim->spillable) [[unlikely]]
        throw RegisterPressureError(function.name, std::int64_t(victims.size()),
                                    std::int64_t(order_for(Reg(victim)).size()));
      if (assignment.spilled.contains(victim->ident))
        continue;
      slots[victim->ident] = function.frame.allocate(victim->bits / 8);
    }
    for (const auto& [ident, slot] : slots)
      assignment.spilled[ident] = slot;
    OrderedMap<int, const VirtReg*> wanted;
    for (auto victim : victims)
      if (assignment.spilled.contains(victim->ident))
        wanted[victim->ident] = victim;
    for (auto& block : function.blocks)
      block->insts = with_spill_code(block->insts, wanted, assignment);
  }


  Insts LinearScan::with_spill_code(const Insts& insts, const OrderedMap<int, const VirtReg*>& wanted,
                                    Assignment& assignment)
  {
    Insts out;
    // For each spilled value in a register just now, which register and which
    // instruction last touched it.
    std::unordered_map<int, std::pair<const VirtReg*, std::size_t>> held;
    for (std::size_t index = 0; index < insts.size(); ++index) {
      const auto& inst = insts[index];
      auto [defs, uses] = defs_and_uses(*inst);
      OrderedMap<int, const VirtReg*> touched;
      for (const auto* list : { &defs, &uses })
        for (auto reg : *list)
          if (reg.is_virtual() && wanted.contains(reg.virt()->ident))
            touched[reg.virt()->ident] = reg.virt();
      if (touched.empty()) {
        out.push_back(inst);
        continue;
      }
      OrderedMap<int, const VirtReg*> replacement;
      Insts before;
      Insts after;
      for (const auto& [ident, victim] : touched) {
        auto slot = *assignment.spilled.get(ident);
        auto names = [&](const std::vector<Reg>& list) {
          return std::ranges::any_of(list, [&](Reg r) { return r.is_virtual() && r.virt()->ident == ident; });
        };
        bool reads = names(uses);
        bool writes = names(defs);
        auto carried = held.find(ident);
        const VirtReg* fresh;
        if (reads && ! writes && carried != held.end() && index > 0 && carried->second.second == index - 1)
          fresh = carried->second.first;
        else {
          fresh = registers.new_virtual(victim->cls, victim->bits);
          if (reads) {
            auto loaded = selector.select_reload(Reg(fresh), slot, inst->span);
            if (loaded.size() == 1) {
              assignment.reloads[loaded[0].get()] = { slot, fresh };
              assignment.kept.push_back(loaded[0]);
            }
            before.insert(before.end(), loaded.begin(), loaded.end());
          }
        }
        replacement[ident] = fresh;
        if (writes) {
          auto stored = selector.select_spill(slot, Reg(fresh), inst->span);
          if (stored.size() == 1) {
            assignment.stores[stored[0].get()] = { slot, fresh };
            assignment.kept.push_back(stored[0]);
          }
          after.insert(after.end(), stored.begin(), stored.end());
        }
        held[ident] = { fresh, index };
      }
      out.insert(out.end(), before.begin(), before.end());
      out.push_back(substituted(inst, replacement));
      out.insert(out.end(), after.begin(), after.end());
    }
    return out;
  }


  void LinearScan::forward_reloads(MachineFunction& function, Assignment& assignment)
  {
    bool moves = selector.moves();
    auto forget = [](OrderedMap<std::int64_t, const RegUnit*>& held, const RegUnit* unit) {
      std::vector<std::int64_t> gone;
      for (const auto& [slot, u] : held)
        if (u == unit)
          gone.push_back(slot);
      for (auto slot : gone)
        held.erase(slot);
    };
    for (auto& block : function.blocks) {
      OrderedMap<std::int64_t, const RegUnit*> held;
      Insts out;
      for (const auto& inst : block->insts) {
        auto loaded_it = assignment.reloads.find(inst.get());
        const std::pair<std::int64_t, const VirtReg*>* loaded =
          loaded_it == assignment.reloads.end() ? nullptr : &loaded_it->second;
        if (loaded != nullptr) {
          auto [slot, fresh] = *loaded;
          auto target_ptr = assignment.units.get(fresh->ident);
          auto source_ptr = held.get(slot);
          if (target_ptr != nullptr && source_ptr != nullptr) {
            auto target = *target_ptr;
            auto source = *source_ptr;
            ++assignment.forwarded;
            if (source != target) {
              if (! moves)
                out.push_back(inst);
              else {
                auto made = selector.select_move(Reg(fresh), MCReg{ Reg(registers.view(source, fresh->bits)) },
                                                 inst->span);
                out.insert(out.end(), made.begin(), made.end());
              }
            }
            forget(held, target);
            held[slot] = target;
            continue;
          }
        }
        out.push_back(inst);
        for (auto reg : defs_and_uses(*inst).first) {
          const RegUnit* unit = nullptr;
          if (reg.is_virtual()) {
            if (auto found = assignment.units.get(reg.virt()->ident))
              unit = *found;
          } else
            unit = reg.phys()->unit;
          if (unit != nullptr)
            forget(held, unit);
        }
        if (loaded != nullptr)
          if (auto unit = assignment.units.get(loaded->second->ident))
            held[loaded->first] = *unit;
        if (auto stored = assignment.stores.find(inst.get()); stored != assignment.stores.end())
          if (auto unit = assignment.units.get(stored->second.second->ident))
            held[stored->second.first] = *unit;
      }
      block->insts = std::move(out);
    }
  }


  InstRef LinearScan::substituted(const InstRef& inst, const OrderedMap<int, const VirtReg*>& replacement)
  {
    auto made = std::make_shared<MCInst>(*inst);
    for (auto& operand : made->operands)
      operand = substitute_operand(operand, replacement);
    return made;
  }


  MCOperand LinearScan::substitute_operand(const MCOperand& operand, const OrderedMap<int, const VirtReg*>& replacement)
  {
    auto replaced = [&](Reg reg) -> Reg {
      if (reg.is_virtual())
        if (auto found = replacement.get(reg.virt()->ident))
          return Reg(*found);
      return reg;
    };
    if (auto reg = std::get_if<MCReg>(&operand)) {
      if (reg->reg.is_virtual() && replacement.contains(reg->reg.virt()->ident))
        return MCReg{ replaced(reg->reg), reg->bits };
      return operand;
    }
    if (auto mem = std::get_if<MCMem>(&operand)) {
      MCMem changed = *mem;
      changed.base = replaced(mem->base);
      changed.index = replaced(mem->index);
      return changed;
    }
    return operand;
  }


  // -- rewriting --------------------------------------------------------------

  void LinearScan::rewrite(MachineFunction& function, const Assignment& assignment)
  {
    for (auto& block : function.blocks)
      for (auto& inst : block->insts)
        inst = rewrite_inst(inst, assignment);
  }


  InstRef LinearScan::rewrite_inst(const InstRef& inst, const Assignment& assignment)
  {
    std::vector<MCOperand> operands;
    for (const auto& operand : inst->operands)
      operands.push_back(rewrite_operand(operand, assignment));
    if (operands == inst->operands)
      return inst;
    auto made = std::make_shared<MCInst>(*inst);
    made->operands = std::move(operands);
    return made;
  }


  MCOperand LinearScan::rewrite_operand(const MCOperand& operand, const Assignment& assignment)
  {
    if (auto reg = std::get_if<MCReg>(&operand)) {
      if (reg->reg.is_virtual())
        return MCReg{ Reg(physical(reg->reg.virt(), reg->width(), assignment)) };
      return operand;
    }
    if (auto mem = std::get_if<MCMem>(&operand)) {
      if (! mem->base.is_virtual() && ! mem->index.is_virtual())
        return operand;
      MCMem changed = *mem;
      if (mem->base.is_virtual())
        changed.base = Reg(physical(mem->base.virt(), mem->base.virt()->bits, assignment));
      if (mem->index.is_virtual())
        changed.index = Reg(physical(mem->index.virt(), mem->index.virt()->bits, assignment));
      return changed;
    }
    return operand;
  }


  const PhysReg* LinearScan::physical(const VirtReg* reg, int bits, const Assignment& assignment) const
  {
    auto unit = assignment.units.get(reg->ident);
    if (unit == nullptr) [[unlikely]]
      throw std::out_of_range("no register was assigned to " + reg->repr());
    return registers.view(*unit, bits);
  }


  std::int64_t LinearScan::drop_identity_moves(MachineFunction& function)
  {
    std::int64_t removed = 0;
    for (auto& block : function.blocks) {
      Insts kept;
      for (auto& inst : block->insts)
        if (is_identity_move(*inst))
          ++removed;
        else
          kept.push_back(inst);
      block->insts = std::move(kept);
    }
    return removed;
  }


  bool LinearScan::is_identity_move(const MCInst& inst) noexcept
  {
    if (! has(inst.desc->flags, InstFlags::move) || inst.operands.size() != 2)
      return false;
    auto first = std::get_if<MCReg>(&inst.operands[0]);
    auto second = std::get_if<MCReg>(&inst.operands[1]);
    if (first == nullptr || second == nullptr)
      return false;
    return first->reg == second->reg && first->width() == second->width();
  }


  Assignment allocate(MachineFunction& function, RegisterInfo& registers, const AllocationOrder& order,
                      SpillSelector& selector)
  {
    return LinearScan(registers, order, selector).run(function);
  }

} // namespace pl4g::mc
