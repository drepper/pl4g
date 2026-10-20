// Packers for the ELF structures.
#include "elf/structs.hh"

#include "elf/const.hh"


namespace pl4g::elf {

  namespace {

    // Writes little-endian fields one after another from a place.
    struct Packer {
      Bytes& out;
      std::size_t at;

      template<typename T>
      Packer& put(T value)
      {
        for (std::size_t i = 0; i < sizeof(T); ++i)
          out[at + i] = std::uint8_t(std::uint64_t(value) >> (8 * i));
        at += sizeof(T);
        return *this;
      }
    };

  } // anonymous namespace


  void pack(Bytes& out, std::size_t at, const Ehdr& header)
  {
    // The identification: the magic, 64-bit, little-endian, current, no
    // system-specific constructs, and nought to the end.
    const std::uint8_t ident[ei_nident] = { 0x7F, 'E', 'L', 'F', elfclass64, elfdata2lsb, ev_current, elfosabi_none };
    for (int i = 0; i < ei_nident; ++i)
      out[at + std::size_t(i)] = ident[i];
    Packer{ out, at + ei_nident }
      .put(header.e_type)
      .put(header.e_machine)
      .put(std::uint32_t(ev_current))
      .put(header.e_entry)
      .put(header.e_phoff)
      .put(header.e_shoff)
      .put(header.e_flags)
      .put(std::uint16_t(ehdr_size))
      .put(std::uint16_t(phdr_size))
      .put(header.e_phnum)
      .put(std::uint16_t(shdr_size))
      .put(header.e_shnum)
      .put(header.e_shstrndx);
  }


  void pack(Bytes& out, std::size_t at, const Phdr& header)
  {
    Packer{ out, at }
      .put(header.p_type)
      .put(header.p_flags)
      .put(header.p_offset)
      .put(header.p_vaddr)
      .put(header.p_vaddr)
      .put(header.p_filesz)
      .put(header.p_memsz)
      .put(header.p_align);
  }


  void pack(Bytes& out, std::size_t at, const Shdr& header)
  {
    Packer{ out, at }
      .put(header.sh_name)
      .put(header.sh_type)
      .put(header.sh_flags)
      .put(header.sh_addr)
      .put(header.sh_offset)
      .put(header.sh_size)
      .put(header.sh_link)
      .put(header.sh_info)
      .put(header.sh_addralign)
      .put(header.sh_entsize);
  }


  void pack(Bytes& out, std::size_t at, const Sym& symbol)
  {
    Packer{ out, at }
      .put(symbol.st_name)
      .put(symbol.st_info)
      .put(symbol.st_other)
      .put(symbol.st_shndx)
      .put(symbol.st_value)
      .put(symbol.st_size);
  }

} // namespace pl4g::elf
