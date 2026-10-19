"""Which divisions of two constants are folded.

A division the program writes answers a result and is lowered to the check that
there is an answer and then a division of the machine's, which is what is folded
here: where the divisor is not nought and the answer fits its type.  What would
stop the program while running is left for it to stop.
"""

from __future__ import annotations

from pypl4g.ir.builder import IRBuilder
from pypl4g.ir.function import Function, FuncAttrs, SpecialKind
from pypl4g.ir.inst import BinOp, BinaryInst
from pypl4g.ir.module import Module
from pypl4g.ir.types import I8, U64
from pypl4g.ir.value import IntConst
from pypl4g.opt.passes.constfold import ConstantFolding


def _folded(op: BinOp, ty, lhs: int, rhs: int):  # noqa: ANN001, ANN202
    """What one division of two constants of *ty* is after folding."""
    module = Module("t")
    func = Function("main", module.types.func_type((), ty),
                    FuncAttrs(special=SpecialKind.STARTUP))
    module.add_function(func)
    module.startup = func
    builder = IRBuilder(module, func)
    builder.position_at(func.add_block())
    quotient = builder.binary(op, builder.int_const(ty, lhs), builder.int_const(ty, rhs))
    ret = builder.ret(quotient)
    ConstantFolding().run(module)
    return ret.operands[0]


def test_an_unsigned_division_folds() -> None:
    """Four over two is two, and what is left of five over two is one."""
    assert getattr(_folded(BinOp.UDIV, U64, 4, 2), "value", None) == 2
    assert getattr(_folded(BinOp.UREM, U64, 5, 2), "value", None) == 1


def test_a_signed_division_truncates_toward_nought() -> None:
    """As the language divides: minus seven over two is minus three, and what
    is left has the sign of what was divided."""
    assert getattr(_folded(BinOp.SDIV, I8, -7, 2), "value", None) == -3
    assert getattr(_folded(BinOp.SREM, I8, -7, 2), "value", None) == -1


def test_a_division_by_nought_is_left() -> None:
    """Nothing to fold it to: the program stops when it runs."""
    assert isinstance(_folded(BinOp.UDIV, U64, 4, 0), BinaryInst)


def test_an_answer_that_does_not_fit_is_left() -> None:
    """The smallest eight-bit number over minus one has no eight-bit answer."""
    found = _folded(BinOp.SDIV, I8, -128, -1)
    assert not isinstance(found, IntConst)
