"""Building IR.

The builder is the only thing that appends to a block, so the invariant that a
block is terminated exactly once has one place to be enforced as code is
generated rather than only when the verifier runs.
"""

from ..source.location import INVALID_SPAN, Span
from .function import BasicBlock, Function
from typing import Sequence

from .inst import (AddressInst, CallInst, BinaryInst, BinOp, BlockTarget, BrInst,
                   CastInst, CastKind,
                   CmpInst, CmpPred, CondBrInst, Instruction, LoadInst, MemStartInst,
                   ExtractInst, FailedInst, RetInst, StoreInst, Terminator,
                   TupleInst, UnaryInst, UnOp,
                   UnreachableInst, UnwrapInst, WrapInst)
from .module import Module
from .types import FloatType, BOOL, IntType, PtrType, Type
from .value import Value


class IRBuilder:
    """Appends instructions to a block of a function."""

    def __init__(self, module: Module, func: Function) -> None:
        self._module = module
        self._func = func
        self._block: BasicBlock | None = func.entry
        self._memory: Value | None = None

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

    def float_const(self, ty: "FloatType", value: float) -> Value:
        """A floating-point constant of type *ty*."""
        return self._module.float_const(ty, value)

    def bool_const(self, value: bool) -> Value:
        """A boolean constant."""
        return self._module.bool_const(BOOL, value)

    # -- instructions ----------------------------------------------------------

    def binary(self, op: BinOp, lhs: Value, rhs: Value, span: Span = INVALID_SPAN,
               ty: Type | None = None) -> Value:
        """Append a binary operation."""
        return self._append(BinaryInst(op, lhs, rhs, span, ty))

    def wrap(self, value: Value, failed: Value, result_ty: Type,
             span: Span = INVALID_SPAN) -> Value:
        """Append the making of a result out of an answer and a truth value."""
        return self._append(WrapInst(value, failed, result_ty, span))

    def make_tuple(self, values: Sequence[Value], ty: Type,
                   span: Span = INVALID_SPAN) -> Value:
        """Append the making of a tuple out of several values."""
        return self._append(TupleInst(values, ty, span))

    def extract(self, value: Value, index: int, ty: Type,
                span: Span = INVALID_SPAN) -> Value:
        """Append the taking of one value out of a tuple."""
        return self._append(ExtractInst(value, index, ty, span))

    def unwrap(self, value: Value, ok_ty: Type, span: Span = INVALID_SPAN) -> Value:
        """Append the reading of a result's answer."""
        return self._append(UnwrapInst(value, ok_ty, span))

    def failed(self, value: Value, span: Span = INVALID_SPAN) -> Value:
        """Append the asking of whether a result is the error."""
        return self._append(FailedInst(value, BOOL, span))

    def unary(self, op: UnOp, value: Value, span: Span = INVALID_SPAN) -> Value:
        """Append a unary operation."""
        return self._append(UnaryInst(op, value, span))

    def compare(self, pred: CmpPred, lhs: Value, rhs: Value,
                span: Span = INVALID_SPAN) -> Value:
        """Append a comparison."""
        return self._append(CmpInst(pred, lhs, rhs, BOOL, span))

    def call(self, callee: object, args: Sequence[Value], result_ty: Type,
             span: Span = INVALID_SPAN) -> Value:
        """Append a call to *callee*."""
        return self._append(CallInst(callee, args, result_ty, span))

    def cast(self, kind: CastKind, value: Value, target: Type,
             span: Span = INVALID_SPAN) -> Value:
        """Append a conversion."""
        return self._append(CastInst(kind, value, target, span))

    def memory(self) -> Value:
        """The memory token at this point of the function.

        A function that never touches memory has none; the first load is what
        starts the chain, and a store will later extend it.
        """
        if self._memory is None:
            self._memory = self._append(MemStartInst())
        return self._memory

    def set_memory(self, token: Value) -> None:
        """Make *token* the memory token from here on.

        Two paths that both touch memory reach the place they join with two
        tokens, so whatever built them has to say which one holds there.  It is
        the same question a name assigned on both paths asks, and gets the same
        answer: a parameter of the block they join at.
        """
        self._memory = token

    def address(self, var: Value, span: Span = INVALID_SPAN) -> Value:
        """Append the taking of a variable's address into a register."""
        assert isinstance(var.ty, PtrType)
        return self._append(AddressInst(var, span))

    def load(self, address: Value, span: Span = INVALID_SPAN) -> Value:
        """Append a load of whatever *address* points at."""
        pointee = address.ty
        assert isinstance(pointee, PtrType)
        return self._append(LoadInst(pointee.pointee, (self.memory(), address), span))

    def store(self, address: Value, value: Value,
              span: Span = INVALID_SPAN) -> Value:
        """Append a store of *value* through *address*, and take the new token."""
        written = self._append(StoreInst(self.memory(), address, value, span))
        self._memory = written
        return written

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
