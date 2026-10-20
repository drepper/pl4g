// Packers for the ELF structures, little-endian and 64-bit.  A port of
// pypl4g/elf/structs.py: each packs its structure into the bytes at a place.
#pragma once

#include <cstdint>
#include <vector>


namespace pl4g::elf {

  using Bytes = std::vector<std::uint8_t>;

  // The ELF header.
  struct Ehdr {
    std::uint16_t e_type = 0;
    std::uint16_t e_machine = 0;
    std::uint64_t e_entry = 0;
    std::uint64_t e_phoff = 0;
    std::uint64_t e_shoff = 0;
    std::uint16_t e_phnum = 0;
    std::uint16_t e_shnum = 0;
    std::uint16_t e_shstrndx = 0;
    std::uint32_t e_flags = 0;
  };

  // One program header.
  struct Phdr {
    std::uint32_t p_type = 0;
    std::uint32_t p_flags = 0;
    std::uint64_t p_offset = 0;
    std::uint64_t p_vaddr = 0;
    std::uint64_t p_filesz = 0;
    std::uint64_t p_memsz = 0;
    std::uint64_t p_align = 0;
  };

  // One section header.
  struct Shdr {
    std::uint32_t sh_name = 0;
    std::uint32_t sh_type = 0;
    std::uint64_t sh_flags = 0;
    std::uint64_t sh_addr = 0;
    std::uint64_t sh_offset = 0;
    std::uint64_t sh_size = 0;
    std::uint32_t sh_link = 0;
    std::uint32_t sh_info = 0;
    std::uint64_t sh_addralign = 0;
    std::uint64_t sh_entsize = 0;
  };

  // One symbol table entry.
  struct Sym {
    std::uint32_t st_name = 0;
    std::uint8_t st_info = 0;
    std::uint8_t st_other = 0;
    std::uint16_t st_shndx = 0;
    std::uint64_t st_value = 0;
    std::uint64_t st_size = 0;
  };

  // Pack each into `out` at `at`, which has the room.
  void pack(Bytes& out, std::size_t at, const Ehdr& header);
  void pack(Bytes& out, std::size_t at, const Phdr& header);
  void pack(Bytes& out, std::size_t at, const Shdr& header);
  void pack(Bytes& out, std::size_t at, const Sym& symbol);

} // namespace pl4g::elf
