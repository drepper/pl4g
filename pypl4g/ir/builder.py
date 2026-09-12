"""Building IR.

The builder is the only thing that appends to a block, so the invariant that a
block is terminated exactly once has one place to be enforced as code is
generated rather than only when the verifier runs.
"""

from ..source.location import INVALID_SPAN, Span
from .function import BasicBlock, Function
from .inst import (BinaryInst, BinOp, BlockTarget, BrInst, CastInst, CastKind,
                   CmpInst, CmpPred, CondBrInst, Instruction, RetInst, Terminator,
                   UnaryInst, UnOp, UnreachableInst)
from .module import Module
from .types import BOOL, IntType, Type
from .value import Value


class IRBuilder:
    """Appends instructions to a block of a function."""

    def __init__(self, module: Module, func: Function) -> None:
        self._module = module
        self._func = func
        self._block: BasicBlock | None = func.entry

    @property
    def module(self) -> Module:
        """The module being built."""
        return self._module

    @property
    def function(self) -> Function:
        """The function being built."""
        return self._func

    @property
    def block(self) -> BasicBlock | None:
        """The block instructions are appended to."""
        return self._block

    @property
    def is_terminated(self) -> bool:
        """Whether the current block already ends in a terminator."""
        return self._block is None or self._block.terminator is not None

    def new_block(self, label: str | None = None) -> BasicBlock:
        """Create a new block in the function."""
        return self._func.add_block(label)

    def position_at(self, block: BasicBlock | None) -> None:
        """Append subsequent instructions to *block*."""
        self._block = block

    def _append(self, inst: Instruction) -> Instruction:
        """Append *inst*, ignoring it if the block is already terminated."""
        if self._block is None or self._block.terminator is not None:
            return inst
        return self._block.append(inst)

    # -- constants -------------------------------------------------------------

    def int_const(self, ty: IntType, value: int) -> Value:
        """An integer constant of type *ty*."""
        return self._module.int_const(ty, value)

    def bool_const(self, value: bool) -> Value:
        """A boolean constant."""
        return self._module.bool_const(BOOL, value)

    # -- instructions ----------------------------------------------------------

    def binary(self, op: BinOp, lhs: Value, rhs: Value, span: Span = INVALID_SPAN) -> Value:
        """Append a binary operation."""
        return self._append(BinaryInst(op, lhs, rhs, span))

    def unary(self, op: UnOp, value: Value, span: Span = INVALID_SPAN) -> Value:
        """Append a unary operation."""
        return self._append(UnaryInst(op, value, span))

    def compare(self, pred: CmpPred, lhs: Value, rhs: Value,
                span: Span = INVALID_SPAN) -> Value:
        """Append a comparison."""
        return self._append(CmpInst(pred, lhs, rhs, BOOL, span))

    def cast(self, kind: CastKind, value: Value, target: Type,
             span: Span = INVALID_SPAN) -> Value:
        """Append a conversion."""
        return self._append(CastInst(kind, value, target, span))

    def ret(self, value: Value | None = None, span: Span = INVALID_SPAN) -> Terminator:
        """Append a return."""
        return self._append(RetInst(value, span))  # type: ignore[return-value]

    def br(self, block: BasicBlock, args: tuple[Value, ...] = (),
           span: Span = INVALID_SPAN) -> Terminator:
        """Append an unconditional branch."""
        return self._append(BrInst(BlockTarget(block, args), span))  # type: ignore[return-value]

    def condbr(self, cond: Value, true_block: BasicBlock, false_block: BasicBlock,
               true_args: tuple[Value, ...] = (), false_args: tuple[Value, ...] = (),
               span: Span = INVALID_SPAN) -> Terminator:
        """Append a conditional branch."""
        return self._append(CondBrInst(  # type: ignore[return-value]
            cond, BlockTarget(true_block, true_args), BlockTarget(false_block, false_args),
            span))

    def unreachable(self, span: Span = INVALID_SPAN) -> Terminator:
        """Append an unreachable marker."""
        return self._append(UnreachableInst(span))  # type: ignore[return-value]
