"""Writing the ELF image.

The writer plans first and materializes second.  ``plan`` computes every offset,
address and size without emitting a byte; ``materialize`` allocates one buffer of
the final size and writes each chunk at the offset the plan recorded.  Nothing is
appended and nothing is back-patched, which is what makes the layout reusable for
incremental rebuilds and what would make "write only the pages that changed" a
matter of comparing two buffers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Sequence

from ..mc.fixup import (FixupApplier, FixupRangeError, MCFixup,
                        UnresolvedSymbol, apply_little_endian, fixup_value)
from ..mc.fragment import (MCDataFragment, MCFragment, MCInstFragment,
                           MCPaddingFragment)
from ..mc.layout import (layout_section, resolve_symbol_offsets,
                         section_bytes)
from ..mc.symbol import MCSection, MCSymbol, SymBinding, SymKind, SymVisibility
from .const import (ET_EXEC, PF_R, PF_W, PF_X, PT_GNU_STACK, PT_LOAD, SHF_ALLOC,
                    SHF_EXECINSTR, SHF_WRITE, SHN_ABS, SHN_UNDEF, SHT_NULL,
                    SHT_PROGBITS, SHT_STRTAB, SHT_SYMTAB, STB_GLOBAL, STB_LOCAL,
                    STB_WEAK, STT_FILE, STT_FUNC, STT_NOTYPE, STT_OBJECT,
                    STV_DEFAULT, STV_HIDDEN, STV_INTERNAL, STV_PROTECTED,
                    EHDR_SIZE, PHDR_SIZE, SHDR_SIZE, SYM_SIZE, st_info)
from .layout import (Chunk, FunctionExtent, ImageKind, ImageLayout, SectionPlan,
                     SegmentPlan, SymbolPlan, align_up)
from .strtab import StringTable
from .structs import pack_ehdr, pack_phdr, pack_shdr, pack_sym

_BINDINGS: Final[dict[SymBinding, int]] = {
    SymBinding.LOCAL: STB_LOCAL,
    SymBinding.GLOBAL: STB_GLOBAL,
    SymBinding.WEAK: STB_WEAK,
}

_VISIBILITIES: Final[dict[SymVisibility, int]] = {
    SymVisibility.DEFAULT: STV_DEFAULT,
    SymVisibility.INTERNAL: STV_INTERNAL,
    SymVisibility.HIDDEN: STV_HIDDEN,
    SymVisibility.PROTECTED: STV_PROTECTED,
}

_KINDS: Final[dict[SymKind, int]] = {
    SymKind.NOTYPE: STT_NOTYPE,
    SymKind.FUNC: STT_FUNC,
    SymKind.OBJECT: STT_OBJECT,
    SymKind.FILE: STT_FILE,
    SymKind.SECTION: STT_NOTYPE,
}


class ImageError(Exception):
    """The image cannot be produced."""

    def __init__(self, detail: str, symbol: str | None = None,
                 out_of_range: tuple[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.symbol = symbol
        #: The value and the field, where a value did not fit the field.
        self.out_of_range = out_of_range


@dataclass(frozen=True, slots=True)
class ImageSettings:
    """What the writer needs to know about the target and the request."""

    machine: int
    base_vaddr: int
    page_size: int
    entry_symbol: str
    #: The architecture's own flag word, which the header carries and nothing
    #: else in the file refers to.
    header_flags: int = 0
    kind: ImageKind = ImageKind.EXECUTABLE
    #: Whether to keep the section headers and the symbol table.
    with_symbols: bool = True
    #: Growth slack to reserve after each function, for incremental rebuilds.
    function_padding: int = 0


class ElfWriter:
    """Turns the assembled sections into an ELF executable."""

    def __init__(self, settings: ImageSettings, sections: Sequence[MCSection],
                 symbols: Sequence[MCSymbol], source_paths: Sequence[str],
                 apply_fixup: FixupApplier = apply_little_endian) -> None:
        self._settings = settings
        self._sections = [s for s in sections if s.fragments]
        self._symbols = list(symbols)
        self._source_paths = list(source_paths)
        #: How this target stores a fixup's value once it has been computed.
        self._apply_fixup = apply_fixup

    # -- planning --------------------------------------------------------------

    def plan(self) -> ImageLayout:
        """Compute the whole layout without writing anything."""
        settings = self._settings
        layout = ImageLayout(kind=settings.kind, machine=settings.machine,
                             base_vaddr=settings.base_vaddr, page_size=settings.page_size,
                             entry_symbol=settings.entry_symbol)
        for section in self._sections:
            layout_section(section)
            resolve_symbol_offsets(section, self._symbols)

        # Sections that are mapped are grouped by the permissions they need,
        # because a segment carries one set of permissions for all of it.  A
        # group that needs nothing gets no segment: an empty one would still
        # cost a page, since no two groups may share one.
        groups = [(self._mapped(executable=False, writable=False), PF_R),
                  (self._mapped(executable=True, writable=False), PF_R | PF_X),
                  (self._mapped(executable=False, writable=True), PF_R | PF_W)]
        groups = [(sections, flags) for sections, flags in groups if sections]
        placed = sum(len(sections) for sections, _ in groups)
        if placed != sum(1 for s in self._sections if s.alloc):
            # The only combination left is writable and executable, which no
            # loader should be asked to grant and which nothing here produces.
            raise ImageError("a mapped section asks for permissions no segment gives")
        phnum = len(groups) + 1
        offset = EHDR_SIZE + phnum * PHDR_SIZE

        self._add_chunk(layout, "ehdr", EHDR_SIZE, 1, 0)
        self._add_chunk(layout, "phdrs", phnum * PHDR_SIZE, 1, EHDR_SIZE)

        # The first group follows the headers in the file and its segment starts
        # at the start of the file, so that the headers themselves are mapped.
        # Every later group starts on a page of its own: two groups that shared
        # a page would have to be mapped with one set of permissions, and which
        # they got would depend on the order the segments were mapped in.
        loads: list[SegmentPlan] = []
        page = settings.page_size
        bias = settings.base_vaddr
        for sections, flags in groups:
            if loads:
                end_vaddr = loads[-1].p_vaddr + loads[-1].p_memsz
                offset = align_up(offset, max(s.alignment for s in sections))
                bias = align_up(end_vaddr, page) + (offset % page) - offset
            offset = self._place_group(layout, sections, offset, bias, flags,
                                       page, loads)
        self._place_symbols()
        layout.functions = self._function_extents(layout)

        shstrtab = StringTable()
        strtab = StringTable()
        symbols = self._symbol_plans(layout, strtab) if settings.with_symbols else []
        layout.symbols = symbols

        if settings.with_symbols:
            # Sections that take no room when the program runs, and that
            # something emitted rather than this writer: they are in the file
            # for whatever reads the file.  They go before the tables so that
            # their names are among the names the table of names holds.
            for section in self._sections:
                if not section.alloc:
                    offset = self._add_nonalloc(
                        layout, section.name, section.sh_type, section.size,
                        max(section.alignment, 1), offset)
            for name in (".shstrtab", ".symtab", ".strtab"):
                shstrtab.add(name)
            for plan in layout.sections:
                shstrtab.add(plan.name)

            offset = self._add_nonalloc(layout, ".shstrtab", SHT_STRTAB, shstrtab.size,
                                        1, offset)
            offset = self._add_nonalloc(layout, ".symtab", SHT_SYMTAB,
                                        len(symbols) * SYM_SIZE, 8, offset,
                                        entsize=SYM_SIZE)
            offset = self._add_nonalloc(layout, ".strtab", SHT_STRTAB, strtab.size,
                                        1, offset)
            self._link_symtab(layout, symbols)
            offset = align_up(offset, 8)
            self._add_chunk(layout, "shdrs", (len(layout.sections) + 1) * SHDR_SIZE,
                            8, offset)
            offset += (len(layout.sections) + 1) * SHDR_SIZE

        layout.segments = [
            *loads,
            # Its presence without the executable bit is what makes the stack
            # non-executable; a missing PT_GNU_STACK gives an executable stack.
            SegmentPlan(p_type=PT_GNU_STACK, p_flags=PF_R | PF_W, p_align=0x10),
        ]
        layout.entry_vaddr = self._entry_vaddr()
        layout.total_size = offset
        self._shstrtab = shstrtab
        self._strtab = strtab
        return layout

    def _mapped(self, *, executable: bool, writable: bool) -> list[MCSection]:
        """The mapped sections that need exactly these permissions."""
        return [s for s in self._sections
                if s.alloc and s.executable == executable and s.writable == writable]

    def _place_group(self, layout: ImageLayout, sections: Sequence[MCSection],
                     offset: int, bias: int, flags: int, page_size: int,
                     loads: list[SegmentPlan]) -> int:
        """Place one group of sections and the segment that maps them.

        *bias* is what turns a file offset into an address.  Because it is the
        same for every section of a group, the congruence the format requires
        between a segment's offset and its address holds for the whole group as
        soon as it holds for its start.
        """
        if not sections:
            return offset
        start = offset
        for section in sections:
            offset = align_up(offset, max(section.alignment, 1))
            plan = SectionPlan(name=section.name, sh_type=SHT_PROGBITS,
                               sh_flags=self._section_flags(section),
                               sh_addralign=max(section.alignment, 1), alloc=True)
            plan.offset = offset
            plan.addr = offset + bias
            plan.size = section.size
            section.vaddr = plan.addr
            layout.sections.append(plan)
            self._add_chunk(layout, section.name, section.size,
                            max(section.alignment, 1), offset)
            offset += section.size
        # The first segment begins at the start of the file so that the headers
        # are mapped with it; a later one begins where its own sections do.
        segment_start = 0 if not loads else start
        size = offset - segment_start
        loads.append(SegmentPlan(p_type=PT_LOAD, p_flags=flags, p_align=page_size,
                                 p_offset=segment_start,
                                 p_vaddr=segment_start + bias,
                                 p_filesz=size, p_memsz=size))
        return offset

    def _section_flags(self, section: MCSection) -> int:
        """The section header flags for *section*."""
        flags = SHF_ALLOC
        if section.executable:
            flags |= SHF_EXECINSTR
        if section.writable:
            flags |= SHF_WRITE
        return flags

    def _add_chunk(self, layout: ImageLayout, name: str, size: int, align: int,
                   offset: int, vaddr: int | None = None) -> Chunk:
        """Record one chunk of the image."""
        chunk = Chunk(name=name, size=size, align=align, file_offset=offset, vaddr=vaddr)
        layout.chunks.append(chunk)
        layout.by_name[name] = chunk
        return chunk

    def _add_nonalloc(self, layout: ImageLayout, name: str, sh_type: int, size: int,
                      align: int, offset: int, entsize: int = 0) -> int:
        """Place a section that is not mapped when the program runs."""
        offset = align_up(offset, align)
        plan = SectionPlan(name=name, sh_type=sh_type, sh_addralign=align,
                           sh_entsize=entsize)
        plan.offset = offset
        plan.size = size
        layout.sections.append(plan)
        self._add_chunk(layout, name, size, align, offset)
        return offset + size

    def _link_symtab(self, layout: ImageLayout, symbols: Sequence[SymbolPlan]) -> None:
        """Fill in the symbol table's link and info fields.

        ``sh_link`` must name the string table the symbol names live in, and
        ``sh_info`` must be the index of the first symbol that is not local.
        Getting either wrong produces a file that readers accept and debuggers
        quietly misread, which is why a test checks both.
        """
        indices = {plan.name: i + 1 for i, plan in enumerate(layout.sections)}
        first_global = next((i for i, s in enumerate(symbols) if s.binding != STB_LOCAL),
                            len(symbols))
        for plan in layout.sections:
            if plan.name == ".symtab":
                plan.sh_link = indices[".strtab"]
                plan.sh_info = first_global

    def _place_symbols(self) -> None:
        """Give every defined symbol the address its section and offset imply."""
        for symbol in self._symbols:
            if symbol.defined and symbol.section is not None:
                symbol.vaddr = symbol.section.vaddr + symbol.offset

    def _entry_vaddr(self) -> int:
        """The address of the entry point."""
        for symbol in self._symbols:
            if symbol.name == self._settings.entry_symbol and symbol.vaddr is not None:
                return symbol.vaddr
        raise ImageError("".join(("the entry point '", self._settings.entry_symbol,
                                  "' is not defined")), self._settings.entry_symbol)

    def _function_extents(self, layout: ImageLayout) -> list[FunctionExtent]:
        """Record where each function lives and how much slack follows it.

        This is the map an incremental rebuild needs, and it is the same
        information the symbol table carries, so it can be read back out of the
        compiler's own previous output.
        """
        extents: list[FunctionExtent] = []
        offsets = {p.name: p.offset for p in layout.sections}
        for symbol in self._symbols:
            if not symbol.defined or symbol.kind is not SymKind.FUNC:
                continue
            if symbol.section is None or symbol.vaddr is None:
                continue
            base = offsets.get(symbol.section.name)
            if base is None:
                continue
            extents.append(FunctionExtent(
                name=symbol.name, file_offset=base + symbol.offset,
                vaddr=symbol.vaddr, size=symbol.size,
                padding=self._slack_after(symbol)))
        return extents

    def _slack_after(self, symbol: MCSymbol) -> int:
        """The growth slack reserved immediately after *symbol*'s definition."""
        section = symbol.section
        if section is None:
            return 0
        end = symbol.offset + symbol.size
        for fragment in section.fragments:
            if isinstance(fragment, MCPaddingFragment) and fragment.offset == end:
                return fragment.reserved
        return 0

    def _symbol_plans(self, layout: ImageLayout, strtab: StringTable) -> list[SymbolPlan]:
        """Build the symbol table, with the local symbols before the global ones."""
        indices = {p.name: i + 1 for i, p in enumerate(layout.sections)}
        entries: list[SymbolPlan] = [SymbolPlan(name="", shndx=SHN_UNDEF)]
        strtab.add("")
        locals_: list[SymbolPlan] = []
        globals_: list[SymbolPlan] = []
        for path in self._source_paths:
            strtab.add(path)
            locals_.append(SymbolPlan(name=path, binding=STB_LOCAL, kind=STT_FILE,
                                      shndx=SHN_ABS))
        for symbol in self._symbols:
            if not symbol.defined or symbol.temporary or symbol.section is None:
                continue
            if symbol.vaddr is None:
                continue
            strtab.add(symbol.name)
            plan = SymbolPlan(name=symbol.name, value=symbol.vaddr, size=symbol.size,
                              binding=_BINDINGS[symbol.binding],
                              kind=_KINDS[symbol.kind],
                              shndx=indices.get(symbol.section.name, SHN_UNDEF),
                              visibility=_VISIBILITIES[symbol.visibility])
            (locals_ if plan.binding == STB_LOCAL else globals_).append(plan)
        entries.extend(locals_)
        entries.extend(globals_)
        return entries

    # -- materializing ---------------------------------------------------------

    def materialize(self, layout: ImageLayout) -> bytes:
        """Write the planned image into one buffer."""
        self._resolve_fixups()
        out = bytearray(layout.total_size)

        out[0:EHDR_SIZE] = pack_ehdr(
            e_type=ET_EXEC, e_machine=layout.machine, e_entry=layout.entry_vaddr,
            e_phoff=EHDR_SIZE,
            e_shoff=layout.by_name["shdrs"].file_offset if "shdrs" in layout.by_name else 0,
            e_phnum=len(layout.segments),
            e_shnum=len(layout.sections) + 1 if layout.sections else 0,
            e_shstrndx=self._index_of(layout, ".shstrtab"),
            e_flags=self._settings.header_flags)

        phdrs = layout.by_name["phdrs"]
        position = phdrs.file_offset
        for segment in layout.segments:
            out[position:position + PHDR_SIZE] = pack_phdr(
                p_type=segment.p_type, p_flags=segment.p_flags, p_offset=segment.p_offset,
                p_vaddr=segment.p_vaddr, p_filesz=segment.p_filesz,
                p_memsz=segment.p_memsz, p_align=segment.p_align)
            position += PHDR_SIZE

        for section in self._sections:
            chunk = layout.by_name.get(section.name)
            if chunk is None:
                continue
            data = section_bytes(section)
            out[chunk.file_offset:chunk.file_offset + len(data)] = data

        if ".shstrtab" in layout.by_name:
            self._write_tables(out, layout)
        return bytes(out)

    def _write_tables(self, out: bytearray, layout: ImageLayout) -> None:
        """Write the string tables, the symbol table and the section headers."""
        shstrtab_chunk = layout.by_name[".shstrtab"]
        strtab_chunk = layout.by_name[".strtab"]
        symtab_chunk = layout.by_name[".symtab"]
        shstr = self._shstrtab.bytes_of()
        out[shstrtab_chunk.file_offset:shstrtab_chunk.file_offset + len(shstr)] = shstr
        strs = self._strtab.bytes_of()
        out[strtab_chunk.file_offset:strtab_chunk.file_offset + len(strs)] = strs

        position = symtab_chunk.file_offset
        for symbol in layout.symbols:
            out[position:position + SYM_SIZE] = pack_sym(
                st_name=self._strtab.add(symbol.name),
                st_info=st_info(symbol.binding, symbol.kind),
                st_other=symbol.visibility,
                st_shndx=symbol.shndx, st_value=symbol.value, st_size=symbol.size)
            position += SYM_SIZE

        position = layout.by_name["shdrs"].file_offset
        out[position:position + SHDR_SIZE] = pack_shdr(
            sh_name=0, sh_type=SHT_NULL, sh_flags=0, sh_addr=0, sh_offset=0, sh_size=0,
            sh_link=0, sh_info=0, sh_addralign=0, sh_entsize=0)
        position += SHDR_SIZE
        for plan in layout.sections:
            out[position:position + SHDR_SIZE] = pack_shdr(
                sh_name=self._shstrtab.add(plan.name), sh_type=plan.sh_type,
                sh_flags=plan.sh_flags, sh_addr=plan.addr if plan.alloc else 0,
                sh_offset=plan.offset, sh_size=plan.size, sh_link=plan.sh_link,
                sh_info=plan.sh_info, sh_addralign=plan.sh_addralign,
                sh_entsize=plan.sh_entsize)
            position += SHDR_SIZE

    def _index_of(self, layout: ImageLayout, name: str) -> int:
        """The section header index of the section called *name*."""
        for index, plan in enumerate(layout.sections):
            if plan.name == name:
                return index + 1
        return 0

    def _resolve_fixups(self) -> None:
        """Patch every fixup, now that every address is known."""
        for section in self._sections:
            for fragment in section.fragments:
                if not isinstance(fragment, MCFragment):
                    continue
                if not isinstance(fragment, (MCInstFragment, MCDataFragment)):
                    continue
                base = section.vaddr + fragment.offset
                target = fragment.encoded if isinstance(fragment, MCInstFragment) \
                    else fragment.contents
                for fixup in fragment.fixups:
                    try:
                        value = fixup_value(fixup, base + fixup.offset)
                    except UnresolvedSymbol as exc:
                        raise ImageError("".join((
                            "'", exc.name, "' is referenced but never defined")),
                            exc.name) from exc
                    try:
                        self._apply_fixup(target, fixup.offset, fixup, value)
                    except FixupRangeError as exc:
                        raise ImageError(str(exc), out_of_range=(
                            str(exc.value), exc.kind.name)) from exc


def write_image(settings: ImageSettings, sections: Sequence[MCSection],
                symbols: Sequence[MCSymbol], source_paths: Sequence[str],
                apply_fixup: FixupApplier = apply_little_endian
                ) -> tuple[bytes, ImageLayout]:
    """Plan and write the image, returning the bytes and the layout."""
    writer = ElfWriter(settings, sections, symbols, source_paths, apply_fixup)
    layout = writer.plan()
    return writer.materialize(layout), layout
