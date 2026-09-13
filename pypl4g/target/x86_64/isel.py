"""Instruction selection for x86-64.

The builder speaks in three-address form with the destination first; x86-64
instructions take two operands, so this is where that difference is resolved.
Every builder call turns into table rows, and the table decides which encoding is
shortest.
"""

from typing import TYPE_CHECKING, Final, Sequence

from ...mc import ops
from ...mc.asmbuilder import InstructionSelector
from ...mc.desc import InstrTable, SelectionError
from ...mc.inst import MCInst
from ...mc.operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef
from ...mc.ops import Condition, Op
from ...ir.inst import BinOp, UnOp
from ...mc.reg import Reg, VirtReg
from ...mc.operand import SymExpr
from ...source.location import Span
from ..branches import (CONDITIONS, UnsupportedBranch, folded_into_branch,
                        labels_of,
                        lower_branch, lower_comparison)
from ..faults import Messages, describe
from ..pool import Constants
from ..narrow import normalize
from ..saturate import (DIVISION, NAMES, SATURATING, TRAPPING, Unsupported,
                        SHIFTS, lower_division, lower_saturating,
                        lower_shift, lower_trapping)

if TYPE_CHECKING:
    from ...mc.reg import PhysReg
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo
    from ...ir.types import Type
    from ..callconv import CallConvDesc
    from ...source.manager import SourceManager
from . import ops as x86ops
from .startup import ABORT_SYMBOL
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
    ops.TRAP.name: "ud2",
}


#: What a branch compares against where its condition is a value rather than a
#: comparison.  The width is the one this target writes a small immediate in.
ZERO_IMMEDIATE: Final[MCImm] = MCImm(0, 32, signed=False)


#: The four a floating-point value answers to.  There are no bitwise operations
#: on one and no saturating ones: what those mean is a question about bits, and
#: a floating-point type says the value is a number and not its bits.
_FLOAT_OPERATIONS: Final[dict[BinOp, "Op"]] = {
    BinOp.ADD: ops.PLUS, BinOp.SUB: ops.MINUS, BinOp.MUL: ops.TIMES,
    BinOp.FDIV: ops.DIVIDE,
}

#: What each operation of the representation is called in the assembler.
_OPERATIONS: Final[dict[BinOp, "Op"]] = {
    BinOp.ADD: ops.PLUS, BinOp.SUB: ops.MINUS, BinOp.MUL: ops.TIMES,
    BinOp.AND: ops.AND, BinOp.OR: ops.OR, BinOp.XOR: ops.XOR,
}

#: The same for the operations that take one operand.
_UNARY_OPERATIONS: Final[dict[UnOp, "Op"]] = {
    UnOp.NOT: ops.NOT, UnOp.NEG: ops.NEG,
}


class UnsupportedOperation(Exception):
    """The backend has no selection rule for this operation yet."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


class X86Selector(InstructionSelector):
    """Turns builder calls into x86-64 instructions."""

    def __init__(self, table: InstrTable | None = None) -> None:
        self.table = table if table is not None else InstrTable(X86_INSTRS)

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
            carried = REGISTERS.new_virtual(GPR, _operand_width(operands))
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
            raise UnsupportedOperation("only direct calls are generated yet", span)
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
        if not isinstance(src, MCReg):
            return self.select_move(dst, src, span)
        if not signed:
            return (self._inst("mov", (MCReg(dst, bits=32),
                                       MCReg(src.reg, bits=32)), span),)
        mnemonic = "movsxd" if bits == 32 else "movsx"
        return (self._inst(mnemonic, (MCReg(dst, bits=64),
                                      MCReg(src.reg, bits=bits)), span),)

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

    def _slot(self, slot: int) -> MCMem:
        """The place in the frame at *slot*, measured from the stack pointer."""
        return MCMem(base=RSP, disp=slot, size_bits=64)

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
        held.extend(self.select_move(REGISTERS.view(RCX.unit, bits), amount, span))
        held.append(self._inst("mov", (MCReg(dst, bits=bits), value), span))
        held.append(self._inst(self._SHIFTS[op.name],
                               (MCReg(dst, bits=bits),
                                MCReg(REGISTERS.view(RCX.unit, 8), bits=8)), span))
        return tuple(held)

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

    def select_save_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Nothing to do; the call already did it."""
        del offset, span
        return ()

    def select_restore_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Nothing to do; the return instruction takes it from the stack."""
        del offset, span
        return ()

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*."""
        if self._is_float(source):
            return (self._inst("movsd", (self._slot(slot),
                                         MCReg(source, bits=128)), span),)
        return (self._inst("mov", (self._slot(slot), MCReg(source, bits=64)), span),)

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        if self._is_float(destination):
            return (self._inst("movsd", (MCReg(destination, bits=128),
                                         self._slot(slot)), span),)
        return (self._inst("mov", (MCReg(destination, bits=64), self._slot(slot)),
                           span),)

    def select_frame(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that make room for *size* bytes on the stack."""
        return (self._inst("sub", (MCReg(RSP), MCImm(size, 32)), span),)

    def select_unframe(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that give that room back."""
        return (self._inst("add", (MCReg(RSP), MCImm(size, 32)), span),)


# -- lowering the IR ------------------------------------------------------------

def lower_function(asm: "Assembler", func: "Function", cconv: "CallConvDesc",
                   registers: "RegisterInfo", messages: "Messages | None" = None,
                   sources: "SourceManager | None" = None,
                   constants: "Constants | None" = None) -> None:
    """Build the machine form of one IR function.

    The bootstrap compiler generates code for as much of the language as its own
    source needs.  A construct with no rule here is reported, not ignored.
    """
    from ...ir.inst import (BinaryInst, BrInst, CallInst, CmpInst, CondBrInst,
                            LoadInst, MemStartInst, RetInst, StoreInst,
                            UnaryInst, UnreachableInst)
    from ...ir.function import Function as _Function
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import BoolType, FloatType, IntType, VOID
    from ...ir.inst import CastInst, CastKind
    from ...ir.value import FloatConst
    from ...ir.layout import DataLayout, encode_float
    from ..globals import symbol_of

    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "visible")
    #: Where each value the function computes is held.  A value gets a register
    #: of its own and the allocator decides which; nothing here knows or cares.
    held: dict[int, VirtReg] = {}
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

        def floating(self, value: "FloatConst", span: Span) -> VirtReg:
            """A register holding the floating-point constant *value*."""
            if constants is None:
                raise UnsupportedOperation(
                    "a floating-point constant, with nowhere to put it", None)
            bits = _width_of(value.ty)
            symbol = constants.symbol(
                encode_float(value.value, value.ty,
                             DataLayout(pointer_size=8)), bits // 8)
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

    operands = _Operands()

    class _Fault:
        """What is emitted where an answer will not fit its type."""

        def __init__(self, what: str, span: "Span") -> None:
            self.text = describe(what, func.name, span, sources)

        def out_of_range(self, asm: "Assembler", span: "Span") -> None:
            """Report the fault and stop; this does not come back."""
            if messages is None:
                raise UnsupportedOperation(
                    "an operation that can fault, with nowhere to report it", None)
            symbol = messages.symbol(self.text)
            first, second = cconv.int_arg_regs[:2]
            asm.address(first, symbol, span)
            asm.loadreg(second, asm.imm(len(self.text.encode("utf-8")), 32,
                                        signed=False), span)
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
    for index, block in enumerate(func.blocks):
        incoming = cconv.int_arg_regs if index == 0 else ()
        for position, param in enumerate(block.params):
            arriving = incoming[position] if position < len(incoming) else None
            held[id(param)] = _new_value(param.ty, registers, hint=arriving)
    entry = func.blocks[0] if func.blocks else None
    if entry is not None:
        for position, param in enumerate(entry.params):
            if position < len(cconv.int_arg_regs):
                asm.loadreg(held[id(param)],
                            MCReg(_argument_register(cconv, position, param.ty,
                                                     registers)), func.span)

    for index, block in enumerate(func.blocks):
        if index > 0:
            asm.block(labels[index])
        for inst in block.insts:
            span = inst.span if inst.span.is_valid else None
            match inst:
                case MemStartInst():
                    # Memory is not held in a register; the token exists to
                    # order the operations that touch it, and there is nothing
                    # yet for it to order.
                    pass
                case LoadInst():
                    address = inst.operands[1]
                    if not isinstance(address, GlobalVar):
                        raise UnsupportedOperation(
                            "reading through an address that is not a variable", span)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.loadreg(
                        destination,
                        asm.mem(disp_sym=SymExpr(
                                    asm.streamer.symbol(symbol_of(address))),
                                rip_relative=True, size_bits=_width_of(inst.ty),
                                signed=_is_signed(inst.ty)),
                        inst.span)
                case StoreInst():
                    address = inst.operands[1]
                    if not isinstance(address, GlobalVar):
                        raise UnsupportedOperation(
                            "writing through an address that is not a variable", span)
                    written = inst.operands[2]
                    if isinstance(written.ty, FloatType):
                        # A floating-point value goes to memory from a register
                        # of its own kind, and a constant one is read out of the
                        # image first, which is what the operand helper does.
                        asm.store(
                            asm.mem(disp_sym=SymExpr(
                                asm.streamer.symbol(symbol_of(address))),
                                rip_relative=True,
                                size_bits=_bits_of(written.ty)),
                            operands.in_register(written, inst.span), inst.span)
                        continue
                    place = asm.mem(
                        disp_sym=SymExpr(asm.streamer.symbol(symbol_of(address))),
                        rip_relative=True, size_bits=_width_of(written.ty),
                        signed=_is_signed(written.ty))
                    constant = _number_of(written)
                    if constant is not None:
                        asm.store(place, MCImm(
                            constant[0],
                            _immediate_width(constant[0], _is_signed(written.ty),
                                             _width_of(written.ty)),
                            signed=_is_signed(written.ty)), inst.span)
                    else:
                        # A store names how much of memory it writes, so it
                        # reads the view of that width of wherever the value is.
                        asm.store(place, MCReg(_value_of(written, held, span),
                                               bits=_width_of(written.ty)), inst.span)
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    if isinstance(ty, FloatType):
                        # A floating-point value goes back in a register of its
                        # own kind, and a constant one is read out of the image
                        # first, which is what the operand helper does.
                        asm.loadreg(_result_register(ty, cconv, registers),
                                    operands.in_register(value, inst.span),
                                    inst.span)
                        asm.ret(inst.span)
                        continue
                    if not isinstance(ty, (IntType, BoolType)):
                        raise UnsupportedOperation("".join((
                            "returning a value of type '", ty.render(), "'")), span)
                    result = _result_register(ty, cconv, registers)
                    constant = _number_of(value)
                    if constant is not None:
                        asm.loadreg(result, MCImm(constant[0], max(32, _width_of(ty)),
                                                  signed=_is_signed(ty)), inst.span)
                    else:
                        asm.loadreg(result, MCReg(_value_of(value, held, span)),
                                    inst.span)
                    asm.ret(inst.span)
                case BinaryInst() if isinstance(inst.ty, FloatType):
                    operation = _FLOAT_OPERATIONS.get(inst.op)
                    if operation is None:
                        raise UnsupportedOperation("".join((
                            "'", inst.op.value, "' on a floating-point value")), span)
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    asm.float_op(operation, destination,
                                 operands.in_register(inst.operands[0], inst.span),
                                 operands.in_register(inst.operands[1], inst.span),
                                 inst.ty.bits, inst.span)
                    # An answer that is an infinity or a not-a-number is an
                    # answer the operation did not have, the way a sum that
                    # will not fit is, and the program stops the same way.
                    carry_on = asm.reserve_label("is.finite")
                    asm.branch_if_finite(destination, inst.ty.bits, carry_on,
                                         inst.span)
                    _Fault("".join((NAMES[inst.op], " with no number for an answer")),
                           inst.span).out_of_range(asm, inst.span)
                    asm.block(carry_on)
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
                    destination = _new_value(
                        inst.ty, registers,
                        hint=(_result_register(inst.ty, cconv, registers)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    try:
                        lower_division(
                            asm, inst.op, inst.ty,
                            operands.value(inst.operands[0], inst.span),
                            operands.value(inst.operands[1], inst.span),
                            destination, operands, max(32, _width_of(inst.ty)),
                            _Fault("".join((NAMES[inst.op],
                                            " that does not fit")), inst.span),
                            _Fault("division by zero", inst.span),
                            inst.span)
                    except Unsupported as unsupported:
                        raise UnsupportedOperation(unsupported.what, span) \
                            from unsupported
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
                case CallInst():
                    callee = inst.callee
                    if not isinstance(callee, _Function):
                        raise UnsupportedOperation(
                            "a call through something that is not a named function",
                            span)
                    if len(inst.operands) > len(cconv.int_arg_regs):
                        raise UnsupportedOperation(
                            "a call with more arguments than the convention passes "
                            "in registers", span)
                    # The arguments go into the registers the convention names,
                    # in order.  Each is moved as late as it can be: everything
                    # the call needs is read before any of them is written, so
                    # one argument cannot be overwritten by another being put in
                    # place -- which is only true while every argument is a
                    # value the function already holds.
                    for position, argument in enumerate(inst.operands):
                        asm.loadreg(
                            _argument_register(cconv, position, argument.ty,
                                               registers),
                            operands.value(argument, inst.span), inst.span)
                    asm.call(symbol_name(callee), inst.span)
                    if inst.ty is not VOID:
                        destination = _new_value(
                            inst.ty, registers,
                            hint=(_result_register(inst.ty, cconv, registers)
                                  if inst is returned else None))
                        held[id(inst)] = destination
                        asm.loadreg(destination,
                                    MCReg(_result_register(inst.ty, cconv,
                                                           registers)),
                                    inst.span)
                case UnreachableInst():
                    asm.op(ops.TRAP, None, span=inst.span)
                case BrInst() | CondBrInst():
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

def _bits_of(ty: "Type") -> int:
    """How wide a floating-point type is, which is the width the format has."""
    from ...ir.types import FloatType

    assert isinstance(ty, FloatType)
    return ty.bits


def _argument_register(cconv: "CallConvDesc", index: int, ty: "Type",
                       registers: "RegisterInfo") -> "PhysReg":
    """The register an argument is passed in, named at the width of its type.

    The caller writes this view and the callee reads it, and both take the width
    from the type, which is what makes them agree.
    """
    from ...ir.types import FloatType

    if isinstance(ty, FloatType):
        # The whole register, not a view of it at the width of the value: what
        # an instruction names is a view, and the value's own register is the
        # whole of it, so this is the same register the callee computes into.
        return registers.view(cconv.float_arg_regs[index].unit,
                              _FLOAT_REGISTER_BITS)
    return registers.view(cconv.int_arg_regs[index].unit, max(32, _width_of(ty)))


def _result_register(ty: "Type", cconv: "CallConvDesc",
                     registers: "RegisterInfo") -> "PhysReg":
    """The register an instruction's result is put in.

    A value narrower than a word lands in the word-wide view of the register a
    result is returned in: the architecture has narrower views, but writing one
    of the narrow ones would leave the rest of the register as it was.
    """
    from ...ir.types import FloatType

    if isinstance(ty, FloatType):
        return registers.view(cconv.float_ret_regs[0].unit,
                              _FLOAT_REGISTER_BITS)
    bits = _width_of(ty)
    return registers.view(cconv.int_ret_regs[0].unit, max(32, bits))


#: How wide a floating-point register is declared to be.  What an instruction
#: names is a view of it at the width of the value, so the whole is what the
#: value's own register is.
_FLOAT_REGISTER_BITS: Final[int] = 128


def _new_value(ty: "Type", registers: "RegisterInfo",
               hint: "PhysReg | None" = None) -> VirtReg:
    """A register for a value the function computes.

    A value narrower than a word gets a word-wide register: the architecture has
    narrower views and an instruction that wants one asks for it, but writing a
    narrow view would leave the rest of the register as it was, so what a value
    is computed into is always at least a word.

    The hint says where the value is wanted anyway.  Taking it turns the move
    that would put it there into a move of a register to itself, which then goes.
    """
    from ...ir.types import FloatType

    if isinstance(ty, FloatType):
        # A floating-point value belongs to the other kind of register, and the
        # allocator now asks a value which kind it wants rather than assuming.
        return registers.new_virtual(VEC, _FLOAT_REGISTER_BITS,
                                     hint=hint)
    bits = _width_of(ty)
    return registers.new_virtual(GPR, max(32, bits), hint=hint)


def _returned_value(func: "Function") -> object:
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


def _value_of(value: object, held: "dict[int, VirtReg]",
              span: "Span | None") -> VirtReg:
    """The register a value the function computed is in."""
    found = held.get(id(value))
    if found is None:
        raise UnsupportedOperation("a value this backend did not compute", span)
    return found


def _width_of(ty: "Type") -> int:
    """How many bits a value of *ty* occupies in memory.

    A truth value is a byte, which is what the layout says it is.  Reading or
    writing one any wider would touch whatever is laid out beside it.
    """
    from ...ir.types import BoolType, FloatType, IntType

    if isinstance(ty, (IntType, FloatType)):
        return ty.bits
    return 8 if isinstance(ty, BoolType) else 64


def _number_of(value: object) -> "tuple[int, Type] | None":
    """The number a constant stands for and its type, or nothing where it is
    not a constant.

    A truth value is one or zero.  That is not a choice made here: it is what
    every instruction on this architecture that produces one produces, and what
    the byte in the image already holds.
    """
    from ...ir.value import BoolConst, IntConst

    if isinstance(value, IntConst):
        return value.value, value.ty
    if isinstance(value, BoolConst):
        return (1 if value.value else 0), value.ty
    return None


def _is_signed(ty: "Type") -> bool:
    """Whether a narrow value of *ty* is widened by its sign when it is read."""
    from ...ir.types import IntType

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


def _operand_width(operands: "Sequence[MCOperand]") -> int:
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
