// The x86-64 register file.
#include "target/x86_64/regs.hh"

#include <array>
#include <format>
#include <stdexcept>
#include <string>


namespace pl4g::target::x86_64 {

  namespace {

    // The sixteen registers of the base architecture's first eight, in
    // encoding order: the whole, the double word, the word and the byte.
    constexpr std::array<std::array<std::string_view, 4>, 8> base_names{ {
      { "rax", "eax", "ax", "al" },
      { "rcx", "ecx", "cx", "cl" },
      { "rdx", "edx", "dx", "dl" },
      { "rbx", "ebx", "bx", "bl" },
      { "rsp", "esp", "sp", "spl" },
      { "rbp", "ebp", "bp", "bpl" },
      { "rsi", "esi", "si", "sil" },
      { "rdi", "edi", "di", "dil" },
    } };

    // The high-byte views, which exist only for the first four units.
    constexpr std::array<std::string_view, 4> high_byte{ "ah", "ch", "dh", "bh" };

  } // anonymous namespace


  Registers::Registers()
  {
    gpr = info.add_class("gpr", 64);
    vec = info.add_class("vec", 512);
    kmask = info.add_class("kmask", 64);
    flags = info.add_class("flags", 64, false);
    seg = info.add_class("seg", 16, false);
    for (int enc = 0; enc < gpr_unit_count; ++enc) {
      std::string wide, dword, word, byte;
      if (enc < int(base_names.size())) {
        wide = base_names[enc][0];
        dword = base_names[enc][1];
        word = base_names[enc][2];
        byte = base_names[enc][3];
      } else {
        wide = std::format("r{}", enc);
        dword = wide + "d";
        word = wide + "w";
        byte = wide + "b";
      }
      auto unit = info.add_unit(gpr, enc, wide);
      info.add_view(unit, wide, 64);
      info.add_view(unit, dword, 32);
      info.add_view(unit, word, 16);
      info.add_view(unit, byte, 8);
      if (enc < int(high_byte.size()))
        info.add_view(unit, std::string(high_byte[enc]), 8, 1);
    }
    for (int enc = 0; enc < 32; ++enc) {
      auto unit = info.add_unit(vec, enc, std::format("zmm{}", enc));
      info.add_view(unit, std::format("xmm{}", enc), 128);
      info.add_view(unit, std::format("ymm{}", enc), 256);
      info.add_view(unit, std::format("zmm{}", enc), 512);
    }
    for (int enc = 0; enc < 8; ++enc) {
      auto unit = info.add_unit(kmask, enc, std::format("k{}", enc));
      info.add_view(unit, std::format("k{}", enc), 64);
    }
    eflags = info.add_view(info.add_unit(flags, 0, "eflags"), "eflags", 64);
    int enc = 0;
    for (std::string_view name : { "es", "cs", "ss", "ds", "fs", "gs" }) {
      auto unit = info.add_unit(seg, enc++, std::string(name));
      info.add_view(unit, std::string(name), 16);
    }
    rax = reg("rax");
    rcx = reg("rcx");
    rdx = reg("rdx");
    rbx = reg("rbx");
    rsp = reg("rsp");
    rbp = reg("rbp");
    rsi = reg("rsi");
    rdi = reg("rdi");
    r11 = reg("r11");
    eax = reg("eax");
    ecx = reg("ecx");
    edx = reg("edx");
    ebp = reg("ebp");
    ebx = reg("ebx");
    edi = reg("edi");
    esi = reg("esi");
  }


  const mc::PhysReg* Registers::reg(std::string_view name) const
  {
    auto found = info.reg_named(name);
    if (found == nullptr) [[unlikely]]
      throw std::logic_error(std::format("no x86-64 register '{}'", name));
    return found;
  }


  Registers& registers()
  {
    static Registers made;
    return made;
  }


  bool rex_required_byte_reg(std::string_view name) noexcept
  {
    return name == "spl" || name == "bpl" || name == "sil" || name == "dil";
  }


  bool high_byte_reg(std::string_view name) noexcept
  {
    return name == "ah" || name == "ch" || name == "dh" || name == "bh";
  }


  int segment_prefix(std::string_view name)
  {
    if (name == "es")
      return 0x26;
    if (name == "cs")
      return 0x2E;
    if (name == "ss")
      return 0x36;
    if (name == "ds")
      return 0x3E;
    if (name == "fs")
      return 0x64;
    if (name == "gs")
      return 0x65;
    throw std::logic_error(std::format("no segment register '{}'", name));
  }

} // namespace pl4g::target::x86_64
