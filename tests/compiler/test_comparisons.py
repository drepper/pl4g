"""The comparison operators: what they parse as, what they type as, and what
answer they give.

The answers are checked through the constant folder, which is the one place a
comparison's answer is visible to a test from the language: nothing can yet turn
a truth value back into a number, so a program cannot exit with one.  That the
*generated code* answers the same way is checked in test_truthvalue.py, which
builds the representation directly and runs the result on all three targets.
Between them the two cover the question end to end.
"""

from __future__ import annotations

import pytest

from conftest import describe, run_compiler

ARROW = "\N{RIGHTWARDS ARROW}"
ASSIGN = "\N{LEFTWARDS ARROW}"

#: Written with the glyphs, since that is the form the language is written in;
#: the two substitutes have a test of their own.
OPERATORS = ("=", "\N{NOT EQUAL TO}", "<", ">",
             "\N{LESS-THAN OR EQUAL TO}", "\N{GREATER-THAN OR EQUAL TO}")


def compile_to_ir(tmp_path, body: str, *extra: str) -> str:  # noqa: ANN001
    """Compile a program and return its representation after optimization."""
    source = tmp_path / "t.pl4g"
    source.write_text(body, encoding="utf-8")
    output = tmp_path / "t.ir"
    proc = run_compiler(["-o", str(output), "--emit=ir", "-O1", *extra, str(source)])
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def keeping(answer: str, left: str, operator: str, right: str) -> str:
    """A program whose only act is to keep the answer to one comparison."""
    return "".join((
        "@[visible]\nlet answer: mut bool = ", answer, "\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " ", left, " ", operator, " ", right, "\n",
        "    0u8\n"))


# -- the answers ----------------------------------------------------------------

#: Every operator, with a pair that makes it true and a pair that makes it
#: false.  A comparison that answered the same way whatever it was given would
#: pass half of these and no more.
ANSWERS = [
    ("=", "7u8", "7u8", True), ("=", "7u8", "8u8", False),
    ("\N{NOT EQUAL TO}", "7u8", "8u8", True),
    ("\N{NOT EQUAL TO}", "7u8", "7u8", False),
    ("<", "3u8", "9u8", True), ("<", "9u8", "3u8", False), ("<", "9u8", "9u8", False),
    (">", "9u8", "3u8", True), (">", "3u8", "9u8", False), (">", "9u8", "9u8", False),
    ("\N{LESS-THAN OR EQUAL TO}", "9u8", "9u8", True),
    ("\N{LESS-THAN OR EQUAL TO}", "9u8", "3u8", False),
    ("\N{GREATER-THAN OR EQUAL TO}", "9u8", "9u8", True),
    ("\N{GREATER-THAN OR EQUAL TO}", "3u8", "9u8", False),
]


@pytest.mark.parametrize(("operator", "left", "right", "expected"), ANSWERS,
                         ids=["".join((c[1], c[0], c[2])) for c in ANSWERS])
def test_each_comparison_of_constants_folds_to_its_answer(
        tmp_path, operator: str, left: str, right: str, expected: bool) -> None:  # noqa: ANN001
    """The folder computes what the machine would have computed."""
    text = compile_to_ir(tmp_path, keeping("false", left, operator, right))
    wanted = "".join(("store.bool %0, @answer, ", "true" if expected else "false"))
    assert wanted in text, "".join((left, " ", operator, " ", right, " gave\n", text))


def test_the_two_signed_orderings_are_not_the_unsigned_ones(tmp_path) -> None:  # noqa: ANN001
    """The type of what is compared says which question was asked.  As numbers
    -1 is below 1; as the bits of a byte it is above."""
    signed = compile_to_ir(tmp_path, keeping("false", "\N{SUPERSCRIPT MINUS}1i8",
                                             "<", "1i8"))
    assert "store.bool %0, @answer, true" in signed, signed
    unsigned = compile_to_ir(tmp_path, keeping("false", "255u8", "<", "1u8"))
    assert "store.bool %0, @answer, false" in unsigned, unsigned


#: Which predicate the representation should name, for a signed and for an
#: unsigned operand type.  Equality asks the same question either way.
PREDICATES = [
    ("=", "eq", "eq"), ("\N{NOT EQUAL TO}", "ne", "ne"),
    ("<", "slt", "ult"), (">", "sgt", "ugt"),
    ("\N{LESS-THAN OR EQUAL TO}", "sle", "ule"),
    ("\N{GREATER-THAN OR EQUAL TO}", "sge", "uge"),
]


@pytest.mark.parametrize(("operator", "signed", "unsigned"), PREDICATES,
                         ids=[p[1] for p in PREDICATES])
def test_the_predicate_follows_the_type_of_the_operands(
        tmp_path, operator: str, signed: str, unsigned: str) -> None:  # noqa: ANN001
    """One operator, two questions, chosen by what is being compared."""
    for ty, wanted in (("i8", signed), ("u8", unsigned)):
        text = compile_to_ir(tmp_path, "".join((
            "let a: ", ty, " = 1", ty, "\nlet b: ", ty, " = 2", ty, "\n\n",
            "@[visible]\nlet answer: mut bool = false\n\n",
            "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
            "    answer ", ASSIGN, " a ", operator, " b\n    0u8\n")))
        assert "".join(("icmp.", wanted, ".", ty)) in text, text


# -- what they bind to ----------------------------------------------------------

def test_a_comparison_binds_looser_than_the_bitwise_operators(tmp_path) -> None:  # noqa: ANN001
    """`flags & mask = mask` asks about `flags & mask`.  C binds it the other
    way round, which is the mistake every language since has declined to make."""
    text = compile_to_ir(tmp_path, "".join((
        "let flags: u8 = 170u8\nlet mask: u8 = 10u8\n\n",
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " flags & mask = mask\n    0u8\n")), "-O0")
    # The "and" is computed, and the comparison reads what it computed.
    assert "and.u8" in text, text
    position_of_and = text.index("and.u8")
    position_of_cmp = text.index("icmp.eq")
    assert position_of_and < position_of_cmp, text


# -- what they refuse -----------------------------------------------------------

def refuses(tmp_path, body: str, number: int) -> None:  # noqa: ANN001
    """Compile *body* and require that it is refused with diagnostic *number*."""
    source = tmp_path / "t.pl4g"
    source.write_text(body, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(source)])
    assert proc.returncode != 0, describe(proc)
    assert "".join(("[PL4G-", str(number), "]")) in proc.stderr, describe(proc)


@pytest.mark.parametrize("operator", OPERATORS)
def test_no_comparison_chains(tmp_path, operator: str) -> None:  # noqa: ANN001
    """One level for all six, and no associativity within it, so every pairing
    of two of them is refused and not only the ones that look odd."""
    refuses(tmp_path, "".join((
        "let a: u8 = 1u8\nlet b: u8 = 2u8\nlet c: u8 = 3u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " bool:\n",
        "    a ", operator, " b ", operator, " c\n")), 3014)


def test_two_different_comparisons_do_not_chain_either(tmp_path) -> None:  # noqa: ANN001
    """They share one level, so `a < b = c` is the same question as `a < b < c`
    and gets the same answer."""
    refuses(tmp_path, "".join((
        "let a: u8 = 1u8\nlet b: u8 = 2u8\nlet c: u8 = 3u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " bool:\n    a < b = c\n")), 3014)


def test_parenthesizing_a_chain_says_what_was_meant(tmp_path) -> None:  # noqa: ANN001
    """The refusal is of the writing, not of the meaning: `(a < b) = ready`
    compares a truth value with a truth value and is perfectly well formed.

    Which is also what makes refusing the unparenthesized form the right call
    rather than a limitation -- there is a way to say it, and it says it.
    """
    text = compile_to_ir(tmp_path, "".join((
        "let a: u8 = 1u8\nlet b: u8 = 2u8\nlet ready: bool = true\n\n",
        "@[visible]\nlet answer: mut bool = false\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    answer ", ASSIGN, " (a < b) = ready\n    0u8\n")), "-O0")
    # The inner comparison answers with a truth value, and the outer one
    # compares that answer -- which is the thing the chain would have meant.
    assert "icmp.ult.u8" in text, text
    assert "icmp.eq.bool" in text, text
