"""How a body is divided into statements, and what a body answers with.

Two rules meet here.  A semicolon *separates* statements and never terminates
one, so what follows a semicolon is another statement, which may be the empty
one.  And the last statement of a body is the body's result, so a body ending in
a semicolon ends in something that produces no value.

Put together, `7u8;` in a function declared to answer with a number is a program
that does not answer -- which is worth being able to write, and worth not
writing by accident.
"""

from __future__ import annotations

import pytest

from conftest import describe, run_compiler

ARROW = "\N{RIGHTWARDS ARROW}"
ASSIGN = "\N{LEFTWARDS ARROW}"


def compile_source(tmp_path, text: str):  # noqa: ANN001, ANN201
    """Compile *text* and return what the compiler said."""
    source = tmp_path / "t.pl4g"
    source.write_text(text, encoding="utf-8")
    return run_compiler(["-o", str(tmp_path / "out"), str(source)])


def raises(proc, number: int) -> bool:  # noqa: ANN001
    """Whether the compiler reported the diagnostic *number*."""
    return "".join(("[PL4G-", str(number), "]")) in proc.stderr


# -- a function that answers with nothing ---------------------------------------

def test_a_header_without_an_arrow_answers_with_nothing(tmp_path) -> None:  # noqa: ANN001
    """And needs no `return`, no last expression, and no complaint."""
    proc = compile_source(tmp_path, "".join((
        "let counter: mut u8 = 0u8\n\n@[constructor, impure]\nfn prepare():\n",
        "    counter ", ASSIGN, " 7u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    counter\n")))
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


def test_writing_the_nothing_out_is_refused(tmp_path) -> None:  # noqa: ANN001
    """One way to say it, and it is to say nothing.  Two spellings of one
    meaning is what the language admits nowhere."""
    proc = compile_source(tmp_path, "".join((
        "@[constructor]\nfn prepare() ", ARROW, " void:\n    0u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    0u8\n")))
    assert raises(proc, 4209), describe(proc)


def test_a_return_in_it_carries_nothing(tmp_path) -> None:  # noqa: ANN001
    """There is nothing for it to carry."""
    proc = compile_source(tmp_path, "".join((
        "@[constructor]\nfn prepare():\n    return 1u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    0u8\n")))
    assert raises(proc, 5004), describe(proc)


def test_the_advice_to_omit_return_is_not_given_where_it_would_not_help(tmp_path) -> None:  # noqa: ANN001
    """A value returned from a function that answers with nothing has something
    else wrong with it; saying the keyword could have been left off would be
    advice that makes it worse, since what would be left is an expression whose
    value goes nowhere."""
    proc = compile_source(tmp_path, "".join((
        "@[constructor]\nfn prepare():\n    return 1u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    0u8\n")))
    assert raises(proc, 5004), describe(proc)
    assert not raises(proc, 5002), describe(proc)


def test_a_bare_return_at_the_end_is_still_redundant(tmp_path) -> None:  # noqa: ANN001
    """The advice does apply where the statement is one the function wanted."""
    proc = compile_source(tmp_path, "".join((
        "let counter: mut u8 = 0u8\n\n@[constructor, impure]\nfn prepare():\n",
        "    counter ", ASSIGN, " 1u8\n    return\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    counter\n")))
    assert raises(proc, 5002), describe(proc)


# -- semicolons -----------------------------------------------------------------

def test_a_semicolon_separates_statements_in_the_layout_notation(tmp_path) -> None:  # noqa: ANN001
    """What a statement is does not depend on which notation it is written in."""
    proc = compile_source(tmp_path, "".join((
        "let a: mut u8 = 0u8\nlet b: mut u8 = 0u8\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    a ", ASSIGN, " 3u8; b ", ASSIGN, " 4u8\n    a + b\n")))
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


def test_a_trailing_semicolon_leaves_no_value(tmp_path) -> None:  # noqa: ANN001
    """It separates and never terminates, so what follows it is a statement --
    the empty one, which produces nothing."""
    proc = compile_source(tmp_path, "".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    @[ignore(5005)]\n    7u8;\n")))
    assert raises(proc, 5003), describe(proc)


def test_a_trailing_semicolon_asks_for_nothing_where_nothing_is_wanted(tmp_path) -> None:  # noqa: ANN001
    """The same semicolon in a function that answers with nothing."""
    proc = compile_source(tmp_path, "".join((
        "let counter: mut u8 = 0u8\n\n@[constructor, impure]\nfn prepare():\n",
        "    counter ", ASSIGN, " 7u8;\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    counter\n")))
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", proc.stderr


@pytest.mark.parametrize(("body", "statements"), (
    ("a", 1), ("a;", 2), ("a; a", 2), ("a;;", 3), ("a; a;", 3),
))
def test_how_many_statements_a_run_of_semicolons_makes(tmp_path, body: str,  # noqa: ANN001
                                                       statements: int) -> None:
    """A semicolon separates, so *n* of them make *n*+1 statements whatever
    stands between them."""
    from pypl4g.diag.engine import collecting_engine
    from pypl4g.front.lexer import tokenize
    from pypl4g.front.parser import parse
    from pypl4g.source.manager import SourceManager

    text = "".join(("@[startup, impure]\nfn main() ", ARROW, " u8:\n    ", body, "\n"))
    sources = SourceManager()
    source = sources.add(tmp_path / "t.pl4g", text)
    engine, collected = collecting_engine(None)
    unit = parse(tokenize(source, engine), "t.pl4g", engine)
    assert [d.info.number for d in collected] == [], collected
    from pypl4g.front import ast

    definition = unit.items[0]
    assert isinstance(definition, ast.FuncDef)
    assert definition.body is not None
    assert len(definition.body.stmts) == statements


def test_the_brace_notation_counts_them_the_same_way(tmp_path) -> None:  # noqa: ANN001
    """The rule is about semicolons, not about which notation they are in."""
    proc = compile_source(tmp_path, "".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8 { 7u8; }\n")))
    assert raises(proc, 5003), describe(proc)
