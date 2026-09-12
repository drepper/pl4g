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
from ...mc.operand import MCImm, MCOperand, MCReg, MCSymRef
from ...mc.ops import Op
from ...mc.reg import Reg
from ...source.location import Span
from . import ops as a64ops
from .opcodes import AARCH64_INSTRS

if TYPE_CHECKING:
    from ...ir.function import Function
    from ...mc.asmbuilder import Assembler
    from ...mc.reg import RegisterInfo
    from ..callconv import CallConvDesc

#: The mnemonic that implements each architecture-neutral binary operation.
_BINARY: Final[dict[str, str]] = {
    ops.PLUS.name: "add",
    ops.MINUS.name: "sub",
    ops.XOR.name: "eor",
}

#: The largest value the move-wide immediate can carry in one instruction.
MAX_MOVE_IMMEDIATE: Final[int] = 0xFFFF


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

    def select_move(self, dst: Reg, src: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that place *src* into *dst*."""
        if self._same_register(dst, src):
            return ()
        if isinstance(src, MCReg):
            return (self._inst("mov", (MCReg(dst), src), span),)
        if isinstance(src, MCImm):
            if src.value < 0 or src.value > MAX_MOVE_IMMEDIATE:
                # A wider constant takes a sequence of move-wide instructions,
                # or a load from a constant pool.  Neither is generated yet.
                raise UnsupportedOperation("".join((
                    "a constant of ", str(src.value),
                    ", which does not fit one move-wide instruction")), span)
            return (self._inst("movz", (MCReg(dst), src), span),)
        raise UnsupportedOperation("moving from this kind of operand", span)

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
            return (self._inst(mnemonic, (MCReg(dst), *sources), span),)
        raise UnsupportedOperation("".join((
            "no AArch64 selection rule for '", op.name, "'")), span)

    def select_call(self, target: MCOperand, span: Span) -> Sequence[MCInst]:
        """Instructions that call *target*."""
        if not isinstance(target, MCSymRef):
            raise UnsupportedOperation("only direct calls are generated yet", span)
        return (self._inst("bl", (target,), span),)

    def select_return(self, span: Span) -> Sequence[MCInst]:
        """Instructions that return from the current function."""
        return (self._inst("ret", (), span),)


def lower_function(asm: "Assembler", func: "Function", cconv: "CallConvDesc",
                   registers: "RegisterInfo") -> None:
    """Build the machine form of one IR function."""
    from ...ir.inst import RetInst
    from ...ir.mangle import symbol_name
    from ...ir.types import IntType
    from ...ir.value import IntConst

    asm.begin_function(symbol_name(func),
                       exported=func.linkage.value == "exported")
    for block in func.blocks:
        for inst in block.insts:
            span = inst.span if inst.span.is_valid else None
            match inst:
                case RetInst() if not inst.operands:
                    asm.ret(inst.span)
                case RetInst():
                    value = inst.operands[0]
                    ty = value.ty
                    if not isinstance(ty, IntType):
                        raise UnsupportedOperation("".join((
                            "returning a value of type '", ty.render(), "'")), span)
                    if not isinstance(value, IntConst):
                        raise UnsupportedOperation("returning a computed value", span)
                    target = registers.view(cconv.int_ret_regs[0].unit,
                                            max(32, ty.bits))
                    asm.loadreg(target, MCImm(value.value, max(32, ty.bits),
                                              signed=ty.signed), inst.span)
                    asm.ret(inst.span)
                case _:
                    raise UnsupportedOperation("".join((
                        "the instruction '", inst.opcode, "'")), span)
    asm.end_function()
