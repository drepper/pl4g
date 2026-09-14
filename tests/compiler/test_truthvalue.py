"""A comparison whose answer is wanted as a value rather than as a place to go.

Folding a comparison into the branch that reads it is what the three
architectures are shaped for, and it is what the compiler did until now.  The
other case -- the answer wanted as a value -- is what every comparison operator
the language is about to gain needs, and it looks different on each target:
x86-64 reads its flags into a byte and widens it, AArch64 reads them into a
word, and RISC-V has no flags and computes the answer outright.

What all three produce is one or zero, and these tests are written against that:
the function returns the truth value, so the exit status of the program *is* the
answer.  Nothing here checks a particular instruction sequence except where the
sequence is the point.
"""

import pytest

from conftest import compiler_targets, describe, run_compiler
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BlockTarget, CmpInst, CmpPred, CondBrInst,
                            RetInst)
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, U8
from pypl4g.ir.verify import verify
from test_branches import build, run


def returns_comparison(triple: str, pred: CmpPred, left: int,
                       right: int) -> Module:
    """A startup function whose result is a comparison of two constants."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), BOOL),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    answer = entry.append(CmpInst(pred, module.int_const(U8, left),
                                  module.int_const(U8, right), BOOL))
    entry.append(RetInst(answer))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


# -- every ordering, on every target --------------------------------------------

#: One case per ordering, once true and once false, since an instruction that
#: answered every question with the same answer would pass half of these.
CASES = [
    (CmpPred.EQ, 7, 7, 1), (CmpPred.EQ, 7, 8, 0),
    (CmpPred.NE, 7, 8, 1), (CmpPred.NE, 7, 7, 0),
    (CmpPred.SLT, 3, 9, 1), (CmpPred.SLT, 9, 3, 0),
    (CmpPred.SLE, 9, 9, 1), (CmpPred.SLE, 9, 3, 0),
    (CmpPred.SGT, 9, 3, 1), (CmpPred.SGT, 3, 9, 0),
    (CmpPred.SGE, 9, 9, 1), (CmpPred.SGE, 3, 9, 0),
    (CmpPred.ULT, 3, 9, 1), (CmpPred.ULT, 9, 3, 0),
    (CmpPred.ULE, 9, 9, 1), (CmpPred.ULE, 9, 3, 0),
    (CmpPred.UGT, 9, 3, 1), (CmpPred.UGT, 3, 9, 0),
    (CmpPred.UGE, 9, 9, 1), (CmpPred.UGE, 3, 9, 0),
]


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("pred", "left", "right", "expected"), CASES,
                         ids=["-".join((c[0].value, str(c[1]), str(c[2])))
                              for c in CASES])
def test_each_ordering_answers_one_or_zero(triple: str, pred: CmpPred, left: int,
                                           right: int, expected: int,
                                           tmp_path) -> None:  # noqa: ANN001
    """The program exits with the answer, so the status is the truth value."""
    path = tmp_path / "out"
    build(returns_comparison(triple, pred, left, right), triple, path)
    assert run(triple, path) == expected


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_answer_is_one_and_not_merely_something(triple: str,
                                                    tmp_path) -> None:  # noqa: ANN001
    """A truth value is one or zero and nothing else.

    An exit status keeps only the low byte, so a comparison answering with the
    flags register, or with all bits set, would show up here and nowhere else.
    """
    path = tmp_path / "out"
    build(returns_comparison(triple, CmpPred.SLT, 1, 2), triple, path)
    assert run(triple, path) == 1


# -- when it is folded, and when it is not --------------------------------------

#: What reading the flags back into a register is called on each target.  RISC-V
#: has no flags, so the comparison itself is what is looked for.
MATERIALIZED = {"x86_64-linux-none": "setl", "aarch64-linux-none": "cset.lt",
                "riscv64-linux-none": "slt"}

#: And what a branch on the same comparison is called.
BRANCHED = {"x86_64-linux-none": "jge", "aarch64-linux-none": "b.ge",
            "riscv64-linux-none": "bge"}


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_comparison_a_branch_absorbs_is_not_also_computed(triple: str,
                                                            tmp_path) -> None:  # noqa: ANN001
    """The case that already worked has to keep working: a comparison read once
    by a branch stays inside the branch, and no register is spent on it."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    yes = func.add_block("yes")
    no = func.add_block("no")
    cond = entry.append(CmpInst(CmpPred.SLT, module.int_const(U8, 1),
                                module.int_const(U8, 2), BOOL))
    entry.append(CondBrInst(cond, BlockTarget(yes), BlockTarget(no)))
    yes.append(RetInst(module.int_const(U8, 1)))
    no.append(RetInst(module.int_const(U8, 2)))
    module.add_function(func)
    module.startup = func
    verify(module)
    mnemonics = build(module, triple, tmp_path / "out")
    assert BRANCHED[triple] in mnemonics, mnemonics
    assert MATERIALIZED[triple] not in mnemonics, mnemonics


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_comparison_read_twice_is_computed_once(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Two branches cannot each absorb the same comparison, so it becomes a
    value, and the branches then test that value rather than comparing again."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    again = func.add_block("again")
    yes = func.add_block("yes")
    no = func.add_block("no")
    cond = entry.append(CmpInst(CmpPred.SLT, module.int_const(U8, 1),
                                module.int_const(U8, 2), BOOL))
    entry.append(CondBrInst(cond, BlockTarget(again), BlockTarget(no)))
    again.append(CondBrInst(cond, BlockTarget(yes), BlockTarget(no)))
    yes.append(RetInst(module.int_const(U8, 4)))
    no.append(RetInst(module.int_const(U8, 5)))
    module.add_function(func)
    module.startup = func
    verify(module)
    path = tmp_path / "out"
    mnemonics = build(module, triple, path)
    assert mnemonics.count(MATERIALIZED[triple]) == 1, mnemonics
    assert run(triple, path) == 4


# -- a truth value that was never compared --------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize("written", (True, False))
def test_a_constant_truth_value_is_returned_like_any_other_number(
        triple: str, written: bool, tmp_path) -> None:  # noqa: ANN001
    """`true` is one and `false` is zero wherever a truth value is wanted, not
    only where a comparison produced it."""
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), BOOL),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    entry.append(RetInst(module.bool_const(BOOL, written)))
    module.add_function(func)
    module.startup = func
    verify(module)
    path = tmp_path / "out"
    build(module, triple, path)
    assert run(triple, path) == (1 if written else 0)


# -- a truth value in memory ----------------------------------------------------

#: How each target reads and writes one byte.  A truth value is a byte, so these
#: are what a program that touches one is made of.
BYTE_WIDE = {"x86_64-linux-none": ("movzx eax", "mov [rip + seen], al"),
             "aarch64-linux-none": ("ldrb", "strb"),
             "riscv64-linux-none": ("lbu", "sb")}

SOURCE = """let ready: bool = true

@[visible]
let seen: mut bool = false

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u8:
    seen \N{LEFTWARDS ARROW} ready
    0u8
"""


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_truth_value_in_memory_is_one_byte(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Reading or writing one any wider would reach into what is laid out
    beside it, which is another variable.

    This was wrong until truth values were given a width of their own: a bool
    was taken for the "anything else" case and read and written eight bytes at
    a time, and the only reason no test caught it is that nothing else had been
    put next to one.
    """
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "t.s"
    proc = run_compiler(["-o", str(output), "--emit=asm",
                         "".join(("--target=", triple)), str(source)])
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    for wanted in BYTE_WIDE[triple]:
        assert wanted in text, "".join((wanted, " is not in\n", text))
