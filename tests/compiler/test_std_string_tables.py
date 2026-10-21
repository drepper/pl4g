"""The tables `std.string` reads numbers and writes them with, worked out again.

`modules/string.pl4g` holds two tables of 128-bit numbers as data in the image:
the powers of five Eisel and Lemire's algorithm multiplies a significand by, and
the powers of ten Schubfach scales a number by.  They are written out in the
module rather than worked out while compiling, as fast_float and every port of
Schubfach have them, so this is what says they are what they say they are: each
is made again here from its definition, with Python's whole numbers, and
compared with what the module holds, entry by entry.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASK = (1 << 64) - 1


def _table(name: str) -> list[int]:
    """The words of the table the function *name* answers, as written."""
    text = (ROOT / "modules" / "string.pl4g").read_text(encoding="utf-8")
    found = re.search("".join((r"^fn ", name, r"\(\)[^\n]*:\n(.*?)^\s*⟧")), text,
                      re.S | re.M)
    assert found is not None, name
    return [int(one, 16) for one in re.findall(r"0x([0-9A-F]{16})", found.group(1))]


def _powers_of_five() -> list[int]:
    """fast_float's `power_of_five_128`, as its script makes it: q from -342 to 308."""
    words: list[int] = []
    for q in range(-342, 0):
        power = 5 ** -q
        z = 0
        while (1 << z) < power:
            z += 1
        b = z + 127 if q >= -27 else 2 * z + 128
        c = 2 ** b // power + 1
        while c >= 1 << 128:
            c //= 2
        words += [c >> 64, c & MASK]
    for q in range(0, 309):
        power = 5 ** q
        while power < 1 << 127:
            power *= 2
        while power >= 1 << 128:
            power //= 2
        words += [power >> 64, power & MASK]
    return words


def _powers_of_ten() -> list[int]:
    """Schubfach's g for 10^k, k from -292 to 324: ⌊10^k × 2^-r⌋ + 1 between 2^127 and
    2^128, r being ⌊k × log2(10)⌋ + 1 - 128 as the module works it out."""
    words: list[int] = []
    for k in range(-292, 325):
        r = ((k * 1741647) >> 19) + 1 - 128
        num = 10 ** k if k >= 0 else 1
        den = 1 if k >= 0 else 10 ** -k
        if r >= 0:
            den <<= r
        else:
            num <<= -r
        g = num // den + 1
        assert 1 << 127 <= g < 1 << 128, k
        words += [g >> 64, g & MASK]
    return words


def test_the_powers_of_five_are_fast_floats() -> None:
    """Every word of the table Eisel and Lemire multiply by."""
    assert _table("powers_of_five") == _powers_of_five()


def test_the_powers_of_ten_are_schubfachs() -> None:
    """Every word of the table Schubfach multiplies by."""
    assert _table("powers_of_ten") == _powers_of_ten()


def test_the_small_tables() -> None:
    """The powers of ten and of five a `u64` holds, the two digits of every number
    below a hundred, and the powers of ten an `f64` holds exactly."""
    text = (ROOT / "modules" / "string.pl4g").read_text(encoding="utf-8")

    def numbers(name: str) -> list[int]:
        found = re.search("".join((r"^fn ", name, r"\(\)[^\n]*:\n(.*?)⟧")), text,
                          re.S | re.M)
        assert found is not None, name
        return [int(one) for one in re.findall(r"\b(\d+)\b", found.group(1))]

    assert numbers("tens") == [10 ** i for i in range(20)]
    assert numbers("fives") == [5 ** i for i in range(28)]
    assert numbers("pairs") == [48 + int(c) for i in range(100) for c in "%02d" % i]
    found = re.search(r"^fn exact_tens\(\)[^\n]*:\n(.*?)⟧", text, re.S | re.M)
    assert found is not None
    assert re.findall(r"1e(\d+)", found.group(1)) == [str(i) for i in range(23)]
