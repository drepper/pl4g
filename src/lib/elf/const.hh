// ELF constants, for the part of the format the compiler generates.  A port
// of pypl4g/elf/const.py.
#pragma once

#include <cstdint>


namespace pl4g::elf {

  inline constexpr int ei_nident = 16;

  inline constexpr std::uint8_t elfclass64 = 2;
  inline constexpr std::uint8_t elfdata2lsb = 1;
  inline constexpr std::uint8_t ev_current = 1;
  // No operating-system-specific constructs are used.
  inline constexpr std::uint8_t elfosabi_none = 0;

  inline constexpr std::uint16_t et_rel = 1;
  inline constexpr std::uint16_t et_exec = 2;
  inline constexpr std::uint16_t et_dyn = 3;

  inline constexpr std::uint16_t em_x86_64 = 62;
  inline constexpr std::uint16_t em_aarch64 = 183;
  inline constexpr std::uint16_t em_riscv = 243;

  inline constexpr std::uint32_t pt_null = 0;
  inline constexpr std::uint32_t pt_load = 1;
  inline constexpr std::uint32_t pt_dynamic = 2;
  inline constexpr std::uint32_t pt_interp = 3;
  inline constexpr std::uint32_t pt_note = 4;
  inline constexpr std::uint32_t pt_phdr = 6;
  inline constexpr std::uint32_t pt_gnu_stack = 0x6474E551;
  inline constexpr std::uint32_t pt_gnu_relro = 0x6474E552;
  inline constexpr std::uint32_t pt_gnu_property = 0x6474E553;

  inline constexpr std::uint32_t pf_x = 1;
  inline constexpr std::uint32_t pf_w = 2;
  inline constexpr std::uint32_t pf_r = 4;

  inline constexpr std::uint32_t sht_null = 0;
  inline constexpr std::uint32_t sht_progbits = 1;
  inline constexpr std::uint32_t sht_symtab = 2;
  inline constexpr std::uint32_t sht_strtab = 3;
  inline constexpr std::uint32_t sht_rela = 4;
  inline constexpr std::uint32_t sht_hash = 5;
  inline constexpr std::uint32_t sht_dynamic = 6;
  inline constexpr std::uint32_t sht_nobits = 8;
  inline constexpr std::uint32_t sht_dynsym = 11;
  inline constexpr std::uint32_t sht_init_array = 14;
  inline constexpr std::uint32_t sht_fini_array = 15;
  // Relative relocations in the compact encoding (ELF gABI, 2022).
  inline constexpr std::uint32_t sht_relr = 19;

  inline constexpr std::uint64_t shf_write = 0x1;
  inline constexpr std::uint64_t shf_alloc = 0x2;
  inline constexpr std::uint64_t shf_execinstr = 0x4;

  inline constexpr std::uint16_t shn_undef = 0;
  inline constexpr std::uint16_t shn_abs = 0xFFF1;

  inline constexpr std::uint8_t stb_local = 0;
  inline constexpr std::uint8_t stb_global = 1;
  inline constexpr std::uint8_t stb_weak = 2;

  inline constexpr std::uint8_t stt_notype = 0;
  inline constexpr std::uint8_t stt_object = 1;
  inline constexpr std::uint8_t stt_func = 2;
  inline constexpr std::uint8_t stt_section = 3;
  inline constexpr std::uint8_t stt_file = 4;

  inline constexpr std::uint8_t stv_default = 0;
  inline constexpr std::uint8_t stv_internal = 1;
  inline constexpr std::uint8_t stv_hidden = 2;
  inline constexpr std::uint8_t stv_protected = 3;

  inline constexpr std::uint64_t ehdr_size = 64;
  inline constexpr std::uint64_t phdr_size = 56;
  inline constexpr std::uint64_t shdr_size = 64;
  inline constexpr std::uint64_t sym_size = 24;
  inline constexpr std::uint64_t rela_size = 24;
  inline constexpr std::uint64_t dyn_size = 16;

  // The entries of a dynamic section a shared object of this compiler has.
  inline constexpr std::int64_t dt_null = 0;
  inline constexpr std::int64_t dt_needed = 1;
  inline constexpr std::int64_t dt_hash = 4;
  inline constexpr std::int64_t dt_strtab = 5;
  inline constexpr std::int64_t dt_symtab = 6;
  inline constexpr std::int64_t dt_rela = 7;
  inline constexpr std::int64_t dt_relasz = 8;
  inline constexpr std::int64_t dt_relaent = 9;
  inline constexpr std::int64_t dt_strsz = 10;
  inline constexpr std::int64_t dt_syment = 11;
  inline constexpr std::int64_t dt_init_array = 25;
  inline constexpr std::int64_t dt_fini_array = 26;
  inline constexpr std::int64_t dt_init_arraysz = 27;
  inline constexpr std::int64_t dt_fini_arraysz = 28;
  inline constexpr std::int64_t dt_relacount = 0x6FFFFFF9;

  // The two relocations a shared object has, by machine: the one that adds
  // where the object was loaded, and the one that fills a slot with where a
  // symbol is.  RISC-V has no relocation for a slot of its own and fills one
  // as any other word holding an address.  Nought for a machine that has
  // neither here.
  constexpr std::uint32_t r_relative(std::uint16_t machine) noexcept
  {
    return machine == em_x86_64 ? 8 : machine == em_aarch64 ? 1027 : machine == em_riscv ? 3 : 0;
  }
  constexpr std::uint32_t r_glob_dat(std::uint16_t machine) noexcept
  {
    return machine == em_x86_64 ? 6 : machine == em_aarch64 ? 1025 : machine == em_riscv ? 2 : 0;
  }

  // A symbol's binding and type packed into the st_info byte.
  constexpr std::uint8_t st_info(std::uint8_t binding, std::uint8_t kind) noexcept
  {
    return std::uint8_t(((binding & 0xF) << 4) | (kind & 0xF));
  }

} // namespace pl4g::elf
