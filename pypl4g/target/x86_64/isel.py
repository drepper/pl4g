"""Instruction selection for x86-64.

The builder speaks in three-address form with the destination first; x86-64
instructions take two operands, so this is where that difference is resolved.
Every builder call turns into table rows, and the table decides which encoding is
shortest.
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
                          stride_of,
                          tag_offset_of)
from ...ir.types import (BOOL, ResultType, VecType, made_of_parts,
                         parts_of)
from ..callconv import (TooManyArguments, argument_places, destroyed_by,
                        result_places)
from ..bitcount import COUNTING, lower_count
from ..saturate import (DIVISION, EXTREMA, NAMES, SATURATING, TRAPPING,
                        Unsupported,
                        SHIFTS, WRAPPING, lower_division_result,
                        lower_extremum, lower_saturating, lower_shift,
                        lower_trapping, lower_wrapping)
from ..runs import lower_trapping as lower_trapping_run, top_bytes

if TYPE_CHECKING:
    from ...mc.reg import PhysReg
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo, RegUnit
    from ...ir.types import Type
    from ..callconv import CallConvDesc
    from ...source.manager import SourceManager
from . import ops as x86ops
from ...ir.function import DEFAULT_CCONV, SYSTEM_CCONV
from .abi import lookup as lookup_cconv
from .startup import ABORT_SYMBOL, REPORT_SYMBOL, SYSCALLS
from .opcodes import X86_INSTRS
from .regs import GPR, INFO as REGISTERS, RAX, RCX, RDX, RSP, VEC

#: The mnemonic that implements each architecture-neutral binary operation.
_BINARY: Final[dict[str, str]] = {
    ops.PLUS.name: "add",
    ops.MINUS.name: "sub",
    ops.TIMES.name: "imul",
    ops.XOR.name: "xor",
    ops.AND.name: "and",
    ops.OR.name: "or",
}

#: Operations that take one source and write the destination in place.
_UNARY: Final[dict[str, str]] = {
    ops.NOT.name: "not",
}

#: The jump that follows a comparison, for each condition.  Naming a condition
#: is naming an instruction here, since the condition is part of the opcode.
_CONDITIONAL: Final[dict[Condition, str]] = {
    Condition.EQ: "je", Condition.NE: "jne",
    Condition.SLT: "jl", Condition.SLE: "jle",
    Condition.SGT: "jg", Condition.SGE: "jge",
    Condition.ULT: "jb", Condition.ULE: "jbe",
    Condition.UGT: "ja", Condition.UGE: "jae",
}

#: The instruction that writes a condition into a byte, for each condition.
#: The condition is part of the opcode here too, so this is the same shape as
#: the table above and not a second mechanism.
_SET: Final[dict[Condition, str]] = {
    Condition.EQ: "sete", Condition.NE: "setne",
    Condition.SLT: "setl", Condition.SLE: "setle",
    Condition.SGT: "setg", Condition.SGE: "setge",
    Condition.ULT: "setb", Condition.ULE: "setbe",
    Condition.UGT: "seta", Condition.UGE: "setae",
}

#: The conditional move that takes a bound, for each condition.  The condition
#: is part of the opcode here as it is for the jumps and the sets above.
_CMOV: Final[dict[Condition, str]] = {
    Condition.EQ: "cmove", Condition.NE: "cmovne",
    Condition.SLT: "cmovl", Condition.SLE: "cmovle",
    Condition.SGT: "cmovg", Condition.SGE: "cmovge",
    Condition.ULT: "cmovb", Condition.ULE: "cmovbe",
    Condition.UGT: "cmova", Condition.UGE: "cmovae",
}

#: Operations that map to a single instruction with no operands.
_NULLARY: Final[dict[str, str]] = {
    x86ops.SYSCALL.name: "syscall",
    x86ops.CPUID.name: "cpuid",
    x86ops.XGETBV.name: "xgetbv",
    ops.TRAP.name: "ud2",
}


#: What a branch compares against where its condition is a value rather than a
#: comparison.  The width is the one this target writes a small immediate in.
ZERO_IMMEDIATE: Final[MCImm] = MCImm(0, 32, signed=False)


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
STACK_POINTER: Final = RSP


#: What memory looks like here.  Every one of these targets has an eight-byte
#: pointer, and nothing else about the layout differs between them.
_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)


class UnsupportedOperation(Exception):
    """The backend has no selection rule for this operation yet."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


class X86Selector(InstructionSelector):
    """Turns builder calls into x86-64 instructions."""

    def __init__(self, table: InstrTable | None = None,
                 rounds: bool = True, counts_ones: bool = False,
                 counts_leading: bool = False) -> None:
        self.table = table if table is not None else InstrTable(X86_INSTRS)
        #: Whether the level this is generating for has the rounding
        #: instruction.  It is SSE4.1, which the second level promises and the
        #: first does not, and there is nothing else on this architecture that
        #: rounds without going through an integer.
        self.rounds = rounds
        #: Whether the level promises the instruction that counts the bits set,
        #: which the second adds, and the one that counts the zeroes above the
        #: highest set bit, which the third does.  A program built for a level
        #: without one gets the sequence that stands in for it.
        self.counts_ones = counts_ones
        self.counts_leading = counts_leading

    def _inst(self, mnemonic: str, operands: Sequence[MCOperand], span: Span) -> MCInst:
        """Select the shortest encoding of *mnemonic* for *operands*."""
        try:
            desc = self.table.select(mnemonic, operands)
        except SelectionError as exc:
            raise UnsupportedOperation(str(exc), span if span.is_valid else None) from exc
        return MCInst(desc=desc, operands=tuple(operands), span=span)

    def _same_register(self, dst: Reg, operand: MCOperand) -> bool:
        """Whether *operand* already names the register *dst*."""
        return isinstance(operand, MCReg) and operand.reg is dst

    def _is_float(self, reg: Reg) -> bool:
        """Whether *reg* is one of the registers a floating-point value lives in."""
        return reg.cls is VEC

    #: Which move carries a floating-point value of a given width.
    _FLOAT_MOVES: Final[dict[int, str]] = {32: "movss", 64: "movsd"}

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        if self._is_float(dst):
            return self._select_float_move(dst, src, span)
        if isinstance(src, MCReg) and self._is_float(src.reg):
            raise UnsupportedOperation(
                "moving a floating-point value into an ordinary register", span)
        if isinstance(src, MCMem):
            return self._select_load(dst, src, span)
        if self._same_register(dst, src):
            return ()
        # Clearing a register by exclusive-or is shorter, but it writes the
        # flags.  Selection always emits the form that is valid everywhere; the
        # peephole pass substitutes the shorter one where it has checked that
        # the flags are dead.
        return (self._inst("mov", (MCReg(dst), _carried(src, dst.bits)), span),)

    def _select_float_move(self, dst: Reg, src: MCOperand,
                           span: Span) -> Sequence[MCInst]:
        """Instructions that put a floating-point value into *dst*.

        A move between registers moves the whole of the wider format, which
        covers the narrower one and costs nothing more; a move from memory moves
        exactly what the place holds, since what is beside it is not the value.
        """
        if isinstance(src, MCMem):
            width = src.size_bits if src.size_bits is not None else 64
            mnemonic = self._FLOAT_MOVES.get(width)
            if mnemonic is None:
                raise UnsupportedOperation("".join((
                    "reading ", str(width), " bits of floating point")), span)
            if src.disp_sym is not None or src.base is not None:
                return (self._inst(mnemonic, (MCReg(dst, bits=128), src), span),)
            raise UnsupportedOperation("a floating-point read of nowhere", span)
        if isinstance(src, MCReg):
            if self._same_register(dst, src):
                return ()
            return (self._inst("movsd", (MCReg(dst, bits=128),
                                         MCReg(src.reg, bits=128)), span),)
        raise UnsupportedOperation(
            "putting that kind of operand in a floating-point register", span)

    def _select_load(self, dst: Reg, src: MCMem, span: Span) -> Sequence[MCInst]:
        """Instructions that read memory into a register.

        This architecture addresses memory directly, so a value anywhere in the
        image is one instruction away, and a narrow value is widened by the same
        instruction that reads it.
        """
        width = src.size_bits if src.size_bits is not None else 32
        if width <= 16:
            mnemonic = "movsx" if src.signed else "movzx"
        else:
            mnemonic = "mov"
        return (self._inst(mnemonic, (MCReg(dst), src), span),)

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
            # The register is a whole word even where the instruction names
            # only a part of it, for the reason ``_new_value`` gives: what a
            # value is computed into is at least a word, and the instruction
            # then says which view of it it reads.
            width = _operand_width(operands)
            carried = REGISTERS.new_virtual(GPR, max(32, width))
            before.extend(self.select_move(carried, _at_least_a_word(operand), span))
            rewritten.append(MCReg(carried, bits=width))
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
            before, ready = self._accepting_pair(mnemonic, dst, sources, span)
            return (*before, *self._two_address(mnemonic, dst, ready[0], ready[1],
                                                span))
        mnemonic = _UNARY.get(op.name)
        if mnemonic is not None:
            if dst is None or len(sources) != 1:
                raise UnsupportedOperation("".join((
                    "'", op.name, "' needs a destination and one source")), span)
            moved = self.select_move(dst, sources[0], span)
            return (*moved, self._inst(mnemonic, (MCReg(dst),), span))
        raise UnsupportedOperation("".join((
            "no x86-64 selection rule for '", op.name, "'")), span)

    def _accepting_pair(self, mnemonic: str, dst: Reg, sources: Sequence[MCOperand],
                        span: Span) -> tuple[Sequence[MCInst], Sequence[MCOperand]]:
        """The two sources as the two-operand form will take them.

        The destination is also the first source here, so the first source has
        to be in a register whatever it is; only the second may be an immediate,
        and only where the table has a row that carries one.
        """
        before: list[MCInst] = []
        left = sources[0]
        if isinstance(left, MCImm):
            carried = REGISTERS.new_virtual(
                GPR, _operand_width((MCReg(dst), *sources)))
            before.extend(self.select_move(carried, left, span))
            left = MCReg(carried)
        after, ready = self._accepting(mnemonic, (MCReg(dst), sources[1]), span)
        return (*before, *after), (left, ready[1])

    def _two_address(self, mnemonic: str, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Lower a three-address operation onto a two-operand instruction."""
        if self._same_register(dst, lhs):
            return (self._inst(mnemonic, (MCReg(dst), rhs), span),)
        moved = self.select_move(dst, lhs, span)
        return (*moved, self._inst(mnemonic, (MCReg(dst), rhs), span))

    def select_store(self, address: MCMem, value: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that write *value* into the memory *address* names.

        This architecture writes to a place in memory directly, and takes the
        value as an immediate where there is one, so a store is usually one
        instruction and needs no register at all.

        The exception is a constant too wide for the immediate a store carries.
        The widest is four bytes, which the instruction sign-extends to eight,
        so a value with anything in its upper half has to go through a register
        -- and a register *can* hold one, since there is a move that takes the
        whole eight bytes.
        """
        if isinstance(value, MCReg) and self._is_float(value.reg):
            width = address.size_bits if address.size_bits is not None else 64
            mnemonic = self._FLOAT_MOVES.get(width)
            if mnemonic is None:
                raise UnsupportedOperation("".join((
                    "writing ", str(width), " bits of floating point")), span)
            return (self._inst(mnemonic, (address, MCReg(value.reg, bits=128)), span),)
        if isinstance(value, MCImm) and not self._fits_a_store(address, value):
            carried = REGISTERS.new_virtual(GPR, 64)
            return (*self.select_move(carried, value, span),
                    self._inst("mov", (address, MCReg(carried,
                                                      bits=address.size_bits)), span))
        return (self._inst("mov", (address, value), span),)

    def select_acquire(self, dst: Reg, address: MCMem,
                       span: Span) -> Sequence[MCInst]:
        """An ordinary read.

        This architecture's reads are already acquiring and its writes already
        releasing -- a read is never seen to move ahead of an earlier read, nor
        a write ahead of an earlier write -- so an ordering that asks for no
        more than that asks for nothing this machine does not already do.  The
        one ordering it would cost an instruction is a write followed by a read
        of another place, which nothing here asks for yet.
        """
        return self.select_move(dst, address, span)

    def select_release(self, address: MCMem, value: MCOperand,
                       span: Span) -> Sequence[MCInst]:
        """An ordinary write, for the reason above."""
        return self.select_store(address, value, span)

    def _fits_a_store(self, address: MCMem, value: MCImm) -> bool:
        """Whether a store of *value* into *address* has an encoding."""
        try:
            self.table.select("mov", (address, value))
        except SelectionError:
            return False
        return True

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            # A call through a register: what is called is whatever it holds,
            # which is how a function held in a value is called.
            return (self._inst("call", (target,), span),)
        return (self._inst("call", (target,), span),)

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        return (self._inst("ret", (), span),)

    def select_jump(self, target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that transfer control to *target*."""
        return (self._inst("jmp", (target,), span),)

    def select_branch(self, cond: Condition, lhs: MCOperand, rhs: MCOperand,
                      target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *lhs* and *rhs* stand in *cond*.

        The comparison writes the flags and the jump reads them, which the rows
        declare, so nothing here has to keep the two together by hand.
        """
        if isinstance(lhs, MCImm) and not isinstance(rhs, MCImm):
            # The comparison takes its immediate second, so the operands are
            # exchanged and the condition with them.
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        return (*self._select_compare(lhs, rhs, cond, span),
                self._inst(_CONDITIONAL[cond], (target,), span))

    #: Every width this architecture has arithmetic at, which is every width
    #: the language has: an eight-bit addition is an instruction, and what it
    #: leaves in the flags is whether an eight-bit answer went past.
    flagged_widths = frozenset((8, 16, 32, 64))

    def select_op_at(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                     bits: int, span: Span) -> Sequence[MCInst]:
        """Compute *op* into *dst*, naming every operand at *bits*.

        The value is moved into the destination at its own full width and the
        arithmetic then names the narrow view of it, which is what leaves the
        upper part of the register as it was -- nothing reads it, a value of a
        narrow type being the narrow view and nothing more, and every place that
        wants it wider widens it with an instruction that says so.
        """
        mnemonic = _BINARY.get(op.name)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "'", op.name, "' at a stated width")), span)
        narrow = MCReg(dst, bits=bits)
        # The destination is also the first source here, so the first source
        # goes into it whatever it was; the table is then asked whether it will
        # take the second as it stands, which is what puts an immediate in a
        # register only where no row carries one.  Both happen before the
        # arithmetic, so neither disturbs what it leaves behind.
        moved: Sequence[MCInst] = ()
        if not self._same_register(dst, left):
            # A number moved into the destination is moved at a width the move
            # has a form for, which the narrow ones do not always have; the
            # arithmetic then names the narrow view of what arrived, and what is
            # above it is never read.
            moved = self.select_move(dst, _at_least_a_word(left), span)
        before, ready = self._accepting(mnemonic, (narrow, _named_at(right, bits)),
                                        span)
        return (*moved, *before, self._inst(mnemonic, ready, span))

    def select_branch_if_in_range(self, op: Op, signed: bool, target: MCSymRef,
                                  span: Span) -> Sequence[MCInst]:
        """Go to *target* where the arithmetic just emitted did not go past.

        Two flags and two questions.  A signed operation goes past when the
        answer's sign is not the one the operands called for, which is what the
        overflow flag says; an unsigned one goes past when it carried out of the
        top or borrowed into it, which is the carry flag.  Neither needs the
        answer looked at again, and neither knows or cares how wide it was.
        """
        del op  # both say it the same way here
        return (self._inst("jno" if signed else "jae", (target,), span),)

    #: What moves a run of elements of a given number of bits.  Each of the three
    #: reads exactly that many and clears the rest of the register, which is what
    #: makes a run shorter than a register a value with none of its neighbours
    #: in it.
    _RUN_MOVES: Final[dict[int, str]] = {32: "movd", 64: "movq", 128: "movdqu"}

    #: What each operation over a whole run is called.  The bitwise three are
    #: one instruction whatever the lanes are, a register of bits being the same
    #: answer however it is divided; the arithmetic is one per lane width.
    _RUN_OPERATIONS: Final[dict[str, str]] = {
        ops.AND.name: "pand", ops.OR.name: "por", ops.XOR.name: "pxor",
    }

    #: The arithmetic, by operation and by how wide one lane is.
    _RUN_ARITHMETIC: Final[dict[tuple[str, int], str]] = {
        (ops.PLUS.name, 8): "paddb", (ops.PLUS.name, 16): "paddw",
        (ops.PLUS.name, 32): "paddd", (ops.PLUS.name, 64): "paddq",
        (ops.MINUS.name, 8): "psubb", (ops.MINUS.name, 16): "psubw",
        (ops.MINUS.name, 32): "psubd", (ops.MINUS.name, 64): "psubq",
        # The low half of the product, which is all a multiplication that may
        # wrap wants.  There is no byte-wide form and none for the widest lane.
        (ops.TIMES.name, 16): "pmullw", (ops.TIMES.name, 32): "pmulld",
    }

    def select_run_move(self, dst: Reg | MCMem, src: MCOperand, bits: int,
                        span: Span) -> Sequence[MCInst]:
        """Move *bits* of a run of elements into or out of one of these registers.

        Which register width is used is not a choice made here: it is how wide
        the register holding the run is, which is how long the run is, which the
        step that cut the run into pieces already decided.  A wider one is the
        newer form of the same instruction, and the table picks it by the width
        of the operands.
        """
        wide = _held_bits(dst, src)
        mnemonic = "movdqu" if bits >= 128 else self._RUN_MOVES.get(bits, "")
        if not mnemonic:
            raise UnsupportedOperation("".join((
                "moving ", str(bits), " bits of a run of elements")), span)
        target: MCOperand = dst if isinstance(dst, MCMem) else MCReg(dst, bits=wide)
        if isinstance(src, MCReg) and not isinstance(dst, MCMem):
            # Between two of these registers the whole of one goes, whatever the
            # run is: what is beyond the run is clear in the source and has to
            # stay clear in the destination.
            return (self._inst("movdqu", (target, MCReg(src.reg, bits=wide)), span),)
        return (self._inst(mnemonic, (target, src), span),)

    def select_run_op(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                      bits: int, span: Span) -> Sequence[MCInst]:
        """Do *op* to every lane of a run at once.

        Two operands here as everywhere else on this architecture, so the left
        goes into the destination first and the instruction reads the right.
        """
        mnemonic = self._RUN_OPERATIONS.get(op.name) \
            or self._RUN_ARITHMETIC.get((op.name, bits))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "'", op.name, "' over a whole run of ", str(bits),
                "-bit elements")), span)
        return self._run_two_or_three(mnemonic, dst, left, right, span)

    def _run_two_or_three(self, mnemonic: str, dst: Reg, left: MCOperand,
                          right: MCOperand, span: Span) -> Sequence[MCInst]:
        """One operation over a run, in whichever form the width has.

        The wider registers came with a form that names the destination
        separately from both sources, so nothing has to be moved into place
        first; the narrower ones have only the form where the destination is
        also the first source.
        """
        wide = dst.bits
        target = MCReg(dst, bits=wide)
        if wide > 128:
            return (self._inst(mnemonic, (target, _named_at(left, wide),
                                          _named_at(right, wide)), span),)
        moved: Sequence[MCInst] = ()
        if not self._same_register(dst, left):
            moved = self.select_run_move(dst, left, wide, span)
        return (*moved, self._inst(mnemonic, (target, _named_at(right, wide)), span))

    #: How one value is spread over a whole register, by the width of the value.
    #: Each step doubles how wide the repeated piece is, so a byte takes three
    #: and eight bytes take one.
    _SPREAD: Final[dict[int, tuple[str, ...]]] = {
        8: ("punpcklbw", "punpcklwd", "pshufd"),
        16: ("punpcklwd", "pshufd"),
        32: ("pshufd",),
        64: ("punpcklqdq",),
    }

    def select_run_splat(self, dst: Reg, src: MCOperand, bits: int,
                         span: Span) -> Sequence[MCInst]:
        """Put one value in every lane of *dst*.

        The value goes into the low lane and is then doubled up until the
        register is full: taking every value of the low half twice is what each
        of the unpack instructions does, and four lanes of one is what the
        shuffle does in a single step once the piece is four bytes wide.
        """
        steps = self._SPREAD.get(bits)
        if steps is None:
            raise UnsupportedOperation("".join((
                "one value of ", str(bits), " bits in every lane")), span)
        before: list[MCInst] = []
        if isinstance(src, MCImm):
            # There is no way to put a number straight into one of these; it
            # goes through an ordinary register, as every other constant does.
            carried = REGISTERS.new_virtual(GPR, max(32, bits))
            before.extend(self.select_move(carried, _at_least_a_word(src), span))
            src = MCReg(carried, bits=max(32, bits))
        elif isinstance(src, MCReg):
            src = MCReg(src.reg, bits=max(32, bits))
        narrow = MCReg(dst, bits=128)
        made = [self._inst("movq" if bits == 64 else "movd", (narrow, src), span)]
        if dst.bits > 128:
            # The wider registers came with an instruction that does the whole
            # of this in one step, reading the value out of the low half.
            made.append(self._inst(_BROADCASTS[bits],
                                   (MCReg(dst, bits=dst.bits), narrow), span))
            return (*before, *made)
        for step in steps:
            operands: tuple[MCOperand, ...] = (narrow, narrow)
            if step == "pshufd":
                operands = (narrow, narrow, MCImm(0, 8, signed=False))
            made.append(self._inst(step, operands, span))
        return (*before, *made)

    #: The saturating arithmetic over a run, by whether it adds, whether the
    #: lanes are signed, and how wide one is.  Only the two narrow widths have
    #: these; the wider ones are still done an element at a time.
    _RUN_SATURATING: Final[dict[tuple[bool, bool, int], str]] = {
        (True, False, 8): "paddusb", (True, False, 16): "paddusw",
        (False, False, 8): "psubusb", (False, False, 16): "psubusw",
        (True, True, 8): "paddsb", (True, True, 16): "paddsw",
        (False, True, 8): "psubsb", (False, True, 16): "psubsw",
    }

    def select_run_saturating(self, dst: Reg, left: MCOperand, right: MCOperand,
                              adding: bool, signed: bool, bits: int,
                              span: Span) -> Sequence[MCInst]:
        """Add or subtract every lane, stopping at the end of the lane's type."""
        mnemonic = self._RUN_SATURATING.get((adding, signed, bits))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "saturating arithmetic over a run of ", str(bits),
                "-bit elements")), span)
        return self._run_two_or_three(mnemonic, dst, left, right, span)

    def select_run_any_lane(self, dst: Reg, src: MCOperand, stride: int,
                            lanes: int, span: Span) -> Sequence[MCInst]:
        """Whether any lane the run covers has its top bit set.

        One instruction gathers the top bit of every byte into an ordinary
        register, and one `and` throws away the bits belonging to bytes that are
        not the last byte of a lane the run covers.  What is left is zero
        exactly when no such lane went past.
        """
        if not isinstance(src, MCReg):
            raise UnsupportedOperation(
                "gathering the top bits of something not in a register", span)
        wide = src.width
        target = MCReg(dst, bits=32)
        wanted = top_bytes(stride, lanes, wide)
        return (self._inst("pmovmskb", (target, MCReg(src.reg, bits=wide)), span),
                self._inst("and", (target, MCImm(wanted, 32, signed=False)), span))

    def select_run_ones(self, dst: Reg, span: Span) -> Sequence[MCInst]:
        """Every bit of *dst* set.

        There is no instruction that puts a constant in one of these registers,
        and this is the one that needs no constant: every lane of a register
        compared with itself is equal to itself, and a lane that compares equal
        is every bit set.
        """
        return self._run_two_or_three("pcmpeqd", dst, MCReg(dst), MCReg(dst), span)

    def select_set(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                   span: Span) -> Sequence[MCInst]:
        """Instructions that put whether *lhs* and *rhs* stand in *cond* into *dst*.

        Three instructions, and the third is not optional.  The one that reads
        the flags writes a byte and leaves the rest of the register as it was,
        so the byte is widened into the register afterwards; the usual trick of
        clearing the register first instead is not open here, because clearing
        it writes the flags that the comparison has just set.
        """
        if isinstance(lhs, MCImm) and not isinstance(rhs, MCImm):
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        low = MCReg(dst, bits=8)
        return (*self._select_compare(lhs, rhs, cond, span),
                self._inst(_SET[cond], (low,), span),
                self._inst("movzx", (MCReg(dst, bits=32), low), span))

    def select_address(self, dst: Reg, symbol: MCSymRef,
                       span: Span) -> Sequence[MCInst]:
        """Instructions that put the address of *symbol* into *dst*.

        One instruction here: the address is computed from the program counter,
        which is what keeps the image free of anything that has to be patched
        when it is loaded.
        """
        return (self._inst("lea", (MCReg(dst, bits=64),
                                   MCMem(disp_sym=symbol.expr, rip_relative=True)),
                           span),)

    def select_widen(self, dst: Reg, src: MCOperand, bits: int, signed: bool,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that put a *bits*-wide value into the whole of *dst*.

        Writing a four-byte register clears the four above it, so widening an
        unsigned value is an ordinary move and costs nothing beyond it.  A
        signed one has an instruction of its own.
        """
        if bits >= 64:
            return self.select_move(dst, src, span)
        if isinstance(src, MCImm):
            return self.select_move(dst, src, span)
        # A place in memory is widened by the same instructions a register is:
        # every one of them reads either.  What it must not be is an ordinary
        # move, which would name the whole of the destination beside four bytes
        # of memory and match no encoding there is.
        narrow = (MCReg(src.reg, bits=bits) if isinstance(src, MCReg)
                  else replace(src, size_bits=bits))
        if not signed:
            if bits == 32:
                return (self._inst("mov", (MCReg(dst, bits=32), narrow), span),)
            return (self._inst("movzx", (MCReg(dst, bits=32), narrow), span),)
        mnemonic = "movsxd" if bits == 32 else "movsx"
        return (self._inst(mnemonic, (MCReg(dst, bits=64), narrow), span),)

    def select_clamp(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                     bound: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that put *bound* into *dst* where the two stand in *cond*.

        The conditional move reads a register or memory and this only ever hands
        it a register, so a bound that arrives as a constant is built first.  It
        moves the whole eight bytes, which is right for a value of any width:
        what is above the value is its own zeroes or its own sign.
        """
        if isinstance(lhs, MCImm) and not isinstance(rhs, MCImm):
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        before: list[MCInst] = []
        if not isinstance(bound, MCReg):
            carried = REGISTERS.new_virtual(GPR, 64)
            before.extend(self.select_move(carried, bound, span))
            bound = MCReg(carried)
        return (*before, *self._select_compare(lhs, rhs, cond, span),
                self._inst(_CMOV[cond], (MCReg(dst, bits=64),
                                         MCReg(bound.reg, bits=64)), span))

    def _select_compare(self, lhs: MCOperand, rhs: MCOperand, cond: Condition,
                        span: Span) -> Sequence[MCInst]:
        """The instructions that set the flags for a comparison.

        Usually one.  Testing a register against itself sets the same flags as
        comparing it with zero and is a byte shorter, so it is what a test for
        zero uses.  A constant too wide for the four bytes a comparison carries
        goes into a register first, which is the same answer a store gives to
        the same question.
        """
        if (isinstance(rhs, MCImm) and rhs.value == 0 and isinstance(lhs, MCReg)
                and cond in (Condition.EQ, Condition.NE)):
            return (self._inst("test", (lhs, lhs), span),)
        if isinstance(rhs, MCImm):
            try:
                self.table.select("cmp", (lhs, rhs))
            except SelectionError:
                # The two operands have to be the same width, and the width
                # is the left one's: a value is correct in the register it is
                # held in and says nothing about what is above that.
                width = lhs.reg.bits if isinstance(lhs, MCReg) else 64
                carried = REGISTERS.new_virtual(GPR, width)
                return (*self.select_move(carried, rhs, span),
                        self._inst("cmp", (lhs, MCReg(carried)), span))
        return (self._inst("cmp", (lhs, rhs), span),)

    # -- the stack -------------------------------------------------------------

    def _slot(self, slot: int, size_bits: int = 64) -> MCMem:
        """The place in the frame at *slot*, measured from the stack pointer."""
        return MCMem(base=RSP, disp=slot, size_bits=size_bits)

    #: What each of the three shifts is called here.  A rotation is built from
    #: shifts rather than from the rotate instructions, so that it means the
    #: same thing for a type narrower than the register it is held in.
    _SHIFTS: Final[dict[str, str]] = {
        ops.SHIFT_LEFT.name: "shl", ops.SHIFT_RIGHT.name: "shr",
        ops.SHIFT_RIGHT_SIGNED.name: "sar",
    }

    #: What each operation is called for each width of floating-point value.
    _FLOAT_BINARY: Final[dict[tuple[str, int], str]] = {
        (ops.PLUS.name, 32): "addss", (ops.PLUS.name, 64): "addsd",
        (ops.MINUS.name, 32): "subss", (ops.MINUS.name, 64): "subsd",
        (ops.TIMES.name, 32): "mulss", (ops.TIMES.name, 64): "mulsd",
        (ops.DIVIDE.name, 32): "divss", (ops.DIVIDE.name, 64): "divsd",
        (ops.LARGER.name, 32): "maxss", (ops.LARGER.name, 64): "maxsd",
        (ops.SMALLER.name, 32): "minss", (ops.SMALLER.name, 64): "minsd",
        # Not an operation of the language: what the magnitude is built from,
        # the mask being a constant in the image.
        (ops.AND.name, 32): "andps", (ops.AND.name, 64): "andpd",
    }

    def select_float_abs(self, dst: Reg, src: MCOperand, bits: int,
                         span: Span) -> Sequence[MCInst]:
        """Never selected here.

        There is no instruction on this architecture that clears one bit of a
        vector register, so the magnitude is an `and` with a mask that has every
        bit but the sign set -- and a mask is a constant in the image, which is
        reached where the constants are, not from here.
        """
        del dst, src, bits
        raise UnsupportedOperation(
            "a magnitude, which here is an and with a mask in the image", span)

    #: What widens a floating-point value to each wider format.
    _FLOAT_EXTEND: Final[dict[tuple[int, int], str]] = {(32, 64): "cvtss2sd"}

    def select_float_extend(self, dst: Reg, src: MCOperand, from_bits: int,
                            to_bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put *src* into *dst* in the wider format."""
        mnemonic = self._FLOAT_EXTEND.get((from_bits, to_bits))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "widening ", str(from_bits), " bits of floating point to ",
                str(to_bits))), span)
        return (self._inst(mnemonic, (MCReg(dst, bits=128), src), span),)

    def select_branch_if_finite(self, value: Reg, bits: int, target: MCSymRef,
                                span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *value* is a finite number.

        The comparison sets the parity flag where the two are not ordered at
        all, which is what a not-a-number makes of every question, so the
        difference compared against itself and a branch on parity is the whole
        of it.  The two operands being one register, nothing but a not-a-number
        can make the answer anything else.
        """
        held = REGISTERS.new_virtual(VEC, _FLOAT_REGISTER_BITS)
        return (*self._select_float_move(held, MCReg(value), span),
                self._inst(self._FLOAT_BINARY[(ops.MINUS.name, bits)],
                           (MCReg(held, bits=128), MCReg(value, bits=128)), span),
                self._inst(self._FLOAT_COMPARE[bits],
                           (MCReg(held, bits=128), MCReg(held, bits=128)), span),
                self._inst("jnp", (target,), span))

    #: What the immediate says.  Bits zero and one name a direction and bit two
    #: says to ignore them and ask `MXCSR` instead, which is the architecture's
    #: own arrangement and the reason one instruction does all four.
    _ROUNDS: Final[dict[Rounding, int]] = {
        Rounding.NEAREST: 0x00, Rounding.DOWN: 0x01, Rounding.UP: 0x02,
        Rounding.CURRENT: 0x04,
    }

    def select_float_round(self, how: Rounding, dst: Reg, src: MCOperand,
                           bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put the whole number *src* rounds to into *dst*."""
        if not self.rounds:
            # The instruction is SSE4.1, which the second level promises and
            # the first does not.  Doing it without one is a round trip through
            # an integer and a correction, which is a to-do line and not a
            # silently different answer.
            raise UnsupportedOperation(
                "rounding a floating-point number at the oldest x86-64 level",
                span)
        return (self._inst("roundss" if bits == 32 else "roundsd",
                           (MCReg(dst, bits=128), src,
                            MCImm(self._ROUNDS[how], 8, signed=False)), span),)

    def select_float_op(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                        bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over two floating-point values."""
        mnemonic = self._FLOAT_BINARY.get((op.name, bits))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "'", op.name, "' on a floating-point value")), span)
        return (*self.select_move(dst, left, span),
                self._inst(mnemonic, (MCReg(dst, bits=128), right), span))

    def select_float_compare(self, cond: Condition, dst: Reg, lhs: MCOperand,
                             rhs: MCOperand, bits: int,
                             span: Span) -> Sequence[MCInst]:
        """Instructions that put whether two floating-point values stand in
        *cond* into *dst*.

        The comparison here sets the flags of an *unsigned* comparison and, on
        top of them, the parity flag where the two are not ordered at all --
        which is what a not-a-number makes of every question.  So the orderings
        are the unsigned ones with the operands exchanged where that is what
        gives the right answer with nothing ordered, and equality has to ask
        about parity as well: two things neither of which is a number are not
        equal, and the flags alone would say they were.
        """
        low = MCReg(dst, bits=8)
        whole = MCReg(dst, bits=32)
        if cond in (Condition.EQ, Condition.NE):
            other = REGISTERS.new_virtual(GPR, 32)
            unordered = "setp" if cond is Condition.NE else "setnp"
            joining = ops.OR if cond is Condition.NE else ops.AND
            return (self._inst(self._FLOAT_COMPARE[bits], (lhs, rhs), span),
                    self._inst("sete" if cond is Condition.EQ else "setne",
                               (low,), span),
                    self._inst("movzx", (whole, low), span),
                    self._inst(unordered, (MCReg(other, bits=8),), span),
                    self._inst("movzx", (MCReg(other, bits=32),
                                         MCReg(other, bits=8)), span),
                    *self.select_op(joining, dst, (whole, MCReg(other)), span))
        exchanged = cond in (Condition.SLT, Condition.ULT, Condition.SLE, Condition.ULE)
        first, second = (rhs, lhs) if exchanged else (lhs, rhs)
        above = cond in (Condition.SLT, Condition.ULT, Condition.SGT, Condition.UGT)
        return (self._inst(self._FLOAT_COMPARE[bits], (first, second), span),
                self._inst("seta" if above else "setae", (low,), span),
                self._inst("movzx", (whole, low), span))

    _FLOAT_COMPARE: Final[dict[int, str]] = {32: "ucomiss", 64: "ucomisd"}

    def select_shift(self, op: Op, dst: Reg, value: MCOperand, amount: MCOperand,
                     bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that move the bits of *value* by *amount* into *dst*.

        The count goes in the low byte of one fixed register, which the shift
        declares it uses -- so the allocator keeps every other value out of it
        while the shift wants it, and nothing else has to be arranged.
        """
        held: list[MCInst] = []
        if self.counts_leading:
            # The third level's form, which takes the count in any register and
            # writes somewhere else again: one instruction, and no register held
            # away from every other value while the shift waits for it.  It is
            # the same level that adds the count of leading zeroes, which is
            # what that flag is asked about here.
            counted, before = self._in_register(amount, 64, span)
            held.extend(before)
            wide, before = self._in_register(value, 64, span)
            held.extend(before)
            held.append(self._inst(self._WIDE_SHIFTS[op.name],
                                   (MCReg(dst, bits=64), wide, counted), span))
            return tuple(held)
        held.extend(self.select_move(REGISTERS.view(RCX.unit, bits), amount, span))
        held.append(self._inst("mov", (MCReg(dst, bits=bits), value), span))
        held.append(self._inst(self._SHIFTS[op.name],
                               (MCReg(dst, bits=bits),
                                MCReg(REGISTERS.view(RCX.unit, 8), bits=8)), span))
        return tuple(held)

    #: What each of the three-operand shifts is called.  They exist only at the
    #: full width here, which is where the compiler's shifts are done anyway:
    #: a value narrower than a register is held in a whole one.
    _WIDE_SHIFTS: Final[dict[str, str]] = {
        ops.SHIFT_LEFT.name: "shlx", ops.SHIFT_RIGHT.name: "shrx",
        ops.SHIFT_RIGHT_SIGNED.name: "sarx",
    }

    def select_count_ones(self, dst: Reg, src: MCOperand,
                          span: Span) -> Sequence[MCInst]:
        """The number of bits set in *src*, which the second level promises."""
        return (self._inst("popcnt", (MCReg(dst, bits=64), src), span),)

    def select_count_leading(self, dst: Reg, src: MCOperand,
                             span: Span) -> Sequence[MCInst]:
        """The number of zeroes above the highest set bit, which the third
        level promises.

        It answers the width for nought, which is what the language says and
        what the older `bsr` this replaces does not: that one leaves the
        destination as it found it, which is a different answer on every run.
        """
        return (self._inst("lzcnt", (MCReg(dst, bits=64), src), span),)

    def select_divide(self, dst: Reg, left: MCOperand, right: MCOperand,
                      signed: bool, remainder: bool, bits: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that divide *left* by *right* into *dst*.

        The dividend goes in one fixed register and is stretched across a second
        before the division, which then writes the quotient to the first and the
        remainder to the second.  The divisor cannot be either of them, and
        nothing here has to say so: the instruction declares that it writes both,
        and the allocator already keeps a value out of a register whose life
        overlaps its own.
        """
        held: list[MCInst] = []
        accumulator = REGISTERS.view(RAX.unit, bits)
        # Named at the width the division is done at, which is the width of the
        # type: a value is correct in the register it is held in, and both sides
        # of the move have to agree on which part of it that is.
        dividend, before = self._in_register(left, bits, span)
        held.extend(before)
        held.extend(self.select_move(accumulator, dividend, span))
        if signed:
            held.append(self._inst("cqo" if bits == 64 else "cdq", (), span))
        else:
            # The upper half of the dividend is zero, and clearing it with a
            # move rather than an exclusive-or leaves the flags alone.
            held.extend(self.select_move(REGISTERS.view(RDX.unit, bits),
                                         MCImm(0, 32, signed=False), span))
        divisor, before = self._in_register(right, bits, span)
        held.extend(before)
        held.append(self._inst("idiv" if signed else "div", (divisor,), span))
        answer = REGISTERS.view((RDX if remainder else RAX).unit, bits)
        held.extend(self.select_move(dst, MCReg(answer), span))
        return tuple(held)

    def select_wide_product(self, low: Reg, high: Reg, left: MCOperand,
                            right: MCOperand, signed: bool,
                            span: Span) -> Sequence[MCInst]:
        """Both halves of the product of *left* and *right*.

        The one-operand multiply is the only instruction here that answers with
        the upper half.  It reads one factor in a fixed register and writes both
        halves to a fixed pair; the other factor cannot land in either of them,
        and nothing here has to say so, for the reason the division above gives.
        """
        held: list[MCInst] = []
        accumulator = REGISTERS.view(RAX.unit, 64)
        first, before = self._in_register(left, 64, span)
        held.extend(before)
        held.extend(self.select_move(accumulator, first, span))
        second, before = self._in_register(right, 64, span)
        held.extend(before)
        held.append(self._inst("imul" if signed else "mul", (second,), span))
        # The upper half first: the low one is in the register the factor came
        # from, and a caller may well have asked for the two in one register.
        held.extend(self.select_move(high, MCReg(REGISTERS.view(RDX.unit, 64)),
                                     span))
        held.extend(self.select_move(low, MCReg(accumulator), span))
        return tuple(held)

    def _in_register(self, operand: MCOperand, bits: int,
                     span: Span) -> tuple[MCReg, Sequence[MCInst]]:
        """*operand* as a register of *bits*, with whatever puts it in one."""
        if isinstance(operand, MCReg):
            return MCReg(operand.reg, bits=bits), ()
        carried = REGISTERS.new_virtual(GPR, bits)
        return MCReg(carried, bits=bits), self.select_move(carried, operand, span)

    def link_slot_size(self) -> int:
        """None.  The call instruction here pushes the return address onto the
        stack, where a further call cannot reach it."""
        return 0

    def frame_walk(self, total: int, size: int,
                   link: int) -> tuple[int | None, int]:
        """The call pushed the return address, so it lies above the whole frame
        and the caller's stack pointer is the eight bytes further that took."""
        del size, link
        return (total, total + 8)

    def select_save_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Nothing to do; the call already did it."""
        del offset, span
        return ()

    def select_restore_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Nothing to do; the return instruction takes it from the stack."""
        del offset, span
        return ()

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*.

        The whole of one of the other registers goes, not the part a floating
        point number occupies: the same register holds a run of elements, and
        which of the two a spill is saving is not something it can see.  A slot
        for one is as wide as the register, so there is room.
        """
        if self._is_float(source):
            wide = max(128, source.bits)
            return (self._inst("movdqu", (self._slot(slot, wide),
                                          MCReg(source, bits=wide)), span),)
        return (self._inst("mov", (self._slot(slot), MCReg(source, bits=64)), span),)

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        if self._is_float(destination):
            wide = max(128, destination.bits)
            return (self._inst("movdqu", (MCReg(destination, bits=wide),
                                          self._slot(slot, wide)), span),)
        return (self._inst("mov", (MCReg(destination, bits=64), self._slot(slot)),
                           span),)

    def select_frame(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that make room for *size* bytes on the stack."""
        return (self._inst("sub", (MCReg(RSP), MCImm(size, 32)), span),)

    def select_unframe(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that give that room back."""
        return (self._inst("add", (MCReg(RSP), MCImm(size, 32)), span),)


# -- lowering the IR ------------------------------------------------------------

def lower_function(asm: Assembler, func: Function, cconv: CallConvDesc,
                   registers: RegisterInfo, messages: Messages | None = None,
                   sources: SourceManager | None = None,
                   constants: Constants | None = None,
                   known_clobbers: Mapping[str, frozenset[RegUnit]] | None = None
                   ) -> None:
    """Build the machine form of one IR function.

    The bootstrap compiler generates code for as much of the language as its own
    source needs.  A construct with no rule here is reported, not ignored.
    """
    from ...ir.inst import (CodeInst, AddressInst, AnyLaneInst, BinaryInst, BrInst,
                            CallInst, CmpInst,
                            CondBrInst, SwitchInst,
                            FrameInst, AssertInst,
                            LoadInst, MemStartInst, RetInst, SplatInst, StoreInst,
                            SyscallInst, UnaryInst, UnreachableInst)
    from ...ir.function import Function as _Function
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import (BOOL, BoolType, DictType, EnumType, FloatType,
                             IntType, MEM, PtrType, ResultType, SetType,
                             VecType, VOID)
    from ...ir.inst import (CastInst, CastKind, ExtractInst, FailedInst,
                            ErrorInst, TupleInst, UnwrapInst, WrapInst)
    from ...ir.value import FloatConst, UndefConst
    from ...ir.layout import encode_float
    from ..globals import symbol_of

    # The convention is the function's and not the image's: which registers may
    # be given out and which have to be handed back as they were found are two
    # of the things a convention settles, and the specification lets two
    # functions of one compilation settle them differently.
    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "visible",
                       allocation_order=cconv.orders(GPR.name, VEC.name),
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
                return MCImm(number,
                             _immediate_width(number, _is_signed(ty),
                                              max(32, _width_of(ty))),
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
                asm.loadreg(carried, MCImm(number, max(32, _width_of(ty)),
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

        def mask(self, bits: int, span: Span) -> VirtReg:
            """A register holding every bit of a *bits*-wide float but the sign."""
            if constants is None:
                raise UnsupportedOperation(
                    "a magnitude, with nowhere to put its mask", None)
            data = ((1 << (bits - 1)) - 1).to_bytes(bits // 8, "little")
            symbol = constants.symbol(data, bits // 8)
            into = registers.new_virtual(VEC, _FLOAT_REGISTER_BITS)
            asm.loadreg(into, asm.mem(disp_sym=SymExpr(asm.streamer.symbol(symbol)),
                                      rip_relative=True, size_bits=bits), span)
            return into

        def scratch(self) -> VirtReg:
            """A register of the full width, for a value with no name of its own."""
            return registers.new_virtual(GPR, 64)

        def run_scratch(self, bits: int) -> VirtReg:
            """A register holding a whole run, for one with no name of its own."""
            return registers.new_virtual(VEC, bits)

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
                case LoadInst() if isinstance(inst.ty, VecType):
                    address = inst.operands[1]
                    bits = _run_covers(inst.ty)
                    destination = _new_value(inst.ty, registers)
                    held[id(inst)] = destination
                    asm.run_move(destination,
                                 place_of(address, span, size_bits=bits),
                                 bits, inst.span)
                case StoreInst() if isinstance(inst.operands[2].ty, VecType):
                    written = inst.operands[2]
                    assert isinstance(written.ty, VecType)
                    asm.run_store(place_of(inst.operands[1], span,
                                           size_bits=_run_covers(written.ty)),
                                  MCReg(operands.register_of(written, inst.span),
                                        bits=_run_bits(written.ty)),
                                  _run_covers(written.ty), inst.span)
                case SplatInst():
                    assert isinstance(inst.ty, VecType)
                    destination = _new_value(inst.ty, registers)
                    held[id(inst)] = destination
                    asm.run_splat(destination,
                                  operands.value(inst.operands[0], inst.span),
                                  _width_of(inst.ty.element), inst.span)
                case BinaryInst() if isinstance(inst.ty, VecType) \
                        and inst.op in TRAPPING:
                    destination = _new_value(inst.ty, registers)
                    held[id(inst)] = destination
                    try:
                        lower_trapping_run(
                            asm, inst.op, inst.ty,
                            operands.value(inst.operands[0], inst.span),
                            operands.value(inst.operands[1], inst.span),
                            destination, operands,
                            _Fault("".join((NAMES[inst.op], " that does not fit")),
                                   inst.span),
                            _LAYOUT, inst.span)
                    except Unsupported as unsupported:
                        raise UnsupportedOperation(unsupported.what, span) \
                            from unsupported
                case BinaryInst() if isinstance(inst.ty, VecType) \
                        and inst.op in SATURATING:
                    destination = _new_value(inst.ty, registers)
                    held[id(inst)] = destination
                    asm.run_saturating(
                        destination,
                        operands.value(inst.operands[0], inst.span),
                        operands.value(inst.operands[1], inst.span),
                        inst.op is BinOp.SAT_ADD,
                        _is_signed(inst.ty.element), _width_of(inst.ty.element),
                        inst.span)
                case BinaryInst() if isinstance(inst.ty, VecType):
                    destination = _new_value(inst.ty, registers)
                    held[id(inst)] = destination
                    operation = _OPERATIONS.get(inst.op)
                    if operation is None:
                        raise UnsupportedOperation("".join((
                            "'", inst.op.value, "' over a whole run")), span)
                    asm.run_op(operation, destination,
                               operands.value(inst.operands[0], inst.span),
                               operands.value(inst.operands[1], inst.span),
                               _width_of(inst.ty.element), inst.span)
                case UnaryInst() if isinstance(inst.ty, VecType):
                    if inst.op is not UnOp.NOT:
                        raise UnsupportedOperation("".join((
                            "'", inst.op.value, "' over a whole run")), span)
                    destination = _new_value(inst.ty, registers)
                    held[id(inst)] = destination
                    # Every bit of a register set, which this architecture has
                    # no constant for and one instruction for: every lane of a
                    # register compared with itself is equal to itself.
                    ones = _new_value(inst.ty, registers)
                    asm.run_ones(ones, inst.span)
                    asm.run_op(ops.XOR, destination,
                               operands.value(inst.operands[0], inst.span),
                               MCReg(ones, bits=_run_bits(inst.ty)),
                               _width_of(inst.ty.element), inst.span)
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
                                MCReg(operands.part_of(written, at, span),
                                      bits=_width_of(one)), inst.span)
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
                            _immediate_width(constant[0], _is_signed(written.ty),
                                             _width_of(written.ty)),
                            signed=_is_signed(written.ty))
                    else:
                        # A store names how much of memory it writes, so it
                        # reads the view of that width of wherever the value is.
                        put = MCReg(_value_of(written, held, span),
                                    bits=_width_of(written.ty))
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
                        asm.loadreg(result, MCImm(constant[0], max(32, _width_of(ty)),
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
                            destination, operands, max(32, _width_of(inst.ty)),
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
                            destination, operands, max(32, _width_of(inst.ty)),
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
                            destination, operands, max(32, _width_of(inst.ty)),
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
                        destination, max(32, _width_of(inst.ty)), inst.span)
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
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.float_round(_ROUNDINGS[inst.op], destination,
                                    operands.in_register(inst.operands[0], inst.span),
                                    _bits_of(inst.ty), inst.span)
                case UnaryInst() if inst.op is UnOp.FABS:
                    # Nothing here clears one bit of a vector register, so the
                    # magnitude is an `and` with a mask that has every bit but
                    # the sign set, and the mask is a constant in the image.
                    bits = _bits_of(inst.ty)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.float_op(ops.AND, destination,
                                 operands.in_register(inst.operands[0], inst.span),
                                 MCReg(operands.mask(bits, inst.span)),
                                 bits, inst.span)
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
                    normalize(asm, inst.ty, destination, max(32, _width_of(inst.ty)), inst.span)
                case CastInst() if inst.kind is CastKind.BITCAST:
                    # Nothing to emit: the bits asked for are the bits already
                    # in the register, and this says to go on reading them as
                    # something else.
                    held[id(inst)] = operands.register_of(inst.operands[0], span)
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
                    # here, so there is nothing to widen and nothing to place
                    # by a convention: the kernel names the registers.
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
    return registers.view(place.unit, max(32, _width_of(ty)))


#: What puts one value in every lane in a single instruction, by how wide the
#: value is.  The narrower registers have no such instruction and build it out
#: of the ones that double a value up.
_BROADCASTS: Final[dict[int, str]] = {
    8: "pbroadcastb", 16: "pbroadcastw", 32: "pbroadcastd", 64: "pbroadcastq",
}


def _held_bits(dst: Reg | MCMem, src: MCOperand) -> int:
    """How wide the register a run is held in is, given a move's two ends."""
    if not isinstance(dst, MCMem):
        return max(128, dst.bits)
    if isinstance(src, MCReg):
        return max(128, src.width)
    return 128


def _at_least_a_word(operand: MCOperand) -> MCOperand:
    """A number written at no fewer bits than a word, and anything else as it is."""
    if isinstance(operand, MCImm) and operand.bits < 32:
        return MCImm(operand.value, 32, signed=operand.signed)
    return operand


def _named_at(operand: MCOperand, bits: int) -> MCOperand:
    """The same operand, named at *bits* where naming it is what it is.

    A register is a view of a register and this says which view.  A number is
    declared at the width the operation is done at, where it says the same thing
    there -- an instruction that works on a byte has no form that carries four,
    and a value of a byte type is a byte.  One that does not say the same thing
    there is left as it was, and goes into a register like any other operand the
    table will not take.
    """
    if isinstance(operand, MCReg):
        return MCReg(operand.reg, bits=bits)
    if isinstance(operand, MCImm) and operand.bits > bits and _fits(operand, bits):
        return MCImm(operand.value, bits, signed=operand.signed)
    return operand


def _fits(imm: MCImm, bits: int) -> bool:
    """Whether *imm* says the same thing declared at *bits*."""
    if imm.signed:
        return -(1 << (bits - 1)) <= imm.value < (1 << (bits - 1))
    return 0 <= imm.value < (1 << bits)


def _result_register(ty: Type, cconv: CallConvDesc,
                     registers: RegisterInfo, index: int = 0) -> PhysReg:
    """The register an instruction's result is put in.

    A value narrower than a word lands in the word-wide view of the register a
    result is returned in: the architecture has narrower views, but writing one
    of the narrow ones would leave the rest of the register as it was.
    """
    from ...ir.types import FloatType

    if isinstance(ty, FloatType):
        return registers.view(cconv.float_ret_regs[index].unit,
                              _FLOAT_REGISTER_BITS)
    bits = _width_of(ty)
    return registers.view(cconv.int_ret_regs[index].unit, max(32, bits))


#: How wide a floating-point register is declared to be.  What an instruction
#: names is a view of it at the width of the value, so the whole is what the
#: value's own register is.
_FLOAT_REGISTER_BITS: Final[int] = 128


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

    A value narrower than a word gets a word-wide register: the architecture has
    narrower views and an instruction that wants one asks for it, but writing a
    narrow view would leave the rest of the register as it was, so what a value
    is computed into is always at least a word.

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
    if isinstance(ty, VecType):
        # As wide as the run it holds, brought up to the narrowest of these
        # registers there is: a run shorter than one is held in a whole one with
        # what is beyond it clear, and a longer one wants a wider register where
        # the machine has one.
        return registers.new_virtual(VEC, _run_bits(ty), hint=hint)
    if isinstance(ty, FloatType):
        # A floating-point value and a whole run of elements both belong to the
        # other kind of register, and the allocator asks a value which kind it
        # wants rather than assuming.  A run takes the whole register whatever
        # its length, since what is beyond the run has to be clear and staying
        # clear is a property of the register and not of part of one.
        return registers.new_virtual(VEC, _FLOAT_REGISTER_BITS,
                                     hint=hint)
    bits = _width_of(ty)
    return registers.new_virtual(GPR, max(32, bits), hint=hint)


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


def _value_of(value: object, held: dict[int, VirtReg],
              span: Span | None) -> VirtReg:
    """The register a value the function computed is in."""
    found = held.get(id(value))
    if found is None:
        raise UnsupportedOperation("a value this backend did not compute", span)
    return found


def _run_covers(ty: VecType) -> int:
    """How many bits of memory a whole run of elements occupies."""
    return ty.lanes * stride_of(ty.element, _LAYOUT) * 8


def _run_bits(ty: VecType) -> int:
    """How wide a register holding this run is.

    The bytes it covers, brought up to the narrowest of these registers there
    is: a run shorter than one is still held in a whole one, with what is beyond
    it clear.
    """
    return max(_FLOAT_REGISTER_BITS,
               ty.lanes * stride_of(ty.element, _LAYOUT) * 8)


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


def _immediate_width(value: int, signed: bool, width: int = 64) -> int:
    """The narrowest standard width that can carry *value* into an operation
    *width* bits wide.

    It is the width the *encoding* uses, which is not the width of the access:
    an eight-byte store carries a four-byte immediate that the instruction
    widens, so what the operand has to say is how large the number is.

    And how it widens it is the whole of the rule.  An immediate narrower than
    the operation is **sign-extended**, so it can only carry a value that reads
    the same as a signed number of that width -- 0xFFFFFFFF in an eight-byte
    operation is not four bytes of immediate, it is minus one.  At the
    operation's own width nothing is extended and any pattern will do, which is
    what lets a one-byte store carry 200.
    """
    for bits in (8, 16, 32):
        if bits >= width:
            break
        if -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
            return bits
    del signed
    return width




def _carried(src: MCOperand, into: int) -> MCOperand:
    """*src*, declared wide enough that moving it into *into* bits says what it
    means.

    An immediate narrower than the register it is moved into is sign-extended,
    so a value that does not read the same as a signed number of its declared
    width has to be declared wider before it is moved -- otherwise the four
    bytes of `0xFFFFFFFF` become eight bytes of minus one.
    """
    if not isinstance(src, MCImm) or src.bits >= into:
        return src
    if -(1 << (src.bits - 1)) <= src.value < (1 << (src.bits - 1)):
        return src
    return MCImm(src.value, into, signed=src.signed)


def _whole(operand: MCOperand) -> MCOperand:
    """*operand* naming the whole of the register it is in, where it is one."""
    return MCReg(operand.reg, bits=64) if isinstance(operand, MCReg) else operand


def _operand_width(operands: Sequence[MCOperand]) -> int:
    """How wide a register holding one of these operands has to be.

    An instruction's register operands are all of one width, so a constant put
    into a register beside them has to be of that width too; thirty-two is the
    answer where there is nothing to take it from, that being the narrowest an
    operation here is ever done at.
    """
    for operand in operands:
        if isinstance(operand, MCReg):
            return operand.bits if operand.bits is not None else operand.reg.bits
    return 32
