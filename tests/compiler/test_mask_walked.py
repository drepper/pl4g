"""Picking where a loop takes its turns from is walked, not copied.

Run tests cannot see this -- the loop adds up the same either way -- so what is
checked is the code: picking anywhere else takes room in the frame for
everything that could be picked, and picking a loop walks takes none.
"""

from __future__ import annotations

import re

from conftest import describe

WALKED = """\
@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}10u8, 20u8, 30u8, 40u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    let s: mut u8 = 0u8
    foreach x := v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}v > 15u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:
        s \N{LEFTWARDS ARROW} s + x
    if s = 90u8: 0u6 else: 1u6
"""

KEPT = WALKED.replace(
    "    foreach x := v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}",
    "    let p: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"
).replace("15u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:",
          "15u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\n    foreach x := p:")


def _room(compile_source, source: str) -> list[str]:  # noqa: ANN001
    """The types of the room the program takes in its frame."""
    proc, output = compile_source(source, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    return re.findall(r"frame\.ptr<mut ([^>]*)>", output.read_text(encoding="utf-8"))


def test_a_loop_over_a_pick_takes_no_room(compile_source) -> None:  # noqa: ANN001
    """Only the mask is in the frame: nothing picked is copied anywhere."""
    room = _room(compile_source, WALKED)
    assert not any(one.startswith("u8") for one in room), room


def test_a_pick_kept_in_a_name_takes_room_for_all_of_it(compile_source) -> None:  # noqa: ANN001
    """Picking into a name is an array, in room enough for all four."""
    room = _room(compile_source, KEPT)
    assert "u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}" in room, room


PER_PLACE = """\
type Person = { age : u8 ; height : u8 }

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let ps: Person\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}Person(.age \N{LEFTWARDS ARROW} 12u8, .height \N{LEFTWARDS ARROW} 150u8), Person(.age \N{LEFTWARDS ARROW} 30u8, .height \N{LEFTWARDS ARROW} 180u8)\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    let n: mut u8 = 0u8
    foreach _ := ps\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}ps\N{DIAERESIS}.age \N{GREATER-THAN OR EQUAL TO} 18u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:
        n \N{LEFTWARDS ARROW} n + 1u8
    if n = 1u8: 0u6 else: 1u6
"""


def test_a_mask_a_loop_picks_with_is_asked_a_place_at_a_time(compile_source) -> None:  # noqa: ANN001
    """Neither the ages nor the truth values are gathered: nothing is in the frame."""
    room = _room(compile_source, PER_PLACE)
    assert room == [], room
