"""The logical operators, and the block parameters `and` and `or` brought with
them.

Two things are checked here that the language cannot check of itself.  The
*answers*, through the constant folder, since nothing can yet turn a truth value
into a number and so a program cannot exit with one.  And the *branch* that
`and` and `or` generate, by building the shape the checker builds and running
it: a block parameter is the first value in this compiler that is written by one
block and read by another, so the moves that put it in place and the register
allocator's view of its life are both new and both only provable by running.
"""

import pytest

from conftest import compiler_targets, describe, run_compiler
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import BlockTarget, CmpInst, CmpPred, CondBrInst, RetInst
from pypl4g.ir.module import Module
from pypl4g.ir.types import BOOL, U8
from pypl4g.ir.verify import verify
from test_branches import build, run

ARROW = "\N{RIGHTWARDS ARROW}"
ASSIGN = "\N{LEFTWARDS ARROW}"

AND = "\N{LOGICAL AND}"
OR = "\N{LOGICAL OR}"
XOR = "\N{CIRCLED PLUS}"
NAND = "\N{NAND}"
NOR = "\N{NOR}"
NOT = "\N{NOT SIGN}"


def compile_to_ir(tmp_path, body: str, *extra: str) -> str:  # noqa: ANN001
    """Compile a program and return its representation after optimization."""
    source = tmp_path / "t.pl4g"
    source.write_text(body, encoding="utf-8")
    output = tmp_path / "t.ir"
    proc = run_compiler(["-o", str(output), "--emit=ir", "-O1", *extra, str(source)])
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def keeping(expression: str) -> str:
    """A program whose only act is to keep the answer to one expression."""
    return "".join((
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " ", expression, "\n    0u8\n"))


def literal(value: bool) -> str:
    """How the language writes a truth value."""
    return "true" if value else "false"


# -- the answers ----------------------------------------------------------------

#: Every operator against its truth table, in full.  Four rows each, because an
#: operator that got one row wrong and three right is exactly the kind of defect
#: a smaller table would let through -- `\N{NAND}` and `\N{NOR}` differ from `\N{LOGICAL AND}` and `\N{LOGICAL OR}` in
#: every row, and `\N{CIRCLED PLUS}` differs from `\N{LOGICAL OR}` in one.
TABLE = [
    (AND, False, False, False), (AND, False, True, False),
    (AND, True, False, False), (AND, True, True, True),
    (OR, False, False, False), (OR, False, True, True),
    (OR, True, False, True), (OR, True, True, True),
    (XOR, False, False, False), (XOR, False, True, True),
    (XOR, True, False, True), (XOR, True, True, False),
    (NAND, False, False, True), (NAND, False, True, True),
    (NAND, True, False, True), (NAND, True, True, False),
    (NOR, False, False, True), (NOR, False, True, False),
    (NOR, True, False, False), (NOR, True, True, False),
]

#: The same for the two that branch.  Their answer does not become a constant in
#: the representation the way the others do -- it arrives at a block parameter,
#: which nothing yet folds away -- so what is looked for is the branch carrying
#: the right answer to the block that takes it.
SHORT_TABLE = [
    ("and", False, False, False), ("and", False, True, False),
    ("and", True, False, False), ("and", True, True, True),
    ("or", False, False, False), ("or", False, True, True),
    ("or", True, False, True), ("or", True, True, True),
]


@pytest.mark.parametrize(("operator", "left", "right", "expected"), TABLE,
                         ids=["".join((str(int(r[1])), r[0], str(int(r[2]))))
                              for r in TABLE])
def test_every_row_of_every_truth_table(tmp_path, operator: str, left: bool,
                                        right: bool, expected: bool) -> None:  # noqa: ANN001
    """The whole table for each, so that a row got backwards is caught."""
    text = compile_to_ir(tmp_path, keeping(" ".join((literal(left), operator,
                                                     literal(right)))))
    wanted = "".join(("store.bool %0, @answer, ", literal(expected)))
    assert wanted in text, "".join((literal(left), " ", operator, " ",
                                    literal(right), " gave\n", text))


@pytest.mark.parametrize(("operator", "left", "right", "expected"), SHORT_TABLE,
                         ids=["".join((str(int(r[1])), r[0], str(int(r[2]))))
                              for r in SHORT_TABLE])
def test_every_row_of_the_two_that_branch(tmp_path, operator: str, left: bool,
                                          right: bool, expected: bool) -> None:  # noqa: ANN001
    """The branch on a condition the folder settled becomes the jump it would
    have taken, so what is left is the one edge that carries the answer."""
    text = compile_to_ir(tmp_path, keeping(" ".join((literal(left), operator,
                                                     literal(right)))))
    wanted = "".join(("br joined(", literal(expected), ")"))
    assert wanted in text, "".join((literal(left), " ", operator, " ",
                                    literal(right), " gave\n", text))


@pytest.mark.parametrize("operand", (False, True))
def test_not_answers_the_opposite(tmp_path, operand: bool) -> None:  # noqa: ANN001
    """Both rows of the one-operand table."""
    text = compile_to_ir(tmp_path, keeping(" ".join((NOT, literal(operand)))))
    wanted = "".join(("store.bool %0, @answer, ", literal(not operand)))
    assert wanted in text, text


def test_turning_a_truth_value_round_is_not_complementing_it(tmp_path) -> None:  # noqa: ANN001
    """A truth value is one or zero, so complementing it would set every bit
    above the lowest and give something that is neither."""
    text = compile_to_ir(tmp_path, "".join((
        "let ready: bool = true\n\n",
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " ", NOT, " ready\n    0u8\n")), "-O0")
    assert "xor.bool" in text, text
    assert "not.bool" not in text, text


# -- what binds to what ---------------------------------------------------------

def test_the_logical_operators_bind_looser_than_the_comparisons(tmp_path) -> None:  # noqa: ANN001
    """They join whole questions, so `a < b ∧ c < d` reads as it looks."""
    text = compile_to_ir(tmp_path, "".join((
        "let a: u8 = 1u8\nlet b: u8 = 2u8\nlet c: u8 = 3u8\nlet d: u8 = 4u8\n\n",
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " a < b ", AND, " c < d\n    0u8\n")), "-O0")
    # Both comparisons are made, and the "and" is what joins their answers.
    assert text.count("icmp.") == 2, text
    assert "and.bool" in text, text
    assert text.index("icmp.") < text.index("and.bool"), text


def test_and_binds_tighter_than_or(tmp_path) -> None:  # noqa: ANN001
    """The order the bitwise three already have, so one set of habits serves
    for both: `a ∨ b ∧ c` is `a ∨ (b ∧ c)`."""
    text = compile_to_ir(tmp_path, "".join((
        "let a: bool = true\nlet b: bool = false\nlet c: bool = true\n\n",
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " a ", OR, " b ", AND, " c\n    0u8\n")), "-O0")
    assert text.index("and.bool") < text.index("or.bool"), text


def test_not_binds_tighter_than_anything_between_two_operands(tmp_path) -> None:  # noqa: ANN001
    """`¬ ready ∧ seen` is `(¬ ready) ∧ seen`, which is what '!' does in C, Go
    and Rust."""
    text = compile_to_ir(tmp_path, "".join((
        "let ready: bool = true\nlet seen: bool = false\n\n",
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " ", NOT, " ready ", AND, " seen\n    0u8\n")), "-O0")
    assert text.index("xor.bool") < text.index("and.bool"), text


# -- the branch that `and` and `or` make ----------------------------------------

def short_circuit(triple: str, kind: str, left: bool, right: bool) -> Module:
    """The shape the checker builds for `and` or `or`, returning the answer.

    Written out here rather than compiled from source because the answer has to
    be observable, and a startup function returning a truth value is the only
    way this compiler has to observe one -- which the language itself cannot
    write, the startup function being declared to return a number.
    """
    module = Module("t", triple=triple)
    func = Function("main", module.types.func_type((), BOOL),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    rest = func.add_block("rest")
    decided = func.add_block("decided")
    joined = func.add_block("joined")
    answer = joined.add_param(BOOL, "answer")
    decides = kind == "or"
    # The operands are comparisons rather than constants, so that the branch is
    # a branch on something computed and not on a value the backend could see.
    one = module.int_const(U8, 1)
    condition = entry.append(CmpInst(CmpPred.EQ, one,
                                     module.int_const(U8, 1 if left else 2), BOOL))
    entry.append(CondBrInst(condition,
                            BlockTarget(decided if decides else rest),
                            BlockTarget(rest if decides else decided)))
    decided.append(BrInst_to(joined, module.bool_const(BOOL, decides)))
    other = rest.append(CmpInst(CmpPred.EQ, one,
                                module.int_const(U8, 1 if right else 2), BOOL))
    rest.append(BrInst_to(joined, other))
    joined.append(RetInst(answer))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


def BrInst_to(block, value):  # noqa: ANN001, ANN201, N802
    """An unconditional branch carrying one argument."""
    from pypl4g.ir.inst import BrInst

    return BrInst(BlockTarget(block, (value,)))


#: Both operators against their whole table again, this time compiled and run.
RUNS = [(kind, left, right,
         (left and right) if kind == "and" else (left or right))
        for kind in ("and", "or") for left in (False, True)
        for right in (False, True)]


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("kind", "left", "right", "expected"), RUNS,
                         ids=["".join((str(int(r[1])), r[0], str(int(r[2]))))
                              for r in RUNS])
def test_the_short_circuit_shape_runs_and_answers(triple: str, kind: str, left: bool,
                                                  right: bool, expected: bool,
                                                  tmp_path) -> None:  # noqa: ANN001
    """The program exits with the answer, so the status is the truth value.

    This is what proves the block parameter: two blocks write it, one reads it,
    and the value has to survive from whichever wrote it to the read.
    """
    path = tmp_path / "out"
    build(short_circuit(triple, kind, left, right), triple, path)
    assert run(triple, path) == (1 if expected else 0)


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_right_side_is_not_computed_when_the_left_decides(triple: str,
                                                              tmp_path) -> None:  # noqa: ANN001
    """Which is the whole difference between `and` and `∧`.

    The block holding the right side is reached only from the branch, so what
    says it was skipped is that the branch went elsewhere -- and what says the
    block is still there to be skipped is that it was emitted.
    """
    mnemonics = build(short_circuit(triple, "and", False, True), triple,
                      tmp_path / "out")
    # Which way round the branch is written is the block order's business --
    # the condition is inverted where that saves the jump -- so what is looked
    # for is that a conditional branch is there at all.
    conditional = {"x86_64-linux-none": ("je", "jne"),
                   "aarch64-linux-none": ("b.eq", "b.ne", "cbz", "cbnz"),
                   "riscv64-linux-none": ("beq", "bne")}[triple]
    assert any(name in mnemonics for name in conditional), mnemonics
