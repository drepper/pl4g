"""The documentation says what it means in the characters it means.

The compiler's Python is written with `\\N{...}` escapes, because a source file
full of glyphs is hard to read and harder to grep.  A Markdown document is the
other way round: nothing processes it, so an escape written in one is the
escape, shown to the reader as backslash-N-brace.  These check that none has
leaked from one into the other.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import ROOT

#: Every document, which is every Markdown file the project keeps.  The timings
#: are generated and hold nothing but numbers.
DOCUMENTS = sorted(p for p in ROOT.glob("**/*.md")
                   if "node_modules" not in p.parts and p.name != "timings.md")

NAMED = re.compile(r"\\N\{[A-Z0-9 -]+\}")
NUMBERED = re.compile(r"\\u[0-9a-fA-F]{4}")


def _ids(paths: list[Path]) -> list[str]:
    """What to call each document in the report."""
    return [p.relative_to(ROOT).as_posix() for p in paths]


@pytest.mark.parametrize("path", DOCUMENTS, ids=_ids(DOCUMENTS))
def test_no_python_escape_reaches_a_document(path: Path) -> None:
    """`\\N{...}` is Python's and means nothing in Markdown.

    The language has no such escape either, so one inside a code block is as
    wrong as one in a sentence: whichever it was meant to be, what a reader
    sees is the escape.
    """
    found = [(at, line) for at, line in enumerate(path.read_text().split("\n"), 1)
             if NAMED.search(line)]
    assert not found, "".join((
        "a Python escape reached ", path.name, ":\n",
        "\n".join("".join((str(at), ": ", line)) for at, line in found)))


@pytest.mark.parametrize("path", DOCUMENTS, ids=_ids(DOCUMENTS))
def test_a_numbered_escape_is_only_ever_in_an_example(path: Path) -> None:
    """`\\uXXXX` is the *language's* escape, so it belongs in a program.

    Written in a sentence it is a glyph that did not survive being put there,
    which is the same mistake as the one above wearing other clothes.  Inside a
    fenced block it is what a program writes, and the specification shows one.
    """
    inside = False
    found: list[tuple[int, str]] = []
    for at, line in enumerate(path.read_text().split("\n"), 1):
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside and NUMBERED.search(line):
            found.append((at, line))
    assert not found, "".join((
        "a numbered escape stands outside an example in ", path.name, ":\n",
        "\n".join("".join((str(at), ": ", line)) for at, line in found)))
