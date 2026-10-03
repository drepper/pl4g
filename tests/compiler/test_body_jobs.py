"""A function's body is a job of its own, parsed when something asks for it.

The parser reads every signature and sets each body aside with where it begins and
ends; a body is parsed by a parser of its own over its own tokens, sharing nothing
with any other.  The program's files have every body parsed straight away, as a
batch that may run on several threads with what each says said afterwards in the
order of the source; a module has a body parsed only when something reads it.
"""

from __future__ import annotations

from pathlib import Path

from pypl4g.diag.engine import collecting_engine
from pypl4g.front import ast
from pypl4g.front.lexer import tokenize
from pypl4g.front.parser import parse
from pypl4g.source.manager import SourceManager

SOURCE = """\
fn first() \N{RIGHTWARDS ARROW} u8:
    let a: u8 = 1u8 +
    a

fn second() \N{RIGHTWARDS ARROW} u8 { 2u8 }

fn third(x: u8) \N{RIGHTWARDS ARROW} u8 pre(x > 0u8):
    x
"""

GOOD = """\
fn one() \N{RIGHTWARDS ARROW} u8:
    1u8

fn two(x: u8) \N{RIGHTWARDS ARROW} u8 pre(x > 0u8) { x }
"""


def _parsed(text: str, defer: bool, workers: int = 1):  # noqa: ANN202
    sources = SourceManager()
    engine, said = collecting_engine(None)
    unit = parse(tokenize(sources.add(Path("t.pl4g"), text), engine), "t.pl4g",
                 engine, defer=defer, workers=workers)
    return unit, said


def test_a_deferred_body_is_parsed_when_read() -> None:
    """Nothing about a body is known until it is read; the signature and its
    clauses are there from the start."""
    unit, said = _parsed(GOOD, defer=True)
    functions = [one for one in unit.items if isinstance(one, ast.FuncDef)]
    assert all(isinstance(one.body, ast.DeferredBlock) and not one.body.parsed
               for one in functions)
    assert functions[1].clauses, "the pre-condition is part of the signature"
    assert said == []
    assert len(functions[0].body.stmts) == 1
    assert functions[0].body.parsed and not functions[1].body.parsed


def test_threads_say_what_one_thread_says_in_the_same_order() -> None:
    """The batch on four threads says what it says on one, in the same order."""
    one, said_one = _parsed(SOURCE, defer=False, workers=1)
    four, said_four = _parsed(SOURCE, defer=False, workers=4)
    assert [d.info.name for d in said_one] == [d.info.name for d in said_four]
    assert said_one, "the broken body is reported"
    assert len(one.items) == len(four.items) == 3
