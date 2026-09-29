"""How a type is written where a reader sees it.

Two spellings, and the difference between them is who is reading.  The IR has
its own -- `ptr<mut Pair>` -- and it is read back as well as written, the
printer and the reader being two halves of one format.  A person reading a
diagnostic wrote `&mut Pair` and has never seen the other, so that is what a
message says.
"""

from __future__ import annotations

import pytest

from pypl4g.ir.types import I32, STR, TypeContext, U8, U64

#: A program whose every mistake is about a type, so that what a message calls
#: one is what is being checked.
PAIR = """\
type Pair = a : u8 ; b : u8
"""


@pytest.fixture
def types() -> TypeContext:
    """A context to make types in, which is all these need."""
    return TypeContext()


def test_a_reference_is_written_the_way_a_program_writes_one(
        types: TypeContext) -> None:
    """`&mut T`, and `ptr<mut T>` only in the IR."""
    held = types.ptr_type(U8, mutable=True)
    assert held.written() == "&mut u8"
    assert held.render() == "ptr<mut u8>"


def test_a_lasting_reference_says_so_where_the_program_does(
        types: TypeContext) -> None:
    """`static` stands where `mut` does, and in the same order."""
    assert types.ptr_type(U8, lasting=True).written() == "&static u8"
    assert types.ptr_type(U8, mutable=True,
                          lasting=True).written() == "&mut static u8"


def test_a_result_is_written_with_its_spaces(types: TypeContext) -> None:
    """`T ? E`, which is how a signature writes one, and `T?` where the error
    carries nothing."""
    assert types.result_type(U64, I32).written() == "u64 ? i32"
    assert types.result_type(U64).written() == "u64?"
    assert types.result_type(U64, I32).render() == "u64?i32"


def test_what_a_type_holds_is_written_the_same_way(types: TypeContext) -> None:
    """It goes all the way down: a list of references says `&mut u8` inside."""
    held = types.ptr_type(U8, mutable=True)
    assert types.list_type(held).written() == "[&mut u8]"
    assert types.array_type(held, (4,)).written() == "&mut u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}4\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"
    assert types.tuple_type((held, U8)).written() == "\N{LEFT ANGLE BRACKET}&mut u8, u8\N{RIGHT ANGLE BRACKET}"
    assert types.dict_type(STR, held).written() == \
        "\N{LEFT DOUBLE PARENTHESIS}str: &mut u8\N{RIGHT DOUBLE PARENTHESIS}"
    assert types.result_type(held, I32).written() == "&mut u8 ? i32"


def test_a_type_a_program_cannot_write_says_what_it_is(
        types: TypeContext) -> None:
    """A cursor has no spelling, so a message says what it is rather than a
    spelling nobody could have written."""
    assert types.cursor_type(U8).written() == "cursor over [u8]"


def test_most_types_are_written_as_the_ir_renders_them(
        types: TypeContext) -> None:
    """The IR borrowed the language's notation wherever it could, so the two
    agree except where something above says they do not."""
    for one in (U8, U64, STR, types.list_type(U8), types.set_type(U8),
                types.array_type(U8, (2, 3)), types.tuple_type((U8, U64))):
        assert one.written() == one.render()


def test_a_message_about_a_reference_says_what_the_source_said(
        compile_source) -> None:  # noqa: ANN001
    """The whole point of the two spellings, asked of the compiler itself."""
    proc, _ = compile_source(PAIR + """\
fn takes(p: &mut Pair) \N{RIGHTWARDS ARROW} u8:
    p\N{POSITION INDICATOR}.a

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let p: mut Pair = Pair(.a \N{LEFTWARDS ARROW} 1u8, .b \N{LEFTWARDS ARROW} 2u8)
    let r: &Pair = &p
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(takes(r), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""")
    assert proc.returncode != 0
    assert "'&mut Pair'" in proc.stderr
    assert "'&Pair'" in proc.stderr
    assert "ptr<" not in proc.stderr


def test_a_message_about_a_result_says_what_the_source_said(
        compile_source) -> None:  # noqa: ANN001
    """A result reads as a signature writes one, spaces and all."""
    proc, _ = compile_source("""\
fn answers() \N{RIGHTWARDS ARROW} u64 ? i32:
    1u64

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let n: u64 = answers()
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(n, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""")
    assert proc.returncode != 0
    assert "'u64 ? i32'" in proc.stderr


def test_the_ir_keeps_its_own_spelling(compile_source) -> None:  # noqa: ANN001
    """Which is why this is a second method and not a change to the first: the
    printer and the reader are two halves of one format."""
    proc, output = compile_source(PAIR + """\
fn takes(p: &mut Pair) \N{RIGHTWARDS ARROW} u8:
    p\N{POSITION INDICATOR}.a

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let p: mut Pair = Pair(.a \N{LEFTWARDS ARROW} 1u8, .b \N{LEFTWARDS ARROW} 2u8)
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(takes(&mut p), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
""", "--emit=ir")
    assert proc.returncode == 0, proc.stderr
    assert "ptr<mut Pair>" in output.read_text(encoding="utf-8")
