"""How an AArch64 instruction is encoded.

Every instruction is one 32-bit word, so the description is the shared
fixed-width one: a template plus the bits each operand occupies.  This module
exists to name that shape for this architecture and to fix the width.
"""

from __future__ import annotations

from typing import Final

from ...mc.fixedwidth import (Field, FieldKind, FixedWidthInstDesc)

#: Every instruction is one word.
INSTRUCTION_SIZE: Final[int] = 4


class A64InstDesc(FixedWidthInstDesc):
    """One row of the AArch64 encoding table."""


__all__ = ["A64InstDesc", "Field", "FieldKind", "INSTRUCTION_SIZE"]
