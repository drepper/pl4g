"""Building IR.

The builder is the only thing that appends to a block, so the invariant that a
block is terminated exactly once has one place to be enforced as code is
generated rather than only when the verifier runs.
"""

from __future__ import annotations

from ..source.location import INVALID_SPAN, Span
from .function import BasicBlock, Function
from typing import Sequence

from .inst import (SyscallInst,
                   AddressInst, AnyLaneInst, AssertInst, CallInst, BinaryInst,
                   CodeInst,
                   BinOp, BlockTarget, BrInst, FrameInst, SplatInst,
                   CastInst, CastKind,
                   CmpInst, CmpPred, CondBrInst, Instruction, LoadInst, MemStartInst,
                   ErrorInst, ExtractInst, FailedInst, RetInst, StoreInst,
                   Terminator,
                   TupleInst, UnaryInst, UnOp,
                   UnreachableInst, UnwrapInst, WrapInst)
from .module import Module
from .types import FloatType, BOOL, IntType, PtrType, Type, U8, VecType
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

    def float_const(self, ty: FloatType, value: float) -> Value:
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
             span: Span = INVALID_SPAN, error: Value | None = None) -> Value:
        """Append the making of a result out of an answer and a truth value.

        *error* is what the error carries where the type says it carries
        something, and nothing where it does not.
        """
        return self._append(WrapInst(value, failed, result_ty, span, error))

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

    def error(self, value: Value, err_ty: Type,
              span: Span = INVALID_SPAN) -> Value:
        """Append the taking of what a result's error carries."""
        return self._append(ErrorInst(value, err_ty, span))

    def unary(self, op: UnOp, value: Value, span: Span = INVALID_SPAN) -> Value:
        """Append a unary operation."""
        return self._append(UnaryInst(op, value, span))

    def compare(self, pred: CmpPred, lhs: Value, rhs: Value,
                span: Span = INVALID_SPAN) -> Value:
        """Append a comparison.

        What it answers with follows what is compared: one truth value for two
        ordinary values, and one per lane for two vectors -- which is what makes
        a comparison written over an array answer an array of truth values with
        nothing else asked.
        """
        answer: Type = BOOL
        if isinstance(lhs.ty, VecType):
            answer = self._module.types.vec_type(BOOL, lhs.ty.lanes)
        return self._append(CmpInst(pred, lhs, rhs, answer, span))

    def splat(self, value: Value, target: Type,
              span: Span = INVALID_SPAN) -> Value:
        """Append the putting of one value in every lane of a vector."""
        return self._append(SplatInst(value, target, span))

    def any_lane(self, value: Value, span: Span = INVALID_SPAN) -> Value:
        """Append the asking of whether any lane of *value* is true."""
        return self._append(AnyLaneInst(value, BOOL, span))

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

        The start of the chain goes at the top of the entry block and not where
        it was first asked for.  It says nothing and depends on nothing -- it is
        where the chain begins, which is where the function begins -- and asking
        for it inside an arm of an `if` would otherwise leave every later use of
        memory reading a value that arm does not reach.
        """
        if self._memory is None:
            start = MemStartInst()
            self._func.entry.insts.insert(0, start)
            self._memory = start  # type: ignore[assignment]
        return self._memory

    def set_memory(self, token: Value) -> None:
        """Make *token* the memory token from here on.

        Two paths that both touch memory reach the place they join with two
        tokens, so whatever built them has to say which one holds there.  It is
        the same question a name assigned on both paths asks, and gets the same
        answer: a parameter of the block they join at.
        """
        self._memory = token

    def frame(self, held: Type, span: Span = INVALID_SPAN) -> Value:
        """Append the taking of storage this function holds for as long as it runs."""
        return self._append(
            FrameInst(held, self._module.types.ptr_type(held, mutable=True), span))

    def check(self, condition: Value, what: str,
              span: Span = INVALID_SPAN) -> Value:
        """Append a check that stops the program where it does not hold."""
        return self._append(AssertInst(condition, what, span))

    def address(self, var: Value, span: Span = INVALID_SPAN) -> Value:
        """Append the taking of a variable's address into a register."""
        assert isinstance(var.ty, PtrType)
        return self._append(AddressInst(var, span))

    def code_address(self, func: object, span: Span = INVALID_SPAN) -> Value:
        """Append the taking of a function's address into a register."""
        return self._append(
            CodeInst(func, self._module.types.ptr_type(U8, mutable=True), span))

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

    def syscall(self, number: Value, args: Sequence[Value], result_ty: Type,
                span: Span = INVALID_SPAN) -> Value:
        """Append a request to the kernel, and take what it answered."""
        return self._append(SyscallInst(number, args, result_ty, span))

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
