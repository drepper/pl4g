"""`std.text` answers `"true"` and `"false"` from the image, copying nothing.

Its signature says `→ str in a, ⎕static`, so text written down is answered as it
is, carrying no allocator, rather than copied into the arena it was handed.  A
program answers the same either way, so the code is what is asked.
"""

from __future__ import annotations

import re

from conftest import describe

PROGRAM = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let pool: mut arena = \N{APL FUNCTIONAL SYMBOL QUAD}arena
    defer \N{APL FUNCTIONAL SYMBOL QUAD}empty(pool)
    if std.text(true, &mut pool) = "true": 0u6 else: 1u6
"""


def test_the_text_of_a_truth_value_is_not_copied(compile_source) -> None:  # noqa: ANN001
    """The instance for `bool` calls nothing: no join, no allocation."""
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    body = re.search(r"fn @text\(bool, [^\n]*\{\n(.*?)\n\}", text, re.S)
    assert body is not None, text
    assert " call." not in body.group(1), body.group(1)
