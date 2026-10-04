"""Every variable whose type the program did not write in full is in the log.

`let x :=`, `let x: mut =`, `⟦⟧` alone, the names a taking apart or a loop
binds: the compiler worked the type out, and what it came to is the compiler's
to say -- at the name, with the type.  A type written in full is not reported,
nor is a shape stated in part that the variable keeps as it was written.
"""

from __future__ import annotations

import json

from conftest import describe

PROGRAM = """\
let limit := 7u8
let table: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1u8, 2u8, 3u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let a := 3u8
    let b: mut = a + 1u8
    let c: u8 = a
    let d: \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1u16, 2u16\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    let t: \N{LEFT ANGLE BRACKET}u8, u16\N{RIGHT ANGLE BRACKET} = \N{LEFT ANGLE BRACKET}1u8, 2u16\N{RIGHT ANGLE BRACKET}
    let x, y := t
    let m: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2,\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1u8, 2u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3u8, 4u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    foreach v := table:
        b \N{LEFTWARDS ARROW} b + v
    foreach w: u8 = table:
        b \N{LEFTWARDS ARROW} b + w
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(b + c + x + limit + m\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0, 0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} + (\N{APL FUNCTIONAL SYMBOL QUAD}narrow(\N{APL FUNCTIONAL SYMBOL QUAD}drop(#d) + \N{APL FUNCTIONAL SYMBOL QUAD}widen(y, \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}), \N{TOP LEFT CORNER}u8\N{TOP RIGHT CORNER}) ?? 0u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""


def _inferred(compile_source, tmp_path) -> dict[str, tuple[int, str]]:  # noqa: ANN001
    """What the log says about types worked out in the program above, by name."""
    log = tmp_path / "report.json"
    proc, _ = compile_source(PROGRAM, "".join(("--report-log=", str(log))))
    assert proc.returncode == 0, describe(proc)
    found: dict[str, tuple[int, str]] = {}
    for one in json.loads(log.read_text(encoding="utf-8"))["reports"]:
        if one["kind"] == "type-inferred" and one["where"]["file"].endswith("t.pl4g"):
            found[one["subject"]] = (one["where"]["line"], one["reason"])
    return found


def test_each_name_given_no_type_is_logged_with_the_one_it_got(compile_source,  # noqa: ANN001
                                                                tmp_path) -> None:  # noqa: ANN001
    """At the top level and in a body, by `:=`, by `mut` alone, by taking apart,
    and by a loop -- each at its name's line, with the type."""
    said = _inferred(compile_source, tmp_path)
    for name, line, ty in (("limit", 1, "u8"), ("a", 6, "u8"), ("b", 7, "u8"),
                           ("x", 11, "u8"), ("y", 11, "u16"), ("v", 13, "u8")):
        assert name in said, (name, said)
        assert said[name][0] == line and "".join(("'", ty, "'")) in said[name][1], \
            (name, said[name])


def test_a_type_written_in_part_says_what_was_written(compile_source,  # noqa: ANN001
                                                      tmp_path) -> None:  # noqa: ANN001
    """`⟦⟧` alone takes the element and the length from the value."""
    said = _inferred(compile_source, tmp_path)
    assert said["d"][0] == 9 and "u16\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}" in said["d"][1], said["d"]
    assert "was written" in said["d"][1], said["d"]


def test_a_type_written_in_full_is_not_logged(compile_source, tmp_path) -> None:  # noqa: ANN001
    """Nor is a shape stated in part that the variable keeps as written, nor a
    loop's name given a type."""
    said = _inferred(compile_source, tmp_path)
    assert not {"table", "c", "t", "m", "w"} & set(said), said
