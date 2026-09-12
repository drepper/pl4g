"""How an AArch64 instruction is encoded.

Every instruction is one 32-bit word.  A row therefore holds a template -- the
word with every operand field left as zero -- and a list saying which bits each
operand occupies.  Encoding is then one loop over that list, with no prefixes,
no escapes and no variable length.

This is a completely different shape from the x86-64 description, which is the
point of the descriptor being split: what the two architectures share is what an
instruction *is*, not how it is spelled in memory.
"""

from dataclasses import dataclass
from enum import Enum

from ...mc.desc import InstDesc
from ...mc.fixup import FixupKind


class FieldKind(Enum):
    """What goes into a run of bits."""

    #: A register number.
    REGISTER = "register"
    #: An immediate, shifted right by the field's ``shift`` before insertion.
    IMMEDIATE = "immediate"
    #: An address, which becomes a fixup because it is not known yet.
    RELOCATION = "relocation"


@dataclass(frozen=True, slots=True)
class Field:
    """One run of bits in the instruction word, and what fills it."""

    kind: FieldKind
    #: Which operand of the instruction fills it.
    operand: int
    lsb: int
    width: int = 5
    #: The value is divided by two to this power before being stored, which is
    #: how a branch offset drops the two bits every instruction address has as
    #: zero.
    shift: int = 0
    signed: bool = False
    #: For a relocation field, the kind of fixup to record.
    reloc: FixupKind | None = None

    @property
    def mask(self) -> int:
        """The field's bits, at bit zero."""
        return (1 << self.width) - 1

    def fits(self, value: int) -> bool:
        """Whether *value* can be stored in this field."""
        if value & ((1 << self.shift) - 1):
            return False
        scaled = value >> self.shift
        if self.signed:
            return -(1 << (self.width - 1)) <= scaled < (1 << (self.width - 1))
        return 0 <= scaled <= self.mask


@dataclass(frozen=True, slots=True)
class A64InstDesc(InstDesc):
    """One row of the AArch64 encoding table."""

    #: The instruction word with every operand field zero.
    template: int = 0
    fields: tuple[Field, ...] = ()
