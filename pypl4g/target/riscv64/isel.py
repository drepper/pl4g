"""Instruction selection for RISC-V.

The builder speaks in three-address form with the destination first, which is
what this architecture's instructions already are, so nothing has to be lowered.

There is only one register width, so a narrow value is not a narrow register: an
``i32`` lives in a full register, sign extended, and it is the *instruction* that
says how wide the operation is.  That is why the return value here is placed in
the whole register rather than in a view of it, as on the other two backends.
"""

from __future__ import annotations

from dataclasses import replace

from typing import TYPE_CHECKING, Final, Mapping, Sequence

from ...mc import ops
from ...mc.asmbuilder import InstructionSelector
from ...mc.desc import InstrTable, SelectionError
from ...mc.inst import MCInst
from ...mc.operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef
from ...mc.ops import Condition, Op, Rounding
from . import isa
from ...ir.inst import BinOp, Ordering, UnOp
from ...mc.reg import Reg, VirtReg
from ...mc.operand import SymExpr
from ...source.location import Span
from ..branches import (CONDITIONS, Move, UnsupportedBranch,
                        folded_into_branch, sequenced,
                        labels_of,
                        lower_branch, lower_comparison)
from .. import statuses
from ..faults import Messages, describe
from ..ordering import cannot_order
from ..pool import Constants
from ..narrow import normalize
from ...ir.value import Value
from ...ir.layout import (DataLayout, align_of, part_offsets_of, size_of,
                          tag_offset_of)
from ...ir.types import (BOOL, ResultType, made_of_parts, parts_of)
from ..callconv import (TooManyArguments, argument_places, destroyed_by,
                        result_places)
from ..bitcount import COUNTING, lower_count
from ..saturate import (DIVISION, EXTREMA, NAMES, SATURATING, TRAPPING,
                        Unsupported,
                        SHIFTS, WRAPPING, lower_division_result,
                        lower_extremum, lower_saturating, lower_shift,
                        lower_trapping, lower_wrapping)
from . import ops as rvops
from ...ir.function import DEFAULT_CCONV, SYSTEM_CCONV
from .abi import lookup as lookup_cconv
from .startup import ABORT_SYMBOL, REPORT_SYMBOL, SYSCALLS
from .opcodes import IMM12_MAX, IMM12_MIN, RISCV_INSTRS
from .regs import FPR, GPR, INFO, RA, SP, ZERO

if TYPE_CHECKING:
    from ...mc.reg import PhysReg
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo, RegUnit
    from ...ir.types import Type
    from ..callconv import CallConvDesc
    from ...source.manager import SourceManager

#: The mnemonic that implements each architecture-neutral binary operation.
_BINARY: Final[dict[str, str]] = {
    ops.PLUS.name: "add",
    ops.MINUS.name: "sub",
    ops.TIMES.name: "mul",
    ops.XOR.name: "xor",
    ops.AND.name: "and",
    ops.OR.name: "or",
}

#: Operations that take one source.  The complement has no instruction of its
#: own here; it is an exclusive-or with every bit set, which the table names.
_UNARY: Final[dict[str, str]] = {
    ops.NOT.name: "not",
}

#: Operations that are one instruction with no operands.
_NULLARY: Final[dict[str, str]] = {
    rvops.ENVIRONMENT_CALL.name: "ecall",
    ops.TRAP.name: "unimp",
}


#: The branch for each condition.  The architecture has six of the ten; the
#: other four are these with the operands the other way round, which is what
#: `Condition.swapped` is for.
_CONDITIONAL: Final[dict[Condition, str]] = {
    Condition.EQ: "beq", Condition.NE: "bne",
    Condition.SLT: "blt", Condition.SGE: "bge",
    Condition.ULT: "bltu", Condition.UGE: "bgeu",
}


#: What a branch compares against where its condition is a value rather than a
#: comparison.  The width is the one this target writes a small immediate in.
ZERO_IMMEDIATE: Final[MCImm] = MCImm(0, 12)


#: The four a floating-point value answers to.  There are no bitwise operations
#: on one and no saturating ones: what those mean is a question about bits, and
#: a floating-point type says the value is a number and not its bits.
_FLOAT_OPERATIONS: Final[dict[BinOp, Op]] = {
    BinOp.ADD: ops.PLUS, BinOp.SUB: ops.MINUS, BinOp.MUL: ops.TIMES,
    BinOp.FDIV: ops.DIVIDE,
    # The larger and the smaller of two.  Only the signed pair of the four ever
    # stands over a floating-point value: there is no unsigned float.
    BinOp.SMAX: ops.LARGER, BinOp.SMIN: ops.SMALLER,
}

#: Which of them answer one of the two they were given, and so cannot answer
#: anything the program did not have already -- there is nothing to ask about
#: what they came to.
_KEEPS_A_NUMBER: Final[frozenset[Op]] = frozenset((ops.LARGER, ops.SMALLER))

#: Which rounding each of the four operations asks for.
_ROUNDINGS: Final[dict[UnOp, Rounding]] = {
    UnOp.FLOOR: Rounding.DOWN, UnOp.CEIL: Rounding.UP,
    UnOp.NEAREST: Rounding.NEAREST, UnOp.ROUNDED: Rounding.CURRENT,
}

#: Above this, a floating-point value of each width is a whole number already:
#: the format has no room left for a fraction.  It is also where the conversion
#: through an integer would stop being exact, so one number answers both.
_WHOLE_ABOVE: Final[dict[int, float]] = {32: float(1 << 23), 64: float(1 << 52)}

#: What each operation of the representation is called in the assembler.
_OPERATIONS: Final[dict[BinOp, Op]] = {
    BinOp.ADD: ops.PLUS, BinOp.SUB: ops.MINUS, BinOp.MUL: ops.TIMES,
    BinOp.AND: ops.AND, BinOp.OR: ops.OR, BinOp.XOR: ops.XOR,
    # The wrapping three are the same instructions with nothing asked
    # afterwards about what they came to.
    BinOp.WRAP_ADD: ops.PLUS, BinOp.WRAP_SUB: ops.MINUS,
    BinOp.WRAP_MUL: ops.TIMES,
}

#: The same for the operations that take one operand.
_UNARY_OPERATIONS: Final[dict[UnOp, Op]] = {
    UnOp.NOT: ops.NOT, UnOp.NEG: ops.NEG,
}


#: Where a function's own room is measured from.
STACK_POINTER: Final = SP


#: What memory looks like here.  Every one of these targets has an eight-byte
#: pointer, and nothing else about the layout differs between them.
_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)

#: The two fences the orderings come to, as the twelve bits the architecture
#: holds the mode, what comes before and what comes after in.  `fence r, rw`
#: says nothing read or written after may move before a read, which is what an
#: acquiring read wants after it; `fence rw, w` says nothing read or written
#: before may move after a write, which is what a releasing write wants before
#: it.  Written as numbers because that is what the field is, and checked
#: against the assembler's own spelling by the encoding test.
FENCE_R_RW: Final[int] = 0x023
FENCE_RW_W: Final[int] = 0x031


class UnsupportedOperation(Exception):
    """The backend has no selection rule for this operation yet."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


class RVSelector(InstructionSelector):
    """Turns builder calls into RISC-V instructions."""

    def __init__(self, table: InstrTable | None = None,
                 built_for: isa.ISA | None = None) -> None:
        self.table = table if table is not None else InstrTable(RISCV_INSTRS)
        #: What the program is built for.  Unlike the other architectures, what
        #: this machine can do is a list rather than a name, and the few
        #: extensions the code generator can use today are asked of it here.
        self.isa: isa.ISA = (built_for if built_for is not None
                             else isa.parse(isa.DEFAULT))

    def _inst(self, mnemonic: str, operands: Sequence[MCOperand], span: Span) -> MCInst:
        """Select the encoding of *mnemonic* for *operands*."""
        try:
            desc = self.table.select(mnemonic, operands)
        except SelectionError as exc:
            raise UnsupportedOperation(str(exc), span if span.is_valid else None) from exc
        return MCInst(desc=desc, operands=tuple(operands), span=span)

    #: Which load reads a value of a given width, and how it widens it.  There
    #: being no narrower register, the instruction is what says which happens.
    _LOADS: Final[dict[tuple[int, bool], str]] = {
        (8, False): "lbu", (8, True): "lb",
        (16, False): "lhu", (16, True): "lh",
        (32, False): "lwu", (32, True): "lw",
        (64, False): "ld", (64, True): "ld",
    }

    def _is_float(self, reg: Reg) -> bool:
        """Whether *reg* is one of the registers a floating-point value lives in."""
        return reg.cls is FPR

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        if self._is_float(dst):
            return self._select_float_move(dst, src, span)
        if isinstance(src, MCReg) and self._is_float(src.reg):
            raise UnsupportedOperation(
                "moving a floating-point value into an ordinary register", span)
        if isinstance(src, MCMem):
            return self._select_load(dst, src, span)
        if isinstance(src, MCReg) and src.reg is dst:
            return ()
        if isinstance(src, MCReg):
            return (self._inst("mv", (MCReg(dst), src), span),)
        if isinstance(src, MCImm):
            return self._materialize(dst, src.value, span)
        raise UnsupportedOperation("moving from this kind of operand", span)

    #: Which load and which store carry a floating-point value of each width.
    _FLOAT_LOADS: Final[dict[int, str]] = {32: "flw", 64: "fld"}
    _FLOAT_STORES: Final[dict[int, str]] = {32: "fsw", 64: "fsd"}

    def _select_float_move(self, dst: Reg, src: MCOperand,
                           span: Span) -> Sequence[MCInst]:
        """Instructions that put a floating-point value into *dst*.

        There is no move between two floating-point registers here: what stands
        for one is the instruction that copies a value and takes its sign from
        the same value, which is the value unchanged, not-a-number included.
        """
        if isinstance(src, MCMem):
            width = src.size_bits if src.size_bits is not None else 64
            mnemonic = self._FLOAT_LOADS.get(width)
            if mnemonic is None:
                raise UnsupportedOperation("".join((
                    "reading ", str(width), " bits of floating point")), span)
            offset = MCImm(src.disp, 12)
            if src.disp_sym is None:
                if src.base is None:
                    raise UnsupportedOperation(
                        "a floating-point read of nowhere", span)
                return (self._inst(mnemonic, (MCReg(dst), MCReg(src.base),
                                              offset), span),)
            symbol = MCSymRef(src.disp_sym)
            held = MCReg(INFO.new_virtual(GPR, 64, spillable=False))
            return (
                self._inst("auipc.hi20", (held, symbol), span),
                self._inst("addi.lo12", (held, held, symbol), span),
                self._inst(mnemonic, (MCReg(dst), held, offset), span),
            )
        if isinstance(src, MCReg):
            if src.reg is dst:
                return ()
            return (self._inst("fsgnj.d", (MCReg(dst), src, src), span),)
        raise UnsupportedOperation(
            "putting that kind of operand in a floating-point register", span)

    def _materialize(self, dst: Reg, value: int, span: Span) -> Sequence[MCInst]:
        """Instructions that build the constant *value*.

        Three cases, each built out of the one below it.  Twelve bits is one
        instruction.  Thirty-two is an upper-immediate load and an add, where
        the add's twelve bits are *signed* -- so the upper half is rounded up
        when the lower one will come out negative, which is what the
        sign-adjustment below does.  Anything wider is the upper part built the
        same way, shifted into place, and the last twelve bits added.

        The shift goes as far left as the upper part's own trailing zeroes
        allow, so that a constant like a power of two costs two instructions
        rather than five.  This is the sequence LLVM generates and it is worth
        following exactly: it is the one the disassembly of every other RISC-V
        program looks like.
        """
        register = MCReg(dst)
        built: list[MCInst] = []
        # What arrives may be the unsigned reading of a pattern whose top bit is
        # set; every instruction below works on the pattern read as signed, so
        # the two readings are made one here and not in four places further on.
        if value >= (1 << 63):
            value -= 1 << 64

        def build(remaining: int) -> None:
            if IMM12_MIN <= remaining <= IMM12_MAX:
                built.append(self._inst("li", (register, MCImm(remaining, 12)), span))
                return
            low = _signed_twelve(remaining)
            upper = (remaining - low) >> 12
            if -(1 << 31) <= remaining < (1 << 31):
                built.append(self._inst(
                    "lui", (register, MCImm(upper & 0xFFFFF, 20, signed=False)), span))
                if low:
                    built.append(self._inst(
                        "addiw", (register, register, MCImm(low, 12)), span))
                return
            # The upper part is built first and then shifted into place, so the
            # zeroes it ends in are shifted through rather than materialized.
            spare = (upper & -upper).bit_length() - 1
            build(upper >> spare)
            built.append(self._inst(
                "slli", (register, register, MCImm(12 + spare, 6, signed=False)), span))
            if low:
                built.append(self._inst(
                    "addi", (register, register, MCImm(low, 12)), span))

        build(value)
        return tuple(built)

    def _select_load(self, dst: Reg, src: MCMem, span: Span) -> Sequence[MCInst]:
        """Instructions that read memory into a register.

        An address is built in two steps, and the second is measured from the
        first rather than from itself -- so the two must stay next to each
        other, and the register holding the address between them must be one
        the allocator will not send to the frame.  Putting the address in the
        destination would make that register the value's own, which is long
        lived and exactly what does get spilled; a register of its own lives for
        three instructions and is never a candidate.
        """
        width = src.size_bits if src.size_bits is not None else 64
        mnemonic = self._LOADS.get((width, src.signed))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "reading ", str(width), " bits from memory")), span)
        if src.disp_sym is None:
            base = src.base if src.base is not None else dst
            return (self._inst(mnemonic, (MCReg(dst), MCReg(base),
                                          MCImm(src.disp, 12)), span),)
        symbol = MCSymRef(src.disp_sym)
        held = MCReg(INFO.new_virtual(GPR, 64, spillable=False))
        return (
            self._inst("auipc.hi20", (held, symbol), span),
            self._inst("addi.lo12", (held, held, symbol), span),
            self._inst(mnemonic, (MCReg(dst), held, MCImm(src.disp, 12)), span),
        )

    def _accepting(self, mnemonic: str, operands: Sequence[MCOperand],
                   span: Span) -> tuple[Sequence[MCInst], Sequence[MCOperand]]:
        """The operands as a row of *mnemonic* will take them.

        An immediate is used where the table has a form that carries one, since
        that is a whole instruction saved, and put in a register where it has
        not.  Asking the table rather than deciding here is what keeps the two
        in step: adding a row that carries an immediate is then all it takes for
        one to be used.
        """
        try:
            self.table.select(mnemonic, operands)
            return (), operands
        except SelectionError:
            pass
        before: list[MCInst] = []
        rewritten: list[MCOperand] = []
        for operand in operands:
            if not isinstance(operand, MCImm):
                rewritten.append(operand)
                continue
            carried = INFO.new_virtual(GPR, 64)
            before.extend(self.select_move(carried, operand, span))
            rewritten.append(MCReg(carried))
        return before, rewritten

    def select_op(self, op: Op, dst: Reg | None, sources: Sequence[MCOperand],
                  span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over *sources* into *dst*."""
        nullary = _NULLARY.get(op.name)
        if nullary is not None:
            return (self._inst(nullary, (), span),)
        if op is ops.MOVE:
            if dst is None:
                raise UnsupportedOperation("a move needs a destination", span)
            return self.select_move(dst, sources[0], span)
        mnemonic = _BINARY.get(op.name)
        if mnemonic is not None:
            if dst is None or len(sources) != 2:
                raise UnsupportedOperation("".join((
                    "'", op.name, "' needs a destination and two sources")), span)
            before, ready = self._accepting(mnemonic, (MCReg(dst), *sources), span)
            return (*before, self._inst(mnemonic, ready, span))
        mnemonic = _UNARY.get(op.name)
        if mnemonic is not None:
            if dst is None or len(sources) != 1:
                raise UnsupportedOperation("".join((
                    "'", op.name, "' needs a destination and one source")), span)
            before, ready = self._accepting(mnemonic, (MCReg(dst), sources[0]), span)
            return (*before, self._inst(mnemonic, ready, span))
        raise UnsupportedOperation("".join((
            "no RISC-V selection rule for '", op.name, "'")), span)

    #: Which store writes a value of a given width.
    _STORES: Final[dict[int, str]] = {8: "sb", 16: "sh", 32: "sw", 64: "sd"}

    def select_acquire(self, dst: Reg, address: MCMem,
                       span: Span) -> Sequence[MCInst]:
        """An ordinary read followed by a fence.

        This architecture has no acquiring form of a plain load -- the bits that
        say so belong to the atomic instructions -- so the ordering is a fence
        of its own: nothing read or written after may be moved before the read.
        """
        return (*self.select_move(dst, address, span),
                self._inst("fence", (MCImm(FENCE_R_RW, 12),), span))

    def select_release(self, address: MCOperand, value: MCOperand,
                       span: Span) -> Sequence[MCInst]:
        """A fence followed by an ordinary write, for the reason above.

        The fence comes first and names what came before: nothing read or
        written before it may be moved after the write.
        """
        assert isinstance(address, MCMem)
        return (self._inst("fence", (MCImm(FENCE_RW_W, 12),), span),
                *self.select_store(address, value, span))

    def select_store(self, address: MCMem, value: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that write *value* into the memory *address* names.

        As on the other fixed-width architecture the address has to be built in
        a register.  It is asked for as a value like any other, so the allocator
        places it and no register has to be set aside that nothing else may use.
        """
        width = address.size_bits if address.size_bits is not None else 64
        if isinstance(value, MCReg) and self._is_float(value.reg):
            return self._select_float_store(address, value, width, span)
        mnemonic = self._STORES.get(width)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "writing ", str(width), " bits to memory")), span)
        held: Sequence[MCInst] = ()
        if isinstance(value, MCReg):
            source = value
        else:
            carried = INFO.new_virtual(GPR, 64)
            held = self.select_move(carried, value, span)
            source = MCReg(carried)
        place = MCReg(INFO.new_virtual(GPR, 64, spillable=False))
        if address.disp_sym is None:
            base = MCReg(address.base) if address.base is not None else place
            return (*held, self._inst(mnemonic, (source, base,
                                                 MCImm(address.disp, 12)), span))
        symbol = MCSymRef(address.disp_sym)
        return (
            *held,
            self._inst("auipc.hi20", (place, symbol), span),
            self._inst("addi.lo12", (place, place, symbol), span),
            self._inst(mnemonic, (source, place, MCImm(address.disp, 12)),
                       span),
        )

    def _select_float_store(self, address: MCMem, value: MCReg, width: int,
                            span: Span) -> Sequence[MCInst]:
        """Instructions that write a floating-point register to memory."""
        mnemonic = self._FLOAT_STORES.get(width)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "writing ", str(width), " bits of floating point")), span)
        offset = MCImm(address.disp, 12)
        if address.disp_sym is None:
            if address.base is None:
                raise UnsupportedOperation("a floating-point write to nowhere", span)
            return (self._inst(mnemonic, (value, MCReg(address.base), offset),
                               span),)
        place = MCReg(INFO.new_virtual(GPR, 64, spillable=False))
        symbol = MCSymRef(address.disp_sym)
        return (
            self._inst("auipc.hi20", (place, symbol), span),
            self._inst("addi.lo12", (place, place, symbol), span),
            self._inst(mnemonic, (value, place, offset), span),
        )

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            # A call through a register: what is called is whatever it holds,
            # which is how a function held in a value is called.
            return (self._inst("jalr", (target,), span),)
        return (self._inst("jal", (target,), span),)

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        return (self._inst("ret", (), span),)

    def select_jump(self, target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that transfer control to *target*."""
        return (self._inst("j", (target,), span),)

    def select_branch(self, cond: Condition, lhs: MCOperand, rhs: MCOperand,
                      target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *lhs* and *rhs* stand in *cond*.

        A branch here compares two registers itself -- there are no condition
        codes to set first -- so this is one instruction where the other two
        architectures need two.  It has instructions for half the orderings and
        gets the other half by exchanging the operands, and the zero register is
        what a comparison against zero names.
        """
        if _CONDITIONAL.get(cond) is None:
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        held: list[MCInst] = []
        left, first = self._as_register(lhs, span)
        right, second = self._as_register(rhs, span)
        held.extend(first)
        held.extend(second)
        mnemonic = _CONDITIONAL[cond]
        return (*held, self._inst(mnemonic, (left, right, target), span))

    def select_set(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                   span: Span) -> Sequence[MCInst]:
        """Instructions that put whether *lhs* and *rhs* stand in *cond* into *dst*.

        There are no condition codes here and no instruction that reads them, so
        unlike the other two targets this does not compare and then collect the
        answer: the comparison *is* the answer.  The architecture has one, "set
        if less than", in a signed and an unsigned form.

        The other six orderings are that one rearranged.  Exchanging the
        operands turns "less" into "greater"; inverting the answer -- an
        exclusive or with one, the answer being one or zero already -- turns
        "less" into "not less", which is "greater or equal".  Equality has no
        ordering in it at all, so it is a subtraction first and then a question
        about the difference: "unsigned less than one" is "is zero", and
        "unsigned greater than zero" is "is not zero".
        """
        held: list[MCInst] = []
        left, first = self._as_register(lhs, span)
        right, second = self._as_register(rhs, span)
        held.extend(first)
        held.extend(second)
        answer = MCReg(dst)
        if cond in (Condition.EQ, Condition.NE):
            held.append(self._inst("sub", (answer, left, right), span))
            if cond is Condition.EQ:
                held.append(self._inst("sltiu", (answer, answer, MCImm(1, 12)), span))
            else:
                held.append(self._inst("sltu", (answer, MCReg(ZERO), answer), span))
            return tuple(held)
        if cond in (Condition.SGT, Condition.SLE, Condition.UGT, Condition.ULE):
            left, right = right, left
        mnemonic = "sltu" if cond in (Condition.ULT, Condition.ULE, Condition.UGT,
                                      Condition.UGE) else "slt"
        held.append(self._inst(mnemonic, (answer, left, right), span))
        if cond in (Condition.SLE, Condition.SGE, Condition.ULE, Condition.UGE):
            held.append(self._inst("xori", (answer, answer, MCImm(1, 12)), span))
        return tuple(held)

    def select_address(self, dst: Reg, symbol: MCSymRef,
                       span: Span) -> Sequence[MCInst]:
        """Instructions that put the address of *symbol* into *dst*.

        The two halves are not independent here: the second is measured from the
        label of the first, so the two must stay together and the register
        between them must be one the allocator will not send to the frame.
        """
        held = MCReg(INFO.new_virtual(GPR, 64, spillable=False))
        return (self._inst("auipc.hi20", (held, symbol), span),
                self._inst("addi.lo12", (held, held, symbol), span),
                self._inst("mv", (MCReg(dst), held), span))

    def select_widen(self, dst: Reg, src: MCOperand, bits: int, signed: bool,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that put a *bits*-wide value into the whole of *dst*.

        There is one register width here, so an unsigned value of any narrower
        type is already the whole of one.  A signed one has to have its sign
        spread over the rest, and there is no instruction that does it: two
        shifts do, the first pushing the value up to the top of the register and
        the second bringing it back down with copies of its top bit behind it.
        """
        if bits >= 64 or isinstance(src, MCImm):
            return self.select_move(dst, src, span)
        if not isinstance(src, MCReg):
            # A place in memory, which the load itself widens: there is a load
            # per width that copies the sign and one per width that copies
            # nought, and the ordinary move would pick the first.
            narrow = replace(src, size_bits=bits, signed=signed)
            return self.select_move(dst, narrow, span)
        if not signed:
            return self.select_move(dst, src, span)
        spare = MCImm(64 - bits, 6, signed=False)
        return (self._inst("slli", (MCReg(dst), src, spare), span),
                self._inst("srai", (MCReg(dst), MCReg(dst), spare), span))

    def select_clamp(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                     bound: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that put *bound* into *dst* where the two stand in *cond*.

        There is nothing here that moves a register only sometimes, so the
        answer is built as a number and used as a mask: one or zero, taken from
        zero to give all ones or none, and the difference between the two values
        let through it.  Five instructions where the other two need one, and no
        branch, which is what makes it worth doing this way rather than jumping
        over a move.
        """
        held: list[MCInst] = []
        flag = INFO.new_virtual(GPR, 64)
        held.extend(self.select_set(cond, flag, lhs, rhs, span))
        mask = MCReg(flag)
        held.append(self._inst("sub", (mask, MCReg(ZERO), mask), span))
        carried, before = self._as_register(bound, span)
        held.extend(before)
        difference = MCReg(INFO.new_virtual(GPR, 64))
        held.append(self._inst("xor", (difference, MCReg(dst), carried), span))
        held.append(self._inst("and", (difference, difference, mask), span))
        held.append(self._inst("xor", (MCReg(dst), MCReg(dst), difference), span))
        return tuple(held)

    def _as_register(self, operand: MCOperand,
                     span: Span) -> tuple[MCReg, Sequence[MCInst]]:
        """The operand as a register, with whatever it takes to put it in one."""
        if isinstance(operand, MCReg):
            return operand, ()
        if isinstance(operand, MCImm) and operand.value == 0:
            # Zero is a register here, which is what makes a comparison against
            # it cost nothing.
            return MCReg(ZERO), ()
        carried = INFO.new_virtual(GPR, 64)
        return MCReg(carried), self.select_move(carried, operand, span)

    # -- the stack -------------------------------------------------------------

    #: What each of the three shifts is called here.
    _SHIFTS: Final[dict[str, str]] = {
        ops.SHIFT_LEFT.name: "sll", ops.SHIFT_RIGHT.name: "srl",
        ops.SHIFT_RIGHT_SIGNED.name: "sra",
    }

    #: What each operation is called for each width of floating-point value.
    _FLOAT_BINARY: Final[dict[tuple[str, int], str]] = {
        (ops.PLUS.name, 32): "fadd.s", (ops.PLUS.name, 64): "fadd.d",
        (ops.MINUS.name, 32): "fsub.s", (ops.MINUS.name, 64): "fsub.d",
        (ops.TIMES.name, 32): "fmul.s", (ops.TIMES.name, 64): "fmul.d",
        (ops.DIVIDE.name, 32): "fdiv.s", (ops.DIVIDE.name, 64): "fdiv.d",
        (ops.LARGER.name, 32): "fmax.s", (ops.LARGER.name, 64): "fmax.d",
        (ops.SMALLER.name, 32): "fmin.s", (ops.SMALLER.name, 64): "fmin.d",
    }

    def select_float_abs(self, dst: Reg, src: MCOperand, bits: int,
                         span: Span) -> Sequence[MCInst]:
        """Instructions that put the magnitude of *src* into *dst*.

        There is no magnitude instruction here and no need of one: the sign of
        the answer is the two signs exclusive-ored, and a value against itself
        gives a sign of zero whatever it was.
        """
        return (self._inst("".join(("fsgnjx", ".s" if bits == 32 else ".d")),
                           (MCReg(dst), src, src), span),)

    def select_float_extend(self, dst: Reg, src: MCOperand, from_bits: int,
                            to_bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put *src* into *dst* in the wider format."""
        if (from_bits, to_bits) != (32, 64):
            raise UnsupportedOperation("".join((
                "widening ", str(from_bits), " bits of floating point to ",
                str(to_bits))), span)
        return (self._inst("fcvt.d.s", (MCReg(dst), src), span),)

    def select_branch_if_finite(self, value: Reg, bits: int, target: MCSymRef,
                                span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *value* is a finite number.

        There are no flags here, so the question is asked as a value: the
        difference against itself compared with itself, which answers one where
        it is a number and zero where it is not, and a branch on that.
        """
        suffix = ".s" if bits == 32 else ".d"
        held = MCReg(INFO.new_virtual(FPR, _FLOAT_REGISTER_BITS))
        answer = MCReg(INFO.new_virtual(GPR, 64))
        return (self._inst("".join(("fsub", suffix)),
                           (held, MCReg(value), MCReg(value)), span),
                self._inst("".join(("feq", suffix)), (answer, held, held), span),
                self._inst("bne", (answer, MCReg(ZERO), target), span))

    #: What the architecture's three-bit rounding field says.  The fourth is
    #: the one that says "whatever the rounding-mode register names", which is
    #: what makes the dynamic field worth having.
    _ROUNDS: Final[dict[Rounding, int]] = {
        Rounding.NEAREST: 0b000, Rounding.DOWN: 0b010, Rounding.UP: 0b011,
        Rounding.CURRENT: 0b111,
    }

    def select_float_round(self, how: Rounding, dst: Reg, src: MCOperand,
                           bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put the whole number *src* rounds to into *dst*.

        One instruction where the program is built for something that has the
        Zfa extension, which is what the application profiles have had since
        2023 and what this compiler builds for unless told otherwise.  Without
        it there is nothing here that rounds a floating-point number where it
        stands, and the answer costs a round trip through an integer and a
        branch -- which the caller emits, since it needs a label and a selector
        has none.
        """
        if not self.isa.has("zfa"):
            raise UnsupportedOperation(
                "rounding a floating-point number in one instruction", span)
        return (self._inst("fround.s" if bits == 32 else "fround.d",
                           (MCReg(dst), src,
                            MCImm(self._ROUNDS[how], 3, signed=False)), span),)

    def select_float_to_int(self, how: Rounding, dst: Reg, src: MCOperand,
                            bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put *src* into *dst* as a whole number.

        A word for the narrower format and a doubleword for the wider, which in
        both cases is more than enough: this is only ever asked of a value below
        the point where the format has no room for a fraction, and that point is
        far below what the integer holds.
        """
        return (self._inst("fcvt.w.s" if bits == 32 else "fcvt.l.d",
                           (MCReg(dst), src,
                            MCImm(self._ROUNDS[how], 3, signed=False)), span),)

    def select_int_to_float(self, dst: Reg, src: MCOperand, bits: int,
                            span: Span) -> Sequence[MCInst]:
        """Instructions that put the whole number *src* into *dst*.

        No rounding is named because none can happen: every number this is
        handed came out of a value the format holds exactly.
        """
        return (self._inst("fcvt.s.w" if bits == 32 else "fcvt.d.l",
                           (MCReg(dst), src), span),)

    def select_float_copysign(self, dst: Reg, magnitude: MCOperand,
                              sign: MCOperand, bits: int,
                              span: Span) -> Sequence[MCInst]:
        """Instructions that put *magnitude* into *dst* with *sign*'s sign.

        One instruction, this architecture having made the sign-injection the
        thing a move, a magnitude and a negation are all written as.
        """
        return (self._inst("".join(("fsgnj", ".s" if bits == 32 else ".d")),
                           (MCReg(dst), magnitude, sign), span),)

    def select_float_op(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                        bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over two floating-point values."""
        mnemonic = self._FLOAT_BINARY.get((op.name, bits))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "'", op.name, "' on a floating-point value")), span)
        return (self._inst(mnemonic, (MCReg(dst), left, right), span),)

    def select_float_compare(self, cond: Condition, dst: Reg, lhs: MCOperand,
                             rhs: MCOperand, bits: int,
                             span: Span) -> Sequence[MCInst]:
        """Instructions that put whether two floating-point values stand in
        *cond* into *dst*.

        There are no flags here and no need for any: the three comparisons write
        one or zero into an ordinary register, which is what a truth value is.
        The three that are not there are the same three with the operands
        exchanged, and inequality is equality turned round.
        """
        suffix = ".s" if bits == 32 else ".d"
        exchanged = cond in (Condition.SGT, Condition.UGT,
                             Condition.SGE, Condition.UGE)
        first, second = (rhs, lhs) if exchanged else (lhs, rhs)
        asking = {Condition.EQ: "feq", Condition.NE: "feq",
                  Condition.SLT: "flt", Condition.ULT: "flt",
                  Condition.SGT: "flt", Condition.UGT: "flt",
                  Condition.SLE: "fle", Condition.ULE: "fle",
                  Condition.SGE: "fle", Condition.UGE: "fle"}[cond]
        built = [self._inst("".join((asking, suffix)),
                            (MCReg(dst), first, second), span)]
        if cond is Condition.NE:
            built.append(self._inst("xori", (MCReg(dst), MCReg(dst),
                                             MCImm(1, 12)), span))
        return tuple(built)

    def select_shift(self, op: Op, dst: Reg, value: MCOperand, amount: MCOperand,
                     bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that move the bits of *value* by *amount* into *dst*."""
        del bits
        held: list[MCInst] = []
        held_value, before = self._as_register(value, span)
        held.extend(before)
        counted, before = self._as_register(amount, span)
        held.extend(before)
        held.append(self._inst(self._SHIFTS[op.name],
                               (MCReg(dst), held_value, counted), span))
        return tuple(held)

    def select_wide_product(self, low: Reg, high: Reg, left: MCOperand,
                            right: MCOperand, signed: bool,
                            span: Span) -> Sequence[MCInst]:
        """Both halves of the product of *left* and *right*.

        Two instructions, one for each half, and the upper one goes first --
        which is what the architecture itself asks for where a pair like this
        is to be recognised as one multiplication, and is needed anyway where
        the low half is asked for in a register a factor is in.
        """
        held: list[MCInst] = []
        first, before = self._as_register(left, span)
        held.extend(before)
        second, before = self._as_register(right, span)
        held.extend(before)
        held.append(self._inst("mulh" if signed else "mulhu",
                               (MCReg(high), first, second), span))
        held.append(self._inst("mul", (MCReg(low), first, second), span))
        return tuple(held)

    def select_divide(self, dst: Reg, left: MCOperand, right: MCOperand,
                      signed: bool, remainder: bool, bits: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that divide *left* by *right* into *dst*.

        One instruction, there being one for each of the four questions.
        """
        del bits
        held: list[MCInst] = []
        dividend, before = self._as_register(left, span)
        held.extend(before)
        divisor, before = self._as_register(right, span)
        held.extend(before)
        mnemonic = ("rem" if remainder else "div") + ("" if signed else "u")
        held.append(self._inst(mnemonic, (MCReg(dst), dividend, divisor), span))
        return tuple(held)

    def link_slot_size(self) -> int:
        """A whole stack unit, the stack having to stay aligned to sixteen."""
        return 16

    def frame_walk(self, total: int, size: int,
                   link: int) -> tuple[int | None, int]:
        """The call left the return address in a register, so it lies wherever
        the function put it -- and nowhere at all where the function calls
        nothing, which is a function that can only be the innermost frame."""
        return (size if link else None, total)

    def select_save_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put the return address into the frame at *offset*."""
        return (self._inst("sd", (MCReg(RA), MCReg(SP), MCImm(offset, 12)), span),)

    def select_restore_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Instructions that take it back out again."""
        return (self._inst("ld", (MCReg(RA), MCReg(SP), MCImm(offset, 12)), span),)

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*."""
        mnemonic = "fsd" if self._is_float(source) else "sd"
        return (self._inst(mnemonic, (MCReg(source), MCReg(SP), MCImm(slot, 12)),
                           span),)

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        mnemonic = "fld" if self._is_float(destination) else "ld"
        return (self._inst(mnemonic, (MCReg(destination), MCReg(SP),
                                      MCImm(slot, 12)), span),)

    def select_frame(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that make room for *size* bytes on the stack."""
        return (self._inst("addi", (MCReg(SP), MCReg(SP), MCImm(-size, 12)), span),)

    def select_unframe(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that give that room back."""
        return (self._inst("addi", (MCReg(SP), MCReg(SP), MCImm(size, 12)), span),)


def lower_function(asm: Assembler, func: Function, cconv: CallConvDesc,
                   registers: RegisterInfo, messages: Messages | None = None,
                   sources: SourceManager | None = None,
                   constants: Constants | None = None,
                   known_clobbers: Mapping[str, frozenset[RegUnit]] | None = None
                   ) -> None:
    """Build the machine form of one IR function."""
    from ...ir.inst import (CodeInst, AddressInst, BinaryInst, BrInst, CallInst, CmpInst,
                            CondBrInst, SwitchInst,
                            FrameInst, AssertInst,
                            LoadInst, MemStartInst, RetInst, StoreInst,
                            UnaryInst, UnreachableInst, SyscallInst)
    from ...ir.function import Function as _Function
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import (BOOL, BoolType, DictType, EnumType, FloatType,
                             IntType, MEM, PtrType, ResultType, SetType, VOID)
    from ...ir.inst import (CastInst, CastKind, ExtractInst, FailedInst,
                            ErrorInst, TupleInst, UnwrapInst, WrapInst)
    from ...ir.value import FloatConst, UndefConst
    from ...ir.layout import encode_float
    from ..globals import symbol_of

    # What the program is built for, asked once: this architecture says what a
    # machine can do with a list of extensions rather than with a name, and the
    # one the code generator chooses a lowering by is whether rounding is an
    # instruction here or a round trip through an integer.
    selector = asm.selector
    rounds_in_one = isinstance(selector, RVSelector) and selector.isa.has("zfa")
    # The convention is the function's and not the image's: which registers may
    # be given out and which have to be handed back as they were found are two
    # of the things a convention settles, and the specification lets two
    # functions of one compilation settle them differently.
    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "visible",
                       allocation_order=cconv.orders(GPR.name, FPR.name),
                       callee_saved=cconv.callee_saved)
    #: Where each value the function computes is held.  A value gets a register
    #: of its own and the allocator decides which; nothing here knows or cares.
    held: dict[int, VirtReg] = {}
    #: The registers of a value that takes more than one, after the first: the
    #: truth value beside a result's answer, and every member of a tuple after
    #: its first.  Such a value is that many ordinary registers and nothing
    #: aggregate at all, which is what keeps the allocator out of it.
    extra: dict[int, list[VirtReg]] = {}
    returned = _returned_value(func)
    symbol = symbol_name(func)
    labels = labels_of(symbol, func)

    class _Operands:
        """How this backend answers for a value of the representation."""

        def value(self, value: object, span: Span) -> MCOperand:
            """The operand for *value*, a constant being an immediate."""
            if isinstance(value, FloatConst):
                # No instruction on any of these carries a floating-point
                # number, so one goes in the image and is read from there.
                return MCReg(self.floating(value, span))
            constant = _number_of(value)
            if constant is not None:
                number, ty = constant
                return MCImm(number, _immediate_width(number, _is_signed(ty)),
                             signed=_is_signed(ty))
            return self.in_register(value, span)

        def in_register(self, value: object, span: Span) -> MCOperand:
            """The operand for *value*, put in a register if it is not in one."""
            if isinstance(value, FloatConst):
                return MCReg(self.floating(value, span))
            constant = _number_of(value)
            if constant is not None:
                # Every comparison here wants a register on its left, and two
                # constants compared with each other is what a program that has
                # not been folded looks like.
                number, ty = constant
                carried = _new_value(ty, registers)
                asm.loadreg(carried, MCImm(number, 12,
                                           signed=_is_signed(ty)), span)
                return MCReg(carried)
            found = held.get(id(value))
            if found is None:
                raise UnsupportedOperation(
                    "a value this backend did not compute",
                    span if span.is_valid else None)
            return MCReg(found)

        def register_of(self, value: object, span: Span | None) -> VirtReg:
            """The register a value the function computed is held in."""
            found = held.get(id(value))
            if found is None:
                raise UnsupportedOperation(
                    "a value this backend did not compute", span)
            return found

        def flag_of(self, value: object, span: Span | None) -> VirtReg:
            """The register holding whether a result has an answer.

            After the answer's own registers, of which there may be several: a
            result whose answer is a string is three registers and not two, and
            the truth value is the third of them.
            """
            return self.part_of(value, _answer_registers(value), span)

        def part_of(self, value: object, index: int,
                    span: Span | None) -> VirtReg:
            """The register holding one of a value's several parts."""
            if index == 0:
                return self.register_of(value, span)
            found = extra.get(id(value))
            if found is None or index > len(found):
                raise UnsupportedOperation(
                    "a value of several parts this backend did not compute", span)
            return found[index - 1]

        def undefined(self, value: object, ty: Type, span: Span) -> MCOperand:
            """The operand for *value*, where it may be one nothing may read.

            The answer half of an error is such a value.  It is written as zero
            rather than left alone, so that a register still holds something a
            program could read without the reading meaning anything.
            """
            if isinstance(value, UndefConst):
                return MCImm(0, max(32, _width_of(ty)), signed=False)
            return self.value(value, span)

        def destination(self, value: object) -> Reg:
            """The register a value is computed into, for one that is written.

            Only a block parameter is asked for this, and every one of them was
            given a register before any block was walked -- a branch writes a
            parameter of a block that may not have been reached yet.
            """
            found = held.get(id(value))
            if found is None:
                raise UnsupportedOperation("a value this backend did not compute",
                                           None)
            return found

        def floating(self, value: FloatConst, span: Span) -> VirtReg:
            """A register holding the floating-point constant *value*."""
            if constants is None:
                raise UnsupportedOperation(
                    "a floating-point constant, with nowhere to put it", None)
            bits = _width_of(value.ty)
            symbol = constants.symbol(
                encode_float(value.value, value.ty, _LAYOUT), bits // 8)
            held = _new_value(value.ty, registers)
            asm.loadreg(held, asm.mem(disp_sym=SymExpr(asm.streamer.symbol(symbol)),
                                      rip_relative=True, size_bits=bits), span)
            return held

        def scratch(self) -> VirtReg:
            """A register of the full width, for a value with no name of its own."""
            return registers.new_virtual(GPR, 64)

    operands = _Operands()

    def place_of(address: object, span: Span | None, **where: object) -> MCMem:
        """Where in memory an address names, for a read or for a write.

        A variable is named by its symbol and reached relative to the
        instruction; any other address is a value of pointer type, held in a
        register, and that register is the base.  How wide the access is and
        where within the place it falls stay the caller's, because one address
        is read and written at more than one width -- a result is two reads of
        one place -- and because only the caller knows the type.
        """
        if isinstance(address, GlobalVar):
            return asm.mem(disp_sym=SymExpr(asm.streamer.symbol(symbol_of(address))),
                           rip_relative=True, **where)
        return asm.mem(base=operands.register_of(address, span), **where)

    class _Fault:
        """What is emitted where an answer will not fit its type."""

        def __init__(self, what: str, span: Span,
                     status: int = statuses.OVERFLOW) -> None:
            self.text = describe(what, func.name, span, sources)
            #: Which kind of stop this is.  The message says it better and says
            #: it to a person; the number is what a caller reads.
            self.status = status

        def observed(self, asm: Assembler, span: Span) -> None:
            """Report the fault and come back, for a build that observes.

            The same message through the same write, and then a return: a
            condition a build chose to observe is something to say and not
            something to stop for, there being the rest of the run to see.  It is
            the helper a failing test says so through, which wants exactly this.

            The call is given everything the convention lets it destroy, unlike
            the one below: that one does not come back, so what it destroys never
            matters, and this one does.  A build that observes therefore pays for
            spilling around every observed check, which is the right way round --
            it is the semantic a reader asked for and not the one a program
            ships with.
            """
            if messages is None:
                raise UnsupportedOperation(
                    "an operation that can fault, with nowhere to report it", None)
            symbol = messages.symbol(self.text)
            first, second = lookup_cconv(SYSTEM_CCONV).int_arg_regs[:2]
            asm.address(first, symbol, span)
            asm.loadreg(second, asm.imm(len(self.text.encode("utf-8")), 32,
                                        signed=False), span)
            asm.call(REPORT_SYMBOL, span,
                     destroyed_by(None, lookup_cconv(SYSTEM_CCONV), registers,
                                  None))

        def out_of_range(self, asm: Assembler, span: Span) -> None:
            """Report the fault and stop; this does not come back."""
            if messages is None:
                raise UnsupportedOperation(
                    "an operation that can fault, with nowhere to report it", None)
            symbol = messages.symbol(self.text)
            # The runtime is written as instructions rather than lowered, so it
            # follows one settled convention whatever the function reporting the
            # fault follows.
            first, second, third = lookup_cconv(SYSTEM_CCONV).int_arg_regs[:3]
            asm.address(first, symbol, span)
            asm.loadreg(second, asm.imm(len(self.text.encode("utf-8")), 32,
                                        signed=False), span)
            asm.loadreg(third, asm.imm(self.status, 32, signed=False), span)
            asm.call(ABORT_SYMBOL, span)

    # Every block parameter gets its register before any block is walked: a
    # branch writes the parameters of the block it goes to, and that block may
    # come later in the layout than the branch does.
    #
    # The entry block's parameters are the function's own, and they arrive in
    # the registers the convention names rather than being written by a branch.
    # They are copied out at once: the registers they come in are ones a call
    # destroys, so a parameter still wanted after a call has to be somewhere
    # else by then.  The hint usually makes the copy disappear where there is
    # no call to make it necessary.
    entry = func.blocks[0] if func.blocks else None
    try:
        arriving = (argument_places(cconv, [p.ty for p in entry.params])
                    if entry is not None else [])
    except TooManyArguments as many:
        raise UnsupportedOperation(
            "a function with more arguments than the convention passes in "
            "registers", None) from many
    for index, block in enumerate(func.blocks):
        for position, param in enumerate(block.params):
            if index != 0:
                if param.ty is MEM:
                    # Not a value and never in a register: what it says is which
                    # path's ordering of the memory operations holds from here.
                    continue
                pieces = parts_of(param.ty)
                given = [_new_value(part, registers) for part in pieces]
                held[id(param)] = given[0]
                if len(given) > 1:
                    extra[id(param)] = given[1:]
                continue
            pieces = parts_of(param.ty)
            given = [_new_value(part, registers,
                                hint=_as_argument(place, part, registers))
                     for part, place in zip(pieces, arriving[position])]
            held[id(param)] = given[0]
            if len(given) > 1:
                extra[id(param)] = given[1:]
    if entry is not None:
        for position, param in enumerate(entry.params):
            pieces = parts_of(param.ty)
            for at, (part, place) in enumerate(zip(pieces, arriving[position])):
                asm.loadreg(operands.part_of(param, at, None),
                            MCReg(_as_argument(place, part, registers)), func.span)

    for index, block in enumerate(func.blocks):
        if index > 0:
            asm.block(labels[index])
        for inst in block.insts:
            span = inst.span if inst.span.is_valid else None
            refused = cannot_order(inst)
            if refused is not None:
                raise UnsupportedOperation(refused, inst.span)
            match inst:
                case FrameInst():
                    # Room of this function's own.  What the value is, is where
                    # that room is, which is the stack pointer and how far in --
                    # and the stack pointer is where the function leaves it, so
                    # the offset is the one the allocator's own slots use.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.op(ops.PLUS, destination, MCReg(STACK_POINTER),
                           MCImm(asm.frame_slot(size_of(inst.held, _LAYOUT),
                                                align_of(inst.held, _LAYOUT)),
                                 32, signed=False),
                           span=inst.span)
                case AssertInst():
                    # Where it holds, nothing; where it does not, the program
                    # says what was wanted and stops.  The branch is written so
                    # that failing is the case that jumps, since it is the case
                    # that does not come back.
                    holds = asm.reserve_label("holds")
                    asm.branch(Condition.NE,
                               operands.in_register(inst.operands[0], inst.span),
                               ZERO_IMMEDIATE, holds, inst.span)
                    told = _Fault(inst.what, inst.span, inst.status)
                    if inst.observing:
                        told.observed(asm, span)
                    else:
                        told.out_of_range(asm, span)
                    asm.block(holds)
                case MemStartInst():
                    # Memory is not held in a register; the token exists to
                    # order the operations that touch it, and there is nothing
                    # yet for it to order.
                    pass
                case CodeInst():
                    # Where a function's code is, as a value.  The same
                    # instruction a variable's address needs, asked of a
                    # function's symbol instead.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.address(destination, symbol_name(inst.callee), inst.span)
                case AddressInst():
                    # The variable's address put where arithmetic can reach it.
                    # Reading and writing through the variable itself needs no
                    # such instruction; this is for a place computed from it.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.address(destination,
                                symbol_of(inst.operands[0]), inst.span)
                case LoadInst() if made_of_parts(inst.ty):
                    # A value of several parts is several values one after
                    # another in memory, so reading one is one read per part at
                    # the offset the layout gives it.  A result, a string, an
                    # array whose type does not say its length: all the same
                    # shape from here, which is what `part_offsets_of` says.
                    address = inst.operands[1]
                    pieces = parts_of(inst.ty)
                    taken = []
                    for at, (one, offset) in enumerate(
                            zip(pieces, part_offsets_of(inst.ty, _LAYOUT))):
                        into = _new_value(
                            one, registers,
                            hint=(_result_register(one, cconv, registers, at)
                                  if inst is returned else None))
                        asm.loadreg(into,
                                    place_of(address, span, disp=offset,
                                             size_bits=_width_of(one),
                                             signed=_is_signed(one)),
                                    inst.span)
                        taken.append(into)
                    held[id(inst)] = taken[0]
                    extra[id(inst)] = taken[1:]
                case LoadInst():
                    address = inst.operands[1]
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    read = place_of(address, span,
                                    size_bits=_width_of(inst.ty),
                                    signed=_is_signed(inst.ty))
                    if inst.ordering is Ordering.ACQUIRE:
                        asm.acquire(destination, read, inst.span)
                    else:
                        asm.loadreg(destination, read, inst.span)
                case StoreInst():
                    address = inst.operands[1]
                    written = inst.operands[2]
                    if made_of_parts(written.ty):
                        # One write per part, as a read of one is one read per
                        # part, and at the same offsets so the two agree.
                        for at, (one, offset) in enumerate(
                                zip(parts_of(written.ty),
                                    part_offsets_of(written.ty, _LAYOUT))):
                            asm.store(
                                place_of(address, span, disp=offset,
                                         size_bits=_width_of(one),
                                         signed=_is_signed(one)),
                                MCReg(operands.part_of(written, at, span)),
                                inst.span)
                        continue
                    if isinstance(written.ty, FloatType):
                        # A floating-point value goes to memory from a register
                        # of its own kind, and a constant one is read out of the
                        # image first, which is what the operand helper does.
                        asm.store(
                            place_of(address, span,
                                     size_bits=_bits_of(written.ty)),
                            operands.in_register(written, inst.span), inst.span)
                        continue
                    place = place_of(address, span,
                                     size_bits=_width_of(written.ty),
                                     signed=_is_signed(written.ty))
                    constant = _number_of(written)
                    if constant is not None:
                        put = MCImm(
                            constant[0],
                            _immediate_width(constant[0], _is_signed(written.ty)),
                            signed=_is_signed(written.ty))
                    else:
                        # A store writes as much of the register as the width
                        # names and reads the rest not at all, so no narrower
                        # view of it has to be asked for here.
                        put = MCReg(_value_of(written, held, span))
                    if inst.ordering is Ordering.RELEASE:
                        asm.release(place, put, inst.span)
                    else:
                        asm.store(place, put, inst.span)
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    pieces = parts_of(ty)
                    if made_of_parts(ty):
                        # One register per part, which is what every one of
                        # these architectures answers with for a two-word value.
                        try:
                            places = result_places(cconv, ty)
                        except TooManyArguments as many:
                            raise UnsupportedOperation("".join((
                                "answering with '", ty.written(),
                                "', which wants more registers than the "
                                "convention answers in")), span) from many
                        answering = [_as_argument(place, part, registers)
                                     for part, place in zip(pieces, places)]
                        for at, into in enumerate(answering):
                            asm.loadreg(into,
                                        MCReg(operands.part_of(value, at, span)),
                                        inst.span)
                        asm.ret(inst.span, answering)
                        continue
                    if isinstance(ty, FloatType):
                        # A floating-point value goes back in a register of its
                        # own kind, and a constant one is read out of the image
                        # first, which is what the operand helper does.
                        answer = _result_register(ty, cconv, registers)
                        asm.loadreg(answer, operands.in_register(value, inst.span),
                                    inst.span)
                        asm.ret(inst.span, (answer,))
                        continue
                    if not isinstance(ty, (IntType, BoolType, EnumType,
                                           PtrType, SetType, DictType)):
                        raise UnsupportedOperation("".join((
                            "returning a value of type '", ty.written(), "'")), span)
                    result = _result_register(ty, cconv, registers)
                    constant = _number_of(value)
                    if constant is not None:
                        asm.loadreg(result, MCImm(constant[0], 12,
                                                  signed=_is_signed(ty)), inst.span)
                    else:
                        asm.loadreg(result, MCReg(_value_of(value, held, span)),
                                    inst.span)
                    asm.ret(inst.span, (result,))
                case BinaryInst() if _is_floating(inst.ty):
                    answer = inst.ty.ok if isinstance(inst.ty, ResultType) \
                        else inst.ty
                    assert isinstance(answer, FloatType)
                    operation = _FLOAT_OPERATIONS.get(inst.op)
                    if operation is None:
                        raise UnsupportedOperation("".join((
                            "'", inst.op.value, "' on a floating-point value")), span)
                    destination = _new_value(
                        answer, registers,
                        hint=(_result_register(answer, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    divisor = operands.in_register(inst.operands[1], inst.span)
                    failed: VirtReg | None = None
                    if isinstance(inst.ty, ResultType):
                        # The one pair a floating-point division has no answer
                        # for is a divisor of zero.  Every other pair has one,
                        # an overflowing one included -- that answer is an
                        # infinity, which is the fault below and not this.
                        failed = _new_value(
                            BOOL, registers,
                            hint=(_result_register(BOOL, cconv, registers, 1)
                                  if inst is returned else None))
                        extra[id(inst)] = [failed]
                        asm.float_compare(
                            Condition.EQ, failed, divisor,
                            MCReg(operands.floating(FloatConst(answer, 0.0),
                                                    inst.span)),
                            answer.bits, inst.span)
                    asm.float_op(operation, destination,
                                 operands.in_register(inst.operands[0], inst.span),
                                 divisor, answer.bits, inst.span)
                    if operation in _KEEPS_A_NUMBER:
                        continue
                    # An answer that is an infinity or a not-a-number is an
                    # answer the operation did not have, the way a sum that
                    # will not fit is, and the program stops the same way.
                    carry_on = asm.reserve_label("is.finite")
                    if failed is not None:
                        # Where there is no answer there is nothing to ask about
                        # one: dividing by zero is what the result already says.
                        asm.branch(Condition.NE, MCReg(failed), ZERO_IMMEDIATE,
                                   carry_on, inst.span)
                    asm.branch_if_finite(destination, answer.bits, carry_on,
                                         inst.span)
                    _Fault("".join((NAMES[inst.op], " with no number for an answer")),
                           inst.span,
                           statuses.NO_ANSWER).out_of_range(asm, inst.span)
                    asm.block(carry_on)
                case BinaryInst() if isinstance(inst.ty, PtrType):
                    # Arithmetic on an address does not go through the
                    # checked path the same operator on a number does:
                    # what a number overflows into is another number,
                    # and what an address past its place names is not a
                    # place, so there is nothing to answer with and no
                    # bound to answer against.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.op(_OPERATIONS[inst.op], destination,
                           operands.value(inst.operands[0], inst.span),
                           operands.value(inst.operands[1], inst.span),
                           span=inst.span)
                case BinaryInst() if inst.op in TRAPPING:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    try:
                        lower_trapping(
                            asm, inst.op, inst.ty,
                            operands.value(inst.operands[0], inst.span),
                            operands.value(inst.operands[1], inst.span),
                            destination, operands, 64,
                            _Fault("".join((NAMES[inst.op], " that does not fit")),
                                   inst.span),
                            inst.span)
                    except Unsupported as unsupported:
                        raise UnsupportedOperation(unsupported.what, span) \
                            from unsupported
                case UnaryInst() if inst.op in COUNTING:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    counted = inst.operands[0].ty
                    lower_count(asm, inst.op,
                                operands.value(inst.operands[0], inst.span),
                                _width_of(counted), _is_signed(counted),
                                destination, operands, inst.span)
                case BinaryInst() if inst.op in SATURATING:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    try:
                        lower_saturating(
                            asm, inst.op, inst.ty,
                            operands.value(inst.operands[0], inst.span),
                            operands.value(inst.operands[1], inst.span),
                            destination, operands, 64,
                            inst.span)
                    except Unsupported as unsupported:
                        raise UnsupportedOperation(unsupported.what, span) \
                            from unsupported
                case BinaryInst() if inst.op in SHIFTS:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    try:
                        lower_shift(
                            asm, inst.op, inst.ty,
                            operands.value(inst.operands[0], inst.span),
                            operands.value(inst.operands[1], inst.span),
                            destination, operands, 64,
                            _Fault("".join((NAMES[inst.op],
                                            " by more than the width of the type")),
                                   inst.span),
                            inst.span)
                    except Unsupported as unsupported:
                        raise UnsupportedOperation(unsupported.what, span) \
                            from unsupported
                case BinaryInst() if inst.op in DIVISION:
                    # The answer of a division is a result: the number where
                    # there is one, and the truth value beside it that says
                    # whether there is.
                    answer = inst.ty.ok if isinstance(inst.ty, ResultType) \
                        else inst.ty
                    destination = _new_value(
                        answer, registers,
                        hint=(_result_register(answer, cconv, registers)
                              if inst is returned else None))
                    failed = _new_value(
                        BOOL, registers,
                        hint=(_result_register(BOOL, cconv, registers, 1)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    extra[id(inst)] = [failed]
                    try:
                        lower_division_result(
                            asm, inst.op, answer,
                            operands.value(inst.operands[0], inst.span),
                            operands.value(inst.operands[1], inst.span),
                            destination, failed, operands,
                            max(32, _width_of(answer)), inst.span)
                    except Unsupported as unsupported:
                        raise UnsupportedOperation(unsupported.what, span) \
                            from unsupported
                case BinaryInst() if inst.op in EXTREMA:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    lower_extremum(
                        asm, inst.op,
                        operands.value(inst.operands[0], inst.span),
                        operands.value(inst.operands[1], inst.span),
                        destination, operands, inst.span)
                case BinaryInst() if inst.op in WRAPPING:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    lower_wrapping(
                        asm, inst.op, inst.ty,
                        operands.value(inst.operands[0], inst.span),
                        operands.value(inst.operands[1], inst.span),
                        destination, 64, inst.span)
                case BinaryInst():
                    operation = _OPERATIONS.get(inst.op)
                    if operation is None:
                        raise UnsupportedOperation("".join((
                            "the operation '", inst.op.value, "'")), span)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.op(operation, destination,
                           operands.value(inst.operands[0], inst.span),
                           operands.value(inst.operands[1], inst.span),
                           span=inst.span)
                case TupleInst():
                    # Several values made into one that travels together, which
                    # here is several registers with nothing between them.
                    pieces = parts_of(inst.ty)
                    taken = [_new_value(part, registers) for part in pieces]
                    held[id(inst)] = taken[0]
                    extra[id(inst)] = taken[1:]
                    for into, value in zip(taken, inst.operands):
                        asm.loadreg(into, operands.value(value, inst.span),
                                    inst.span)
                case ExtractInst():
                    # Nothing to emit: the value asked for is already in a
                    # register of its own, and this says to go on using it.
                    held[id(inst)] = operands.part_of(inst.operands[0],
                                                      inst.index, span)
                case WrapInst():
                    # One value made of several, which here is that many
                    # registers with nothing between them: the answer's own
                    # registers, the truth value after them, and what the error
                    # carries after that.  An answer is usually one register and
                    # may be more -- a string is two -- which is why the places
                    # of the other two are counted from it rather than written.
                    assert isinstance(inst.ty, ResultType)
                    answer = inst.ty.ok
                    answer_parts = parts_of(answer)
                    slots = _result_registers(inst.ty)
                    simple = len(answer_parts) == 1
                    taken = [
                        _new_value(part, registers,
                                   hint=(_result_register(part, cconv, registers,
                                                          index)
                                         if inst is returned and simple else None))
                        for index, part in enumerate(slots)]
                    held[id(inst)] = taken[0]
                    extra[id(inst)] = taken[1:]
                    # `at` and not `index`: the loop this is inside of is
                    # walking the function's blocks, and a name reused here
                    # would tell its terminator it was somewhere else.
                    for at, part in enumerate(answer_parts):
                        asm.loadreg(taken[at],
                                    _part_source(operands, inst.operands[0],
                                                 at, part, len(answer_parts),
                                                 inst.span),
                                    inst.span)
                    asm.loadreg(taken[len(answer_parts)],
                                operands.value(inst.operands[1], inst.span),
                                inst.span)
                    if len(inst.operands) > 2:
                        assert inst.ty.err is not None
                        carried = parts_of(inst.ty.err)
                        for at, part in enumerate(carried):
                            asm.loadreg(
                                taken[len(answer_parts) + 1 + at],
                                _part_source(operands, inst.operands[2], at,
                                             part, len(carried), inst.span),
                                inst.span)
                case UnwrapInst():
                    # Nothing to emit: the answer is already in registers of its
                    # own, and this says to go on using them.
                    how_many = _answer_registers(inst.operands[0])
                    held[id(inst)] = operands.register_of(inst.operands[0], span)
                    if how_many > 1:
                        extra[id(inst)] = [
                            operands.part_of(inst.operands[0], index, span)
                            for index in range(1, how_many)]
                case FailedInst():
                    held[id(inst)] = operands.flag_of(inst.operands[0], span)
                case ErrorInst():
                    # Nothing to emit either: what the error carries is already
                    # in registers of its own, after the answer's and the truth
                    # value's.
                    first = _answer_registers(inst.operands[0]) + 1
                    held[id(inst)] = operands.part_of(inst.operands[0], first,
                                                      span)
                    if made_of_parts(inst.ty):
                        extra[id(inst)] = [
                            operands.part_of(inst.operands[0], first + index,
                                             span)
                            for index in range(1, len(parts_of(inst.ty)))]
                case UnaryInst() if inst.op in _ROUNDINGS:
                    # No instruction here rounds a floating-point number where
                    # it stands, so it goes out to a whole number and back --
                    # and the conversion is what carries the rounding, the
                    # architecture having put the mode in the instruction.
                    #
                    # That works below the point where the format has room for
                    # a fraction and nowhere else, so a value at or above it is
                    # answered with itself: it is a whole number already, and it
                    # is also where the integer would not hold it.
                    bits = _bits_of(inst.ty)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    source = operands.in_register(inst.operands[0], inst.span)
                    if rounds_in_one:
                        asm.float_round(_ROUNDINGS[inst.op], destination,
                                        source, bits, inst.span)
                        continue
                    asm.loadreg(destination, source, inst.span)
                    magnitude = registers.new_virtual(FPR, _FLOAT_REGISTER_BITS)
                    asm.float_abs(magnitude, source, bits, inst.span)
                    small = registers.new_virtual(GPR, 64)
                    asm.float_compare(
                        Condition.SLT, small, MCReg(magnitude),
                        MCReg(operands.floating(
                            FloatConst(inst.ty, _WHOLE_ABOVE[bits]), inst.span)),
                        bits, inst.span)
                    whole = asm.reserve_label("already.whole")
                    asm.branch(Condition.EQ, MCReg(small), ZERO_IMMEDIATE,
                               whole, inst.span)
                    counted = registers.new_virtual(GPR, 64)
                    asm.float_to_int(_ROUNDINGS[inst.op], counted, source, bits,
                                     inst.span)
                    back = registers.new_virtual(FPR, _FLOAT_REGISTER_BITS)
                    asm.int_to_float(back, MCReg(counted), bits, inst.span)
                    # Rounding never changes a sign and the round trip through
                    # an integer loses one: a value between minus one and zero
                    # rounds up to minus zero and comes back as zero.
                    asm.float_copysign(destination, MCReg(back), source, bits,
                                       inst.span)
                    asm.block(whole)
                case UnaryInst() if inst.op is UnOp.FABS:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.float_abs(destination,
                                  operands.in_register(inst.operands[0], inst.span),
                                  _bits_of(inst.ty), inst.span)
                case UnaryInst():
                    unary = _UNARY_OPERATIONS.get(inst.op)
                    if unary is None:
                        raise UnsupportedOperation("".join((
                            "the operation '", inst.op.value, "'")), span)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.op(unary, destination,
                           operands.in_register(inst.operands[0], inst.span),
                           span=inst.span)
                    # The complement of a narrow unsigned value sets the bits
                    # above it, where the value it stands for has zeroes there.
                    normalize(asm, inst.ty, destination, 64, inst.span)
                case CastInst() if inst.kind is CastKind.BITCAST:
                    # Nothing to emit: the bits asked for are the bits already
                    # in the register, and this says to go on reading them as
                    # something else.  A constant is put in one first -- a
                    # number read as an address, which is how nothing is said.
                    found = operands.in_register(inst.operands[0], inst.span)
                    assert isinstance(found, MCReg)
                    held[id(inst)] = found.reg
                case CastInst() if inst.kind in (CastKind.ZEXT, CastKind.SEXT):
                    # A value of a narrow type is already in a register the
                    # whole width of a word, extended the way its own type says;
                    # this reads the part of it that is the value and extends it
                    # the way the cast says instead.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.widen(destination,
                              operands.in_register(inst.operands[0], inst.span),
                              _width_of(inst.operands[0].ty),
                              inst.kind is CastKind.SEXT, inst.span)
                case CastInst() if inst.kind is CastKind.TRUNC:
                    # Narrowing is the same instruction: what it takes is the
                    # low part, and what it leaves is that part extended the way
                    # the narrower type says, which is the invariant every value
                    # in a register keeps.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.widen(destination,
                              operands.in_register(inst.operands[0], inst.span),
                              _width_of(inst.ty), _is_signed(inst.ty), inst.span)
                case CastInst() if inst.kind is CastKind.FEXT:
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.float_extend(destination,
                                     operands.in_register(inst.operands[0],
                                                          inst.span),
                                     _bits_of(inst.operands[0].ty),
                                     _bits_of(inst.ty), inst.span)
                case CmpInst() if isinstance(inst.operands[0].ty, FloatType):
                    # A floating-point comparison is never folded into a branch:
                    # what a branch would read is the flags, and the answer a
                    # not-a-number gives needs more than one reading of them.
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.float_compare(
                        CONDITIONS[inst.pred], destination,
                        operands.in_register(inst.operands[0], inst.span),
                        operands.in_register(inst.operands[1], inst.span),
                        inst.operands[0].ty.bits, inst.span)
                case CmpInst():
                    # A comparison read exactly once is folded into the branch
                    # that reads it and nothing is emitted here; read any other
                    # number of times, its answer is a value like any other.
                    if folded_into_branch(func, inst):
                        continue
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    lower_comparison(asm, inst, operands, destination)
                case SyscallInst():
                    # Everything in it is a machine word by the time it gets
                    # here, so there is nothing to widen and nothing to place by
                    # a convention: the kernel names the registers.
                    handed = [Move(into=registers.view(SYSCALLS.number.unit, 64),
                                   source=operands.value(inst.number, span))]
                    for at, one in enumerate(inst.arguments):
                        handed.append(Move(into=SYSCALLS.arguments[at],
                                           source=operands.value(one, span)))
                    for move in sequenced(handed, asm.temporary):
                        asm.loadreg(move.into, move.source, span)
                    asm.kernel(SYSCALLS.enter, span,
                               clobbers=(SYSCALLS.answer, *SYSCALLS.clobbers),
                               reads=[move.into for move in handed])
                    taken_back = _new_value(inst.ty, registers)
                    held[id(inst)] = taken_back
                    asm.loadreg(taken_back, MCReg(SYSCALLS.answer), span)
                case CallInst():
                    callee = inst.callee
                    # A call through a value rather than to a definition: what
                    # is called is whatever the register holds, so the
                    # convention cannot be the callee's -- nothing here knows
                    # which callee it is -- and is the one every function of
                    # this language follows.
                    through = inst.target
                    if through is None and not isinstance(callee, _Function):
                        raise UnsupportedOperation(
                            "a call through something that is not a named function",
                            span)
                    given = inst.arguments
                    # A call is placed by the *callee's* convention and not by
                    # this function's.  Which register an argument goes in is
                    # what the function being called says, and the specification
                    # lets two functions of one compilation say different
                    # things.
                    theirs = lookup_cconv(DEFAULT_CCONV if through is not None
                                          else callee.cconv)
                    try:
                        going = argument_places(
                            theirs, [a.ty for a in given])
                    except TooManyArguments as many:
                        raise UnsupportedOperation(
                            "a call with more arguments than the convention passes "
                            "in registers", span) from many
                    # The arguments are a parallel copy, for the reason a
                    # branch's are: a value may already be in the register
                    # another argument is being moved into, which is likelier
                    # the more a convention's argument registers are ones the
                    # allocator prefers.
                    handed: list[Move] = []
                    for position, argument in enumerate(given):
                        pieces = parts_of(argument.ty)
                        if made_of_parts(argument.ty):
                            for at, (part, place) in enumerate(
                                    zip(pieces, going[position])):
                                handed.append(Move(
                                    into=_as_argument(place, part, registers),
                                    source=MCReg(operands.part_of(argument, at,
                                                                  span))))
                            continue
                        handed.append(Move(
                            into=_as_argument(going[position][0], argument.ty,
                                              registers),
                            source=operands.value(argument, inst.span)))
                    if through is not None:
                        # The register the callee is in is read by the call, so
                        # it goes into the parallel copy with the arguments: an
                        # argument register is one the callee may already be in,
                        # and a move that wrote it first would call whatever the
                        # argument happened to be.
                        called = asm.temporary(
                            operands.register_of(through, inst.span))
                        handed.append(Move(
                            into=called,
                            source=operands.value(through, inst.span)))
                    for move in sequenced(handed, asm.temporary):
                        asm.loadreg(move.into, move.source, inst.span)
                    asm.call(MCReg(called) if through is not None
                             else symbol_name(callee),
                             inst.span,
                             destroyed_by(callee if through is None else None,
                                           theirs, registers, known_clobbers),
                             reads=[move.into for move in handed
                                    if through is None or move.into is not called])
                    if made_of_parts(inst.ty):
                        pieces = parts_of(inst.ty)
                        try:
                            places = result_places(theirs, inst.ty)
                        except TooManyArguments as many:
                            raise UnsupportedOperation("".join((
                                "a call answering with '", inst.ty.written(),
                                "', which wants more registers than the "
                                "convention answers in")), span) from many
                        taken = []
                        for part, place in zip(pieces, places):
                            into = _new_value(part, registers)
                            asm.loadreg(into,
                                        MCReg(_as_argument(place, part, registers)),
                                        inst.span)
                            taken.append(into)
                        held[id(inst)] = taken[0]
                        extra[id(inst)] = taken[1:]
                    elif inst.ty is not VOID:
                        # Wanted where this function answers if that is what
                        # becomes of it, and otherwise where the call left it:
                        # either way the hint is a register the value is
                        # already in or about to be wanted in, so the move
                        # usually turns into one of a register to itself.
                        destination = _new_value(
                            inst.ty, registers,
                            hint=_result_register(
                                inst.ty, cconv if inst is returned else theirs,
                                registers))
                        held[id(inst)] = destination
                        asm.loadreg(destination,
                                    MCReg(_result_register(inst.ty, theirs,
                                                           registers)),
                                    inst.span)
                case UnreachableInst():
                    asm.op(ops.TRAP, None, span=inst.span)
                case BrInst() | CondBrInst() | SwitchInst():
                    try:
                        lower_branch(asm, func, labels, index, inst, operands,
                                     ZERO_IMMEDIATE)
                    except UnsupportedBranch as unsupported:
                        raise UnsupportedOperation(
                            unsupported.what,
                            unsupported.span if unsupported.span.is_valid else None
                        ) from unsupported
                case _:
                    raise UnsupportedOperation("".join((
                        "the instruction '", inst.opcode, "'")), span)
    asm.end_function()

def _is_floating(ty: Type) -> bool:
    """Whether *ty* is a floating-point type, or a result answering with one."""
    from ...ir.types import FloatType, ResultType

    if isinstance(ty, ResultType):
        ty = ty.ok
    return isinstance(ty, FloatType)


def _bits_of(ty: Type) -> int:
    """How wide a floating-point type is, which is the width the format has."""
    from ...ir.types import FloatType

    assert isinstance(ty, FloatType)
    return ty.bits


def _as_argument(place: PhysReg, ty: Type,
                 registers: RegisterInfo) -> PhysReg:
    """*place* named at the width a value of *ty* is passed at.

    The caller writes this view and the callee reads it, and both take the width
    from the type, which is what makes them agree.  Which register *place* is
    comes from the convention, counted per kind rather than per position.
    """
    from ...ir.types import FloatType

    if isinstance(ty, FloatType):
        # The whole register, not a view of it at the width of the value: what
        # an instruction names is a view, and the value's own register is the
        # whole of it, so this is the same register the callee computes into.
        return registers.view(place.unit, _FLOAT_REGISTER_BITS)
    return registers.view(place.unit, 64)


def _result_register(ty: Type, cconv: CallConvDesc,
                     registers: RegisterInfo, index: int = 0) -> PhysReg:
    """The register an instruction's result is put in.

    There is only one register width here, so a narrow value simply sits in a
    whole register; the instruction that produced it is what decided whether the
    bits above it are copies of its sign or zeroes.
    """
    from ...ir.types import FloatType

    if isinstance(ty, FloatType):
        return cconv.float_ret_regs[index]
    del registers
    return cconv.int_ret_regs[index]


#: How wide a floating-point register is declared to be.  What an instruction
#: names is a view of it at the width of the value, so the whole is what the
#: value's own register is.
_FLOAT_REGISTER_BITS: Final[int] = 64


def _part_source(operands: object, value: object, index: int, ty: Type,
                 how_many: int, span: Span) -> MCOperand:
    """Where one part of a value being put into a result comes from.

    A value of one part may be anything an operand may be -- a constant, or the
    undefined one an error wraps -- so it is asked for the way everything else
    asks.  A value of several is a value the function computed, and what is
    wanted is the register its part is in; the undefined one is nought in each
    of them, which is what an error's unread answer has always been.
    """
    from ...ir.value import UndefConst

    if how_many == 1:
        return operands.undefined(value, ty, span)
    if isinstance(value, UndefConst):
        return MCImm(0, max(32, _width_of(ty)), signed=False)
    return MCReg(operands.part_of(value, index, span))


def _answer_registers(value: object) -> int:
    """How many registers the answer of a result takes.

    One for every answer that is one value, and its own several for one that is
    several -- a result whose answer is a string is the string's two registers
    and then the truth value.  What reads a result asks this rather than
    counting from one, so that a wider answer moves the truth value along with
    it instead of landing on top of it.
    """
    ty = getattr(value, "ty", None)
    if not isinstance(ty, ResultType):
        return 1
    return len(parts_of(ty.ok))


def _result_registers(ty: ResultType) -> tuple[Type, ...]:
    """Every register a result takes: its answer's, the truth value, the error's."""
    return (*parts_of(ty.ok), BOOL,
            *(parts_of(ty.err) if ty.err is not None else ()))


def _new_value(ty: Type, registers: RegisterInfo,
               hint: PhysReg | None = None) -> VirtReg:
    """A register for a value the function computes.

    Every register here is the full width and a narrow value simply occupies
    one, so unlike the other two backends there is no view to choose.

    The hint says where the value is wanted anyway.  Taking it turns the move
    that would put it there into a move of a register to itself, which then goes.
    """
    from ...ir.types import FloatType, ProductType

    if isinstance(ty, ProductType):
        # A record travels as the values it is made of, and whatever asks for a
        # register asks once per value -- so what arrives here is a record whose
        # values are one, which is a register of whatever that one is.  A record
        # of more than one has no register to be in and says so.
        inner = parts_of(ty)
        if len(inner) == 1 and not made_of_parts(inner[0]):
            return _new_value(inner[0], registers, hint=hint)
        raise UnsupportedOperation("".join((
            "a value of type '", ty.written(), "'")), None)
    if isinstance(ty, FloatType):
        return registers.new_virtual(FPR, _FLOAT_REGISTER_BITS,
                                     hint=hint)
    return registers.new_virtual(GPR, 64, hint=hint)


def _value_of(value: object, held: dict[int, VirtReg],
              span: Span | None) -> VirtReg:
    """The register a value the function computed is in."""
    found = held.get(id(value))
    if found is None:
        raise UnsupportedOperation("a value this backend did not compute", span)
    return found


def _returned_value(func: Function) -> object:
    """The value the function returns, where it computes one.

    It is worth knowing before the value is computed, because that is when the
    register it will be wanted in can be asked for.
    """
    from ...ir.inst import RetInst

    for block in func.blocks:
        for inst in block.insts:
            if isinstance(inst, RetInst) and inst.operands:
                return inst.operands[0]
    return None

def _width_of(ty: Type) -> int:
    """How many bits a value of *ty* occupies in memory.

    A truth value is a byte, which is what the layout says it is.  Reading or
    writing one any wider would touch whatever is laid out beside it.
    """
    from ...ir.types import (BoolType, CharType, EnumType, FloatType,
                             IntType, ProductType)

    if isinstance(ty, IntType):
        # What holds it: a type narrower than a machine width is read and
        # written at the width that contains it, with the bits above its own
        # saying what its sign says.
        return ty.held
    if isinstance(ty, FloatType):
        return ty.bits
    if isinstance(ty, (CharType, EnumType)):
        return ty.holder.bits
    if isinstance(ty, ProductType):
        # A record of one value is that value, which is what it is held as and
        # what a load or a store of it touches.
        inner = parts_of(ty)
        if len(inner) == 1 and not made_of_parts(inner[0]):
            return _width_of(inner[0])
    return 8 if isinstance(ty, BoolType) else 64


def _number_of(value: object) -> tuple[int, Type] | None:
    """The number a constant stands for and its type, or nothing where it is
    not a constant.

    A truth value is one or zero.  That is not a choice made here: it is what
    every instruction on this architecture that produces one produces, and what
    the byte in the image already holds.
    """
    from ...ir.value import BoolConst, CharConst, EnumConst, IntConst

    if isinstance(value, (IntConst, CharConst)):
        # A code point is the number Unicode gave it, which is what is compared
        # and what is stored; its type says how wide that number is held.
        return value.value, value.ty
    if isinstance(value, BoolConst):
        return (1 if value.value else 0), value.ty
    if isinstance(value, EnumConst):
        # Which value of the enumeration it is.  A value of one is not a number
        # of the type that holds it, but what is compared and what is stored is
        # that number, and this is where the two meet.
        return value.number, value.ty
    return None


def _is_signed(ty: Type) -> bool:
    """Whether a narrow value of *ty* is widened by its sign when it is read."""
    from ...ir.types import CharType, EnumType, IntType

    if isinstance(ty, (CharType, EnumType)):
        return ty.holder.signed
    return isinstance(ty, IntType) and ty.signed


def _immediate_width(value: int, signed: bool) -> int:
    """The narrowest standard width that holds *value*.

    It is the width the *encoding* uses, which is not the width of the access:
    an eight-byte store carries a four-byte immediate that the instruction
    widens, so what the operand has to say is how large the number is.
    """
    for bits in (8, 16, 32, 64):
        if signed:
            if -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
                return bits
        elif 0 <= value < (1 << bits):
            return bits
    return 64


def _signed_twelve(value: int) -> int:
    """The low twelve bits of *value*, read as a signed number.

    An instruction that carries twelve bits sign-extends them, so what it adds
    is this and not the bits themselves.
    """
    low = value & 0xFFF
    return low - 0x1000 if low >= 0x800 else low
