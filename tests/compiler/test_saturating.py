"""The saturating operations, which answer with the nearest value their type can
hold rather than going past it.

Every case here is compiled and run.  The program computes the operation and
compares the answer with the one written down, and exits with whether they
agree -- so a wrong answer is a failing test and not a difference in a dump.

The table is written out in full rather than generated, because what is being
checked is arithmetic and a generator would have to do the arithmetic to check
it.  The values are the ones where the answer changes: on either side of each
end, and at it.
"""

import pytest

from conftest import compiler_targets
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import BinaryInst, BinOp, CmpInst, CmpPred, RetInst
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, I8, I16, I32, I64, U8, U16, U32, U64
from pypl4g.ir.verify import verify
from test_branches import build, run

ADD, SUB, MUL = BinOp.SAT_ADD, BinOp.SAT_SUB, BinOp.SAT_MUL

#: (operation, type, left, right, answer).  A type's own ends are written as
#: numbers so that the table says what it means rather than referring to itself.
CASES = [
    # -- a byte, unsigned: nothing below zero, nothing above 255 -------------
    (ADD, U8, 100, 100, 200), (ADD, U8, 200, 100, 255), (ADD, U8, 255, 255, 255),
    (SUB, U8, 9, 5, 4), (SUB, U8, 5, 9, 0), (SUB, U8, 0, 255, 0),
    (MUL, U8, 10, 10, 100), (MUL, U8, 20, 20, 255), (MUL, U8, 255, 255, 255),
    (MUL, U8, 0, 255, 0),
    # -- a byte, signed: the two ends are not the same distance from zero ----
    (ADD, I8, 100, 27, 127), (ADD, I8, 100, 100, 127),
    (ADD, I8, -100, -28, -128), (ADD, I8, -100, -100, -128),
    (SUB, I8, -100, 100, -128), (SUB, I8, 100, -100, 127),
    (SUB, I8, -128, -128, 0), (SUB, I8, 0, -128, 127),
    (MUL, I8, 100, 100, 127), (MUL, I8, -100, 100, -128),
    (MUL, I8, -128, -1, 127), (MUL, I8, 11, 11, 121),
    # -- the widths between, where a register is still wider than the type ---
    (ADD, U16, 65535, 1, 65535), (SUB, U16, 0, 1, 0), (MUL, U16, 256, 256, 65535),
    (ADD, I16, 32767, 1, 32767), (ADD, I16, -32768, -1, -32768),
    (MUL, I16, 181, 181, 32761), (MUL, I16, -181, 181, -32761),
    (ADD, U32, 4294967295, 1, 4294967295), (SUB, U32, 0, 1, 0),
    (MUL, U32, 65536, 65536, 4294967295),
    (MUL, U32, 4294967295, 4294967295, 4294967295),
    (ADD, I32, 2147483647, 1, 2147483647), (ADD, I32, -2147483648, -1, -2147483648),
    (MUL, I32, 46341, 46341, 2147483647), (MUL, I32, -46341, 46341, -2147483648),
    (SUB, I32, -2147483648, 1, -2147483648),
    # -- the width of the register, where nothing wider is available ---------
    (ADD, U64, 18446744073709551615, 1, 18446744073709551615),
    (ADD, U64, 1, 2, 3),
    (SUB, U64, 0, 1, 0), (SUB, U64, 7, 3, 4),
    (ADD, I64, 9223372036854775807, 1, 9223372036854775807),
    (ADD, I64, -9223372036854775808, -1, -9223372036854775808),
    (ADD, I64, 2, 3, 5), (ADD, I64, -2, -3, -5),
    (SUB, I64, -9223372036854775808, 1, -9223372036854775808),
    (SUB, I64, 9223372036854775807, -1, 9223372036854775807),
    (SUB, I64, 3, 5, -2),
]


def answers(triple: str, op: BinOp, ty, left: int, right: int,  # noqa: ANN001
            expected: int) -> Module:
    """A program that exits with whether the operation gives *expected*."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), BOOL),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    computed = entry.append(BinaryInst(op, module.int_const(ty, left),
                                       module.int_const(ty, right)))
    entry.append(RetInst(entry.append(
        CmpInst(CmpPred.EQ, computed, module.int_const(ty, expected), BOOL))))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("op", "ty", "left", "right", "expected"), CASES,
                         ids=["".join((c[0].value, ".", c[1].render(), ".",
                                       str(c[2]), ".", str(c[3]))) for c in CASES])
def test_the_answer_is_the_nearest_the_type_can_hold(triple: str, op: BinOp, ty,  # noqa: ANN001
                                                     left: int, right: int,
                                                     expected: int,
                                                     tmp_path) -> None:  # noqa: ANN001
    """Compiled and run, so the answer is the machine's and not a dump's."""
    path = tmp_path / "out"
    build(answers(triple, op, ty, left, right, expected), triple, path)
    assert run(triple, path) == 1, "".join((
        op.value, " of ", str(left), " and ", str(right), " as ", ty.render(),
        " is not ", str(expected)))


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize("ty", (U64, I64), ids=("u64", "i64"))
def test_the_widest_multiplication_is_reported_not_guessed(triple: str, ty,  # noqa: ANN001
                                                           tmp_path) -> None:  # noqa: ANN001
    """Seeing that a product of two whole registers went past the end needs the
    upper half of it, which two of these architectures have as one instruction
    and the third has only in a form with a fixed pair of registers.

    Refused on every target rather than on the one that cannot do it, so that a
    program means the same thing wherever it is compiled.
    """
    from pypl4g.diag.engine import collecting_engine
    from pypl4g.mc.streamer import MCStreamer
    from pypl4g.target.registry import lookup as lookup_target

    module = answers(triple, MUL, ty, 2, 3, 6)
    target = lookup_target(triple)
    assert target is not None
    engine, collected = collecting_engine(None)
    streamer = MCStreamer(encode=target.encode)
    target.generate(module, target.new_assembler(streamer, 0), engine, 0)
    assert 8501 in [d.info.number for d in collected], \
        [d.info.name for d in collected]
