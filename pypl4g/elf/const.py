"""ELF constants, for the part of the format the compiler generates."""

from __future__ import annotations

from typing import Final

EI_NIDENT: Final[int] = 16
ELFMAG: Final[bytes] = b"\x7fELF"

ELFCLASS64: Final[int] = 2
ELFDATA2LSB: Final[int] = 1
EV_CURRENT: Final[int] = 1
#: No operating-system-specific constructs are used, so the generic value
#: applies.  Emitting an indirect function or a GNU property note would make
#: this ELFOSABI_GNU.
ELFOSABI_NONE: Final[int] = 0

ET_REL: Final[int] = 1
ET_EXEC: Final[int] = 2
ET_DYN: Final[int] = 3

EM_X86_64: Final[int] = 62
EM_AARCH64: Final[int] = 183
EM_RISCV: Final[int] = 243

PT_NULL: Final[int] = 0
PT_LOAD: Final[int] = 1
PT_DYNAMIC: Final[int] = 2
PT_INTERP: Final[int] = 3
PT_NOTE: Final[int] = 4
PT_PHDR: Final[int] = 6
PT_GNU_STACK: Final[int] = 0x6474E551
PT_GNU_RELRO: Final[int] = 0x6474E552
PT_GNU_PROPERTY: Final[int] = 0x6474E553

PF_X: Final[int] = 1
PF_W: Final[int] = 2
PF_R: Final[int] = 4

SHT_NULL: Final[int] = 0
SHT_PROGBITS: Final[int] = 1
SHT_SYMTAB: Final[int] = 2
SHT_STRTAB: Final[int] = 3
SHT_RELA: Final[int] = 4
SHT_HASH: Final[int] = 5
SHT_DYNAMIC: Final[int] = 6
SHT_NOBITS: Final[int] = 8
SHT_DYNSYM: Final[int] = 11
SHT_INIT_ARRAY: Final[int] = 14
SHT_FINI_ARRAY: Final[int] = 15

SHF_WRITE: Final[int] = 0x1
SHF_ALLOC: Final[int] = 0x2
SHF_EXECINSTR: Final[int] = 0x4

SHN_UNDEF: Final[int] = 0
SHN_ABS: Final[int] = 0xFFF1

STB_LOCAL: Final[int] = 0
STB_GLOBAL: Final[int] = 1
STB_WEAK: Final[int] = 2

STT_NOTYPE: Final[int] = 0
STT_OBJECT: Final[int] = 1
STT_FUNC: Final[int] = 2
STT_SECTION: Final[int] = 3
STT_FILE: Final[int] = 4

STV_DEFAULT: Final[int] = 0
STV_INTERNAL: Final[int] = 1
STV_HIDDEN: Final[int] = 2
STV_PROTECTED: Final[int] = 3

EHDR_SIZE: Final[int] = 64
PHDR_SIZE: Final[int] = 56
SHDR_SIZE: Final[int] = 64
SYM_SIZE: Final[int] = 24
RELA_SIZE: Final[int] = 24
DYN_SIZE: Final[int] = 16

#: The entries of a dynamic section a shared object of this compiler has.
DT_NULL: Final[int] = 0
DT_NEEDED: Final[int] = 1
DT_HASH: Final[int] = 4
DT_STRTAB: Final[int] = 5
DT_SYMTAB: Final[int] = 6
DT_RELA: Final[int] = 7
DT_RELASZ: Final[int] = 8
DT_RELAENT: Final[int] = 9
DT_STRSZ: Final[int] = 10
DT_SYMENT: Final[int] = 11
DT_INIT_ARRAY: Final[int] = 25
DT_FINI_ARRAY: Final[int] = 26
DT_INIT_ARRAYSZ: Final[int] = 27
DT_FINI_ARRAYSZ: Final[int] = 28
DT_RELACOUNT: Final[int] = 0x6FFFFFF9

#: The two relocations a shared object has, by machine: the one that adds
#: where the object was loaded, and the one that fills a slot with where a
#: symbol is.  RISC-V has no relocation for a slot of its own and fills one
#: as any other word holding an address.
RELATIVE: Final[dict[int, int]] = {EM_X86_64: 8, EM_AARCH64: 1027, EM_RISCV: 3}
GLOB_DAT: Final[dict[int, int]] = {EM_X86_64: 6, EM_AARCH64: 1025, EM_RISCV: 2}


def st_info(binding: int, kind: int) -> int:
    """Pack a symbol's binding and type into the st_info byte."""
    return ((binding & 0xF) << 4) | (kind & 0xF)
