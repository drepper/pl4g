"""What the optimizer knows a value can be, and the checks that settles.

A run test cannot see a check that is not made -- the program answers the same
either way -- so what is checked here is the code: which checks are left, which
operations are the plain ones, and which comparisons are gone.  That the checks
which can fail still fire is what the language tests `checks-unsettled-*` say.
"""

from __future__ import annotations

import re

from conftest import describe, function_ir

PROGRAM = """\
unit \N{CURRENCY SIGN}size \N{RIGHTWARDS ARROW} \N{CURRENCY SIGN}idx

let TABLE: u64\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}20\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0u64, 1u64, 2u64, 3u64, 4u64, 5u64, 6u64, 7u64, 8u64, 9u64, 10u64, 11u64, 12u64, 13u64, 14u64, 15u64, 16u64, 17u64, 18u64, 19u64\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static

@[opaque]
fn summed(v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}) \N{RIGHTWARDS ARROW} u64:
    let n: u64 \N{CURRENCY SIGN}idx = #v
    let total: mut u64 = 0u64
    let at: mut u64 \N{CURRENCY SIGN}idx = 0
    while at < n:
        total \N{LEFTWARDS ARROW} \N{APL FUNCTIONAL SYMBOL QUAD}wrap(total + \N{APL FUNCTIONAL SYMBOL QUAD}widen(v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}at\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}))
        at \N{LEFTWARDS ARROW} at + 1
    total

@[opaque]
fn tabled(x: u64) \N{RIGHTWARDS ARROW} u64:
    let bits: u64 = 64u64 - \N{APL FUNCTIONAL SYMBOL QUAD}widen(\N{APL FUNCTIONAL SYMBOL QUAD}lead(x), \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER})
    let t: u64 \N{CURRENCY SIGN}idx = \N{APL FUNCTIONAL SYMBOL QUAD}unit((bits \N{MULTIPLICATION SIGN} 1233u64) \N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK} 12u64, \N{TOP LEFT CORNER}idx\N{TOP RIGHT CORNER})
    TABLE\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}t\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}

@[opaque]
fn twice(v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, at: u64 \N{CURRENCY SIGN}idx) \N{RIGHTWARDS ARROW} u8:
    \N{APL FUNCTIONAL SYMBOL QUAD}wrap(v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}at\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} + v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}at\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})

@[opaque]
fn ahead(v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, at: u64 \N{CURRENCY SIGN}idx) \N{RIGHTWARDS ARROW} u64 \N{CURRENCY SIGN}size:
    let n: u64 \N{CURRENCY SIGN}idx = #v
    if at < n and v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}at\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 1u8:
        #v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}at + 1\N{HORIZONTAL ELLIPSIS}n\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
    else:
        0

@[opaque]
fn narrowed(x: u64) \N{RIGHTWARDS ARROW} u8:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(x & 255u64, \N{TOP LEFT CORNER}u8\N{TOP RIGHT CORNER}) ?? 1u8

@[opaque]
fn moved(x: u64, by: u64) \N{RIGHTWARDS ARROW} u64:
    x \N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK} (by & 63u64)

@[opaque]
fn product(a: u8, b: u8) \N{RIGHTWARDS ARROW} u64:
    \N{APL FUNCTIONAL SYMBOL QUAD}widen(a, \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}) \N{MULTIPLICATION SIGN} \N{APL FUNCTIONAL SYMBOL QUAD}widen(b, \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}) + 1u64

@[opaque]
fn unsettled(v: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, x: u8, by: u8) \N{RIGHTWARDS ARROW} u8:
    let n: u64 \N{CURRENCY SIGN}idx = #v
    let at: u64 \N{CURRENCY SIGN}idx = \N{APL FUNCTIONAL SYMBOL QUAD}unit(\N{APL FUNCTIONAL SYMBOL QUAD}widen(x, \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}), \N{TOP LEFT CORNER}idx\N{TOP RIGHT CORNER})
    if at \N{LESS-THAN OR EQUAL TO} n:
        v\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}at\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} + (x \N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK} (by \N{LEFT FLOOR} 8u8))
    else:
        0u8

@[opaque]
fn build(n: u64) \N{RIGHTWARDS ARROW} [u8]:
    let out: mut [u8] = []
    let i: mut u64 = 0u64
    while i < n:
        out \N{DOUBLE PLUS}\N{LEFTWARDS ARROW} \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}7u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
        i \N{LEFTWARDS ARROW} i + 1u64
    out

@[opaque]
fn kept(x: [u8]) \N{RIGHTWARDS ARROW} [u8]:
    let y: [u8] = x
    let out: mut [u8] = y
    out \N{DOUBLE PLUS}\N{LEFTWARDS ARROW} \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}7u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
    out

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let some: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1u8, 2u8, 3u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    let made: [u8] = build(2u64)
    let all: u64 = summed(some) + tabled(5u64) + \N{APL FUNCTIONAL SYMBOL QUAD}widen(twice(some, 1), \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}) + \N{APL FUNCTIONAL SYMBOL QUAD}drop(ahead(some, 0)) + \N{APL FUNCTIONAL SYMBOL QUAD}widen(narrowed(300u64), \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}) + moved(1u64, 3u64) + product(2u8, 3u8) + \N{APL FUNCTIONAL SYMBOL QUAD}widen(unsettled(some, 1u8, 1u8), \N{TOP LEFT CORNER}u64\N{TOP RIGHT CORNER}) + \N{APL FUNCTIONAL SYMBOL QUAD}drop(#kept(made))
    if all = 0u64: 1u6 else: 0u6
"""


def _ir(compile_source, level: str = "-O1") -> str:  # noqa: ANN001
    proc, output = compile_source(PROGRAM, "--emit=ir", level)
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def _checks(body: str) -> int:
    return len(re.findall(r"^\s+assert ", body, re.M))


def _checked(body: str, op: str) -> int:
    """How many of the checked operation *op* are left: `add`, not `wrap.add`."""
    return len(re.findall("".join((r"= ", op, r"\.[ui]")), body))


def test_an_index_the_loop_compared_is_not_checked(compile_source) -> None:  # noqa: ANN001
    """`at < n` with `n = #v` is what `v⟦at⟧` would ask, and `at + 1` cannot go
    past the end of a type `at` is below something in."""
    body = function_ir(_ir(compile_source), "summed")
    assert _checks(body) == 0, body
    assert _checked(body, "add") == 0, body


def test_they_are_checked_where_nothing_is_optimized(compile_source) -> None:  # noqa: ANN001
    """The same function without the optimizer has both."""
    body = function_ir(_ir(compile_source, "-O0"), "summed")
    assert _checks(body) >= 1, body
    assert _checked(body, "add") >= 1, body


def test_an_index_worked_out_below_the_length_is_not_checked(compile_source) -> None:  # noqa: ANN001
    """A count of leading noughts is 64 at most, 64 less it is nought at least,
    and the product and the shift put it below 20: neither the difference, the
    product nor the index is checked."""
    body = function_ir(_ir(compile_source), "tabled")
    assert _checks(body) == 0, body
    assert _checked(body, "sub") == 0 and _checked(body, "mul") == 0, body


def test_a_check_made_once_is_not_made_again(compile_source) -> None:  # noqa: ANN001
    """What the first check stopped the program for not holding holds after it."""
    body = function_ir(_ir(compile_source), "twice")
    assert _checks(body) == 1, body


def test_what_the_first_half_of_an_and_said_holds_in_the_second(compile_source) -> None:  # noqa: ANN001
    """`at < n and v⟦at⟧ = 1u8`: the read is where `at < n` held, and so is the
    slice from `at + 1`, which is `n` at most."""
    body = function_ir(_ir(compile_source), "ahead")
    assert _checks(body) == 0, body
    assert _checked(body, "add") == 0, body


def test_a_narrowing_of_what_fits_asks_nothing(compile_source) -> None:  # noqa: ANN001
    """`x & 255` is a byte, so the comparison with 255 is answered and gone."""
    body = function_ir(_ir(compile_source), "narrowed")
    assert "icmp" not in body, body


def test_a_shift_by_less_than_the_width_is_the_plain_one(compile_source) -> None:  # noqa: ANN001
    """`by & 63` is below 64."""
    body = function_ir(_ir(compile_source), "moved")
    assert _checked(body, "shl") == 0 and "wrap.shl" in body, body


def test_a_product_of_bytes_fits(compile_source) -> None:  # noqa: ANN001
    """Two bytes multiplied are below 2^16, and one more is too."""
    body = function_ir(_ir(compile_source), "product")
    assert _checked(body, "mul") == 0 and _checked(body, "add") == 0, body


def test_what_can_fail_is_still_checked(compile_source) -> None:  # noqa: ANN001
    """`at ≤ n` leaves `at = n`, a byte plus a byte may not fit, and a distance
    of eight is the width of a byte: all three are checked."""
    body = function_ir(_ir(compile_source), "unsettled")
    assert _checks(body) == 1, body
    assert _checked(body, "add") == 1 and _checked(body, "shl") == 1, body


def test_a_list_made_in_a_variable_goes_out_as_it_is(compile_source) -> None:  # noqa: ANN001
    """The answer is the variable's room where it is the heap's: the copy is
    on the way where it is not, and no longer on every way out."""
    body = function_ir(_ir(compile_source, "-O0"), "build")
    assert "owned.heaps" in body, body


def test_one_bound_to_another_name_is_copied(compile_source) -> None:  # noqa: ANN001
    """`y` is the parameter's room, and so is `out` until it is joined onto."""
    body = function_ir(_ir(compile_source, "-O0"), "kept")
    assert "owned.heaps" not in body, body
