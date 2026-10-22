// Giving every value a register: linear scan over live ranges with holes,
// spilling the value cheapest to keep in the frame, by rewriting and starting
// again.  A port of pypl4g/mc/regalloc.py, choice for choice -- what it
// decides is what pypl4g decides, so the code that comes out is the same.
#pragma once

#include <cstdint>
#include <set>
#include <span>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "mc/machine.hh"
#include "util/ordered.hh"


namespace pl4g::mc {

  // Not even spilling made the values fit: one instruction wants more
  // registers at once than the target has.
  struct RegisterPressureError : std::runtime_error {
    std::string function;
    std::int64_t wanted;
    std::int64_t available;
    RegisterPressureError(std::string function, std::int64_t wanted, std::int64_t available);
  };


  // Where a register starts being wanted and where it stops, and the
  // stretches in between where it is.
  struct LiveRange {
    Reg reg;
    std::int64_t start = 0;
    std::int64_t end = 0;
    // What spilling it would cost: every read and write, each counted as ten
    // to the depth of the loop it stands in.
    std::int64_t cost = 0;
    std::vector<std::pair<std::int64_t, std::int64_t>> stretches;

    // Whether a stretch of one meets a stretch of the other.
    bool covers(const LiveRange& other) const noexcept;
    // How many points it holds a unit for, never fewer than one.
    std::int64_t length() const noexcept;
  };


  // What the allocator decided, for the dump and for the tests.
  struct Assignment {
    // The unit chosen for each virtual register, by its identity.
    OrderedMap<int, const RegUnit*> units;
    // Ranges in the order they were considered.
    std::vector<LiveRange> ranges;
    // Moves removed because both ends were put in one register.
    std::int64_t coalesced = 0;
    // The virtual registers sent to the frame, by identity, and their slots.
    OrderedMap<int, std::int64_t> spilled;
    // The frame reads and writes the spilling put in, by instruction: which
    // slot, and the register standing in.
    std::unordered_map<const MCInst*, std::pair<std::int64_t, const VirtReg*>> reloads;
    std::unordered_map<const MCInst*, std::pair<std::int64_t, const VirtReg*>> stores;
    // Frame reads made into nothing or into a move between registers.
    std::int64_t forwarded = 0;
    // The virtual registers put in the frame only across the calls they live
    // across, by identity, and their slots; and the reads and writes of the
    // frame that put them there.
    OrderedMap<int, std::int64_t> split;
    std::unordered_map<int, std::set<const MCInst*>> split_code;
    // What keeps the instructions named above alive, so that no other one
    // ever takes an address they had.
    Insts kept;
  };


  // The part of a target the allocator needs: how it reaches the frame, and
  // where it has one, how it moves a register to another.
  class SpillSelector {
  public:
    virtual ~SpillSelector() = default;
    // Instructions that write `source` to the frame slot at `slot`.
    virtual Insts select_spill(std::int64_t slot, Reg source, Span span) = 0;
    // Instructions that read the frame slot at `slot` into `destination`.
    virtual Insts select_reload(Reg destination, std::int64_t slot, Span span) = 0;
    // Whether `select_move` is there to ask (Python's `getattr`).
    virtual bool moves() const noexcept { return false; }
    // Instructions that place `source` into `destination`.
    virtual Insts select_move(Reg destination, const MCOperand& source, Span span);
  };


  // Which units may be given out, in the order they are preferred: one list
  // for every value, or one per class of register, by the class's name.
  struct AllocationOrder {
    OrderedMap<std::string, std::vector<const RegUnit*>> classes;
    bool by_class = false;

    // One list for every class.
    static AllocationOrder single(std::vector<const RegUnit*> units);
    // One list for each class named.
    static AllocationOrder per_class(OrderedMap<std::string, std::vector<const RegUnit*>> classes);
    // Every unit, in the order the classes and the lists give them.
    std::vector<const RegUnit*> preferred() const;
    bool empty() const noexcept { return classes.empty() || (! by_class && classes.begin()->second.empty()); }
  };


  // Every register an operand names, and whether the operand may write it.
  std::vector<std::pair<Reg, bool>> registers_of(const MCOperand& operand);
  // The registers an instruction writes and the registers it reads.
  std::pair<std::vector<Reg>, std::vector<Reg>> defs_and_uses(const MCInst& inst);


  // Assigns a register to every virtual register of a function.
  class LinearScan {
  public:
    LinearScan(RegisterInfo& registers, const AllocationOrder& order, SpillSelector& selector);

    // Assign every virtual register of `function` and rewrite it.
    Assignment run(MachineFunction& function);

  private:
    struct BlockFlow;
    using Key = const void*;

    const std::vector<const RegUnit*>& order_for(Reg reg) const;
    std::size_t widest_order() const noexcept;

    std::pair<std::vector<LiveRange>, std::vector<LiveRange>> live_ranges(MachineFunction& function);
    std::vector<std::int64_t> weights(const MachineFunction& function, std::span<const BlockFlow> flows);
    std::unordered_map<Key, std::vector<std::pair<std::int64_t, std::int64_t>>>
    stretches(const MachineFunction& function, std::span<const BlockFlow> flows,
              const OrderedMap<Key, Reg>& seen, bool virtual_);
    std::pair<std::vector<BlockFlow>, OrderedMap<Key, Reg>> flow(const MachineFunction& function);
    static Key key(Reg reg) noexcept;

    std::vector<const VirtReg*> assign(std::span<const LiveRange> ranges, std::span<const LiveRange> blocked,
                                       Assignment& assignment);
    const RegUnit* choose(const LiveRange& found, const std::set<const RegUnit*>& taken,
                          const Assignment& assignment) const;

    void spill(MachineFunction& function, std::span<const VirtReg* const> victims, Assignment& assignment);
    std::vector<std::pair<std::int64_t, double>> calls_across(const LiveRange* found) const;
    void split_at_calls(MachineFunction& function,
                        const OrderedMap<int, std::pair<const VirtReg*, std::set<std::int64_t>>>& split,
                        Assignment& assignment);
    std::vector<double> frequencies(const MachineFunction& function, std::span<const BlockFlow> flows);
    Insts with_spill_code(const Insts& insts, const OrderedMap<int, const VirtReg*>& wanted,
                          Assignment& assignment);
    void forward_reloads(MachineFunction& function, Assignment& assignment);
    InstRef substituted(const InstRef& inst, const OrderedMap<int, const VirtReg*>& replacement);
    MCOperand substitute_operand(const MCOperand& operand, const OrderedMap<int, const VirtReg*>& replacement);

    void rewrite(MachineFunction& function, const Assignment& assignment);
    InstRef rewrite_inst(const InstRef& inst, const Assignment& assignment);
    MCOperand rewrite_operand(const MCOperand& operand, const Assignment& assignment);
    const PhysReg* physical(const VirtReg* reg, int bits, const Assignment& assignment) const;
    std::int64_t drop_identity_moves(MachineFunction& function);
    static bool is_identity_move(const MCInst& inst) noexcept;

    RegisterInfo& registers;
    SpillSelector& selector;
    AllocationOrder orders;
    // What each virtual register is moved to or from, by identity, worked out
    // afresh for every round.
    std::unordered_map<int, std::vector<int>> partners;
    // How often each register is read and written, in the measure of
    // `block_frequencies`, and every call with how often it runs: of the round
    // being allocated.
    std::unordered_map<Key, double> often;
    std::vector<std::pair<std::int64_t, double>> calls;
  };

  // Assign every virtual register of `function`.
  Assignment allocate(MachineFunction& function, RegisterInfo& registers, const AllocationOrder& order,
                      SpillSelector& selector);

} // namespace pl4g::mc
