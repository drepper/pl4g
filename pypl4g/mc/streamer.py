"""The streamer: where emitted instructions, data and symbols are collected."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..source.location import INVALID_SPAN, Span
from .fixup import MCFixup
from .fragment import (MCAlignFragment, MCDataFragment, MCFragment,
                       MCInstFragment, MCPaddingFragment)
from .inst import MCInst
from .symbol import MCSection, MCSymbol, SymBinding, SymKind, SymVisibility

#: Encodes one instruction into bytes and the fixups it leaves behind.
type Encoder = Callable[[MCInst], tuple[bytes, list[MCFixup]]]


@dataclass(slots=True)
class MCStreamer:
    """Collects the generated image, section by section."""

    encode: Encoder
    sections: dict[str, MCSection] = field(default_factory=dict)
    symbols: dict[str, MCSymbol] = field(default_factory=dict)
    #: Symbols in the order they were defined, which is the order the image's
    #: symbol table keeps.
    defined_order: list[MCSymbol] = field(default_factory=list)
    current: MCSection | None = None

    def get_section(self, name: str, *, executable: bool = False, writable: bool = False,
                    alignment: int = 1, alloc: bool = True,
                    sh_type: int = 1, sh_link_to: str = "",
                    sh_entsize: int = 0) -> MCSection:
        """Return the named section, creating it if necessary."""
        found = self.sections.get(name)
        if found is None:
            found = MCSection(name=name, executable=executable, writable=writable,
                              alignment=alignment, alloc=alloc, sh_type=sh_type,
                              sh_link_to=sh_link_to, sh_entsize=sh_entsize)
            self.sections[name] = found
        return found

    def switch_section(self, section: MCSection) -> None:
        """Direct subsequent emission into *section*."""
        self.current = section

    def symbol(self, name: str) -> MCSymbol:
        """Return the symbol *name*, creating an undefined one if necessary."""
        found = self.symbols.get(name)
        if found is None:
            found = MCSymbol(name=name)
            self.symbols[name] = found
        return found

    def define_symbol(self, name: str, binding: SymBinding = SymBinding.LOCAL,
                      kind: SymKind = SymKind.NOTYPE, temporary: bool = False,
                      visibility: SymVisibility = SymVisibility.DEFAULT) -> MCSymbol:
        """Define a symbol at the current position of the current section."""
        assert self.current is not None
        sym = self.symbol(name)
        sym.section = self.current
        sym.fragment_index = len(self.current.fragments)
        sym.defined = True
        sym.binding = binding
        sym.visibility = visibility
        sym.kind = kind
        sym.temporary = temporary
        self.defined_order.append(sym)
        return sym

    def set_symbol_size(self, sym: MCSymbol) -> None:
        """Mark where the symbol's definition ends.

        The size in bytes follows from the layout, so only the position in the
        fragment list is recorded here.
        """
        assert sym.section is not None
        sym.end_fragment_index = len(sym.section.fragments)

    def _append(self, fragment: MCFragment) -> MCFragment:
        """Append *fragment* to the current section."""
        assert self.current is not None
        fragment.section = self.current
        self.current.fragments.append(fragment)
        return fragment

    def emit_inst(self, inst: MCInst) -> MCInstFragment:
        """Encode and emit one instruction."""
        encoded, fixups = self.encode(inst)
        fragment = MCInstFragment(inst=inst, encoded=bytearray(encoded), fixups=fixups)
        self._append(fragment)
        return fragment

    def emit_insts(self, insts: Sequence[MCInst]) -> None:
        """Encode and emit several instructions."""
        for inst in insts:
            self.emit_inst(inst)

    def emit_bytes(self, data: bytes, fixups: Sequence[MCFixup] = ()) -> MCDataFragment:
        """Emit literal bytes."""
        fragment = MCDataFragment(contents=bytearray(data), fixups=list(fixups))
        self._append(fragment)
        return fragment

    def emit_align(self, alignment: int, fill: int = 0xCC) -> MCAlignFragment:
        """Emit padding up to the next multiple of *alignment*."""
        fragment = MCAlignFragment(alignment=alignment, fill=fill)
        self._append(fragment)
        assert self.current is not None
        self.current.alignment = max(self.current.alignment, alignment)
        return fragment

    def emit_padding(self, reserved: int, fill: int = 0xCC) -> MCPaddingFragment:
        """Reserve growth slack, for later in-place patching."""
        fragment = MCPaddingFragment(reserved=reserved, fill=fill)
        self._append(fragment)
        return fragment

    def undefined_symbols(self) -> list[MCSymbol]:
        """Every symbol that is referred to but never defined."""
        return [s for s in self.symbols.values() if not s.defined]


def span_or_invalid(span: Span | None) -> Span:
    """Return *span*, or the invalid span when there is none."""
    return INVALID_SPAN if span is None else span
