"""A debugging dump of the symbolic representation.

This is a dump, not a syntax: nothing parses it, nothing depends on it, and it
carries no promise of stability.  It exists so that a golden test can notice when
instruction selection changes, and so that a person can see what was built.
"""

from __future__ import annotations

from typing import Sequence

from .fragment import (MCAlignFragment, MCDataFragment, MCFragment,
                       MCInstFragment, MCPaddingFragment)
from .symbol import MCSection, MCSymbol


def _symbols_at(symbols: Sequence[MCSymbol], section: MCSection,
                offset: int) -> list[MCSymbol]:
    """The symbols defined at *offset* of *section*."""
    return [s for s in symbols if s.defined and s.section is section and s.offset == offset]


def dump_sections(sections: Sequence[MCSection],
                  symbols: Sequence[MCSymbol]) -> str:
    """Render the built representation of every section."""
    out: list[str] = ["\N{REFERENCE MARK} symbolic assembler dump; internal form, not a syntax"]
    for section in sections:
        out.append("")
        out.append("".join(("section ", section.name,
                            " executable" if section.executable else "",
                            " writable" if section.writable else "")))
        shown: set[int] = set()
        for fragment in section.fragments:
            if not isinstance(fragment, MCFragment):
                continue
            for symbol in _symbols_at(symbols, section, fragment.offset):
                if id(symbol) not in shown:
                    shown.add(id(symbol))
                    out.append("".join((symbol.name, ":")))
            out.append(_dump_fragment(fragment))
    out.append("")
    return "\n".join(out)


def _dump_fragment(fragment: MCFragment) -> str:
    """Render one fragment."""
    encoded = fragment.bytes_of().hex(" ")
    match fragment:
        case MCInstFragment() if fragment.inst is not None:
            body = fragment.inst.render()
            fixups = "".join(("   \N{REFERENCE MARK} fixup ", ", ".join(
                "".join((f.kind.label, " \N{RIGHTWARDS ARROW} ", f.target.render()))
                for f in fragment.fixups))) if fragment.fixups else ""
            return "".join(("    ", encoded.ljust(24), " ", body, fixups))
        case MCAlignFragment():
            return "".join(("    ", encoded.ljust(24),
                            " \N{REFERENCE MARK} align ", str(fragment.alignment)))
        case MCPaddingFragment():
            return "".join(("    ", encoded.ljust(24),
                            " \N{REFERENCE MARK} growth slack ", str(fragment.reserved)))
        case MCDataFragment():
            return "".join(("    ", encoded.ljust(24), " \N{REFERENCE MARK} data"))
        case _:
            return "    \N{REFERENCE MARK} empty fragment"
