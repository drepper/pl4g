"""A module's function bodies are left as text by the lexer and lexed when read.

The lexer finds where an indented body ends a line at a time -- the first line at
the left margin, the comments just before it being the next definition's -- and
emits one `BODY` token in its place; the body's own job lexes it when something
reads the body.  What comes of that is what the lexer would have made there, so
the tokens of a whole file, put back together, are the tokens it always made.
"""

from __future__ import annotations

from pathlib import Path

from pypl4g.diag.engine import collecting_engine
from pypl4g.front.lexer import tokenize, tokenize_body
from pypl4g.front.token import TokKind
from pypl4g.source.manager import SourceManager

ROOT = Path(__file__).resolve().parents[2]


def _put_back(text: str) -> tuple[list, list, int]:  # noqa: ANN001
    sources = SourceManager()
    engine, said = collecting_engine(None)
    source = sources.add(Path("t.pl4g"), text)
    whole = tokenize(source, engine)
    lazy = tokenize(source, engine, lazy=True)
    rebuilt = []
    for one in lazy:
        if one.kind is TokKind.BODY:
            rebuilt.extend(t for t in tokenize_body(source, one.span, engine)
                           if t.kind is not TokKind.EOF)
        else:
            rebuilt.append(one)
    assert said == []
    return ([(t.kind, t.text) for t in whole], [(t.kind, t.text) for t in rebuilt],
            sum(t.kind is TokKind.BODY for t in lazy))


def test_std_put_back_together_is_std() -> None:
    """Every body of `std` is set aside, and what they lex to is what was."""
    whole, rebuilt, bodies = _put_back(
        (ROOT / "modules" / "std.pl4g").read_text(encoding="utf-8"))
    assert rebuilt == whole
    assert bodies > 20


def test_a_body_with_a_pair_open_at_the_margin_is_lexed_as_it_was() -> None:
    """A line at the margin inside a pair belongs to the body; the count of the
    brackets says so, and such a body is lexed where it stands."""
    text = ("fn f() \N{RIGHTWARDS ARROW} u8:\n"
            "    let a: u8 = g(1u8,\n"
            "2u8)\n"
            "    a\n"
            "\n"
            "\N{REFERENCE MARK}\N{REFERENCE MARK} the next one's\n"
            "fn g(x: u8, y: u8) \N{RIGHTWARDS ARROW} u8:\n"
            "    x + y\n")
    whole, rebuilt, bodies = _put_back(text)
    assert rebuilt == whole
    assert bodies == 1, "the second body is set aside, the first is not"
