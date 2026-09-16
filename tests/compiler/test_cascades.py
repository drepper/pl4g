"""Diagnostics reported because of an earlier diagnostic.

A compiler that finds one thing wrong goes on looking, and what it finds after
that is often a consequence rather than a second mistake.  Most consequences
are harmless: a value of the error type is accepted everywhere, so nothing more
is said about it.  The ones checked here are the two where something *would*
have been said and must not be -- above all a message that says the compiler
does not implement something, which is a claim about the compiler and not about
the program, and which is fatal.
"""

from __future__ import annotations

from pathlib import Path

from pypl4g.diag import ids as D
from pypl4g.diag.engine import collecting_engine
from pypl4g.front.lexer import tokenize
from pypl4g.front.parser import parse
from pypl4g.ir.module import Module
from pypl4g.sema.check import check
from pypl4g.sema.modules import ModuleRegistry
from pypl4g.source.manager import SourceManager

ARROW = "\N{RIGHTWARDS ARROW}"
DEREF = "\N{POSITION INDICATOR}"


def reported(source: str) -> list[int]:
    """Every diagnostic checking *source* produces, in the order it made them."""
    sources = SourceManager()
    unit_source = sources.add(Path("t.pl4g"), source)
    engine, collected = collecting_engine(None)
    unit = parse(tokenize(unit_source, engine), "t.pl4g", engine)
    check(Module("t"), [unit], engine, ModuleRegistry(), sources)
    return [d.info.number for d in collected]


def body(text: str) -> str:
    """A startup function around *text*."""
    return "".join(("@[startup, impure]\nfn main() ", ARROW, " u6:\n", text,
                    "    0u6\n"))


def test_a_literal_beside_an_error_asks_for_nothing() -> None:
    """The other side of the comparison is wrong, so this side is not asked.

    An integer literal with no suffix takes its type from what is around it,
    and where there is nothing to take it from this compiler says so and stops.
    A sibling that is in error is not nothing to take it from -- it is a type
    that was already reported -- so saying the compiler has not implemented
    something would be a second message about one mistake, and a fatal one.
    """
    found = reported(body("".join((
        "    let v: u32 = nosuch\n",
        "    if v = 0:\n        0u6\n    else:\n        1u6\n"))))
    assert D.IMPL_UNIMPLEMENTED_FEATURE not in found, \
        "the literal took the error as its context and asked for nothing"
    assert found.count(D.LANG_FILESTRUCT_UNDEFINED_NAME) == 1, \
        "the one thing wrong is reported once"


def test_a_reference_of_the_wrong_kind_reports_once() -> None:
    """A place lent where writing is wanted, when the place may not be written.

    One mistake, one message: what the name is bound to is of the error type
    from there on, and a literal it is later compared against has to take that
    quietly.
    """
    found = reported(body("".join((
        "    let v: u32 = 0u32\n",
        "    let r: &mut u32 = &v\n",
        "    if r", DEREF, " = 0:\n        0u6\n    else:\n        1u6\n"))))
    assert D.IMPL_UNIMPLEMENTED_FEATURE not in found
    assert found == [D.LANG_REF_PLACE_NOT_MUTABLE]


def test_a_literal_with_nowhere_to_look_is_still_reported() -> None:
    """The rule above takes nothing away from the rule it guards."""
    assert D.IMPL_UNIMPLEMENTED_FEATURE in reported(body("    let n := 1\n"))
