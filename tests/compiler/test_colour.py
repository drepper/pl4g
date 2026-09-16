"""Colour in the diagnostics, and the highlighting of the snippets.

Colour is decoration and never information: everything a colour says here is
said by the text as well.  So what these check is that it appears where a
terminal is being written to and nowhere else, that what the text says is the
same either way, and that the pieces of a source line are coloured as the
grammar says they are -- which is the one part that could be wrong about the
program rather than merely about how it looks.
"""

from __future__ import annotations

import io
import re

import pytest

from conftest import run_compiler
from pypl4g.diag.highlight import Highlighter
from pypl4g.diag.style import ColourWhen, Palette

#: Any escape sequence, which is what "no colour" means the absence of.
ESCAPE = re.compile("\N{ESCAPE}\\[[0-9;]*m")

SOURCE = "".join(("@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n",
                  "    let n : u8 = nosuch + 1u8\n    0u6\n"))


@pytest.fixture
def written(tmp_path) -> object:
    """A program with one mistake in it, and where it was written."""
    path = tmp_path / "c.pl4g"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def test_a_pipe_gets_no_colour(written, tmp_path) -> None:
    """The default looks at the stream, and a pipe is not a terminal."""
    proc = run_compiler(["-o", str(tmp_path / "out"), str(written)])
    assert "\N{ESCAPE}[" not in proc.stderr
    assert "PL4G-4003" in proc.stderr


def test_always_colours_and_never_does_not(written, tmp_path) -> None:
    """The two that say outright say it whatever the stream is."""
    always = run_compiler(["--color=always", "-o", str(tmp_path / "a"),
                           str(written)])
    never = run_compiler(["--color=never", "-o", str(tmp_path / "n"),
                          str(written)])
    assert "\N{ESCAPE}[" in always.stderr
    assert "\N{ESCAPE}[" not in never.stderr
    assert ESCAPE.sub("", always.stderr) == never.stderr, \
        "colour is decoration: taking it away leaves what was there without it"


def test_no_color_wins_over_always(written, tmp_path) -> None:
    """A program that sets it has said it is reading this."""
    proc = run_compiler(["--color=always", "-o", str(tmp_path / "out"),
                         str(written)], env={"NO_COLOR": "1"})
    assert "\N{ESCAPE}[" not in proc.stderr


def test_the_severity_and_the_carets_are_coloured(written, tmp_path) -> None:
    """What a reader looks for first, and what says where."""
    proc = run_compiler(["--color=always", "-o", str(tmp_path / "out"),
                         str(written)])
    assert "\N{ESCAPE}[1;31merror\N{ESCAPE}[0m" in proc.stderr
    assert "\N{ESCAPE}[1;32m" in proc.stderr, "the carets are written in green"
    assert "\N{ESCAPE}[2m[PL4G-4003]\N{ESCAPE}[0m" in proc.stderr


def test_the_snippet_is_highlighted(written, tmp_path) -> None:
    """The grammar says `let` is a keyword and `u8` a type, and both are coloured.

    A lexer would have called `u8` a name like any other; that it is written as
    a type is the thing the grammar knows and the reason the highlighting comes
    from there.
    """
    proc = run_compiler(["--color=always", "-o", str(tmp_path / "out"),
                         str(written)])
    assert "\N{ESCAPE}[35mlet\N{ESCAPE}[0m" in proc.stderr
    assert "\N{ESCAPE}[36mu8\N{ESCAPE}[0m" in proc.stderr
    assert "\N{ESCAPE}[33m1u8\N{ESCAPE}[0m" in proc.stderr


def test_a_line_of_glyphs_is_cut_in_the_right_places() -> None:
    """tree-sitter counts bytes and everything else counts characters.

    The language is written in glyphs of three bytes, so a run measured in one
    and used in the other lands somewhere in the middle of a bracket.  This is
    the check that the conversion happens.
    """
    source = "".join(("@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n",
                      "    let v : u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = "
                      "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\n    0u6\n"))
    line = source.splitlines()[2]
    found = Highlighter().of_line(source, source.index(line), len(line))
    if not found:
        pytest.skip("no tree-sitter module or no built library")
    for begins, ends, name in found:
        assert 0 <= begins < ends <= len(line)
        assert line[begins:ends].strip() == line[begins:ends], \
            "a run that begins or ends on a space is one that lost its place"
    assert any(line[begins:ends] == "u8" and name == "type"
               for begins, ends, name in found)
    assert any(line[begins:ends] == "1u8" and name == "number"
               for begins, ends, name in found)


def test_no_grammar_is_no_highlighting_and_no_complaint(monkeypatch) -> None:
    """The compiler has no dependency outside the standard library.

    Without the module or without the built library there is no colour in the
    snippet and nothing is said about it: what is missing is decoration, and a
    compiler saying so would say it about every line of every diagnostic.
    """
    import pypl4g.diag.highlight as module
    monkeypatch.setattr(module, "_LIBRARY", module._LIBRARY.parent / "no.so")
    assert Highlighter().of_line(SOURCE, 0, 10) == []


def test_a_palette_that_is_off_hands_back_what_it_was_given() -> None:
    """One object either way, so that nothing asks whether there is colour."""
    plain = Palette.chosen(ColourWhen.NEVER, io.StringIO())
    assert plain.severity("error") == "error"
    assert plain.capture("keyword", "let") == "let"
    assert plain.caret("^~~") == "^~~"
