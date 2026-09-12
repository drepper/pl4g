"""The AArch64 encoder.

One word, one loop.  There are no prefixes, no opcode escapes and no variable
length: the whole encoding is a template with operand fields written into it, so
the encoder has nothing to decide.
"""

from typing import Sequence

from ...mc.fixup import MCFixup
from ...mc.inst import MCInst
from ...mc.operand import MCImm, MCOperand, MCReg, MCSymRef
from ...mc.reg import PhysReg, Reg, VirtReg
from ...source.location import INVALID_SPAN, Span
from .desc import A64InstDesc, Field, FieldKind
from .opcodes import INSTRUCTION_SIZE


class EncodingError(Exception):
    """An instruction cannot be encoded as written."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


def _physical(reg: Reg, span: Span | None) -> PhysReg:
    """Return *reg* as a physical register, or refuse to encode it.

    Refusing here is the contract the register allocator has to satisfy: once it
    runs, no virtual register reaches the encoder.
    """
    if isinstance(reg, VirtReg):
        raise EncodingError("".join((
            "virtual register %v", str(reg.ident),
            " reached the encoder; it has not been assigned")), span)
    return reg


def _register_field(operand: MCOperand, span: Span | None) -> int:
    """The five-bit number of the register an operand names."""
    if not isinstance(operand, MCReg):
        raise EncodingError("a register operand was expected", span)
    return _physical(operand.reg, span).enc & 0x1F


def _immediate_field(operand: MCOperand, field: Field, span: Span | None) -> int:
    """The bits an immediate operand contributes."""
    if not isinstance(operand, MCImm):
        raise EncodingError("an immediate operand was expected", span)
    if not field.fits(operand.value):
        raise EncodingError("".join((
            "an immediate of ", str(operand.value), " does not fit the ",
            str(field.width), "-bit field of this instruction")), span)
    return (operand.value >> field.shift) & field.mask


def encode(inst: MCInst) -> tuple[bytes, list[MCFixup]]:
    """Encode one instruction into its word and the fixups it leaves behind."""
    desc = inst.desc
    if not isinstance(desc, A64InstDesc):
        raise EncodingError("".join((
            "'", desc.mnemonic, "' is not described for this architecture")))
    span = inst.span if inst.span.is_valid else None
    word = desc.template
    fixups: list[MCFixup] = []

    for field in desc.fields:
        operand = _operand(inst.operands, field, span)
        match field.kind:
            case FieldKind.REGISTER:
                word |= _register_field(operand, span) << field.lsb
            case FieldKind.IMMEDIATE:
                word |= _immediate_field(operand, field, span) << field.lsb
            case FieldKind.RELOCATION:
                fixups.append(_relocation(operand, field, span))

    return word.to_bytes(INSTRUCTION_SIZE, "little"), fixups


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

    The bits are left as they are in the template, because the value is inserted
    into the word rather than written over it.
    """
    if not isinstance(operand, MCSymRef):
        raise EncodingError("a symbol reference was expected", span)
    if field.reloc is None:
        raise EncodingError("a relocation field names no relocation kind", span)
    return MCFixup(offset=0, kind=field.reloc, target=operand.expr,
                   span=span if span is not None else INVALID_SPAN)
