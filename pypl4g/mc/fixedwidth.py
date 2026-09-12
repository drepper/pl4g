"""Encoding for architectures whose instructions are all the same width.

Such an architecture describes an instruction as a template -- the word with
every operand field left as zero -- and a list saying which bits each operand
occupies.  Encoding is then one loop over that list: no prefixes, no escapes,
no length to work out.

That shape is shared by more than one architecture, so it lives here rather than
in any one of them.  It is still not the *only* shape: a byte-stream
architecture describes an encoding quite differently, which is why the
descriptor a target extends says only what an instruction is.

What stays with each target is the table, the relocations and how a relocated
value is stored -- a field whose bits are scattered across the word, as several
of these architectures have, can only be described where it is defined.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ..source.location import INVALID_SPAN, Span
from .desc import InstDesc
from .fixup import FixupKind, MCFixup
from .inst import MCInst
from .operand import MCImm, MCOperand, MCReg, MCSymRef
from .reg import PhysReg, Reg, VirtReg


class EncodingError(Exception):
    """An instruction cannot be encoded as written."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


class FieldKind(Enum):
    """What goes into a run of bits."""

    #: A register number.
    REGISTER = "register"
    #: An immediate, divided down by the field's shift before insertion.
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
    #: how a branch offset drops the bits every instruction address has as zero.
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
class FixedWidthInstDesc(InstDesc):
    """One row of a fixed-width architecture's encoding table."""

    #: The instruction word with every operand field zero.
    template: int = 0
    fields: tuple[Field, ...] = ()


def physical(reg: Reg, span: Span | None) -> PhysReg:
    """Return *reg* as a physical register, or refuse to encode it.

    Refusing here is the contract the register allocator has to satisfy: once it
    runs, no virtual register reaches the encoder.
    """
    if isinstance(reg, VirtReg):
        raise EncodingError("".join((
            "virtual register %v", str(reg.ident),
            " reached the encoder; it has not been assigned")), span)
    return reg


def _register_field(operand: MCOperand, field: Field, span: Span | None) -> int:
    """The bits the register an operand names contributes."""
    if not isinstance(operand, MCReg):
        raise EncodingError("a register operand was expected", span)
    return physical(operand.reg, span).enc & field.mask


def _immediate_field(operand: MCOperand, field: Field, span: Span | None) -> int:
    """The bits an immediate operand contributes."""
    if not isinstance(operand, MCImm):
        raise EncodingError("an immediate operand was expected", span)
    if not field.fits(operand.value):
        raise EncodingError("".join((
            "an immediate of ", str(operand.value), " does not fit the ",
            str(field.width), "-bit field of this instruction")), span)
    return (operand.value >> field.shift) & field.mask


def _operand(operands: Sequence[MCOperand], field: Field,
             span: Span | None) -> MCOperand:
    """The operand a field refers to."""
    if field.operand >= len(operands):
        raise EncodingError("".join((
            "an encoding names operand ", str(field.operand),
            " but only ", str(len(operands)), " were given")), span)
    return operands[field.operand]


def _relocation(operand: MCOperand, field: Field, span: Span | None) -> MCFixup:
    """Record that a field must be filled in once addresses are known.

    The bits stay as the template left them, because a relocated value is
    inserted into the word rather than written over it: the same word holds the
    opcode and the registers.
    """
    if not isinstance(operand, MCSymRef):
        raise EncodingError("a symbol reference was expected", span)
    if field.reloc is None:
        raise EncodingError("a relocation field names no relocation kind", span)
    return MCFixup(offset=0, kind=field.reloc, target=operand.expr,
                   span=span if span is not None else INVALID_SPAN)


def encode(inst: MCInst, size: int) -> tuple[bytes, list[MCFixup]]:
    """Encode one instruction into its word and the fixups it leaves behind."""
    desc = inst.desc
    if not isinstance(desc, FixedWidthInstDesc):
        raise EncodingError("".join((
            "'", desc.mnemonic, "' is not described for this architecture")))
    span = inst.span if inst.span.is_valid else None
    word = desc.template
    fixups: list[MCFixup] = []

    for field in desc.fields:
        operand = _operand(inst.operands, field, span)
        match field.kind:
            case FieldKind.REGISTER:
                word |= _register_field(operand, field, span) << field.lsb
            case FieldKind.IMMEDIATE:
                word |= _immediate_field(operand, field, span) << field.lsb
            case FieldKind.RELOCATION:
                fixups.append(_relocation(operand, field, span))

    return word.to_bytes(size, "little"), fixups


def read_word(data: bytearray, offset: int, size: int) -> int:
    """The instruction word at *offset*."""
    return int.from_bytes(data[offset:offset + size], "little")


def write_word(data: bytearray, offset: int, size: int, word: int) -> None:
    """Store the instruction word at *offset*."""
    data[offset:offset + size] = (word & ((1 << (size * 8)) - 1)).to_bytes(size, "little")


def insert_bits(data: bytearray, offset: int, size: int, value: int, lsb: int,
                width: int) -> None:
    """Put *value* into a run of bits of the word at *offset*."""
    word = read_word(data, offset, size)
    mask = (1 << width) - 1
    write_word(data, offset, size, (word & ~(mask << lsb)) | ((value & mask) << lsb))
