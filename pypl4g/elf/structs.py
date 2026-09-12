"""Packers for the ELF structures.

Each structure is packed by a prepared ``struct.Struct``, so the sizes the format
requires are checked once, here, rather than assumed at every use.
"""

from struct import Struct
from typing import Final

from .const import (EHDR_SIZE, EI_NIDENT, ELFCLASS64, ELFDATA2LSB, ELFMAG,
                    ELFOSABI_NONE, EV_CURRENT, PHDR_SIZE, SHDR_SIZE, SYM_SIZE)

EHDR: Final[Struct] = Struct("<16sHHIQQQIHHHHHH")
PHDR: Final[Struct] = Struct("<IIQQQQQQ")
SHDR: Final[Struct] = Struct("<IIQQQQIIQQ")
SYM: Final[Struct] = Struct("<IBBHQQ")

assert EHDR.size == EHDR_SIZE
assert PHDR.size == PHDR_SIZE
assert SHDR.size == SHDR_SIZE
assert SYM.size == SYM_SIZE


def e_ident() -> bytes:
    """The identification bytes of a little-endian 64-bit ELF file."""
    return b"".join((
        ELFMAG,
        bytes((ELFCLASS64, ELFDATA2LSB, EV_CURRENT, ELFOSABI_NONE, 0)),
        bytes(EI_NIDENT - 9),
    ))


def pack_ehdr(*, e_type: int, e_machine: int, e_entry: int, e_phoff: int, e_shoff: int,
              e_phnum: int, e_shnum: int, e_shstrndx: int, e_flags: int = 0) -> bytes:
    """Pack the ELF header."""
    return EHDR.pack(e_ident(), e_type, e_machine, EV_CURRENT, e_entry, e_phoff,
                     e_shoff, e_flags, EHDR_SIZE, PHDR_SIZE, e_phnum, SHDR_SIZE,
                     e_shnum, e_shstrndx)


def pack_phdr(*, p_type: int, p_flags: int, p_offset: int, p_vaddr: int,
              p_filesz: int, p_memsz: int, p_align: int) -> bytes:
    """Pack one program header."""
    return PHDR.pack(p_type, p_flags, p_offset, p_vaddr, p_vaddr, p_filesz, p_memsz,
                     p_align)


def pack_shdr(*, sh_name: int, sh_type: int, sh_flags: int, sh_addr: int,
              sh_offset: int, sh_size: int, sh_link: int, sh_info: int,
              sh_addralign: int, sh_entsize: int) -> bytes:
    """Pack one section header."""
    return SHDR.pack(sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link,
                     sh_info, sh_addralign, sh_entsize)


def pack_sym(*, st_name: int, st_info: int, st_other: int, st_shndx: int,
             st_value: int, st_size: int) -> bytes:
    """Pack one symbol table entry."""
    return SYM.pack(st_name, st_info, st_other, st_shndx, st_value, st_size)
