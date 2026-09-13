"""Constant folding.

Replaces an operation on constants by the constant it computes.  The result is
checked against the range of its type rather than wrapped silently, since the
language admits no surprising interpretation of values.

A comparison of constants folds too, and its answer is a truth value rather
than a number, so it needs no range check: there is no truth value that does
not fit in a `bool`.  The logical operators are the bitwise instructions asked
of values that are one or zero, so the same folders answer them.
"""

from ...ir.inst import BinaryInst, BinOp, CmpInst, CmpPred, Instruction
from ...ir.module import Module
from ...ir.types import BOOL, IntType
from ...ir.value import BoolConst, IntConst, Value

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

_FOLDERS = {
    BinOp.ADD: lambda a, b: a + b,
    BinOp.SUB: lambda a, b: a - b,
    BinOp.MUL: lambda a, b: a * b,
    BinOp.AND: lambda a, b: a & b,
    BinOp.OR: lambda a, b: a | b,
    BinOp.XOR: lambda a, b: a ^ b,
}


class ConstantFolding:
    """Folds arithmetic on integer constants."""

    name = "constfold"

    def run(self, module: Module) -> bool:
        """Fold what can be folded; report whether anything changed."""
        changed = False
        for func in module.functions.values():
            while self._sweep(module, func):
                changed = True
        return changed

    def _sweep(self, module: Module, func: object) -> bool:
        """Fold once, and say whether anything folded.

        Folding one operation turns the next one's operand into a constant, so
        this runs until nothing more folds: an expression is a tree and one walk
        collapses one level of it.
        """
        replacements: dict[int, Value] = {}
        for block in getattr(func, "blocks"):
            for inst in block.insts:
                folded = self._fold(module, inst)
                if folded is not None:
                    replacements[id(inst)] = folded
        if not replacements:
            return False
        self._apply(func, replacements)
        return True

    def _fold(self, module: Module, inst: Instruction) -> Value | None:
        """Return the constant *inst* computes, if it computes one."""
        if isinstance(inst, CmpInst):
            return self._fold_comparison(module, inst)
        if not isinstance(inst, BinaryInst):
            return None
        folder = _FOLDERS.get(inst.op)
        if folder is None:
            return None
        lhs, rhs = inst.operands
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
        if not ty.holds(value):
            return None
        return module.int_const(ty, value)

    def _fold_comparison(self, module: Module, inst: CmpInst) -> Value | None:
        """Return the truth value *inst* answers with, if both sides are known.

        Truth values are compared as the numbers they are, one and zero, which
        is what the backends do with them and what makes `true = true` answer
        the same here as it would have at run time.
        """
        numbers = [_as_number(operand) for operand in inst.operands]
        if any(number is None for number in numbers):
            return None
        asking = _COMPARISONS.get(inst.pred)
        if asking is None:
            return None
        return module.bool_const(BOOL, asking(numbers[0], numbers[1]))

    def _apply(self, func: object, replacements: dict[int, Value]) -> None:
        """Rewrite every use of a folded instruction and drop the instruction."""
        blocks = getattr(func, "blocks")
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
            block.insts = [i for i in block.insts if id(i) not in replacements]


def _as_number(value: Value) -> int | None:
    """The number a constant operand stands for, where it is a constant."""
    if isinstance(value, IntConst):
        return value.value
    if isinstance(value, BoolConst):
        return 1 if value.value else 0
    return None
