"""Instruction selection for AArch64.

The builder speaks in three-address form with the destination first, which is
what this architecture's instructions already are, so there is no lowering step
of the kind x86-64 needs.
"""

from typing import TYPE_CHECKING, Final, Sequence

from ...mc import ops
from ...mc.asmbuilder import InstructionSelector
from ...mc.desc import InstrTable, SelectionError
from ...mc.inst import MCInst
from ...mc.operand import MCImm, MCMem, MCOperand, MCReg, MCSymRef
from ...mc.ops import Condition, Op
from ...ir.inst import BinOp, UnOp
from ...mc.reg import PhysReg, Reg, VirtReg
from ...mc.operand import SymExpr
from ...source.location import Span
from ..branches import (CONDITIONS, UnsupportedBranch, folded_into_branch,
                        labels_of,
                        lower_branch, lower_comparison)
from ..faults import Messages, describe
from ..pool import Constants
from ..narrow import normalize
from ...ir.layout import DataLayout, tag_offset_of
from ..callconv import TooManyArguments, argument_places
from ..saturate import (DIVISION, NAMES, SATURATING, TRAPPING, Unsupported,
                        SHIFTS, lower_division_result, lower_saturating,
                        lower_shift, lower_trapping)
from . import ops as a64ops
from .startup import ABORT_SYMBOL
from .opcodes import AARCH64_INSTRS
from .regs import GPR, INFO, SP, VEC, X30

if TYPE_CHECKING:
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import PhysReg, RegisterInfo
    from ...ir.types import Type
    from ..callconv import CallConvDesc
    from ...source.manager import SourceManager

#: The mnemonic that implements each architecture-neutral binary operation.
_BINARY: Final[dict[str, str]] = {
    ops.PLUS.name: "add",
    ops.MINUS.name: "sub",
    ops.TIMES.name: "mul",
    ops.XOR.name: "eor",
    ops.AND.name: "and",
    ops.OR.name: "orr",
}

#: Operations that take one source.  This architecture writes a destination
#: rather than working in place, so there is no move to emit first.
_UNARY: Final[dict[str, str]] = {
    ops.NOT.name: "mvn",
}

#: The branch that follows a comparison, for each condition.  The names are the
#: architecture's own: unsigned orderings are spelled with the carry flag, which
#: is what "higher" and "lower" mean here.
_CONDITIONAL: Final[dict[Condition, str]] = {
    Condition.EQ: "b.eq", Condition.NE: "b.ne",
    Condition.SLT: "b.lt", Condition.SLE: "b.le",
    Condition.SGT: "b.gt", Condition.SGE: "b.ge",
    Condition.ULT: "b.lo", Condition.ULE: "b.ls",
    Condition.UGT: "b.hi", Condition.UGE: "b.hs",
}

#: The instruction that writes a condition into a register, for each condition.
#: The architecture spells it as one instruction with the condition inside it,
#: which is why there is a row per condition rather than an operand for one.
_SET: Final[dict[Condition, str]] = {
    Condition.EQ: "cset.eq", Condition.NE: "cset.ne",
    Condition.SLT: "cset.lt", Condition.SLE: "cset.le",
    Condition.SGT: "cset.gt", Condition.SGE: "cset.ge",
    Condition.ULT: "cset.lo", Condition.ULE: "cset.ls",
    Condition.UGT: "cset.hi", Condition.UGE: "cset.hs",
}

#: The conditional select that takes a bound, for each condition.  Unlike
#: `cset`, the condition in the word is the one the name says.
_CSEL: Final[dict[Condition, str]] = {
    Condition.EQ: "csel.eq", Condition.NE: "csel.ne",
    Condition.SLT: "csel.lt", Condition.SLE: "csel.le",
    Condition.SGT: "csel.gt", Condition.SGE: "csel.ge",
    Condition.ULT: "csel.lo", Condition.ULE: "csel.ls",
    Condition.UGT: "csel.hi", Condition.UGE: "csel.hs",
}

#: Which condition a floating-point comparison is read with.  The flags are the
#: same, but a not-a-number leaves them saying "unordered", which the unsigned
#: readings of below and below-or-equal answer correctly: nothing is below
#: anything when nothing is ordered at all.
_FLOAT_CONDITION: Final[dict[Condition, Condition]] = {
    Condition.SLT: Condition.ULT, Condition.SLE: Condition.ULE,
}

#: The largest value the move-wide immediate can carry in one instruction.
MAX_MOVE_IMMEDIATE: Final[int] = 0xFFFF


#: What a branch compares against where its condition is a value rather than a
#: comparison.  The width is the one this target writes a small immediate in.
ZERO_IMMEDIATE: Final[MCImm] = MCImm(0, 12, signed=False)


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


#: What memory looks like here.  Every one of these targets has an eight-byte
#: pointer, and nothing else about the layout differs between them.
_LAYOUT: Final[DataLayout] = DataLayout(pointer_size=8)


class UnsupportedOperation(Exception):
    """The backend has no selection rule for this operation yet."""

    def __init__(self, detail: str, span: Span | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.span = span


class A64Selector(InstructionSelector):
    """Turns builder calls into AArch64 instructions."""

    def __init__(self, table: InstrTable | None = None) -> None:
        self.table = table if table is not None else InstrTable(AARCH64_INSTRS)

    def _inst(self, mnemonic: str, operands: Sequence[MCOperand], span: Span) -> MCInst:
        """Select the encoding of *mnemonic* for *operands*."""
        try:
            desc = self.table.select(mnemonic, operands)
        except SelectionError as exc:
            raise UnsupportedOperation(str(exc), span if span.is_valid else None) from exc
        return MCInst(desc=desc, operands=tuple(operands), span=span)

    def _same_register(self, dst: Reg, operand: MCOperand) -> bool:
        """Whether *operand* already names the register *dst*."""
        return isinstance(operand, MCReg) and operand.reg is dst

    #: Which load reads a value of a given width, and how it widens it.
    _LOADS: Final[dict[tuple[int, bool], str]] = {
        (8, False): "ldrb", (8, True): "ldrsb",
        (16, False): "ldrh", (16, True): "ldrsh",
        (32, False): "ldr", (32, True): "ldr",
        (64, False): "ldr", (64, True): "ldr",
    }

    def _is_float(self, reg: Reg) -> bool:
        """Whether *reg* is one of the registers a floating-point value lives in."""
        return reg.cls is VEC

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
        if isinstance(src, MCReg):
            return (self._inst("mov", (MCReg(dst), src), span),)
        if isinstance(src, MCImm):
            if 0 <= src.value <= MAX_MOVE_IMMEDIATE:
                return (self._inst("movz", (MCReg(dst), src), span),)
            return self._materialize(dst, src.value, span)
        raise UnsupportedOperation("moving from this kind of operand", span)

    def _select_float_move(self, dst: Reg, src: MCOperand,
                           span: Span) -> Sequence[MCInst]:
        """Instructions that put a floating-point value into *dst*.

        An integer load builds the address it needs in its own destination,
        which this one cannot: the address is an ordinary value and the
        destination is not an ordinary register, so a register of its own is
        asked for.  A move between registers moves eight bytes whatever the
        width of the value, which covers the narrower format and costs no more.
        """
        if isinstance(src, MCMem):
            width = src.size_bits if src.size_bits is not None else 64
            if width not in (32, 64):
                raise UnsupportedOperation("".join((
                    "reading ", str(width), " bits of floating point")), span)
            held = MCReg(dst, bits=width)
            place = MCReg(INFO.new_virtual(GPR, 64))
            offset = MCImm(src.disp, 12, signed=False)
            if src.disp_sym is None:
                base = MCReg(src.base, bits=64) if src.base is not None else place
                return (self._inst("ldr", (held, base, offset), span),)
            symbol = MCSymRef(src.disp_sym)
            return (
                self._inst("adrp", (place, symbol), span),
                self._inst("add.lo12", (place, place, symbol), span),
                self._inst("ldr", (held, place, offset), span),
            )
        if isinstance(src, MCReg):
            if self._same_register(dst, src):
                return ()
            return (self._inst("fmov", (MCReg(dst, bits=64),
                                        MCReg(src.reg, bits=64)), span),)
        raise UnsupportedOperation(
            "putting that kind of operand in a floating-point register", span)

    def _materialize(self, dst: Reg, value: int, span: Span) -> Sequence[MCInst]:
        """Instructions that build the constant *value* a quarter of a word at a
        time.

        One instruction can set sixteen bits, so a wider constant takes up to
        four.  Which four is decided by the value: the first sets a quarter and
        clears the rest, each after it sets a quarter and leaves the rest alone,
        and a quarter that is already what it should be is skipped -- so a
        constant with a small number in it costs a small number of instructions
        however wide its type.

        Where the value has more quarters of ones than of zeroes, the first
        instruction is the one that turns every bit round instead.  That is what
        makes -1 one instruction rather than four, and small negative numbers
        two rather than four.

        The whole register is written, whatever the width of the value: the
        quarters are the quarters of the eight-byte pattern, and a value of a
        narrower type has zeroes or sign in the quarters above it either way.
        """
        pattern = value & 0xFFFFFFFFFFFFFFFF
        quarters = [(pattern >> (16 * index)) & 0xFFFF for index in range(4)]
        turned = [quarter ^ 0xFFFF for quarter in quarters]
        wide = MCReg(dst, bits=64)

        def _set(index: int, quarter: int, mnemonic: str) -> MCInst:
            return self._inst(mnemonic, (wide, MCImm(quarter, 16, signed=False),
                                         MCImm(16 * index, 8, signed=False)), span)

        if sum(1 for quarter in turned if quarter) < sum(1 for q in quarters if q):
            # Every quarter turned round being zero means the value is every
            # bit set, which is what one `movn` of zero says.
            first = next((index for index, q in enumerate(turned) if q), 0)
            built = [_set(first, turned[first], "movn")]
            built.extend(_set(index, quarter, "movk")
                         for index, quarter in enumerate(quarters)
                         if index != first and quarter != 0xFFFF)
            return tuple(built)
        present = [index for index, quarter in enumerate(quarters) if quarter]
        if not present:
            return (_set(0, 0, "movz"),)
        built = [_set(present[0], quarters[present[0]], "movz")]
        built.extend(_set(index, quarters[index], "movk") for index in present[1:])
        return tuple(built)

    def _select_load(self, dst: Reg, src: MCMem, span: Span) -> Sequence[MCInst]:
        """Instructions that read memory into a register.

        No instruction here can name an address outright, so one is built a page
        at a time and then added to.  The destination doubles as the register
        that holds it: the address is consumed by the load that overwrites it,
        so nothing else has to be found to put it in.
        """
        width = src.size_bits if src.size_bits is not None else 64
        mnemonic = self._LOADS.get((width, src.signed))
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "reading ", str(width), " bits from memory")), span)
        # The address is built in the destination register, whichever that turns
        # out to be: the load consumes it and overwrites it, so nothing else has
        # to be found to hold it.  Naming the wide view of a register whose own
        # width may be narrower is what the width on the operand is for.
        address = MCReg(dst, bits=64)
        if src.disp_sym is None:
            base = MCReg(src.base, bits=64) if src.base is not None else address
            return (self._inst(mnemonic, (MCReg(dst), base,
                                          MCImm(src.disp, 12, signed=False)), span),)
        symbol = MCSymRef(src.disp_sym)
        return (
            self._inst("adrp", (address, symbol), span),
            self._inst("add.lo12", (address, address, symbol), span),
            self._inst(mnemonic, (MCReg(dst), address,
                                  MCImm(src.disp, 12, signed=False)), span),
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
            carried = INFO.new_virtual(GPR, _operand_width(operands))
            before.extend(self.select_move(carried, operand, span))
            rewritten.append(MCReg(carried))
        return before, rewritten

    def select_op(self, op: Op, dst: Reg | None, sources: Sequence[MCOperand],
                  span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over *sources* into *dst*."""
        if op is a64ops.SUPERVISOR_CALL:
            return (self._inst("svc", (MCImm(0, 16, signed=False),), span),)
        if op is ops.TRAP:
            # One rather than zero, so that a trap the compiler put there is
            # told apart in a disassembly from the padding between functions,
            # which is the same instruction with zero.
            return (self._inst("udf", (MCImm(1, 16, signed=False),), span),)
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
            "no AArch64 selection rule for '", op.name, "'")), span)

    #: Which store writes a value of a given width.  There is no signed form:
    #: what is written is what the register holds, narrowed to the width named.
    _STORES: Final[dict[int, str]] = {8: "strb", 16: "strh", 32: "str", 64: "str"}

    def select_store(self, address: MCMem, value: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that write *value* into the memory *address* names.

        Nothing here can name a place in memory outright, so the address is
        built in a register set aside for the purpose -- one the calling
        convention leaves to the caller to preserve, so that holding it across
        these three instructions costs nothing.
        """
        width = address.size_bits if address.size_bits is not None else 64
        if isinstance(value, MCReg) and self._is_float(value.reg):
            return self._select_float_store(address, value.reg, width, span)
        mnemonic = self._STORES.get(width)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "writing ", str(width), " bits to memory")), span)
        held = self._value_in_register(value, width, span)
        if address.disp_sym is None:
            base = (MCReg(address.base, bits=64) if address.base is not None
                    else MCReg(INFO.new_virtual(GPR, 64)))
            return (*held[0], self._inst(mnemonic, (held[1], base,
                                                    MCImm(address.disp, 12,
                                                          signed=False)), span))
        # A register is needed to hold the address, and it is a value like any
        # other: asking for one and letting the allocator place it is what keeps
        # this from setting a register aside that nothing else may then use.
        place = MCReg(INFO.new_virtual(GPR, 64))
        symbol = MCSymRef(address.disp_sym)
        return (
            *held[0],
            self._inst("adrp", (place, symbol), span),
            self._inst("add.lo12", (place, place, symbol), span),
            self._inst(mnemonic, (held[1], place,
                                  MCImm(address.disp, 12, signed=False)), span),
        )

    def _value_in_register(self, value: MCOperand, width: int,
                           span: Span) -> tuple[Sequence[MCInst], MCReg]:
        """Put the value to be written in a register, if it is not in one."""
        if isinstance(value, MCReg):
            return (), value
        held = INFO.new_virtual(GPR, 32 if width <= 32 else 64)
        return self.select_move(held, value, span), MCReg(held)

    def _select_float_store(self, address: MCMem, value: Reg, width: int,
                            span: Span) -> Sequence[MCInst]:
        """Instructions that write a floating-point register to memory."""
        if width not in (32, 64):
            raise UnsupportedOperation("".join((
                "writing ", str(width), " bits of floating point")), span)
        held = MCReg(value, bits=width)
        place = MCReg(INFO.new_virtual(GPR, 64))
        offset = MCImm(address.disp, 12, signed=False)
        if address.disp_sym is None:
            base = (MCReg(address.base, bits=64) if address.base is not None
                    else place)
            return (self._inst("str", (held, base, offset), span),)
        symbol = MCSymRef(address.disp_sym)
        return (
            self._inst("adrp", (place, symbol), span),
            self._inst("add.lo12", (place, place, symbol), span),
            self._inst("str", (held, place, offset), span),
        )

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            raise UnsupportedOperation("only direct calls are generated yet", span)
        return (self._inst("bl", (target,), span),)

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        return (self._inst("ret", (), span),)

    def select_jump(self, target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that transfer control to *target*."""
        return (self._inst("b", (target,), span),)

    def select_branch(self, cond: Condition, lhs: MCOperand, rhs: MCOperand,
                      target: MCSymRef, span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *lhs* and *rhs* stand in *cond*.

        Comparing a register with zero for equality has an instruction of its
        own that needs no flags, so that case is one instruction rather than two.
        """
        if isinstance(lhs, MCImm) and not isinstance(rhs, MCImm):
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        if (isinstance(lhs, MCReg) and isinstance(rhs, MCImm) and rhs.value == 0
                and cond in (Condition.EQ, Condition.NE)):
            mnemonic = "cbz" if cond is Condition.EQ else "cbnz"
            if mnemonic in self.table.mnemonics:
                return (self._inst(mnemonic, (lhs, target), span),)
        return (*self._select_compare(lhs, rhs, span),
                self._inst(_CONDITIONAL[cond], (target,), span))

    def select_address(self, dst: Reg, symbol: MCSymRef,
                       span: Span) -> Sequence[MCInst]:
        """Instructions that put the address of *symbol* into *dst*.

        A page at a time and then added to, the two halves being independent of
        one another here.
        """
        address = MCReg(dst, bits=64)
        return (self._inst("adrp", (address, symbol), span),
                self._inst("add.lo12", (address, address, symbol), span))

    def select_widen(self, dst: Reg, src: MCOperand, bits: int, signed: bool,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that put a *bits*-wide value into the whole of *dst*.

        Writing a word-wide view clears the word above it, so widening an
        unsigned value is an ordinary move.  A signed one is the bitfield move
        the architecture spells `sxtw`.
        """
        if bits >= 64:
            return self.select_move(dst, src, span)
        if not isinstance(src, MCReg):
            return self.select_move(dst, src, span)
        if not signed:
            return (self._inst("mov", (MCReg(dst, bits=32),
                                       MCReg(src.reg, bits=32)), span),)
        mnemonic = {8: "sxtb", 16: "sxth", 32: "sxtw"}.get(bits)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "widening a value of ", str(bits), " bits")), span)
        return (self._inst(mnemonic, (MCReg(dst, bits=64),
                                      MCReg(src.reg, bits=32)), span),)

    def select_clamp(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                     bound: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that put *bound* into *dst* where the two stand in *cond*.

        The select takes two registers and picks between them, so it is handed
        the bound and the destination and writes the destination.  A bound that
        arrives as a constant is built first.
        """
        if isinstance(lhs, MCImm) and not isinstance(rhs, MCImm):
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        before: list[MCInst] = []
        if not isinstance(bound, MCReg):
            carried = INFO.new_virtual(GPR, 64)
            before.extend(self.select_move(carried, bound, span))
            bound = MCReg(carried)
        whole = MCReg(dst, bits=64)
        return (*before, *self._select_compare(lhs, rhs, span),
                self._inst(_CSEL[cond], (whole, MCReg(bound.reg, bits=64), whole),
                           span))

    def _select_compare(self, lhs: MCOperand, rhs: MCOperand,
                        span: Span) -> Sequence[MCInst]:
        """The instructions that set the flags for a comparison.

        Usually one.  A comparison carries twelve bits of constant, so anything
        wider is built in a register first -- which is the same answer every
        other instruction here gives to the same question.
        """
        if isinstance(rhs, MCImm):
            try:
                self.table.select("cmp", (lhs, rhs))
            except SelectionError:
                # The two operands have to be the same width, and the width
                # is the left one's: a value is correct in the register it is
                # held in and says nothing about what is above that.
                width = lhs.reg.bits if isinstance(lhs, MCReg) else 64
                carried = INFO.new_virtual(GPR, width)
                return (*self.select_move(carried, rhs, span),
                        self._inst("cmp", (lhs, MCReg(carried)), span))
        return (self._inst("cmp", (lhs, rhs), span),)

    def select_set(self, cond: Condition, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                   span: Span) -> Sequence[MCInst]:
        """Instructions that put whether *lhs* and *rhs* stand in *cond* into *dst*.

        Two instructions: the comparison writes the flags and one instruction
        reads them into a register.  Writing the word-wide view clears the rest
        of the register, so nothing has to widen the answer afterwards.
        """
        if isinstance(lhs, MCImm) and not isinstance(rhs, MCImm):
            lhs, rhs, cond = rhs, lhs, cond.swapped()
        return (*self._select_compare(lhs, rhs, span),
                self._inst(_SET[cond], (MCReg(dst, bits=32),), span))

    # -- the stack -------------------------------------------------------------

    #: What each of the three shifts is called here.
    _SHIFTS: Final[dict[str, str]] = {
        ops.SHIFT_LEFT.name: "lslv", ops.SHIFT_RIGHT.name: "lsrv",
        ops.SHIFT_RIGHT_SIGNED.name: "asrv",
    }

    #: What each operation is called for each width of floating-point value.
    _FLOAT_BINARY: Final[dict[str, str]] = {
        ops.PLUS.name: "fadd", ops.MINUS.name: "fsub",
        ops.TIMES.name: "fmul", ops.DIVIDE.name: "fdiv",
    }

    def select_float_abs(self, dst: Reg, src: MCOperand, bits: int,
                         span: Span) -> Sequence[MCInst]:
        """Instructions that put the magnitude of *src* into *dst*."""
        return (self._inst("fabs", (MCReg(dst, bits=bits), _at(src, bits)), span),)

    def select_float_extend(self, dst: Reg, src: MCOperand, from_bits: int,
                            to_bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put *src* into *dst* in the wider format."""
        if (from_bits, to_bits) != (32, 64):
            raise UnsupportedOperation("".join((
                "widening ", str(from_bits), " bits of floating point to ",
                str(to_bits))), span)
        return (self._inst("fcvt", (MCReg(dst, bits=64), _at(src, 32)), span),)

    def select_branch_if_finite(self, value: Reg, bits: int, target: MCSymRef,
                                span: Span) -> Sequence[MCInst]:
        """Instructions that go to *target* when *value* is a finite number.

        A comparison of the difference against itself is equal where it is a
        number and unordered where it is not, and "unordered" is not "equal",
        so the branch taken on equality is the branch taken on a finite value.
        """
        held = INFO.new_virtual(VEC, _FLOAT_REGISTER_BITS)
        view = MCReg(value, bits=bits)
        difference = MCReg(held, bits=bits)
        return (self._inst("fsub", (difference, view, view), span),
                self._inst("fcmp", (difference, difference), span),
                self._inst("b.eq", (target,), span))

    def select_float_op(self, op: Op, dst: Reg, left: MCOperand, right: MCOperand,
                        bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over two floating-point values."""
        mnemonic = self._FLOAT_BINARY.get(op.name)
        if mnemonic is None:
            raise UnsupportedOperation("".join((
                "'", op.name, "' on a floating-point value")), span)
        return (self._inst(mnemonic, (MCReg(dst, bits=bits), _at(left, bits),
                                      _at(right, bits)), span),)

    def select_float_compare(self, cond: Condition, dst: Reg, lhs: MCOperand,
                             rhs: MCOperand, bits: int,
                             span: Span) -> Sequence[MCInst]:
        """Instructions that put whether two floating-point values stand in
        *cond* into *dst*.

        The comparison sets the same flags an integer one does, but a
        not-a-number leaves them saying "unordered", which the *unsigned*
        readings of less-than and less-or-equal answer correctly: nothing is
        below anything when nothing is ordered.
        """
        return (self._inst(self._FLOAT_COMPARE[bits],
                           (_at(lhs, bits), _at(rhs, bits)), span),
                self._inst(_SET[_FLOAT_CONDITION.get(cond, cond)],
                           (MCReg(dst, bits=32),), span))

    _FLOAT_COMPARE: Final[dict[int, str]] = {32: "fcmp", 64: "fcmp"}

    def select_shift(self, op: Op, dst: Reg, value: MCOperand, amount: MCOperand,
                     bits: int, span: Span) -> Sequence[MCInst]:
        """Instructions that move the bits of *value* by *amount* into *dst*."""
        held: list[MCInst] = []
        held_value, before = self._in_register(value, bits, span)
        held.extend(before)
        counted, before = self._in_register(amount, bits, span)
        held.extend(before)
        held.append(self._inst(self._SHIFTS[op.name],
                               (MCReg(dst, bits=bits), held_value, counted), span))
        return tuple(held)

    def select_divide(self, dst: Reg, left: MCOperand, right: MCOperand,
                      signed: bool, remainder: bool, bits: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that divide *left* by *right* into *dst*.

        There is a division and no remainder, so what is left over is worked out
        from the quotient: the dividend less the quotient times the divisor,
        which is one instruction here.
        """
        held: list[MCInst] = []
        dividend, before = self._in_register(left, bits, span)
        held.extend(before)
        divisor, before = self._in_register(right, bits, span)
        held.extend(before)
        quotient = MCReg(dst, bits=bits) if not remainder else MCReg(
            INFO.new_virtual(GPR, 64), bits=bits)
        held.append(self._inst("sdiv" if signed else "udiv",
                               (quotient, dividend, divisor), span))
        if remainder:
            held.append(self._inst("msub", (MCReg(dst, bits=bits), quotient,
                                            divisor, dividend), span))
        return tuple(held)

    def _in_register(self, operand: MCOperand, bits: int,
                     span: Span) -> tuple[MCReg, Sequence[MCInst]]:
        """*operand* as a register of *bits*, with whatever puts it in one."""
        if isinstance(operand, MCReg):
            return MCReg(operand.reg, bits=bits), ()
        carried = INFO.new_virtual(GPR, 64)
        return MCReg(carried, bits=bits), self.select_move(carried, operand, span)

    def link_slot_size(self) -> int:
        """A whole stack unit, the stack having to stay aligned to sixteen."""
        return 16

    def select_save_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Instructions that put the return address into the frame at *offset*."""
        return (self._inst("str", (MCReg(X30, bits=64), MCReg(SP),
                                   MCImm(offset, 12, signed=False)), span),)

    def select_restore_link(self, offset: int, span: Span) -> Sequence[MCInst]:
        """Instructions that take it back out again."""
        return (self._inst("ldr", (MCReg(X30, bits=64), MCReg(SP),
                                   MCImm(offset, 12, signed=False)), span),)

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*.

        Eight bytes whichever kind of register it is: the slot is that wide, and
        what is written is read back by the instruction beside this one.
        """
        return (self._inst("str", (MCReg(source, bits=64), MCReg(SP),
                                   MCImm(slot, 12, signed=False)), span),)

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        return (self._inst("ldr", (MCReg(destination, bits=64), MCReg(SP),
                                   MCImm(slot, 12, signed=False)), span),)

    def select_frame(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that make room for *size* bytes on the stack."""
        return (self._inst("sub", (MCReg(SP), MCReg(SP),
                                   MCImm(size, 12, signed=False)), span),)

    def select_unframe(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that give that room back."""
        return (self._inst("add", (MCReg(SP), MCReg(SP),
                                   MCImm(size, 12, signed=False)), span),)


def lower_function(asm: "Assembler", func: "Function", cconv: "CallConvDesc",
                   registers: "RegisterInfo", messages: "Messages | None" = None,
                   sources: "SourceManager | None" = None,
                   constants: "Constants | None" = None) -> None:
    """Build the machine form of one IR function."""
    from ...ir.inst import (BinaryInst, BrInst, CallInst, CmpInst, CondBrInst,
                            LoadInst, MemStartInst, RetInst, StoreInst,
                            UnaryInst, UnreachableInst)
    from ...ir.function import Function as _Function
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import BOOL, BoolType, FloatType, IntType, ResultType, VOID
    from ...ir.inst import (CastInst, CastKind, FailedInst, UnwrapInst,
                            WrapInst)
    from ...ir.value import FloatConst, UndefConst
    from ...ir.layout import encode_float
    from ..globals import symbol_of

    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "visible")
    #: Where each value the function computes is held.  A value gets a register
    #: of its own and the allocator decides which; nothing here knows or cares.
    held: dict[int, VirtReg] = {}
    #: The other half of a value whose type is a result: the truth value saying
    #: whether there is an answer.  A result is two registers and never one, so
    #: the allocator sees two ordinary values and nothing aggregate at all.
    flags: dict[int, VirtReg] = {}
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
                asm.loadreg(carried, MCImm(number, max(32, _width_of(ty)),
                                           signed=_is_signed(ty)), span)
                return MCReg(carried)
            found = held.get(id(value))
            if found is None:
                raise UnsupportedOperation(
                    "a value this backend did not compute",
                    span if span.is_valid else None)
            return MCReg(found)

        def register_of(self, value: object, span: "Span | None") -> VirtReg:
            """The register a value the function computed is held in."""
            found = held.get(id(value))
            if found is None:
                raise UnsupportedOperation(
                    "a value this backend did not compute", span)
            return found

        def flag_of(self, value: object, span: "Span | None") -> VirtReg:
            """The register holding whether a result has an answer."""
            found = flags.get(id(value))
            if found is None:
                raise UnsupportedOperation(
                    "a result this backend did not compute", span)
            return found

        def undefined(self, value: object, ty: "Type", span: Span) -> MCOperand:
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

        def floating(self, value: "FloatConst", span: Span) -> VirtReg:
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
                if isinstance(param.ty, ResultType):
                    raise UnsupportedOperation(
                        "a block parameter whose type is a result", None)
                held[id(param)] = _new_value(param.ty, registers)
                continue
            first, second = arriving[position]
            answer = param.ty.ok if isinstance(param.ty, ResultType) else param.ty
            held[id(param)] = _new_value(
                answer, registers, hint=_as_argument(first, answer, registers))
            if second is not None:
                flags[id(param)] = _new_value(
                    BOOL, registers, hint=_as_argument(second, BOOL, registers))
    if entry is not None:
        for position, param in enumerate(entry.params):
            first, second = arriving[position]
            answer = param.ty.ok if isinstance(param.ty, ResultType) else param.ty
            asm.loadreg(held[id(param)],
                        MCReg(_as_argument(first, answer, registers)), func.span)
            if second is not None:
                asm.loadreg(flags[id(param)],
                            MCReg(_as_argument(second, BOOL, registers)), func.span)

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
                case LoadInst() if isinstance(inst.ty, ResultType):
                    address = inst.operands[1]
                    if not isinstance(address, GlobalVar):
                        raise UnsupportedOperation(
                            "reading through an address that is not a variable", span)
                    answer = inst.ty.ok
                    destination = _new_value(
                        answer, registers,
                        hint=(_result_register(answer, cconv, registers)
                              if inst is returned else None))
                    failed = _new_value(
                        BOOL, registers,
                        hint=(_result_register(BOOL, cconv, registers, 1)
                              if inst is returned else None))
                    held[id(inst)] = destination
                    flags[id(inst)] = failed
                    # Two reads of one place: the answer where an answer goes,
                    # and the truth value where the layout puts it.
                    place = asm.streamer.symbol(symbol_of(address))
                    asm.loadreg(destination,
                                asm.mem(disp_sym=SymExpr(place), rip_relative=True,
                                        size_bits=_width_of(answer),
                                        signed=_is_signed(answer)), inst.span)
                    asm.loadreg(failed,
                                asm.mem(disp_sym=SymExpr(place), rip_relative=True,
                                        disp=tag_offset_of(inst.ty, _LAYOUT),
                                        size_bits=8, signed=False), inst.span)
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
                    if isinstance(written.ty, ResultType):
                        # Two writes of one place, as a read of one is two.
                        answer = written.ty.ok
                        place = asm.streamer.symbol(symbol_of(address))
                        asm.store(
                            asm.mem(disp_sym=SymExpr(place), rip_relative=True,
                                    size_bits=_width_of(answer),
                                    signed=_is_signed(answer)),
                            MCReg(operands.register_of(written, span)), inst.span)
                        asm.store(
                            asm.mem(disp_sym=SymExpr(place), rip_relative=True,
                                    disp=tag_offset_of(written.ty, _LAYOUT),
                                    size_bits=8),
                            MCReg(operands.flag_of(written, span)), inst.span)
                        continue
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
                            _immediate_width(constant[0], _is_signed(written.ty)),
                            signed=_is_signed(written.ty)), inst.span)
                    else:
                        # A narrow store here names the whole register and
                        # writes as much of it as the width says, so unlike
                        # x86-64 there is no narrower view to ask for.
                        asm.store(place, MCReg(_value_of(written, held, span)),
                                  inst.span)
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    if isinstance(ty, ResultType):
                        # Two registers, which is what every one of these
                        # architectures returns a two-word answer in.
                        asm.loadreg(_result_register(ty.ok, cconv, registers),
                                    MCReg(operands.register_of(value, span)),
                                    inst.span)
                        asm.loadreg(_result_register(BOOL, cconv, registers, 1),
                                    MCReg(operands.flag_of(value, span)),
                                    inst.span)
                        asm.ret(inst.span)
                        continue
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
                        flags[id(inst)] = failed
                        asm.float_compare(
                            Condition.EQ, failed, divisor,
                            MCReg(operands.floating(FloatConst(answer, 0.0),
                                                    inst.span)),
                            answer.bits, inst.span)
                    asm.float_op(operation, destination,
                                 operands.in_register(inst.operands[0], inst.span),
                                 divisor, answer.bits, inst.span)
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
                    flags[id(inst)] = failed
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
                case WrapInst():
                    # One value made of two, which here is two registers with
                    # nothing between them: the answer goes where an answer
                    # goes, and the truth value beside it.
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
                    flags[id(inst)] = failed
                    asm.loadreg(destination,
                                operands.undefined(inst.operands[0], answer,
                                                   inst.span),
                                inst.span)
                    asm.loadreg(failed, operands.value(inst.operands[1], inst.span),
                                inst.span)
                case UnwrapInst():
                    # Nothing to emit: the answer half is already in a register
                    # of its own, and this says to go on using it.
                    held[id(inst)] = operands.register_of(inst.operands[0], span)
                case FailedInst():
                    held[id(inst)] = operands.flag_of(inst.operands[0], span)
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
                    try:
                        going = argument_places(
                            cconv, [a.ty for a in inst.operands])
                    except TooManyArguments as many:
                        raise UnsupportedOperation(
                            "a call with more arguments than the convention passes "
                            "in registers", span) from many
                    # The arguments go into the registers the convention names,
                    # in order.  Each is moved as late as it can be: everything
                    # the call needs is read before any of them is written, so
                    # one argument cannot be overwritten by another being put in
                    # place -- which is only true while every argument is a
                    # value the function already holds.
                    for position, argument in enumerate(inst.operands):
                        first, second = going[position]
                        if isinstance(argument.ty, ResultType):
                            asm.loadreg(
                                _as_argument(first, argument.ty.ok, registers),
                                MCReg(operands.register_of(argument, span)),
                                inst.span)
                            assert second is not None
                            asm.loadreg(
                                _as_argument(second, BOOL, registers),
                                MCReg(operands.flag_of(argument, span)), inst.span)
                            continue
                        asm.loadreg(
                            _as_argument(first, argument.ty, registers),
                            operands.value(argument, inst.span), inst.span)
                    asm.call(symbol_name(callee), inst.span)
                    if isinstance(inst.ty, ResultType):
                        destination = _new_value(
                            inst.ty.ok, registers,
                            hint=(_result_register(inst.ty.ok, cconv, registers)
                                  if inst is returned else None))
                        failed = _new_value(
                            BOOL, registers,
                            hint=(_result_register(BOOL, cconv, registers, 1)
                                  if inst is returned else None))
                        held[id(inst)] = destination
                        flags[id(inst)] = failed
                        asm.loadreg(destination,
                                    MCReg(_result_register(inst.ty.ok, cconv,
                                                           registers)), inst.span)
                        asm.loadreg(failed,
                                    MCReg(_result_register(BOOL, cconv, registers,
                                                           1)), inst.span)
                    elif inst.ty is not VOID:
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

def _is_floating(ty: "Type") -> bool:
    """Whether *ty* is a floating-point type, or a result answering with one."""
    from ...ir.types import FloatType, ResultType

    if isinstance(ty, ResultType):
        ty = ty.ok
    return isinstance(ty, FloatType)


def _bits_of(ty: "Type") -> int:
    """How wide a floating-point type is, which is the width the format has."""
    from ...ir.types import FloatType

    assert isinstance(ty, FloatType)
    return ty.bits


def _as_argument(place: "PhysReg", ty: "Type",
                 registers: "RegisterInfo") -> "PhysReg":
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


def _result_register(ty: "Type", cconv: "CallConvDesc",
                     registers: "RegisterInfo", index: int = 0) -> "PhysReg":
    """The register an instruction's result is put in.

    A value narrower than a word lands in the word-wide view, which the
    architecture clears the rest of when it is written.
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


def _new_value(ty: "Type", registers: "RegisterInfo",
               hint: "PhysReg | None" = None) -> VirtReg:
    """A register for a value the function computes.

    A value narrower than a word gets a word-wide register: an instruction that
    wants a narrower view asks for it, but what a value is computed into is
    always at least a word.

    The hint says where the value is wanted anyway.  Taking it turns the move
    that would put it there into a move of a register to itself, which then goes.
    """
    from ...ir.types import FloatType, ProductType, SumType

    if isinstance(ty, (ProductType, SumType)):
        # A product is every one of its fields at once and a sum is one of its
        # variants and a tag; neither is a thing a register holds.  What they
        # want is a place in memory, and nothing in the language makes a value
        # of one yet, so this is where saying so belongs.
        raise UnsupportedOperation("".join((
            "a value of type '", ty.render(), "'")), None)
    if isinstance(ty, FloatType):
        # A floating-point value belongs to the other kind of register, and the
        # allocator now asks a value which kind it wants rather than assuming.
        return registers.new_virtual(VEC, _FLOAT_REGISTER_BITS,
                                     hint=hint)
    bits = _width_of(ty)
    return registers.new_virtual(GPR, max(32, bits), hint=hint)


def _value_of(value: object, held: "dict[int, VirtReg]",
              span: "Span | None") -> VirtReg:
    """The register a value the function computed is in."""
    found = held.get(id(value))
    if found is None:
        raise UnsupportedOperation("a value this backend did not compute", span)
    return found


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


def _at(operand: MCOperand, bits: int) -> MCOperand:
    """*operand* naming the part of its register a value of *bits* occupies."""
    return MCReg(operand.reg, bits=bits) if isinstance(operand, MCReg) else operand
