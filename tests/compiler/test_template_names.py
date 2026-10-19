"""Where what is said about a name in a template points.

A name read out of a template is put in as a piece the caller wrote, so what is said
about it points at the caller's text -- at the name itself inside the literal where
the literal holds no escape, which is what a reader looking for the typo wants, and
at the literal where it does.
"""

from __future__ import annotations

from conftest import describe

PROGRAM = """let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    if std.format\N{TOP LEFT CORNER}"ab {totl}"\N{TOP RIGHT CORNER} = "": 1u6 else: 0u6
"""


def test_an_unknown_name_points_at_itself(compile_source) -> None:  # noqa: ANN001
    """The column is the name's own, inside the literal."""
    proc, _ = compile_source(PROGRAM)
    assert proc.returncode != 0, describe(proc)
    column = PROGRAM.splitlines()[4].index("totl") + 1
    assert "".join(("t.pl4g:5:", str(column), ": error: 'totl'")) in proc.stderr, \
        describe(proc)


def test_a_literal_with_an_escape_is_pointed_at_whole(compile_source) -> None:  # noqa: ANN001
    """Past an escape a place in the text is not a place in the source, so what is
    pointed at is the literal."""
    escaped = PROGRAM.replace('"ab {totl}"', '"a\\\\tb {totl}"')
    proc, _ = compile_source(escaped)
    assert proc.returncode != 0, describe(proc)
    column = escaped.splitlines()[4].index('"a') + 1
    assert "".join(("t.pl4g:5:", str(column), ": error: 'totl'")) in proc.stderr, \
        describe(proc)
