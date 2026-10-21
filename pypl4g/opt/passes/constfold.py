"""Constant folding.

Replaces an operation on constants by the constant it computes.  The result is
checked against the range of its type rather than wrapped silently, since the
language admits no surprising interpretation of values.

A comparison of constants folds too, and its answer is a truth value rather
than a number, so it needs no range check: there is no truth value that does
not fit in a `bool`.  The logical operators are the bitwise instructions asked
of values that are one or zero, so the same folders answer them.

A check whose condition folded to a truth goes away with it.  That is what makes
a condition written in a signature free where the compiler can see it holds: the
inliner puts the callee where it was called, the arguments become constants
there, and what is left is a check on a constant.  A check that folded to a
falsehood is *not* removed -- the program stops when it runs, which is what it
should do, and a compilation error from here would be a diagnostic the optimizer
has no channel for.
"""

from __future__ import annotations

from typing import Final, Sequence

from ...ir.inst import (AssertInst, BinaryInst, BinOp, CastInst, CastKind,
                        CmpInst, CmpPred, ExtractInst, FailedInst, Instruction,
                        SelectInst, TupleInst, UnwrapInst, WrapInst)
from ...ir.function import Function
from ...ir.module import Module
from ...ir.types import BOOL, CHAR, BoolType, CharType, IntType, PtrType
from ...ir.value import BoolConst, CharConst, IntConst, UndefConst, Value

#: What each comparison asks, as a question about two numbers.  The signed and
#: the unsigned orderings are separate entries because they are separate
#: questions; the values reaching here are already in the range of their type,
#: so asking the question in Python asks the right one.
_COMPARISONS = {
    CmpPred.EQ: lambda a, b: a == b,
    CmpPred.NE: lambda a, b: a != b,
    CmpPred.SLT: lambda a, b: a < b,
    CmpPred.SLE: lambda a, b: a <= b,
    CmpPred.SGT: lambda a, b: a > b,
    CmpPred.SGE: lambda a, b: a >= b,
    CmpPred.ULT: lambda a, b: a < b,
    CmpPred.ULE: lambda a, b: a <= b,
    CmpPred.UGT: lambda a, b: a > b,
    CmpPred.UGE: lambda a, b: a >= b,
}

def _towards_zero(left: int, right: int) -> int:
    """*left* divided by *right*, truncated toward zero."""
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


def _left_over(left: int, right: int) -> int:
    """What is left of *left* after dividing by *right*, with the sign of *left*."""
    return left - _towards_zero(left, right) * right


#: The operations that clamp rather than going past the ends of their type.
_SATURATING = frozenset((BinOp.SAT_ADD, BinOp.SAT_SUB, BinOp.SAT_MUL))

#: The ones that go past it and keep the low bits, which is what the program
#: asked for by writing them inside `⎕wrap`.  An answer outside the type is
#: not a reason to leave the operation standing here either: it is the answer,
#: read as the type reads those bits.
_WRAPPING = frozenset((BinOp.WRAP_ADD, BinOp.WRAP_SUB, BinOp.WRAP_MUL))


def _wrapped(value: int, ty: IntType) -> int:
    """*value* as the low bits of it, read the way its type reads them."""
    found = value & ((1 << ty.bits) - 1)
    if ty.signed and found > ty.high:
        found -= 1 << ty.bits
    return found

#: The ones that have no answer for every pair of operands.
_DIVISIONS = frozenset((BinOp.SDIV, BinOp.UDIV, BinOp.SREM, BinOp.UREM))

_FOLDERS = {
    BinOp.ADD: lambda a, b: a + b,
    BinOp.SUB: lambda a, b: a - b,
    BinOp.MUL: lambda a, b: a * b,
    BinOp.SAT_ADD: lambda a, b: a + b,
    BinOp.SAT_SUB: lambda a, b: a - b,
    BinOp.SAT_MUL: lambda a, b: a * b,
    # The divisions the representation holds are the machine's: a division the
    # program writes answers a result, and what it is lowered to is the check
    # that there is an answer and then one of these.  So one of two constants
    # folds where the divisor is not nought -- left standing below -- and the
    # answer fits, which the smallest signed number over minus one does not.
    BinOp.UDIV: lambda a, b: a // b,
    BinOp.UREM: lambda a, b: a % b,
    BinOp.SDIV: _towards_zero,
    BinOp.SREM: _left_over,
    BinOp.AND: lambda a, b: a & b,
    BinOp.OR: lambda a, b: a | b,
    BinOp.XOR: lambda a, b: a ^ b,
    BinOp.WRAP_ADD: lambda a, b: a + b,
    BinOp.WRAP_SUB: lambda a, b: a - b,
    BinOp.WRAP_MUL: lambda a, b: a * b,
    # The moving ones are not here, wrapping or not: none of the five folds
    # today, and adding the wrapping five alone would make a program mean one
    # thing inside a wrap and another outside it for no reason anyone asked for.
}


class ConstantFolding:
    """Folds arithmetic on integer constants."""

    name = "constfold"

    def run(self, module: Module) -> bool:
        """Fold what can be folded; report whether anything changed."""
        self.touched: list[Function] = []
        for func in module.functions.values():
            folded = False
            while self._sweep(module, func):
                folded = True
            if folded:
                self.touched.append(func)
        return bool(self.touched)

    def _reread(self, module: Module, inst: Instruction) -> Value | None:
        """A constant read as another type, where the operand is one."""
        held = inst.operands[0]
        number = _as_number(held)
        if number is None:
            return None
        ty = inst.ty
        if isinstance(ty, IntType):
            return module.int_const(ty, number)
        if ty is CHAR:
            return module.char_const(number)
        return None

    def _sweep(self, module: Module, func: object) -> bool:
        """Fold once, and say whether anything folded.

        Folding one operation turns the next one's operand into a constant, so
        this runs until nothing more folds: an expression is a tree and one walk
        collapses one level of it.
        """
        replacements: dict[int, Value] = {}
        settled: list[int] = []
        for block in getattr(func, "blocks"):
            for inst in block.insts:
                if isinstance(inst, AssertInst):
                    condition = inst.operands[0]
                    if isinstance(condition, BoolConst) and condition.value:
                        settled.append(id(inst))
                    continue
                folded = self._fold(module, inst)
                if folded is not None:
                    replacements[id(inst)] = folded
        if not replacements and not settled:
            return False
        self._apply(func, replacements, settled)
        return True

    def _fold(self, module: Module, inst: Instruction) -> Value | None:
        """Return the constant *inst* computes, if it computes one."""
        if isinstance(inst, CmpInst):
            return self._fold_comparison(module, inst)
        if isinstance(inst, SelectInst):
            # A choice made by a truth value that is known is the one it makes,
            # and one between a value and itself is that value.
            chooser, first, second = inst.operands
            if isinstance(chooser, BoolConst):
                return first if chooser.value else second
            return first if first is second else None
        if isinstance(inst, CastInst) and inst.kind is CastKind.BITCAST \
                and inst.operands[0].ty is inst.ty:
            # Read as what it already is, which is reading it.
            return inst.operands[0]
        if isinstance(inst, CastInst) and inst.kind is CastKind.BITCAST:
            # The same bits read as something else, which for something written
            # down is the same thing written down.  It matters beyond saving an
            # instruction: a bitcast emits nothing and reads its operand out of a
            # register, and a constant is in no register -- so one folded into this
            # is a value the backend has nowhere to find.
            return self._reread(module, inst)
        if isinstance(inst, ExtractInst):
            # One part of a value put together from its parts is that part: a
            # string rebuilt after a call that answered two words, and taken
            # apart again by whatever reads it, is the two words it was.
            whole = inst.operands[0]
            if isinstance(whole, TupleInst) and inst.index < len(whole.operands):
                part = whole.operands[inst.index]
                # Equal and not necessarily the same object: what a part is
                # read as is worked out from the whole's type, and two ways of
                # working out one type need not meet in one instance of it.
                if part.ty == inst.ty:
                    return part
            return None
        if isinstance(inst, (FailedInst, UnwrapInst)):
            # A result made of its parts answers with them: whether it failed
            # is the truth value it was made with, and its answer the value --
            # which is what a division by a constant leaves, a result that
            # never failed.
            # An answer that is not one -- a result made as an error carries
            # an undefined value there -- is left where it is: there is
            # nothing to read in its place, and a register holding nothing
            # in particular is what the backend reads it from.
            whole = inst.operands[0]
            if isinstance(whole, WrapInst):
                part = whole.operands[1 if isinstance(inst, FailedInst) else 0]
                if part.ty == inst.ty and not isinstance(part, UndefConst):
                    return part
            return None
        if not isinstance(inst, BinaryInst):
            return None
        folder = _FOLDERS.get(inst.op)
        if folder is None:
            return None
        lhs, rhs = inst.operands
        same = _unchanged(inst)
        if same is not None:
            return same
        apart = _apart(inst)
        if apart is not None and isinstance(inst.ty, IntType):
            return module.int_const(inst.ty, _wrapped(apart, inst.ty))
        if inst.op in _DIVISIONS and isinstance(rhs, IntConst) and rhs.value == 0:
            # Nothing to fold and nothing to report from here: the program
            # stops when it runs, and saying so is the checker's business.
            return None
        ty = inst.ty
        if ty is BOOL:
            # The logical operators are these same instructions asked of values
            # that are one or zero, so the same folder answers them -- and the
            # answer is a truth value, which needs no range check.
            numbers = [_as_number(lhs), _as_number(rhs)]
            if any(number is None for number in numbers):
                return None
            return module.bool_const(BOOL, bool(folder(numbers[0], numbers[1])))
        if not isinstance(lhs, IntConst) or not isinstance(rhs, IntConst):
            return None
        if not isinstance(ty, IntType):
            return None
        value = folder(lhs.value, rhs.value)
        if inst.op in _SATURATING:
            # These do not go past the ends of their type, so an answer outside
            # it is the end it went past and not a reason to leave the operation
            # standing.
            value = max(ty.low, min(ty.high, value))
        elif inst.op in _WRAPPING:
            value = _wrapped(value, ty)
        elif not ty.holds(value):
            return None
        return module.int_const(ty, value)

    def _fold_comparison(self, module: Module, inst: CmpInst) -> Value | None:
        """Return the truth value *inst* answers with, if both sides are known.

        Truth values are compared as the numbers they are, one and zero, which
        is what the backends do with them and what makes `true = true` answer
        the same here as it would have at run time.
        """
        lhs, rhs = inst.operands
        if lhs is rhs and isinstance(lhs.ty, (IntType, BoolType, CharType,
                                               PtrType)):
            # A value compared with itself, which is what an array's length
            # checked against the length of the array it picks from comes to
            # when the two are one array.  Not for floating point, where a
            # value that is not a number is not equal to itself.
            return module.bool_const(BOOL, inst.pred in _REFLEXIVE)
        numbers = [_as_number(operand) for operand in inst.operands]
        asking = _COMPARISONS.get(inst.pred)
        if any(number is None for number in numbers):
            # Two places at known distances from one: an element against the
            # first of its array, a copy against what it might have been.
            one, other = _place_and_offset(lhs), _place_and_offset(rhs)
            if asking is None or one is None or other is None \
                    or not _same_place(one[0], other[0]) \
                    or inst.pred not in (CmpPred.EQ, CmpPred.NE):
                return None
            return module.bool_const(BOOL, asking(one[1], other[1]))
        if asking is None:
            return None
        if asking is None:
            return None
        return module.bool_const(BOOL, asking(numbers[0], numbers[1]))

    def _apply(self, func: object, replacements: dict[int, Value],
               settled: Sequence[int] = ()) -> None:
        """Rewrite every use of a folded instruction and drop the instruction.

        A check that folded to a truth is dropped too: it computes nothing, so
        nothing uses it, and what it was there for is known to hold.
        """
        blocks = getattr(func, "blocks")
        # What an instruction folded to may itself have folded, a part of a part:
        # every use is pointed at the end of the chain, nothing in it surviving.
        for key, found in list(replacements.items()):
            while id(found) in replacements:
                found = replacements[id(found)]
            replacements[key] = found
        for block in blocks:
            for inst in block.insts:
                for index, operand in enumerate(inst.operands):
                    found = replacements.get(id(operand))
                    if found is not None:
                        inst.operands[index] = found
                for target in (inst.successors() if hasattr(inst, "successors") else ()):
                    for index, arg in enumerate(target.args):
                        found = replacements.get(id(arg))
                        if found is not None:
                            target.args[index] = found
            gone = set(settled)
            block.insts = [i for i in block.insts
                           if id(i) not in replacements and id(i) not in gone]


#: The comparisons a value passes against itself.
_REFLEXIVE = frozenset((CmpPred.EQ, CmpPred.SLE, CmpPred.SGE, CmpPred.ULE,
                        CmpPred.UGE))

#: What leaves a number as it is, on the right of each operation: adding,
#: taking away, or-ing and exclusive-or-ing nought, and multiplying by one.
#: None of them can go past an end of the type, checked or not, so the checked,
#: the wrapping and the saturating forms are all the operand itself.
_NEUTRAL_RIGHT: Final = {
    BinOp.ADD: 0, BinOp.SUB: 0, BinOp.WRAP_ADD: 0, BinOp.WRAP_SUB: 0,
    BinOp.SAT_ADD: 0, BinOp.SAT_SUB: 0, BinOp.OR: 0, BinOp.XOR: 0,
    BinOp.MUL: 1, BinOp.WRAP_MUL: 1, BinOp.SAT_MUL: 1,
}

#: And on the left, for the ones where the two sides may change places.
_NEUTRAL_LEFT: Final = {
    BinOp.ADD: 0, BinOp.WRAP_ADD: 0, BinOp.SAT_ADD: 0, BinOp.OR: 0,
    BinOp.XOR: 0, BinOp.MUL: 1, BinOp.WRAP_MUL: 1, BinOp.SAT_MUL: 1,
}


def _unchanged(inst: BinaryInst) -> Value | None:
    """The operand *inst* leaves as it is, where the other one is neutral.

    The element of an array of bytes is at its index times one, and that
    multiplication was in every turn of every loop over bytes.  A truth value is
    left alone: the logical operators are these instructions too, and what
    leaves one as it is, is a question about truth values and not about one
    and nought.
    """
    if inst.ty is BOOL:
        return None
    lhs, rhs = inst.operands
    if isinstance(rhs, IntConst) and _NEUTRAL_RIGHT.get(inst.op) == rhs.value \
            and lhs.ty is inst.ty:
        return lhs
    if isinstance(lhs, IntConst) and _NEUTRAL_LEFT.get(inst.op) == lhs.value \
            and rhs.ty is inst.ty:
        return rhs
    if inst.op is BinOp.AND and isinstance(inst.ty, IntType):
        every = -1 if inst.ty.signed else (1 << inst.ty.bits) - 1
        if isinstance(rhs, IntConst) and rhs.value == every and lhs.ty is inst.ty:
            return lhs
        if isinstance(lhs, IntConst) and lhs.value == every and rhs.ty is inst.ty:
            return rhs
    return None


def _place_and_offset(value: Value) -> tuple[Value, int] | None:
    """Where *value* points, as a place and a number of bytes past it, where
    that can be read off what worked it out: bitcasts -- the same bits as
    another pointer, or as a word -- and constants added on."""
    seen = value
    offset = 0
    while True:
        if isinstance(seen, CastInst) and seen.kind is CastKind.BITCAST \
                and isinstance(seen.operands[0].ty, PtrType):
            seen = seen.operands[0]
            continue
        if isinstance(seen, BinaryInst) and seen.op in (BinOp.ADD, BinOp.WRAP_ADD) \
                and isinstance(seen.ty, PtrType) \
                and isinstance(seen.operands[1], IntConst):
            offset += seen.operands[1].value
            seen = seen.operands[0]
            continue
        break
    if not isinstance(seen.ty, PtrType):
        return None
    return seen, offset


def _apart(inst: BinaryInst) -> int | None:
    """How far apart two addresses taken as words are, where both are one
    place and constants added on: the second element's address less the
    first's is the stride, which is how a view is asked whether it is a run."""
    if inst.op not in (BinOp.SUB, BinOp.WRAP_SUB):
        return None
    lhs, rhs = inst.operands
    if not (isinstance(lhs, CastInst) and isinstance(rhs, CastInst)):
        return None
    one, other = _place_and_offset(lhs), _place_and_offset(rhs)
    if one is None or other is None or not _same_place(one[0], other[0]):
        return None
    return one[1] - other[1]


def _same_place(one: Value, other: Value) -> bool:
    """Whether two pointers are one: the same value, or the same part taken out
    of the same value twice -- where the first element of an array is, asked
    once by each of two things that wanted it."""
    if one is other:
        return True
    return isinstance(one, ExtractInst) and isinstance(other, ExtractInst) \
        and one.index == other.index and one.operands[0] is other.operands[0]


def _as_number(value: Value) -> int | None:
    """The number a constant operand stands for, where it is a constant."""
    if isinstance(value, IntConst):
        return value.value
    if isinstance(value, BoolConst):
        return 1 if value.value else 0
    if isinstance(value, CharConst):
        return value.value
    return None
