"""Arrays: where their elements live, and how much room they take.

What a value of an array type is, is where its elements are.  Where those
elements are depends on the array: a variable at the top level holds them
itself, and one inside a function holds them in the function's own room.  How
much room they take is where the specification's freedom about layout first
shows: a variable something outside the image reads is laid out the way that
world expects, and every other one whichever way is better.
"""

import subprocess

import pytest

from conftest import compiler_targets, describe, run_compiler, runner_for
from pypl4g.ir.layout import DataLayout, WIDE_ENOUGH, align_of, size_of, stride_of
from pypl4g.ir.types import TypeContext, U8, U16, U32, U64

ARROW = "\N{RIGHTWARDS ARROW}"
OPEN = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"
CLOSE = "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"

LAYOUT = DataLayout(pointer_size=8)
SYSTEM = DataLayout(pointer_size=8, system=True)
TYPES = TypeContext()


# -- what one occupies -----------------------------------------------------------

@pytest.mark.parametrize(("element", "length", "size"), [
    (U8, 4, 4), (U16, 4, 8), (U32, 4, 16), (U64, 4, 32), (U8, 0, 0),
])
def test_an_array_is_its_elements_and_nothing_else(element: object, length: int,
                                                   size: int) -> None:
    """How many there are is in the type, so no room is spent saying it again."""
    assert size_of(TYPES.array_type(element, (length,)), LAYOUT) == size  # type: ignore[arg-type]


def test_an_array_of_no_stated_shape_is_a_place_and_a_count_per_dimension() -> None:
    """Which says nothing about where the elements are, only how many."""
    assert size_of(TYPES.array_type(U8, (None,)), LAYOUT) == 16
    assert size_of(TYPES.array_type(U8, (None, None)), LAYOUT) == 24
    assert size_of(TYPES.array_type(U8, (None, None, None)), LAYOUT) == 32


@pytest.mark.parametrize(("shape", "count"), [
    ((4,), 4), ((2, 3), 6), ((2, 2, 2), 8), ((3, 0), 0),
])
def test_a_shape_is_as_many_elements_as_it_multiplies_out_to(
        shape: tuple, count: int) -> None:
    """However many dimensions, the elements are one run: row-major says the
    last dimension is the one whose neighbours are next to each other."""
    held = TYPES.array_type(U32, shape)
    assert held.count == count
    assert size_of(held, LAYOUT) == count * 4


@pytest.mark.parametrize("element", [U8, U16, U32, U64])
def test_the_system_aligns_an_array_as_one_element(element: object) -> None:
    """An array starts where its first element would, which is what C says."""
    small = TYPES.array_type(element, (2,))  # type: ignore[arg-type]
    assert align_of(small, SYSTEM) == align_of(element, SYSTEM)  # type: ignore[arg-type]


def test_and_this_compiler_aligns_a_large_one_wider() -> None:
    """The freedom the specification gives, used for the smallest thing that pays.

    An array worth reading a word at a time is put where a word can be read.
    Nothing outside the image can tell, which is exactly the condition the
    specification puts on taking the freedom.
    """
    wide = TYPES.array_type(U8, (WIDE_ENOUGH,))
    assert align_of(wide, LAYOUT) == WIDE_ENOUGH
    assert align_of(wide, SYSTEM) == 1
    # And a small one is left alone: there is nothing to gain and padding to lose.
    narrow = TYPES.array_type(U8, (WIDE_ENOUGH - 1,))
    assert align_of(narrow, LAYOUT) == align_of(narrow, SYSTEM) == 1


def test_the_size_is_the_same_either_way() -> None:
    """Only the alignment differs, which is what keeps indexing one rule.

    How far apart two elements are is what an index is multiplied by, and the
    front end works that out without knowing which layout the variable ended up
    with.  A freedom that changed it would have to be told to both.
    """
    for element in (U8, U16, U32, U64):
        held = TYPES.array_type(element, (7,))  # type: ignore[arg-type]
        assert size_of(held, LAYOUT) == size_of(held, SYSTEM)
        assert size_of(held, LAYOUT) == 7 * stride_of(element, LAYOUT)  # type: ignore[arg-type]


# -- and that the programs run ---------------------------------------------------

def run_it(tmp_path, triple: str, source: str) -> int:  # noqa: ANN001
    """Compile *source* for *triple*, run it, and return its status."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(path)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(output)],
                         capture_output=True, timeout=60)
    return ran.returncode


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_index_is_multiplied_by_the_width_of_an_element(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """Four arrays of four widths, each read at the same index."""
    source = "".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    let a: u8", OPEN, "3", CLOSE, " = ", OPEN, "1u8, 2u8, 4u8", CLOSE, "\n",
        "    let b: u16", OPEN, "3", CLOSE, " = ", OPEN, "1u16, 2u16, 8u16", CLOSE, "\n",
        "    let c: u32", OPEN, "3", CLOSE, " = ", OPEN, "1u32, 2u32, 16u32", CLOSE, "\n",
        "    let d: u64", OPEN, "3", CLOSE, " = ", OPEN, "1u64, 2u64, 32u64", CLOSE, "\n",
        "    let at: u8 = 2u8\n",
        "    a", OPEN, "at", CLOSE, " + 8u8\n"))
    assert run_it(tmp_path, triple, source) == 12


@pytest.mark.parametrize("triple", compiler_targets())
def test_an_index_outside_the_array_stops_the_program(
        triple: str, tmp_path) -> None:  # noqa: ANN001
    """Where the index is not written down the check is one comparison and a
    branch that does not come back, which is the shape every fault here has."""
    source = "".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    let a: u8", OPEN, "4", CLOSE, " = ", OPEN, "1u8, 2u8, 4u8, 8u8", CLOSE, "\n",
        "    let at: u8 = 9u8\n",
        "    a", OPEN, "at", CLOSE, "\n"))
    assert run_it(tmp_path, triple, source) < 0, "it should die by a signal"


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_table_is_read_in_row_major_order(triple: str, tmp_path) -> None:  # noqa: ANN001
    """The last dimension is the one whose neighbours are next to each other.

    Written as a table and read back as one: an index worked out the other way
    round reads the wrong element, and a two-by-three whose entries are all
    different is enough to see it.
    """
    source = "".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    let m: u8", OPEN, "2,3", CLOSE, " = ",
        OPEN, OPEN, "1u8,2u8,4u8", CLOSE, ",", OPEN, "8u8,16u8,32u8", CLOSE, CLOSE, "\n",
        "    let r: u8 = 1u8\n    let c: u8 = 2u8\n",
        "    m", OPEN, "r,c", CLOSE, " + m", OPEN, "0,1", CLOSE, "\n"))
    assert run_it(tmp_path, triple, source) == 34


@pytest.mark.parametrize("triple", compiler_targets())
def test_every_dimension_is_checked_against_its_own(triple: str,
                                                    tmp_path) -> None:  # noqa: ANN001
    """An index inside the whole run but outside its dimension is outside.

    `m⟦0,5⟧` of a two-by-three would be the third element of the second row if
    the check were against how many elements there are in all, which is what a
    single check would amount to.
    """
    source = "".join((
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n",
        "    let m: u8", OPEN, "2,3", CLOSE, " = ",
        OPEN, OPEN, "1u8,2u8,4u8", CLOSE, ",", OPEN, "8u8,16u8,32u8", CLOSE, CLOSE, "\n",
        "    let c: u8 = 5u8\n",
        "    m", OPEN, "0,c", CLOSE, "\n"))
    assert run_it(tmp_path, triple, source) < 0, "it should die by a signal"
