// The x86-64 register file: pypl4g's target/x86_64/regs.py.
//
// Every register is a view onto a unit, so the width variants of a
// general-purpose register and those of a vector register are described by
// the same mechanism.  The general-purpose class holds thirty-two units from
// the start -- the sixteen the base architecture has and the sixteen the
// extended general-purpose registers add -- so nothing here changes when they
// are encoded, only the prefix the encoder emits does.
#pragma once

#include <set>
#include <string_view>

#include "mc/reg.hh"


namespace pl4g::target::x86_64 {

  // The register file and the registers the back end names outright.  Made
  // once, for the life of the process, as pypl4g's module-level one is.
  struct Registers {
    mc::RegisterInfo info;
    const mc::RegClass* gpr = nullptr;
    const mc::RegClass* vec = nullptr;
    const mc::RegClass* kmask = nullptr;
    const mc::RegClass* flags = nullptr;
    const mc::RegClass* seg = nullptr;
    const mc::PhysReg* eflags = nullptr;

    const mc::PhysReg* rax = nullptr;
    const mc::PhysReg* rcx = nullptr;
    const mc::PhysReg* rdx = nullptr;
    const mc::PhysReg* rbx = nullptr;
    const mc::PhysReg* rsp = nullptr;
    const mc::PhysReg* rbp = nullptr;
    const mc::PhysReg* rsi = nullptr;
    const mc::PhysReg* rdi = nullptr;
    const mc::PhysReg* r11 = nullptr;
    const mc::PhysReg* eax = nullptr;
    const mc::PhysReg* ecx = nullptr;
    const mc::PhysReg* edx = nullptr;
    const mc::PhysReg* ebp = nullptr;
    const mc::PhysReg* ebx = nullptr;
    const mc::PhysReg* edi = nullptr;
    const mc::PhysReg* esi = nullptr;

    Registers();
    Registers(const Registers&) = delete;
    Registers& operator=(const Registers&) = delete;

    // The register called `name`.
    const mc::PhysReg* reg(std::string_view name) const;
  };

  // The one register file.
  Registers& registers();

  // The number of general-purpose units, and the first that needs one of the
  // extended prefixes.
  inline constexpr int gpr_unit_count = 32;
  inline constexpr int gpr_extended_first = 16;

  // The byte registers that need an empty REX prefix to be named at all, and
  // the ones no instruction with a REX prefix may name.
  bool rex_required_byte_reg(std::string_view name) noexcept;
  bool high_byte_reg(std::string_view name) noexcept;

  // The segment override prefix of a segment register, by its name.
  int segment_prefix(std::string_view name);

} // namespace pl4g::target::x86_64
