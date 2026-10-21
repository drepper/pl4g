// The RISC-V 64-bit register file: pypl4g's target/riscv64/regs.py.
//
// There is no register that is only part of another -- narrower arithmetic is
// a different instruction, not a different view -- and no condition-code
// register.  Every register has two names, the number the encoding uses and
// the name the calling convention gives its role, and both name one object.
#pragma once

#include <array>
#include <string_view>
#include <vector>

#include "mc/reg.hh"


namespace pl4g::target::riscv64 {

  // The register file and the registers the back end names outright.  One for
  // the process, as pypl4g's module-level `INFO` is: the encoding table refers
  // to its classes and its registers.
  struct Registers {
    mc::RegisterInfo info;
    const mc::RegClass* gpr = nullptr;
    // The floating-point registers of the F and D extensions.
    const mc::RegClass* fpr = nullptr;
    // The vector registers of the V extension, at the smallest width it allows.
    const mc::RegClass* vec = nullptr;

    const mc::PhysReg* zero = nullptr;
    const mc::PhysReg* ra = nullptr;
    const mc::PhysReg* sp = nullptr;
    // The frame pointer.
    const mc::PhysReg* fp = nullptr;
    const mc::PhysReg* a0 = nullptr;
    const mc::PhysReg* a1 = nullptr;
    // Where a system call number is passed.
    const mc::PhysReg* a7 = nullptr;
    const mc::PhysReg* s1 = nullptr;
    // The one register a masked vector instruction reads its mask from;
    // nothing is ever given it.
    const mc::PhysReg* v0 = nullptr;
    // The register a function that works on runs keeps to itself: for the
    // address of a place in its frame no offset reaches, which a vector
    // register is set aside through, and for a frame too large for one.
    const mc::PhysReg* spare = nullptr;
    // Every register of the vector extension, in order.
    std::vector<const mc::PhysReg*> vector_registers;

    Registers();
    Registers(const Registers&) = delete;
    Registers& operator=(const Registers&) = delete;

    // The register called `name`, by either of its names.
    const mc::PhysReg* reg(std::string_view name) const;
  };

  // The one register file.
  Registers& registers();

  // The name the calling convention gives each register, by number.
  inline constexpr std::array<std::string_view, 32> abi_names{
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2", "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
  };

  // Registers a called function must leave as it found them.
  inline constexpr std::array<std::string_view, 13> callee_saved_names{
    "sp", "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11",
  };
  // Registers a call is free to change, the return address among them.
  inline constexpr std::array<std::string_view, 16> caller_saved_names{
    "ra", "t0", "t1", "t2", "t3", "t4", "t5", "t6", "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7",
  };

} // namespace pl4g::target::riscv64
