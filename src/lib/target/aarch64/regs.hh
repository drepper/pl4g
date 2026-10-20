// The AArch64 register file.  A port of pypl4g/target/aarch64/regs.py.
//
// The same unit and view split the x86-64 back end uses describes this
// architecture too: a unit is storage, a register is a view onto it at a
// width.  Here the views are the 64-bit and 32-bit forms of each general
// register, and the byte through quadword forms of each vector register.
//
// Register number 31 means two things depending on the instruction: the zero
// register in most encodings and the stack pointer in a few.  They are two
// units that happen to share an encoding, because that is what they are --
// writing the stack pointer changes something, writing the zero register does
// not -- and because it keeps the interference rule honest.
#pragma once

#include <string_view>

#include "mc/reg.hh"


namespace pl4g::target::aarch64 {

  // The number that names the zero register or the stack pointer.
  inline constexpr int sp_enc = 31;

  // The register file and the registers the back end names outright.  One for
  // the whole process, as pypl4g's module-level one is.
  struct Registers {
    mc::RegisterInfo info;
    const mc::RegClass* gpr = nullptr;
    // The SIMD and floating-point registers, and the scalable-vector
    // predicates.
    const mc::RegClass* vec = nullptr;
    const mc::RegClass* pred = nullptr;
    const mc::RegClass* flags = nullptr;
    const mc::PhysReg* xzr = nullptr;
    const mc::PhysReg* wzr = nullptr;
    const mc::PhysReg* sp = nullptr;
    const mc::PhysReg* wsp = nullptr;
    // The condition flags.
    const mc::PhysReg* nzcv = nullptr;
    const mc::PhysReg* x0 = nullptr;
    const mc::PhysReg* x1 = nullptr;
    const mc::PhysReg* x8 = nullptr;
    const mc::PhysReg* x19 = nullptr;
    // The frame pointer, and the link register a call writes.
    const mc::PhysReg* x29 = nullptr;
    const mc::PhysReg* x30 = nullptr;
    const mc::PhysReg* w0 = nullptr;
    const mc::PhysReg* w8 = nullptr;
    const mc::PhysReg* w19 = nullptr;

    Registers();
    Registers(const Registers&) = delete;
    Registers& operator=(const Registers&) = delete;
  };

  // The registers.
  Registers& regs();

  // The register called `name`.
  const mc::PhysReg* reg(std::string_view name);

} // namespace pl4g::target::aarch64
