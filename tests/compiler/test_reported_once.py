"""A mistake is reported once, where it is made, and as what it is.

Each of these is a program a lowering reported twice: once where the value was
lowered against what was wanted of it, and again by the construct around it,
which asked the same question of the same value.
"""

from __future__ import annotations

import re


def _errors(proc) -> list[str]:  # noqa: ANN001
    """The diagnostics a compilation wrote, one line each."""
    return re.findall(r"error: .*\[PL4G-\d+\]", proc.stderr)


def test_a_code_point_of_the_wrong_type(compile_source) -> None:  # noqa: ANN001
    """`⎕chr` of a `u8` is its argument of the wrong type, said once."""
    proc, _ = compile_source(
        "let std := ⎕import(\"std\")\n\n"
        "@[startup]\n"
        "fn main(init: std.Init) → u6:\n"
        "    let n: u8 = 65u8\n"
        "    let c: char = ⎕chr(n)\n"
        "    if c = 'A': 0u6 else: 1u6\n")
    assert proc.returncode != 0
    assert _errors(proc) == ["error: argument 1 of '⎕chr' is 'u32', not 'u8' [PL4G-4213]"]


def test_a_number_given_for_a_code_point(compile_source) -> None:  # noqa: ANN001
    """`⎕ord` of a number is the same mistake the other way, in a comparison."""
    proc, _ = compile_source(
        "let std := ⎕import(\"std\")\n\n"
        "@[startup]\n"
        "fn main(init: std.Init) → u6:\n"
        "    let n: u32 = 65u32\n"
        "    if ⎕ord(n) = 65u32: 0u6 else: 1u6\n")
    assert proc.returncode != 0
    assert _errors(proc) == ["error: argument 1 of '⎕ord' is 'char', not 'u32' [PL4G-4213]"]
