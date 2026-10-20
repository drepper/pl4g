// The register model.
#include "mc/reg.hh"

#include <stdexcept>


namespace pl4g::mc {

  std::string VirtReg::repr() const
  {
    return "VirtReg(%v" + std::to_string(ident) + ")";
  }


  std::string Reg::name() const
  {
    if (phys_ != nullptr)
      return phys_->name;
    if (virt_ != nullptr)
      return virt_->repr();
    return "None";
  }


  bool interferes(Reg left, Reg right) noexcept
  {
    if (left.is_virtual() || right.is_virtual())
      return left == right;
    if (left.phys()->unit != right.phys()->unit)
      return false;
    auto [left_lo, left_hi] = left.phys()->byte_range();
    auto [right_lo, right_hi] = right.phys()->byte_range();
    return left_lo < right_hi && right_lo < left_hi;
  }


  const RegClass* RegisterInfo::add_class(std::string name, int full_bits, bool allocatable)
  {
    auto& made = class_store.emplace_back(RegClass{ name, next_class_index++, full_bits, allocatable });
    classes[name] = &made;
    return &made;
  }


  const RegUnit* RegisterInfo::add_unit(const RegClass* cls, int enc, std::string canonical)
  {
    auto& made = unit_store.emplace_back(RegUnit{ cls, enc, canonical });
    units[canonical] = &made;
    return &made;
  }


  const PhysReg* RegisterInfo::add_view(const RegUnit* unit, std::string name, int bits, int byte_off)
  {
    auto& made = reg_store.emplace_back(PhysReg{ unit, name, bits, byte_off });
    registers[name] = &made;
    return &made;
  }


  const VirtReg* RegisterInfo::new_virtual(const RegClass* cls, int bits, const PhysReg* hint, bool spillable)
  {
    return &virtual_store.emplace_back(VirtReg{ next_virtual++, cls, bits, hint, spillable });
  }


  const PhysReg* RegisterInfo::add_alias(std::string name, const PhysReg* reg)
  {
    registers[name] = reg;
    return reg;
  }


  const PhysReg* RegisterInfo::view(const RegUnit* unit, int bits, int byte_off) const
  {
    for (const auto& [_, reg] : registers)
      if (reg->unit == unit && reg->bits == bits && reg->byte_off == byte_off)
        return reg;
    throw std::out_of_range(unit->canonical + " has no " + std::to_string(bits) + "-bit view");
  }


  const PhysReg* RegisterInfo::widest(const RegUnit* unit)
  {
    if (auto it = widest_cache.find(unit); it != widest_cache.end())
      return it->second;
    const PhysReg* found = nullptr;
    for (const auto& [_, reg] : registers)
      if (reg->unit == unit && reg->byte_off == 0 && (found == nullptr || reg->bits > found->bits))
        found = reg;
    if (found == nullptr) [[unlikely]]
      throw std::out_of_range(unit->canonical + " has no view at all");
    widest_cache.emplace(unit, found);
    return found;
  }


  std::vector<const RegUnit*> RegisterInfo::members_of(const RegClass* cls) const
  {
    std::vector<const RegUnit*> found;
    for (const auto& [_, unit] : units)
      if (unit->cls == cls)
        found.push_back(unit);
    return found;
  }


  const RegClass* RegisterInfo::class_named(std::string_view name) const
  {
    auto found = classes.get(std::string(name));
    return found == nullptr ? nullptr : *found;
  }


  const RegUnit* RegisterInfo::unit_named(std::string_view name) const
  {
    auto found = units.get(std::string(name));
    return found == nullptr ? nullptr : *found;
  }


  const PhysReg* RegisterInfo::reg_named(std::string_view name) const
  {
    auto found = registers.get(std::string(name));
    return found == nullptr ? nullptr : *found;
  }

} // namespace pl4g::mc
