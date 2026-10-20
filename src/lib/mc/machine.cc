// The machine-level function.
#include "mc/machine.hh"

#include "mc/regalloc.hh"
#include "util/ordered.hh"


namespace pl4g::mc {

  std::int64_t FrameInfo::reserve(std::int64_t size, std::int64_t align)
  {
    taken = (taken + align - 1) / align * align;
    auto offset = taken;
    taken += size;
    return offset;
  }


  std::int64_t FrameInfo::allocate(std::optional<std::int64_t> size)
  {
    ++slots;
    auto wide = size ? std::max(*size, slot_size) : slot_size;
    return reserve(wide, wide);
  }


  std::int64_t FrameInfo::size() const noexcept
  {
    if (taken == 0)
      return 0;
    return (taken + alignment - 1) / alignment * alignment;
  }


  MachineBasicBlock* MachineFunction::add_block(std::optional<std::string> label)
  {
    auto made = std::make_unique<MachineBasicBlock>(
      label ? *label : ".L" + name + "_" + std::to_string(blocks.size()));
    blocks.push_back(std::move(made));
    return blocks.back().get();
  }


  Insts MachineFunction::instructions() const
  {
    Insts all;
    for (const auto& block : blocks)
      all.insert(all.end(), block->insts.begin(), block->insts.end());
    return all;
  }


  std::optional<std::size_t> MachineFunction::index_of(std::string_view label) const
  {
    for (std::size_t i = 0; i < blocks.size(); ++i)
      if (blocks[i]->label == label)
        return i;
    return std::nullopt;
  }


  std::vector<std::vector<std::size_t>> MachineFunction::successor_indices() const
  {
    std::vector<std::vector<std::size_t>> found;
    for (const auto& block : blocks) {
      std::vector<std::size_t> reached;
      for (const auto& label : block->successors)
        if (auto at = index_of(label))
          reached.push_back(*at);
      found.push_back(std::move(reached));
    }
    return found;
  }


  std::optional<std::string> MachineFunction::edges_are_complete() const
  {
    for (std::size_t index = 0; index < blocks.size(); ++index) {
      const auto& block = *blocks[index];
      if (! block.insts.empty()) {
        auto flags = block.insts.back()->desc->flags;
        if (has(flags, InstFlags::barrier) || has(flags, InstFlags::return_))
          continue;
      }
      if (index + 1 >= blocks.size())
        return name + ": the block '" + block.label + "' ends by going on, past the last block";
      const auto& following = blocks[index + 1]->label;
      if (std::ranges::find(block.successors, following) == block.successors.end())
        return name + ": the block '" + block.label + "' goes on into '" + following + "' without saying so";
    }
    return std::nullopt;
  }


  std::vector<const VirtReg*> MachineFunction::virtual_registers() const
  {
    OrderedMap<int, const VirtReg*> found;
    for (const auto& block : blocks)
      for (const auto& inst : block->insts)
        for (const auto& operand : inst->operands)
          if (auto reg = std::get_if<MCReg>(&operand); reg != nullptr && reg->reg.is_virtual())
            found[reg->reg.virt()->ident] = reg->reg.virt();
    std::vector<const VirtReg*> all;
    for (const auto& [_, reg] : found)
      all.push_back(reg);
    return all;
  }


  std::set<const RegUnit*> clobbered_units(const MachineFunction& function)
  {
    std::set<const RegUnit*> found;
    for (const auto& block : function.blocks)
      for (const auto& inst : block->insts)
        for (auto reg : defs_and_uses(*inst).first)
          if (reg.is_physical())
            found.insert(reg.phys()->unit);
    for (auto unit : function.preserved)
      found.erase(unit);
    return found;
  }


  bool physical_only(std::span<const Reg> registers) noexcept
  {
    for (auto reg : registers)
      if (! reg.is_physical())
        return false;
    return true;
  }

} // namespace pl4g::mc
