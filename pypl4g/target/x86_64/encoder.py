"""The x86-64 encoder.

One generic function walks a fixed sequence of phases over a table row: legacy
prefixes, then the prefix that carries the register extensions, then the opcode
map escape and the opcode, then ModRM with its SIB byte and displacement, then
the immediate or the branch displacement.  A new prefix family is a new emitter
in the second phase; every other phase is untouched, because the fields that VEX
and EVEX need -- the opcode map number and the mandatory prefix -- are already
the fields a legacy encoding uses.
"""

from __future__ import annotations

from typing import Final, Sequence

from .desc import EncKind, ModRMUse, OpMap, OpSize, X86InstDesc
from ...mc.fixup import MCFixup, PCREL8, PCREL32
from ...mc.inst import MCInst
from ...mc.operand import (BinExpr, ConstExpr, MCImm, MCMem, MCOperand, MCReg,
                           MCSymRef)
from ...mc.reg import PhysReg, Reg, VirtReg
from ...source.location import INVALID_SPAN, Span
from .regs import (GPR_EXTENDED_FIRST, HIGH_BYTE_REGS, REX_REQUIRED_BYTE_REGS,
                   SEGMENT_PREFIX)


class EncodingError(Exception):
    """An instruction cannot be encoded as written."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


_MAP_BYTES: Final[dict[OpMap, bytes]] = {
    OpMap.PRIMARY: b"",
    OpMap.M0F: b"\x0f",
    OpMap.M0F38: b"\x0f\x38",
    OpMap.M0F3A: b"\x0f\x3a",
}

_SCALE_BITS: Final[dict[int, int]] = {1: 0, 2: 1, 4: 2, 8: 3}

#: The encoding of rsp, which in the rm field means "a SIB byte follows" and in
#: the SIB index field means "no index".
_RSP_ENC: Final[int] = 4
#: The encoding of rbp, which in several places means "a displacement follows".
_RBP_ENC: Final[int] = 5


def _physical(reg: Reg, span: Span | None) -> PhysReg:
    """Return *reg* as a physical register, or refuse to encode it.

    Refusing here is the contract the register allocator has to satisfy: once it
    runs, no virtual register reaches the encoder.
    """
    if isinstance(reg, VirtReg):
        raise EncodingError("".join((
            "virtual register %v", str(reg.ident),
            " reached the encoder; it has not been assigned")), span)
    if reg.enc >= GPR_EXTENDED_FIRST and reg.cls.name == "gpr":
        raise EncodingError("".join((
            "register '", reg.name, "' needs the extended register encodings, "
            "which this compiler does not emit yet")), span)
    return reg


def _reg_of(operand: MCOperand, span: Span | None) -> PhysReg:
    """The physical register an operand names."""
    if not isinstance(operand, MCReg):
        raise EncodingError("a register operand was expected", span)
    return _physical(operand.reg, span)


def _mem_operand(desc: X86InstDesc, operands: Sequence[MCOperand]) -> MCMem | None:
    """The memory operand of the instruction, if it has one."""
    for operand in operands:
        if isinstance(operand, MCMem):
            return operand
    return None


def _rex_bits(desc: X86InstDesc, operands: Sequence[MCOperand],
              span: Span | None) -> tuple[int, int, int, int, bool]:
    """Compute the W, R, X and B bits and whether a REX prefix is needed."""
    w = 1 if desc.opsize is OpSize.REXW else 0
    r = x = b = 0
    if desc.modrm is ModRMUse.REG_RM and desc.reg_op is not None:
        r = _reg_of(operands[desc.reg_op], span).enc >> 3
    if desc.plus_reg and desc.reg_op is not None:
        b = _reg_of(operands[desc.reg_op], span).enc >> 3
    elif desc.rm_op is not None:
        rm = operands[desc.rm_op]
        if isinstance(rm, MCReg):
            b = _physical(rm.reg, span).enc >> 3
        elif isinstance(rm, MCMem):
            if rm.base is not None:
                b = _physical(rm.base, span).enc >> 3
            if rm.index is not None:
                x = _physical(rm.index, span).enc >> 3
    needs_empty = any(isinstance(o, MCReg) and isinstance(o.reg, PhysReg)
                      and o.reg.name in REX_REQUIRED_BYTE_REGS for o in operands)
    return w, r, x, b, bool(w or r or x or b or needs_empty)


def _check_high_byte(operands: Sequence[MCOperand], span: Span | None) -> None:
    """Refuse the high-byte registers, which no REX-carrying form may name."""
    for operand in operands:
        if isinstance(operand, MCReg) and isinstance(operand.reg, PhysReg) \
                and operand.reg.name in HIGH_BYTE_REGS:
            raise EncodingError("".join((
                "register '", operand.reg.name,
                "' cannot be used by an instruction that needs a REX prefix")), span)


def _emit_modrm(out: bytearray, mod: int, reg: int, rm: int) -> None:
    """Emit one ModRM byte."""
    out.append((mod << 6) | ((reg & 7) << 3) | (rm & 7))


def _emit_sib(out: bytearray, scale: int, index: int, base: int) -> None:
    """Emit one SIB byte."""
    out.append((_SCALE_BITS[scale] << 6) | ((index & 7) << 3) | (base & 7))


def _emit_memory(out: bytearray, fixups: list[MCFixup], mem: MCMem, reg_field: int,
                 trailing: int, span: Span | None) -> None:
    """Emit the ModRM, SIB and displacement bytes for a memory operand."""
    if mem.rip_relative:
        _emit_modrm(out, 0, reg_field, _RBP_ENC)
        if mem.disp_sym is not None:
            # A displacement beside a symbol is part of what the fixup aims at:
            # the whole of the address goes in the one field, so there is
            # nowhere else for it to be added.
            target = mem.disp_sym if mem.disp == 0 else BinExpr(
                "+", mem.disp_sym, ConstExpr(mem.disp))
            fixups.append(MCFixup(offset=len(out), kind=PCREL32,
                                  target=target, trailing=trailing,
                                  span=span if span is not None else INVALID_SPAN))
            out += b"\x00\x00\x00\x00"
        else:
            out += (mem.disp & 0xFFFFFFFF).to_bytes(4, "little")
        return

    base = _physical(mem.base, span) if mem.base is not None else None
    index = _physical(mem.index, span) if mem.index is not None else None
    if index is not None and index.enc == _RSP_ENC:
        raise EncodingError("'rsp' cannot be used as an index register", span)
    if mem.scale not in _SCALE_BITS:
        raise EncodingError("".join(("a scale of ", str(mem.scale),
                                     " is not encodable; it must be 1, 2, 4 or 8")), span)

    if base is None:
        # No base at all: the absolute and index-only forms both need a SIB byte
        # whose base field says "no base", followed by a full displacement.
        _emit_modrm(out, 0, reg_field, _RSP_ENC)
        _emit_sib(out, mem.scale, index.enc if index is not None else _RSP_ENC, _RBP_ENC)
        out += (mem.disp & 0xFFFFFFFF).to_bytes(4, "little")
        return

    needs_sib = index is not None or (base.enc & 7) == _RSP_ENC
    # rbp and r13 have no zero-displacement form, so they always carry one.
    if mem.disp == 0 and (base.enc & 7) != _RBP_ENC:
        mod = 0
    elif -128 <= mem.disp <= 127:
        mod = 1
    else:
        mod = 2
    _emit_modrm(out, mod, reg_field, _RSP_ENC if needs_sib else base.enc)
    if needs_sib:
        _emit_sib(out, mem.scale, index.enc if index is not None else _RSP_ENC, base.enc)
    if mod == 1:
        out += (mem.disp & 0xFF).to_bytes(1, "little")
    elif mod == 2:
        out += (mem.disp & 0xFFFFFFFF).to_bytes(4, "little")


def _emit_imm(out: bytearray, operand: MCOperand, bits: int, span: Span | None) -> None:
    """Emit an immediate of the given width.

    A value the field cannot hold is refused.  Storing it with its upper bits
    dropped would make the instruction mean a number the program never named.
    """
    if not isinstance(operand, MCImm):
        raise EncodingError("an immediate operand was expected", span)
    if not (-(1 << (bits - 1)) <= operand.value < (1 << bits)):
        raise EncodingError("".join((
            "an immediate of ", str(operand.value), " does not fit the ", str(bits),
            "-bit field of this instruction")), span)
    size = bits // 8
    out += (operand.value & ((1 << bits) - 1)).to_bytes(size, "little")


def _emit_rel(out: bytearray, fixups: list[MCFixup], operand: MCOperand, bits: int,
              span: Span | None) -> None:
    """Emit a branch displacement, leaving a fixup to patch it later."""
    if not isinstance(operand, MCSymRef):
        raise EncodingError("a symbol reference was expected as the branch target", span)
    kind = PCREL32 if bits == 32 else PCREL8
    fixups.append(MCFixup(offset=len(out), kind=kind, target=operand.expr,
                          trailing=0, span=span if span is not None else INVALID_SPAN))
    out += bytes(bits // 8)


#: What the `pp` field of a VEX prefix says, by the prefix byte it stands for.
#: A row that names no mandatory prefix has none, which is zero.
_VEX_PP: Final[dict[int | None, int]] = {None: 0, 0x66: 1, 0xF3: 2, 0xF2: 3}


def _emit_vex(out: bytearray, desc: X86InstDesc, operands: Sequence[MCOperand],
              span: Span | None) -> None:
    """Emit the VEX prefix for one instruction.

    Everything in it is already in the row: which opcode map the opcode is in,
    which prefix byte selects the instruction, and whether the operand size bit
    is set.  What it adds to a REX prefix is two things -- a third register
    operand named in `vvvv`, and the width of the vector registers in `L` -- so
    a three-operand form needs no move before it and the same row serves the
    sixteen-byte and the thirty-two-byte width by the one field.

    Every one of R, X, B and `vvvv` is stored turned round, which is what makes
    a prefix naming none of the extended registers come out as the two-byte
    form rather than as a run of set bits.
    """
    if desc.vex is None:
        raise EncodingError("a VEX row that says nothing about its prefix", span)
    w = 1 if desc.opsize is OpSize.REXW else 0
    r = x = b = 0
    if desc.reg_op is not None:
        r = _reg_of(operands[desc.reg_op], span).enc >> 3
    if desc.rm_op is not None:
        rm = operands[desc.rm_op]
        if isinstance(rm, MCReg):
            b = _physical(rm.reg, span).enc >> 3
        elif isinstance(rm, MCMem):
            if rm.base is not None:
                b = _physical(rm.base, span).enc >> 3
            if rm.index is not None:
                x = _physical(rm.index, span).enc >> 3
    named = 0
    if desc.vex.vvvv_op is not None:
        named = _reg_of(operands[desc.vex.vvvv_op], span).enc
    length = 1 if desc.vex.length == 256 else 0
    pp = _VEX_PP.get(desc.mandatory_prefix)
    if pp is None:
        raise EncodingError("a VEX row whose mandatory prefix has no pp field", span)
    last = ((~named & 0xF) << 3) | (length << 2) | pp
    if x == 0 and b == 0 and w == 0 and desc.map is OpMap.M0F:
        # The two-byte form, which says only what it has to.
        out.append(0xC5)
        out.append(((~r & 1) << 7) | last)
        return
    out.append(0xC4)
    out.append(((~r & 1) << 7) | ((~x & 1) << 6) | ((~b & 1) << 5) | desc.map.value)
    out.append((w << 7) | last)


def encode(inst: MCInst) -> tuple[bytes, list[MCFixup]]:
    """Encode one instruction into bytes and the fixups it leaves behind."""
    desc = inst.desc
    assert isinstance(desc, X86InstDesc)
    operands = inst.operands
    span = inst.span if inst.span.is_valid else None
    out = bytearray()
    fixups: list[MCFixup] = []

    # -- phase 1: the legacy prefixes ------------------------------------------
    # A VEX prefix carries both of these in fields of its own, so they are not
    # emitted beside it: the mandatory prefix becomes `pp` and the operand size
    # becomes `W`.
    mem = _mem_operand(desc, operands)
    if mem is not None and mem.seg is not None:
        out.append(SEGMENT_PREFIX[mem.seg.name])
    if "lock" in inst.prefixes:
        out.append(0xF0)
    if desc.enc is EncKind.LEGACY:
        if desc.mandatory_prefix is not None:
            out.append(desc.mandatory_prefix)
        elif desc.opsize is OpSize.P66:
            out.append(0x66)

    # -- phase 2: the prefix carrying the register extensions ------------------
    if desc.enc is EncKind.LEGACY:
        w, r, x, b, needed = _rex_bits(desc, operands, span)
        if needed:
            _check_high_byte(operands, span)
            out.append(0x40 | (w << 3) | (r << 2) | (x << 1) | b)
    elif desc.enc is EncKind.VEX:
        _emit_vex(out, desc, operands, span)
    else:
        raise EncodingError("".join((
            "the ", desc.enc.value, " encodings are not emitted yet")), span)

    # -- phase 3: the opcode map escape and the opcode -------------------------
    # A VEX prefix says which map the opcode is in, so the escape bytes that
    # would have said it are not emitted either.
    if desc.enc is EncKind.LEGACY:
        out += _MAP_BYTES[desc.map]
    opcode = desc.opcode
    if desc.plus_reg:
        if desc.reg_op is None:
            raise EncodingError("a row with plus_reg names no register operand", span)
        opcode |= _reg_of(operands[desc.reg_op], span).enc & 7
    out.append(opcode)

    # -- phase 4: ModRM, SIB and the displacement ------------------------------
    if desc.modrm is not ModRMUse.NONE:
        if desc.rm_op is None:
            raise EncodingError("a row with a ModRM byte names no rm operand", span)
        if desc.modrm is ModRMUse.EXT_RM:
            if desc.ext is None:
                raise EncodingError("a row with an extension digit does not give one", span)
            reg_field = desc.ext
        else:
            if desc.reg_op is None:
                raise EncodingError("a row with a ModRM byte names no reg operand", span)
            reg_field = _reg_of(operands[desc.reg_op], span).enc
        rm = operands[desc.rm_op]
        if isinstance(rm, MCReg):
            _emit_modrm(out, 3, reg_field, _physical(rm.reg, span).enc)
        elif isinstance(rm, MCMem):
            trailing = desc.imm_bits // 8 if desc.imm_bits is not None else 0
            _emit_memory(out, fixups, rm, reg_field, trailing, span)
        else:
            raise EncodingError("a register or memory operand was expected", span)

    # -- phase 5: the immediate or the branch displacement ---------------------
    if desc.imm_op is not None:
        if desc.imm_bits is None:
            raise EncodingError("a row with an immediate does not give its width", span)
        _emit_imm(out, operands[desc.imm_op], desc.imm_bits, span)
    if desc.rel_op is not None:
        if desc.rel_bits is None:
            raise EncodingError("a row with a branch target does not give its width", span)
        _emit_rel(out, fixups, operands[desc.rel_op], desc.rel_bits, span)

    return bytes(out), fixups
