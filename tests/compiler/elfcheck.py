"""A minimal, independent ELF reader for the tests.

Written from the format rather than by reusing the compiler's own structures, so
that a mistake in the writer cannot be repeated here and go unnoticed.
"""

import struct
from dataclasses import dataclass
from typing import Sequence

EHDR = struct.Struct("<16sHHIQQQIHHHHHH")
PHDR = struct.Struct("<IIQQQQQQ")
SHDR = struct.Struct("<IIQQQQIIQQ")
SYM = struct.Struct("<IBBHQQ")

PT_LOAD = 1
PT_GNU_STACK = 0x6474E551
PF_X, PF_W, PF_R = 1, 2, 4
SHT_STRTAB, SHT_SYMTAB, SHT_NOBITS = 3, 2, 8
STB_LOCAL = 0


@dataclass(frozen=True, slots=True)
class Segment:
    """One program header."""

    p_type: int
    p_flags: int
    p_offset: int
    p_vaddr: int
    p_filesz: int
    p_memsz: int
    p_align: int


@dataclass(frozen=True, slots=True)
class Section:
    """One section header."""

    name: str
    sh_type: int
    sh_flags: int
    sh_addr: int
    sh_offset: int
    sh_size: int
    sh_link: int
    sh_info: int
    sh_addralign: int
    sh_entsize: int


@dataclass(frozen=True, slots=True)
class Symbol:
    """One symbol table entry."""

    name: str
    binding: int
    kind: int
    shndx: int
    value: int
    size: int
    other: int = 0


@dataclass(frozen=True, slots=True)
class Image:
    """A parsed ELF file."""

    data: bytes
    e_type: int
    e_machine: int
    e_entry: int
    e_shstrndx: int
    segments: tuple[Segment, ...]
    sections: tuple[Section, ...]
    symbols: tuple[Symbol, ...]

    def section(self, name: str) -> Section | None:
        """The section called *name*, if there is one."""
        return next((s for s in self.sections if s.name == name), None)

    def symbol(self, name: str) -> Symbol | None:
        """The symbol called *name*, if there is one."""
        return next((s for s in self.symbols if s.name == name), None)

    def visibility_of(self, symbol: Symbol) -> int:
        """How far a symbol is visible, which lives in the low bits of st_other."""
        return symbol.other & 0x3


def _string_at(data: bytes, base: int, offset: int) -> str:
    """Read the NUL-terminated string at *offset* of the table at *base*."""
    end = data.index(b"\0", base + offset)
    return data[base + offset:end].decode("utf-8")


def parse(data: bytes) -> Image:
    """Parse an ELF file."""
    (ident, e_type, e_machine, _version, e_entry, e_phoff, e_shoff, _flags,
     e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum,
     e_shstrndx) = EHDR.unpack_from(data, 0)
    assert ident[:4] == b"\x7fELF", "not an ELF file"
    assert ident[4] == 2, "not 64-bit"
    assert ident[5] == 1, "not little endian"
    assert e_ehsize == EHDR.size
    assert e_phentsize == PHDR.size
    assert e_shnum == 0 or e_shentsize == SHDR.size

    segments = tuple(
        Segment(p_type=f[0], p_flags=f[1], p_offset=f[2], p_vaddr=f[3], p_filesz=f[5],
                p_memsz=f[6], p_align=f[7])
        for f in (PHDR.unpack_from(data, e_phoff + i * PHDR.size) for i in range(e_phnum)))

    raw_sections = [SHDR.unpack_from(data, e_shoff + i * SHDR.size)
                    for i in range(e_shnum)]
    shstr_base = raw_sections[e_shstrndx][4] if e_shnum else 0
    sections = tuple(
        Section(name=_string_at(data, shstr_base, f[0]) if e_shnum else "",
                sh_type=f[1], sh_flags=f[2], sh_addr=f[3], sh_offset=f[4], sh_size=f[5],
                sh_link=f[6], sh_info=f[7], sh_addralign=f[8], sh_entsize=f[9])
        for f in raw_sections)

    symbols: tuple[Symbol, ...] = ()
    symtab = next((s for s in sections if s.sh_type == SHT_SYMTAB), None)
    if symtab is not None:
        strtab = sections[symtab.sh_link]
        entries = []
        for index in range(symtab.sh_size // SYM.size):
            st_name, st_info, _other, st_shndx, st_value, st_size = SYM.unpack_from(
                data, symtab.sh_offset + index * SYM.size)
            entries.append(Symbol(name=_string_at(data, strtab.sh_offset, st_name),
                                  binding=st_info >> 4, kind=st_info & 0xF,
                                  shndx=st_shndx, value=st_value, size=st_size,
                                  other=_other))
        symbols = tuple(entries)

    return Image(data=data, e_type=e_type, e_machine=e_machine, e_entry=e_entry,
                 e_shstrndx=e_shstrndx, segments=segments, sections=sections,
                 symbols=symbols)


def check_well_formed(image: Image) -> list[str]:
    """Return every violated requirement of the format, as readable text."""
    problems: list[str] = []
    size = len(image.data)

    def require(condition: bool, detail: str) -> None:
        """Record *detail* when *condition* does not hold."""
        if not condition:
            problems.append(detail)

    for segment in image.segments:
        require(segment.p_offset + segment.p_filesz <= size,
                "a segment extends past the end of the file")
        require(segment.p_filesz <= segment.p_memsz,
                "a segment is larger in the file than in memory")
        if segment.p_type == PT_LOAD and segment.p_align > 1:
            require(segment.p_offset % segment.p_align
                    == segment.p_vaddr % segment.p_align,
                    "a loadable segment's offset and address are not congruent")
            require(not (segment.p_flags & PF_W and segment.p_flags & PF_X),
                    "a loadable segment is both writable and executable")

    stack = [s for s in image.segments if s.p_type == PT_GNU_STACK]
    require(len(stack) == 1, "there is no PT_GNU_STACK, so the stack is executable")
    if stack:
        require(not stack[0].p_flags & PF_X, "the stack is executable")

    for section in image.sections[1:]:
        if section.sh_type != SHT_NOBITS:
            require(section.sh_offset + section.sh_size <= size,
                    "".join(("section ", section.name, " extends past the end")))
        if section.sh_type == SHT_STRTAB and section.sh_size:
            table = image.data[section.sh_offset:section.sh_offset + section.sh_size]
            require(table.startswith(b"\0") and table.endswith(b"\0"),
                    "".join(("string table ", section.name, " is not NUL-bounded")))
        if section.sh_type == SHT_SYMTAB:
            require(image.sections[section.sh_link].sh_type == SHT_STRTAB,
                    "the symbol table's link does not name a string table")
            locals_ = sum(1 for s in image.symbols if s.binding == STB_LOCAL)
            require(section.sh_info == locals_,
                    "the symbol table's info is not the first non-local index")
            require(all(s.binding == STB_LOCAL for s in image.symbols[:locals_]),
                    "local symbols do not come first")

    executable = [s for s in image.segments
                  if s.p_type == PT_LOAD and s.p_flags & PF_X]
    require(any(s.p_vaddr <= image.e_entry < s.p_vaddr + s.p_memsz
                for s in executable),
            "the entry point is not inside an executable segment")
    return problems


def unique(values: Sequence[str]) -> bool:
    """Whether every value is distinct."""
    return len(set(values)) == len(values)
