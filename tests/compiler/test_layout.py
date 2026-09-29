"""How much room a value of a type takes, and where its parts start.

No type carries this: the specification lets the compiler reorder the fields of
a product, so a size or an offset held inside a type would throw that freedom
away.  These check what the computation answers today, which is declaration
order -- the numbers below are what a later pass that reorders would change,
and are here so that it changes them deliberately.
"""

from __future__ import annotations

import pytest

from pypl4g.ir.layout import (DataLayout, NoLayoutError, align_of, offsets_of,
                              size_of, tag_offset_of)
from pypl4g.ir.types import (BOOL, F32, F64, I32, MemType, ProductType,
                             ResultType, SumType, U8, U16, U64, VOID)

LAYOUT = DataLayout(pointer_size=8)


def product(*fields: tuple[str, object], name: str = "P") -> ProductType:
    """A product of the fields given, named so that it is nominal."""
    return ProductType(tuple(fields), name=name)  # pyright: ignore[reportArgumentType]


def a_sum(*variants: tuple[str, object], name: str = "S") -> SumType:
    """A sum of the variants given."""
    return SumType(tuple(variants), name=name)  # pyright: ignore[reportArgumentType]


def test_a_product_is_its_fields_in_order_with_padding_between() -> None:
    """Each field starts where its own alignment says it may."""
    ty = product(("a", U8), ("b", U64), ("c", U16))
    assert offsets_of(ty, LAYOUT) == (0, 8, 16)
    assert align_of(ty, LAYOUT) == 8
    # Sixteen bytes reached, two used by 'c', and the whole rounded to eight.
    assert size_of(ty, LAYOUT) == 24


def test_a_product_of_one_field_is_that_field() -> None:
    """A record of one field costs what the field costs."""
    ty = product(("value", U8))
    assert (size_of(ty, LAYOUT), align_of(ty, LAYOUT)) == (1, 1)
    assert offsets_of(ty, LAYOUT) == (0,)


def test_a_product_needs_no_padding_where_its_fields_are_ordered_well() -> None:
    """The same fields the other way round cost less, which is what a pass that
    reorders them would find."""
    wasteful = product(("a", U8), ("b", U64))
    thrifty = product(("b", U64), ("a", U8))
    assert size_of(wasteful, LAYOUT) == 16
    assert size_of(thrifty, LAYOUT) == 16
    assert offsets_of(wasteful, LAYOUT) == (0, 8)
    assert offsets_of(thrifty, LAYOUT) == (0, 8)


def test_a_sum_is_its_largest_variant_and_a_tag() -> None:
    """One variant at a time, so the room is the largest of them, and one byte
    beside it says which."""
    ty = a_sum(("small", U8), ("large", F64))
    assert align_of(ty, LAYOUT) == 8
    assert tag_offset_of(ty, LAYOUT) == 8
    assert size_of(ty, LAYOUT) == 16


def test_a_sum_of_narrow_variants_costs_one_byte_more_than_the_widest() -> None:
    """Nothing is rounded up to that is not asked for."""
    ty = a_sum(("a", U8), ("b", BOOL))
    assert (size_of(ty, LAYOUT), align_of(ty, LAYOUT)) == (2, 1)
    assert tag_offset_of(ty, LAYOUT) == 1


def test_a_variant_that_carries_nothing_takes_no_room() -> None:
    """Which is what an enumeration is: every variant `void`, so the whole of
    the value is the tag."""
    ty = a_sum(("red", VOID), ("green", VOID), ("blue", VOID))
    assert (size_of(ty, LAYOUT), align_of(ty, LAYOUT)) == (1, 1)
    assert tag_offset_of(ty, LAYOUT) == 0


def test_a_result_is_its_answer_and_one_byte() -> None:
    """The same shape as a sum, and not one: the error carries nothing."""
    ty = ResultType(U8)
    assert (size_of(ty, LAYOUT), align_of(ty, LAYOUT)) == (2, 1)
    assert tag_offset_of(ty, LAYOUT) == 1
    wide = ResultType(F64)
    assert (size_of(wide, LAYOUT), align_of(wide, LAYOUT)) == (16, 8)


def test_a_product_of_products_nests() -> None:
    """A field that is itself a record is laid out where its alignment says."""
    point = product(("x", F32), ("y", F32), name="Point")
    line = product(("from", point), ("to", point), name="Line")
    assert size_of(point, LAYOUT) == 8
    assert offsets_of(line, LAYOUT) == (0, 8)
    assert size_of(line, LAYOUT) == 16


def test_two_definitions_with_the_same_fields_are_two_types() -> None:
    """A definition says what a value *is*, so two records that happen to be
    laid out alike are not one type."""
    point = product(("x", F32), ("y", F32), name="Point")
    other = product(("x", F32), ("y", F32), name="Vector")
    assert point != other
    assert size_of(point, LAYOUT) == size_of(other, LAYOUT)
    from_here = ProductType((("x", F32),), name="P", origin="a.pl4g")
    from_there = ProductType((("x", F32),), name="P", origin="b.pl4g")
    assert from_here != from_there


def test_a_named_type_renders_as_its_name() -> None:
    """Which is what a diagnostic about one should say, not its fields."""
    assert product(("x", I32), name="Point").render() == "Point"
    assert a_sum(("a", I32), name="Choice").render() == "Choice"
    assert ProductType((("x", I32),)).render() == "{x: i32}"


def test_a_memory_token_has_no_layout() -> None:
    """It is not a value and is never in memory."""
    with pytest.raises(NoLayoutError):
        size_of(MemType(), LAYOUT)


# -- a member that is itself several values ------------------------------------

def test_a_tuple_is_its_leaves_and_not_its_members() -> None:
    """A member that is several values is that many parts.

    What a part *is* is one value in one register, so a member counted as one
    would be a member given one register and needing three -- which is how this
    went wrong for as long as it was wrong.
    """
    from pypl4g.ir.types import NARROWING, TupleType, parts_of

    held = TupleType((ResultType(U8, NARROWING), U8))
    assert parts_of(held) == (U8, BOOL, NARROWING, U8)


def test_and_a_record_is_too() -> None:
    """The two are one thing to everything below the front end, so a field that
    is a result spreads out where a member that is one does."""
    from pypl4g.ir.types import NARROWING, parts_of

    held = ProductType(
        (("first", ResultType(U8, NARROWING)), ("second", U8)), name="Holder")
    assert parts_of(held) == (U8, BOOL, NARROWING, U8)


def test_which_leaves_belong_to_which_member() -> None:
    """What reads a member out asks this: where its parts begin among the
    whole's, and how many of them to take."""
    from pypl4g.ir.types import NARROWING, TupleType, parts_within

    held = TupleType((U8, ResultType(U8, NARROWING), U8))
    assert parts_within(held) == ((0, 1), (1, 3), (4, 1))


def test_a_type_with_no_members_is_asked_nothing() -> None:
    """A whole number has no members to ask about, and answering with itself
    would be answering a question nobody asked."""
    from pypl4g.ir.types import parts_within

    assert parts_within(U8) == ()


@pytest.mark.parametrize("held", [
    ResultType(U8, VOID),
    ProductType((("a", U8), ("b", U64)), name="R"),
], ids=["result", "record"])
def test_every_part_has_an_offset(held) -> None:  # noqa: ANN001
    """The two are read together -- one says what the parts are and the other
    where each of them is -- so a shape that made them disagree would be read
    as a part written where another one is."""
    from pypl4g.ir.layout import part_offsets_of
    from pypl4g.ir.types import parts_of

    assert len(part_offsets_of(held, LAYOUT)) == len(parts_of(held))


def test_including_where_a_member_is_itself_several() -> None:
    """Which is the case the two most easily disagree about."""
    from pypl4g.ir.layout import part_offsets_of
    from pypl4g.ir.types import NARROWING, TupleType, parts_of

    held = TupleType((ResultType(U8, NARROWING), U8))
    assert len(part_offsets_of(held, LAYOUT)) == len(parts_of(held))
    assert len(sorted(set(part_offsets_of(held, LAYOUT)))) == 4
