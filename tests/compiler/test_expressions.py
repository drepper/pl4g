"""Expressions: how they parse, what they mean, and what they compile to."""

from __future__ import annotations

import subprocess
from subprocess import CompletedProcess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for
from pypl4g.diag.engine import collecting_engine
from pypl4g.front import ast
from pypl4g.front.lexer import tokenize
from pypl4g.front.parser import parse
from pypl4g.source.manager import SourceManager
from pathlib import Path

ARROW = "\N{RIGHTWARDS ARROW}"


def expression(text: str) -> ast.Expr:
    """Parse *text* as the body of a startup function and return the expression."""
    source = "".join(("@[startup, impure]\nfn main() ", ARROW, " u6:\n    ", text, "\n"))
    sources = SourceManager()
    unit_source = sources.add(Path("t.pl4g"), source)
    engine, collected = collecting_engine(None)
    unit = parse(tokenize(unit_source, engine), "t.pl4g", engine)
    assert [d.info.number for d in collected] == [], \
        [d.info.name for d in collected]
    func = unit.items[0]
    assert isinstance(func, ast.FuncDef)
    assert func.body is not None
    statement = func.body.stmts[0]
    assert isinstance(statement, ast.ExprStmt)
    return statement.value


def shape(node: ast.Expr) -> str:
    """The tree as a parenthesized string, so a test can state it in one line."""
    match node:
        case ast.Binary():
            return "".join(("(", shape(node.left), " ", node.op.value, " ",
                            shape(node.right), ")"))
        case ast.Unary():
            return "".join(("(", node.op.value, shape(node.operand), ")"))
        case ast.IntLit():
            return str(node.value)
        case ast.NameRef():
            return node.name
        case ast.BoolLit():
            return "true" if node.value else "false"
        case _:
            return "?"


# -- how they parse -------------------------------------------------------------

@pytest.mark.parametrize(("written", "tree"), [
    ("1u6", "1"),
    ("a", "a"),
    ("~a", "(~a)"),
    ("~~a", "(~(~a))"),
    ("a & b", "(a & b)"),
    # Left-associative, so the left pair binds first.
    ("a & b & c", "((a & b) & c)"),
    ("a | b | c", "((a | b) | c)"),
    # "and" binds tighter than "exclusive or", which binds tighter than "or".
    ("a | b & c", "(a | (b & c))"),
    ("a & b | c", "((a & b) | c)"),
    ("a | b ^ c & d", "(a | (b ^ (c & d)))"),
    ("a ^ b | c ^ d", "((a ^ b) | (c ^ d))"),
    # The operator before an operand binds tighter than any between two.
    ("~a & b", "((~a) & b)"),
    ("~(a & b)", "(~(a & b))"),
    # Parentheses say how the operators group and nothing else.
    ("(a | b) & c", "((a | b) & c)"),
    ("(((a)))", "a"),
])
def test_the_tree_is_the_one_the_precedence_says(written: str, tree: str) -> None:
    """One table decides all of this; these are the readings it produces."""
    assert shape(expression(written)) == tree


def test_a_group_may_hold_a_whole_expression() -> None:
    """Nothing inside a group is restricted; it is an expression like any other."""
    assert shape(expression("(~a | b) ^ (c & ~d)")) == "(((~a) | b) ^ (c & (~d)))"


# -- what they compile to -------------------------------------------------------

def compile_expression(text: str, *extra: str,
                       tmp_path) -> tuple[CompletedProcess[str], str]:  # noqa: ANN001
    """Compile a startup function whose result is *text*, returning the process
    result and the intermediate representation."""
    source = tmp_path / "t.pl4g"
    source.write_text("".join((
        "let a: u6 = 0b11_0011u6\nlet b: u6 = 0b01_0101u6\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u6:\n    ", text, "\n")), encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "--emit=ir", *extra, str(source)])
    return proc, output.read_text(encoding="utf-8") if proc.returncode == 0 else ""


@pytest.mark.parametrize(("written", "opcode"), [
    ("a & b", "and.u6"), ("a | b", "or.u6"), ("a ^ b", "xor.u6"), ("~a", "not.u6"),
])
def test_each_operator_becomes_its_operation(written: str, opcode: str,
                                             tmp_path) -> None:  # noqa: ANN001
    """The source names an operator and the representation names an operation."""
    proc, text = compile_expression(written, tmp_path=tmp_path)
    assert proc.returncode == 0, describe(proc)
    assert opcode in text, text


def test_a_constant_expression_folds_to_a_constant(tmp_path) -> None:  # noqa: ANN001
    """Folding one operation turns the next one's operand into a constant, so
    folding runs until nothing more folds -- an expression is a tree."""
    proc, text = compile_expression("1u6 | 6u6 ^ 3u6 & 2u6", "-O1", tmp_path=tmp_path)
    assert proc.returncode == 0, describe(proc)
    assert "ret.u6 5" in text, text
    assert "and" not in text and "xor" not in text and "or" not in text, text


def test_a_literal_takes_its_type_from_the_other_side(tmp_path) -> None:  # noqa: ANN001
    """Both sides have one type, so whichever says what it is says it for both.

    A rule that only looked leftwards would accept one of these and refuse the
    other, which would be a difference with nothing behind it.
    """
    for written in ("a & 3", "3 & a"):
        proc, text = compile_expression(written, tmp_path=tmp_path)
        assert proc.returncode == 0, "".join((written, ": ", describe(proc)))
        assert "and.u6" in text, text


# -- what they refuse -----------------------------------------------------------

@pytest.mark.parametrize(("written", "number"), [
    ("~true", "4205"),
    ("a & true", "4205"),
    ("true & a", "4205"),
    ("a & 2u16", "4206"),
])
def test_a_wrong_operand_is_reported_once(written: str, number: str,
                                          tmp_path) -> None:  # noqa: ANN001
    """One mistake, one message: a value whose type was already reported is
    poison, so whatever reads it says nothing further."""
    proc, _ = compile_expression(written, tmp_path=tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "".join(("[PL4G-", number, "]")) in proc.stderr, proc.stderr
    assert proc.stderr.count("[PL4G-") == 1, proc.stderr


def test_a_group_that_is_never_closed_is_reported(tmp_path) -> None:  # noqa: ANN001
    """The function is then discarded, so a second message about the program
    missing its startup function follows -- that is recovery, not a cascade."""
    proc, _ = compile_expression("(a & b", tmp_path=tmp_path)
    assert proc.returncode != 0, proc.stdout
    assert "[PL4G-3009]" in proc.stderr, proc.stderr


# -- and that they run ----------------------------------------------------------

#: What each expression comes to, worked out by hand from a = 0b110011 and
#: b = 0b010101, which are `u6` values because the status a program exits
#: with is one.  A complement is of six bits and not of eight, which is what
#: the three answers with a leading one are about.  They are checked against a
#: running program, not against the compiler's own opinion of them.
RESULTS = [
    ("a & b", 0b010001),
    ("a | b", 0b110111),
    ("a ^ b", 0b100110),
    ("~a", 0b001100),
    ("(a & b) | (~a ^ b)", 0b011001),
    ("a & b | ~a ^ b", 0b011001),
    ("~(a & b)", 0b101110),
]


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("written", "expected"), RESULTS,
                         ids=[r[0].replace(" ", "") for r in RESULTS])
def test_the_program_computes_it(triple: str, written: str, expected: int,
                                 tmp_path) -> None:  # noqa: ANN001
    """On every target, with the answer worked out independently."""
    source = tmp_path / "t.pl4g"
    source.write_text("".join((
        "let a: u6 = 0b11_0011u6\nlet b: u6 = 0b01_0101u6\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u6:\n    ", written, "\n")),
        encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(output)], capture_output=True,
                         timeout=60)
    assert ran.returncode == expected, describe(ran)
