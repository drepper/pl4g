// The register model.
//
// A `RegUnit` is physical storage; a `PhysReg` is a view onto a unit at a
// given width and byte offset.  That one mechanism describes al/ah/ax/eax/rax
// and xmm0/ymm0/zmm0 without any new concept, and it makes the rule for whether
// two registers interfere -- same unit, overlapping byte range -- right for
// both.  A port of pypl4g/mc/reg.py.
//
// Every object here is made by a `RegisterInfo` and lives as long as it does;
// registers are compared by identity, as Python's `is` compares them.
#pragma once

#include <cstdint>
#include <deque>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "util/ordered.hh"


namespace pl4g::mc {

  // A family of registers that an allocator can treat alike.
  struct RegClass {
    std::string name;
    int index = 0;
    // The width of the widest view of a register of this class.
    int full_bits = 0;
    bool allocatable = true;
  };

  // One physical storage location.
  struct RegUnit {
    const RegClass* cls = nullptr;
    // The number the hardware encoding uses for this register.
    int enc = 0;
    std::string canonical;
  };

  // A view onto a register unit at a given width and offset.
  struct PhysReg {
    const RegUnit* unit = nullptr;
    std::string name;
    int bits = 0;
    // Offset in bytes of this view within the unit; 1 for ah, ch, dh, bh.
    int byte_off = 0;

    const RegClass* cls() const noexcept { return unit->cls; }
    int enc() const noexcept { return unit->enc; }
    // The half-open range of bytes of the unit this view covers.
    std::pair<int, int> byte_range() const noexcept { return { byte_off, byte_off + bits / 8 }; }
  };

  // A register the allocator has yet to assign.
  struct VirtReg {
    int ident = 0;
    const RegClass* cls = nullptr;
    int bits = 0;
    const PhysReg* hint = nullptr;
    // Whether the allocator may put this in the frame instead of a register.
    bool spillable = true;

    // `VirtReg(%vN)`, as Python's repr writes it.
    std::string repr() const;
  };


  // A physical or a virtual register, or none.  Compared by identity.
  class Reg {
  public:
    Reg() = default;
    Reg(const PhysReg* p) noexcept : phys_(p) {}
    Reg(const VirtReg* v) noexcept : virt_(v) {}

    bool is_none() const noexcept { return phys_ == nullptr && virt_ == nullptr; }
    explicit operator bool() const noexcept { return ! is_none(); }
    bool is_virtual() const noexcept { return virt_ != nullptr; }
    bool is_physical() const noexcept { return phys_ != nullptr; }
    const PhysReg* phys() const noexcept { return phys_; }
    const VirtReg* virt() const noexcept { return virt_; }

    const RegClass* cls() const noexcept { return phys_ != nullptr ? phys_->cls() : virt_->cls; }
    int bits() const noexcept { return phys_ != nullptr ? phys_->bits : virt_->bits; }
    // The physical name, or the virtual register's repr.
    std::string name() const;

    bool operator==(const Reg& other) const noexcept = default;

  private:
    const PhysReg* phys_ = nullptr;
    const VirtReg* virt_ = nullptr;
  };

  // Whether two registers name storage that overlaps.
  bool interferes(Reg left, Reg right) noexcept;


  // The register file of one target.
  class RegisterInfo {
  public:
    RegisterInfo() = default;
    RegisterInfo(const RegisterInfo&) = delete;
    RegisterInfo& operator=(const RegisterInfo&) = delete;

    // Register a new class of registers.
    const RegClass* add_class(std::string name, int full_bits, bool allocatable = true);
    // Register a new storage location.
    const RegUnit* add_unit(const RegClass* cls, int enc, std::string canonical);
    // Register a view onto `unit` and return it.
    const PhysReg* add_view(const RegUnit* unit, std::string name, int bits, int byte_off = 0);
    // Allocate a fresh virtual register.
    const VirtReg* new_virtual(const RegClass* cls, int bits, const PhysReg* hint = nullptr, bool spillable = true);
    // Register another name for a register that already exists.
    const PhysReg* add_alias(std::string name, const PhysReg* reg);

    // The view onto `unit` of the given width, at the given byte offset.
    const PhysReg* view(const RegUnit* unit, int bits, int byte_off = 0) const;
    // The widest view onto `unit` there is.
    const PhysReg* widest(const RegUnit* unit);
    // Every storage location of the class `cls`.
    std::vector<const RegUnit*> members_of(const RegClass* cls) const;

    const RegClass* class_named(std::string_view name) const;
    const RegUnit* unit_named(std::string_view name) const;
    const PhysReg* reg_named(std::string_view name) const;

    OrderedMap<std::string, const RegClass*> classes;
    OrderedMap<std::string, const RegUnit*> units;
    OrderedMap<std::string, const PhysReg*> registers;

  private:
    std::deque<RegClass> class_store;
    std::deque<RegUnit> unit_store;
    std::deque<PhysReg> reg_store;
    std::deque<VirtReg> virtual_store;
    int next_class_index = 0;
    int next_virtual = 0;
    std::unordered_map<const RegUnit*, const PhysReg*> widest_cache;
  };

} // namespace pl4g::mc
