"""`@[opaque]`: every call kept as written, by a function that stays pure."""

from __future__ import annotations

import re

from conftest import describe

PROGRAM = """\
@[opaque]
fn three() \N{RIGHTWARDS ARROW} u32:
    3u32

fn plain() \N{RIGHTWARDS ARROW} u32:
    3u32

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let a: u32 = three() + three()
    let b: u32 = plain() + plain()
    if a + b = 12u32: 0u6 else: 1u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""


def _main(compile_source) -> str:  # noqa: ANN001
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == "", describe(proc)
    text = output.read_text(encoding="utf-8")
    found = re.search(r"fn @main\(.*?\n}", text, re.S)
    assert found is not None, text
    return found.group(0)


def test_every_call_to_an_opaque_function_is_kept(compile_source) -> None:  # noqa: ANN001
    """Two calls with nothing to tell them apart stay two, and stay calls."""
    assert len(re.findall(r"= call\.u32", _main(compile_source))) == 2


def test_a_pure_function_is_still_folded(compile_source) -> None:  # noqa: ANN001
    """The same body, not opaque, is worked out while compiling."""
    body = _main(compile_source)
    assert "plain" not in body and len(re.findall(r"= call\.", body)) == 2
