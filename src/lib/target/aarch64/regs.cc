// The AArch64 register file.
#include "target/aarch64/regs.hh"

#include <stdexcept>
#include <string>


namespace pl4g::target::aarch64 {

  Registers::Registers()
  {
    gpr = info.add_class("gpr", 64);
    vec = info.add_class("vec", 128);
    pred = info.add_class("pred", 16);
    flags = info.add_class("flags", 32, false);
    for (int enc = 0; enc < 31; ++enc) {
      auto unit = info.add_unit(gpr, enc, "x" + std::to_string(enc));
      info.add_view(unit, "x" + std::to_string(enc), 64);
      info.add_view(unit, "w" + std::to_string(enc), 32);
    }
    // The zero register: reads as zero, writes are discarded.
    auto zero = info.add_unit(gpr, sp_enc, "xzr");
    xzr = info.add_view(zero, "xzr", 64);
    wzr = info.add_view(zero, "wzr", 32);
    // The stack pointer, which shares the zero register's number but not its
    // meaning.  Not allocatable, so a unit of its own outside the pool.
    auto stack = info.add_unit(gpr, sp_enc, "sp");
    sp = info.add_view(stack, "sp", 64);
    wsp = info.add_view(stack, "wsp", 32);
    for (int enc = 0; enc < 32; ++enc) {
      auto unit = info.add_unit(vec, enc, "v" + std::to_string(enc));
      for (auto [prefix, bits] : { std::pair{ "b", 8 }, { "h", 16 }, { "s", 32 }, { "d", 64 }, { "q", 128 } })
        info.add_view(unit, prefix + std::to_string(enc), bits);
    }
    for (int enc = 0; enc < 16; ++enc) {
      auto unit = info.add_unit(pred, enc, "p" + std::to_string(enc));
      info.add_view(unit, "p" + std::to_string(enc), 16);
    }
    // Named so that an instruction which writes them can say so.
    nzcv = info.add_view(info.add_unit(flags, 0, "nzcv"), "nzcv", 32);
    x0 = info.reg_named("x0");
    x1 = info.reg_named("x1");
    x8 = info.reg_named("x8");
    x19 = info.reg_named("x19");
    x29 = info.reg_named("x29");
    x30 = info.reg_named("x30");
    w0 = info.reg_named("w0");
    w8 = info.reg_named("w8");
    w19 = info.reg_named("w19");
  }


  Registers& regs()
  {
    static Registers made;
    return made;
  }


  const mc::PhysReg* reg(std::string_view name)
  {
    auto found = regs().info.reg_named(name);
    if (found == nullptr) [[unlikely]]
      throw std::logic_error("no AArch64 register " + std::string(name));
    return found;
  }

} // namespace pl4g::target::aarch64
