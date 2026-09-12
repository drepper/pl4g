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
from ...mc.operand import MCImm, MCOperand, MCReg, MCSymRef
from ...mc.ops import Op
from ...mc.reg import Reg
from ...source.location import Span

if TYPE_CHECKING:
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo
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
        if self._same_register(dst, src):
            return ()
        # Clearing a register by exclusive-or is shorter, but it writes the
        # flags.  Selection always emits the form that is valid everywhere; the
        # peephole pass substitutes the shorter one where it has checked that
        # the flags are dead.
        return (self._inst("mov", (MCReg(dst), src), span),)

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
    from ...ir.inst import RetInst
    from ...ir.types import IntType
    from ...ir.value import IntConst

    asm.begin_function(func.name, exported=func.linkage.value == "exported")
    for block in func.blocks:
        for inst in block.insts:
            match inst:
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    if not isinstance(ty, IntType):
                        raise UnsupportedOperation(
                            "".join(("returning a value of type '", ty.render(), "'")),
                            inst.span if inst.span.is_valid else None)
                    target = registers.view(cconv.int_ret_regs[0].unit, max(32, ty.bits))
                    if not isinstance(value, IntConst):
                        raise UnsupportedOperation(
                            "returning a computed value", 
                            inst.span if inst.span.is_valid else None)
                    asm.loadreg(target, MCImm(value.value, max(32, ty.bits),
                                              signed=ty.signed), inst.span)
                    asm.ret(inst.span)
                case _:
                    raise UnsupportedOperation(
                        "".join(("the instruction '", inst.opcode, "'")),
                        inst.span if inst.span.is_valid else None)
    asm.end_function()
