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
from ..branches import (UnsupportedBranch, folded_into_branch, labels_of,
                        lower_branch, lower_comparison)
from ..narrow import normalize
from . import ops as a64ops
from .opcodes import AARCH64_INSTRS
from .regs import GPR, INFO, SP

if TYPE_CHECKING:
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import PhysReg, RegisterInfo
    from ...ir.types import Type
    from ..callconv import CallConvDesc

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

#: The largest value the move-wide immediate can carry in one instruction.
MAX_MOVE_IMMEDIATE: Final[int] = 0xFFFF


#: What a branch compares against where its condition is a value rather than a
#: comparison.  The width is the one this target writes a small immediate in.
ZERO_IMMEDIATE: Final[MCImm] = MCImm(0, 12, signed=False)


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

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
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
            carried = INFO.new_virtual(GPR, 32)
            before.extend(self.select_move(carried, operand, span))
            rewritten.append(MCReg(carried))
        return before, rewritten

    def select_op(self, op: Op, dst: Reg | None, sources: Sequence[MCOperand],
                  span: Span) -> Sequence[MCInst]:
        """Instructions that compute *op* over *sources* into *dst*."""
        if op is a64ops.SUPERVISOR_CALL:
            return (self._inst("svc", (MCImm(0, 16, signed=False),), span),)
        if op is ops.TRAP:
            return (self._inst("brk", (MCImm(1, 16, signed=False),), span),)
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
                # The comparison is made at the full width of the register, which
                # is what the two operands have to agree on.  That is right
                # whatever the type: a value narrower than its register carries
                # its own zeroes or its own sign above itself.
                carried = INFO.new_virtual(GPR, 64)
                return (*self.select_move(carried, rhs, span),
                        self._inst("cmp", (_whole(lhs), MCReg(carried)), span))
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

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*."""
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
                   registers: "RegisterInfo") -> None:
    """Build the machine form of one IR function."""
    from ...ir.inst import (BinaryInst, BrInst, CmpInst, CondBrInst, LoadInst,
                            MemStartInst, RetInst, StoreInst, UnaryInst,
                            UnreachableInst)
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import BoolType, IntType
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
            constant = _number_of(value)
            if constant is not None:
                number, ty = constant
                return MCImm(number, _immediate_width(number, _is_signed(ty)),
                             signed=_is_signed(ty))
            return self.in_register(value, span)

        def in_register(self, value: object, span: Span) -> MCOperand:
            """The operand for *value*, put in a register if it is not in one."""
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

    operands = _Operands()

    # Every block parameter gets its register before any block is walked: a
    # branch writes the parameters of the block it goes to, and that block may
    # come later in the layout than the branch does.
    for block in func.blocks:
        for param in block.params:
            held[id(param)] = _new_value(param.ty, registers)

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

def _result_register(ty: "Type", cconv: "CallConvDesc",
                     registers: "RegisterInfo") -> "PhysReg":
    """The register an instruction's result is put in.

    A value narrower than a word lands in the word-wide view, which the
    architecture clears the rest of when it is written.
    """
    bits = _width_of(ty)
    return registers.view(cconv.int_ret_regs[0].unit, max(32, bits))


def _new_value(ty: "Type", registers: "RegisterInfo",
               hint: "PhysReg | None" = None) -> VirtReg:
    """A register for a value the function computes.

    A value narrower than a word gets a word-wide register: an instruction that
    wants a narrower view asks for it, but what a value is computed into is
    always at least a word.

    The hint says where the value is wanted anyway.  Taking it turns the move
    that would put it there into a move of a register to itself, which then goes.
    """
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
    from ...ir.types import BoolType, IntType

    if isinstance(ty, IntType):
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
