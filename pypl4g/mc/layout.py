"""Laying fragments out within their sections.

Offsets are assigned first and only then are fixups resolved, so that emission
never has to wait for a symbol to be defined.  Instructions whose encoding can
grow are re-encoded until nothing changes; growth only ever increases a size, so
the loop terminates.
"""

from dataclasses import dataclass
from typing import Callable, Sequence

from .fragment import MCAlignFragment, MCDataFragment, MCFragment, MCInstFragment
from .symbol import MCSection, MCSymbol

#: Re-encodes an instruction fragment, returning whether its size changed.
type Relaxer = Callable[[MCInstFragment, int], bool]


def align_up(value: int, alignment: int) -> int:
    """Round *value* up to the next multiple of *alignment*."""
    if alignment <= 1:
        return value
    remainder = value % alignment
    return value if remainder == 0 else value + alignment - remainder


@dataclass(slots=True)
class SectionLayout:
    """The result of laying out one section."""

    section: MCSection
    size: int


def layout_section(section: MCSection) -> SectionLayout:
    """Assign every fragment of *section* its offset, and return the total size."""
    offset = 0
    for fragment in section.fragments:
        if not isinstance(fragment, MCFragment):
            continue
        if isinstance(fragment, MCAlignFragment):
            fragment.set_size(align_up(offset, fragment.alignment) - offset)
        fragment.offset = offset
        offset += fragment.size
    section.size = offset
    return SectionLayout(section=section, size=offset)


def resolve_symbol_offsets(section: MCSection, symbols: Sequence[MCSymbol]) -> None:
    """Turn each symbol's fragment position into a byte offset and a size.

    This has to happen after the section is laid out: alignment padding has no
    size until then, so a symbol defined after it would otherwise be placed
    inside the padding rather than after it.
    """
    fragments = [f for f in section.fragments if isinstance(f, MCFragment)]

    def offset_at(index: int | None) -> int | None:
        """The byte offset of the fragment at *index*, or the section's end."""
        if index is None:
            return None
        if index >= len(fragments):
            return section.size
        return fragments[index].offset

    for symbol in symbols:
        if not symbol.defined or symbol.section is not section:
            continue
        start = offset_at(symbol.fragment_index)
        if start is None:
            continue
        symbol.offset = start
        end = offset_at(symbol.end_fragment_index)
        if end is not None:
            symbol.size = max(0, end - start)


def place_symbols(sections: Sequence[MCSection], symbols: Sequence[MCSymbol]) -> None:
    """Give every defined symbol the address its section and offset imply."""
    for symbol in symbols:
        if symbol.defined and symbol.section is not None:
            symbol.vaddr = symbol.section.vaddr + symbol.offset


def relax(sections: Sequence[MCSection], relaxer: Relaxer,
          max_rounds: int = 8) -> int:
    """Re-encode growable instructions until every displacement fits.

    Returns the number of rounds it took.  A round that changes nothing ends the
    process; because relaxation only ever grows an encoding, that always happens.
    """
    for round_number in range(1, max_rounds + 1):
        changed = False
        for section in sections:
            layout_section(section)
        for section in sections:
            for fragment in section.fragments:
                if isinstance(fragment, MCInstFragment) and fragment.relaxable:
                    if relaxer(fragment, section.vaddr + fragment.offset):
                        changed = True
        if not changed:
            return round_number
    raise RuntimeError("instruction relaxation did not converge")


def apply_fixups(sections: Sequence[MCSection],
                 patcher: Callable[[MCInstFragment | MCDataFragment, int], None]) -> None:
    """Patch every fixup of every fragment, now that addresses are known."""
    for section in sections:
        for fragment in section.fragments:
            if isinstance(fragment, (MCInstFragment, MCDataFragment)):
                patcher(fragment, section.vaddr + fragment.offset)


def section_bytes(section: MCSection) -> bytes:
    """The bytes of a laid-out section."""
    out = bytearray(section.size)
    for fragment in section.fragments:
        if isinstance(fragment, MCFragment):
            data = fragment.bytes_of()
            out[fragment.offset:fragment.offset + len(data)] = data
    return bytes(out)
