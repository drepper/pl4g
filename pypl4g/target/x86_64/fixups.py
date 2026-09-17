"""How the object format's relocations name this target's fixups.

This architecture needs no fixup kinds of its own: a displacement and an
address are the four and eight byte fields every target has, and the generic
ones say all there is to say.  What is here is the one thing that *is* this
architecture's -- which relocation number means which of them -- for reading the
runtime built from C.
"""

from __future__ import annotations

from typing import Final

from ...mc.fixup import ABS32, ABS64, PCREL32, FixupKind

#: Which of these kinds the object format's relocation types come to.  A number
#: here is a promise about what `bin/pl4g-runtime` may find in the runtime's
#: object; one it finds and this does not have stops the extraction rather than
#: being filled in wrongly.
#:
#: `PLT32` is a call through a table that is not there: nothing this compiler
#: builds is dynamically linked, so what it comes to is the ordinary
#: displacement a call already carries.
FROM_ELF: Final[dict[int, str]] = {
    1: ABS64.name,       # R_X86_64_64
    2: PCREL32.name,     # R_X86_64_PC32
    4: PCREL32.name,     # R_X86_64_PLT32
    10: ABS32.name,      # R_X86_64_32
    11: ABS32.name,      # R_X86_64_32S
}


#: Every kind a packaged patch may name, by the name the patch carries.
BY_NAME: Final[dict[str, FixupKind]] = {
    one.name: one for one in (ABS32, ABS64, PCREL32)}
