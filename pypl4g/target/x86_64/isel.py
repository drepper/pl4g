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
from ...mc.ops import Op
from ...mc.reg import Reg
from ...mc.operand import SymExpr
from ...source.location import Span

if TYPE_CHECKING:
    from ...mc.reg import PhysReg
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo
    from ...ir.types import Type
    from ..callconv import CallConvDesc
from . import ops as x86ops
from .opcodes import X86_INSTRS

#: The mnemonic that implements each architecture-neutral binary operation.
_BINARY: Final[dict[str, str]] = {
    ops.PLUS.name: "add",
    ops.MINUS.name: "sub",
    ops.XOR.name: "xor",
}

#: Operations that map to a single instruction with no operands.
_NULLARY: Final[dict[str, str]] = {
    x86ops.SYSCALL.name: "syscall",
    ops.TRAP.name: "ud2",
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

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        if isinstance(src, MCMem):
            return self._select_load(dst, src, span)
        if self._same_register(dst, src):
            return ()
        # Clearing a register by exclusive-or is shorter, but it writes the
        # flags.  Selection always emits the form that is valid everywhere; the
        # peephole pass substitutes the shorter one where it has checked that
        # the flags are dead.
        return (self._inst("mov", (MCReg(dst), src), span),)

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
            return self._two_address(mnemonic, dst, sources[0], sources[1], span)
        raise UnsupportedOperation("".join((
            "no x86-64 selection rule for '", op.name, "'")), span)

    def _two_address(self, mnemonic: str, dst: Reg, lhs: MCOperand, rhs: MCOperand,
                     span: Span) -> Sequence[MCInst]:
        """Lower a three-address operation onto a two-operand instruction."""
        if self._same_register(dst, lhs):
            return (self._inst(mnemonic, (MCReg(dst), rhs), span),)
        moved = self.select_move(dst, lhs, span)
        return (*moved, self._inst(mnemonic, (MCReg(dst), rhs), span))

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            raise UnsupportedOperation("only direct calls are generated yet", span)
        return (self._inst("call", (target,), span),)

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        return (self._inst("ret", (), span),)


# -- lowering the IR ------------------------------------------------------------

def lower_function(asm: "Assembler", func: "Function", cconv: "CallConvDesc",
                   registers: "RegisterInfo") -> None:
    """Build the machine form of one IR function.

    The bootstrap compiler generates code for as much of the language as its own
    source needs.  A construct with no rule here is reported, not ignored.
    """
    from ...ir.inst import LoadInst, MemStartInst, RetInst
    from ...ir.mangle import symbol_name
    from ...ir.module import GlobalVar
    from ...ir.types import IntType
    from ...ir.value import IntConst
    from ..globals import symbol_of

    _check_single_use(func)
    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "exported")
    previous: object = None
    for block in func.blocks:
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
                    asm.loadreg(
                        _result_register(inst.ty, cconv, registers),
                        asm.mem(disp_sym=SymExpr(
                                    asm.streamer.symbol(symbol_of(address))),
                                rip_relative=True, size_bits=_width_of(inst.ty),
                                signed=_is_signed(inst.ty)),
                        inst.span)
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    if not isinstance(ty, IntType):
                        raise UnsupportedOperation("".join((
                            "returning a value of type '", ty.render(), "'")), span)
                    if isinstance(value, IntConst):
                        asm.loadreg(_result_register(ty, cconv, registers),
                                    MCImm(value.value, max(32, ty.bits),
                                          signed=ty.signed), inst.span)
                    elif value is not previous:
                        # Anything else is a value the single register was no
                        # longer holding by the time the return was reached.
                        raise UnsupportedOperation("returning a computed value", span)
                    asm.ret(inst.span)
                case _:
                    raise UnsupportedOperation("".join((
                        "the instruction '", inst.opcode, "'")), span)
            previous = inst
    asm.end_function()

def _result_register(ty: "Type", cconv: "CallConvDesc",
                     registers: "RegisterInfo") -> "PhysReg":
    """The register an instruction's result is put in.

    A value narrower than a word lands in the word-wide view of the register a
    result is returned in: the architecture has narrower views, but writing one
    of the narrow ones would leave the rest of the register as it was.
    """
    from ...ir.types import IntType

    bits = ty.bits if isinstance(ty, IntType) else 64
    return registers.view(cconv.int_ret_regs[0].unit, max(32, bits))


def _check_single_use(func: "Function") -> None:
    """Refuse a function that would need more than one value live at once.

    Every value a function computes is put in the same register, which is only
    correct while nothing else needs one at the same time.  Where a value is
    used anywhere but in the instruction directly after it, that no longer
    holds, and the function is reported as beyond what this compiler generates
    rather than quietly compiled wrong.  A register allocator is what lifts this.
    """
    from ...ir.types import MEM

    for block in func.blocks:
        for index, inst in enumerate(block.insts):
            if not inst.has_result or inst.ty is MEM:
                # A memory token occupies no register: it orders the operations
                # that touch memory and is never held anywhere.
                continue
            users = [position for position, other in enumerate(block.insts)
                     if any(operand is inst for operand in other.operands)]
            # A value nothing reads needs no register at all.  One that is read
            # must be read by the instruction directly after it, since the
            # register it sits in is about to hold the next value.
            if users and users != [index + 1]:
                raise UnsupportedOperation(
                    "a function needing more than one value at a time, which needs "
                    "a register allocator",
                    inst.span if inst.span.is_valid else None)


def _width_of(ty: "Type") -> int:
    """How many bits a value of *ty* occupies in memory."""
    from ...ir.types import IntType

    return ty.bits if isinstance(ty, IntType) else 64


def _is_signed(ty: "Type") -> bool:
    """Whether a narrow value of *ty* is widened by its sign when it is read."""
    from ...ir.types import IntType

    return isinstance(ty, IntType) and ty.signed
