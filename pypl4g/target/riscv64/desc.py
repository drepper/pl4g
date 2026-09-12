"""How a RISC-V instruction is encoded.

Every instruction of the base set is one 32-bit word, so the description is the
shared fixed-width one: a template plus the bits each operand occupies.

The compressed extension, which adds 16-bit forms, is deliberately not used.  A
fixed width keeps the layout exact, and the specification asks for small code
rather than the smallest possible code; enabling it later means rows whose size
differs, which the table's shortest-encoding rule already handles.
"""

from typing import Final

from ...mc.fixedwidth import Field, FieldKind, FixedWidthInstDesc

#: Every instruction of the base set is one word.
INSTRUCTION_SIZE: Final[int] = 4


class RVInstDesc(FixedWidthInstDesc):
    """One row of the RISC-V encoding table."""


__all__ = ["RVInstDesc", "Field", "FieldKind", "INSTRUCTION_SIZE"]
