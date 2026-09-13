"""Instruction selection for RISC-V.

The builder speaks in three-address form with the destination first, which is
what this architecture's instructions already are, so nothing has to be lowered.

There is only one register width, so a narrow value is not a narrow register: an
``i32`` lives in a full register, sign extended, and it is the *instruction* that
says how wide the operation is.  That is why the return value here is placed in
the whole register rather than in a view of it, as on the other two backends.
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
from ..branches import (UnsupportedBranch, folded_into_branch, labels_of,
                        lower_branch, lower_comparison)
from ..narrow import normalize
from ..saturate import SATURATING, Unsupported, lower_saturating
from . import ops as rvops
from .opcodes import IMM12_MAX, IMM12_MIN, RISCV_INSTRS
from .regs import GPR, INFO, SP, ZERO

if TYPE_CHECKING:
    from ...mc.reg import PhysReg
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo
    from ...ir.types import Type
    from ..callconv import CallConvDesc

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


class RVSelector(InstructionSelector):
    """Turns builder calls into RISC-V instructions."""

    def __init__(self, table: InstrTable | None = None) -> None:
        self.table = table if table is not None else InstrTable(RISCV_INSTRS)

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

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        if isinstance(src, MCMem):
            return self._select_load(dst, src, span)
        if isinstance(src, MCReg) and src.reg is dst:
            return ()
        if isinstance(src, MCReg):
            return (self._inst("mv", (MCReg(dst), src), span),)
        if isinstance(src, MCImm):
            return self._materialize(dst, src.value, span)
        raise UnsupportedOperation("moving from this kind of operand", span)

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

    def select_store(self, address: MCMem, value: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that write *value* into the memory *address* names.

        As on the other fixed-width architecture the address has to be built in
        a register.  It is asked for as a value like any other, so the allocator
        places it and no register has to be set aside that nothing else may use.
        """
        width = address.size_bits if address.size_bits is not None else 64
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

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            raise UnsupportedOperation("only direct calls are generated yet", span)
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

    def select_widen(self, dst: Reg, src: MCOperand, bits: int, signed: bool,
                     span: Span) -> Sequence[MCInst]:
        """Instructions that put a *bits*-wide value into the whole of *dst*.

        There is one register width here, so a value of any narrower type is
        already the whole of one and this is a move.
        """
        del bits, signed
        return self.select_move(dst, src, span)

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

    def select_spill(self, slot: int, source: Reg, span: Span) -> Sequence[MCInst]:
        """Instructions that write *source* to the frame slot at *slot*."""
        return (self._inst("sd", (MCReg(source), MCReg(SP), MCImm(slot, 12)), span),)

    def select_reload(self, destination: Reg, slot: int,
                      span: Span) -> Sequence[MCInst]:
        """Instructions that read the frame slot at *slot* into *destination*."""
        return (self._inst("ld", (MCReg(destination), MCReg(SP), MCImm(slot, 12)),
                           span),)

    def select_frame(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that make room for *size* bytes on the stack."""
        return (self._inst("addi", (MCReg(SP), MCReg(SP), MCImm(-size, 12)), span),)

    def select_unframe(self, size: int, span: Span) -> Sequence[MCInst]:
        """Instructions that give that room back."""
        return (self._inst("addi", (MCReg(SP), MCReg(SP), MCImm(size, 12)), span),)


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
                asm.loadreg(carried, MCImm(number, 12,
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

        def scratch(self) -> VirtReg:
            """A register of the full width, for a value with no name of its own."""
            return registers.new_virtual(GPR, 64)

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
                        # A store writes as much of the register as the width
                        # names and reads the rest not at all, so no narrower
                        # view of it has to be asked for here.
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
                        asm.loadreg(result, MCImm(constant[0], 12,
                                                  signed=_is_signed(ty)), inst.span)
                    else:
                        asm.loadreg(result, MCReg(_value_of(value, held, span)),
                                    inst.span)
                    asm.ret(inst.span)
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
                    normalize(asm, inst.ty, destination, 64, inst.span)
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

    There is only one register width here, so a narrow value simply sits in a
    whole register; the instruction that produced it is what decided whether the
    bits above it are copies of its sign or zeroes.
    """
    del ty, registers
    return cconv.int_ret_regs[0]


def _new_value(ty: "Type", registers: "RegisterInfo",
               hint: "PhysReg | None" = None) -> VirtReg:
    """A register for a value the function computes.

    Every register here is the full width and a narrow value simply occupies
    one, so unlike the other two backends there is no view to choose.

    The hint says where the value is wanted anyway.  Taking it turns the move
    that would put it there into a move of a register to itself, which then goes.
    """
    del ty
    return registers.new_virtual(GPR, 64, hint=hint)


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


def _signed_twelve(value: int) -> int:
    """The low twelve bits of *value*, read as a signed number.

    An instruction that carries twelve bits sign-extends them, so what it adds
    is this and not the bits themselves.
    """
    low = value & 0xFFF
    return low - 0x1000 if low >= 0x800 else low
