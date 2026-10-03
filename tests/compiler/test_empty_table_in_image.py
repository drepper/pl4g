"""An empty table at the top level is laid out in the image and built by nothing.

A set or a dictionary written `⸨⸩` in the heap needs no constructor: its block and a
first run of empty entries are bytes of the image, written where they may be
written, and the first insertion that does not fit grows it from the heap.  A
program that never puts anything in it -- `std`'s error names, in every program
that imports `std` -- runs nothing for it.
"""

from __future__ import annotations

from conftest import describe

PROGRAM = """\
let names: mut \N{LEFT DOUBLE PARENTHESIS}u64: u8\N{RIGHT DOUBLE PARENTHESIS} = \N{LEFT DOUBLE PARENTHESIS}\N{RIGHT DOUBLE PARENTHESIS}
let filled: mut \N{LEFT DOUBLE PARENTHESIS}u64: u8\N{RIGHT DOUBLE PARENTHESIS} = \N{LEFT DOUBLE PARENTHESIS}1u64: 2u8\N{RIGHT DOUBLE PARENTHESIS}

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    names\N{LEFT DOUBLE PARENTHESIS}1u64\N{RIGHT DOUBLE PARENTHESIS} \N{LEFTWARDS ARROW} 3u8
    if #names = 1 \N{LOGICAL AND} #filled = 1: 0u6 else: 1u6
"""


def test_only_a_table_with_entries_is_built(compile_source) -> None:  # noqa: ANN001
    """The constructor builds `filled` and has nothing to do for `names`."""
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    body = text[text.index("fn @__pl4g_globals"):]
    body = body[:body.index("\n}")]
    assert "@filled" in body, body
    assert "@names" not in body, body
