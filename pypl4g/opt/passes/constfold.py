"""Constant folding.

Replaces an operation on constants by the constant it computes.  The result is
checked against the range of its type rather than wrapped silently, since the
language admits no surprising interpretation of values.
"""

from ...ir.inst import BinaryInst, BinOp, Instruction
from ...ir.module import Module
from ...ir.types import IntType
from ...ir.value import IntConst, Value

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
        if not isinstance(inst, BinaryInst):
            return None
        folder = _FOLDERS.get(inst.op)
        if folder is None:
            return None
        lhs, rhs = inst.operands
        if not isinstance(lhs, IntConst) or not isinstance(rhs, IntConst):
            return None
        ty = inst.ty
        if not isinstance(ty, IntType):
            return None
        value = folder(lhs.value, rhs.value)
        if not ty.holds(value):
            return None
        return module.int_const(ty, value)

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
