// The RISC-V 64-bit register file.
#include "target/riscv64/regs.hh"

#include <format>
#include <stdexcept>


namespace pl4g::target::riscv64 {

  Registers::Registers()
  {
    gpr = info.add_class("gpr", 64);
    fpr = info.add_class("fpr", 64);
    vec = info.add_class("vec", 128);
    for (int enc = 0; enc < 32; ++enc) {
      std::string abi(abi_names[std::size_t(enc)]);
      auto unit = info.add_unit(gpr, enc, abi);
      auto view = info.add_view(unit, abi, 64);
      info.add_alias(std::format("x{}", enc), view);
    }
    // The frame pointer has a second conventional name.
    info.add_alias("fp", *info.registers.get("s0"));
    for (int enc = 0; enc < 32; ++enc) {
      auto unit = info.add_unit(fpr, enc, std::format("f{}", enc));
      info.add_view(unit, std::format("f{}", enc), 64);
      info.add_view(unit, std::format("fs{}", enc), 32);
    }
    for (int enc = 0; enc < 32; ++enc) {
      auto unit = info.add_unit(vec, enc, std::format("v{}", enc));
      info.add_view(unit, std::format("v{}", enc), 128);
    }
    zero = reg("zero");
    ra = reg("ra");
    sp = reg("sp");
    fp = reg("s0");
    a0 = reg("a0");
    a1 = reg("a1");
    a7 = reg("a7");
    s1 = reg("s1");
  }


  const mc::PhysReg* Registers::reg(std::string_view name) const
  {
    auto found = info.registers.get(std::string(name));
    if (found == nullptr) [[unlikely]]
      throw std::logic_error(std::format("there is no register '{}'", name));
    return *found;
  }


  Registers& registers()
  {
    static Registers made;
    return made;
  }

} // namespace pl4g::target::riscv64
