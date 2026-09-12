"""Variables: how they are written, what their type is, and where they live."""

import subprocess

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler, runner_for
from pypl4g.ir.layout import DataLayout, NoLayoutError, align_of, size_of
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import (BOOL, I32, MEM, ProductType, TypeContext, U8, U16,
                             U32, U64, VOID)
from pypl4g.target.globals import initial_bytes, symbol_of

LAYOUT = DataLayout(pointer_size=8)
TYPES = TypeContext()


@pytest.mark.parametrize(("ty", "size", "alignment"), [
    (U8, 1, 1), (U16, 2, 2), (U32, 4, 4), (U64, 8, 8), (I32, 4, 4),
    (BOOL, 1, 1), (VOID, 0, 1),
])
def test_layout_of_the_primitive_types(ty: object, size: int, alignment: int) -> None:
    """A value takes as much room as its width says, and starts on it."""
    assert size_of(ty, LAYOUT) == size  # type: ignore[arg-type]
    assert align_of(ty, LAYOUT) == alignment  # type: ignore[arg-type]


def test_a_pointer_is_as_wide_as_the_target_says() -> None:
    """Layout is computed against a target, which is why it is not in the type."""
    pointer = TYPES.ptr_type(U8)
    assert size_of(pointer, DataLayout(pointer_size=8)) == 8
    assert size_of(pointer, DataLayout(pointer_size=4)) == 4


def test_a_product_type_is_laid_out_in_declaration_order_for_now() -> None:
    """Reordering it is permitted and belongs here, where no type has to change."""
    record = ProductType((("flag", U8), ("count", U32)))
    assert align_of(record, LAYOUT) == 4
    assert size_of(record, LAYOUT) == 8, "the small field is padded out to the large one"


def test_a_memory_token_has_no_layout() -> None:
    """It orders operations; it is not a thing in memory."""
    with pytest.raises(NoLayoutError):
        align_of(MEM, LAYOUT)


@pytest.mark.parametrize(("ty", "value", "expected"), [
    (U8, 42, b"\x2a"),
    (U16, 40000, b"\x40\x9c"),
    (U32, 3000000000, b"\x00\x5e\xd0\xb2"),
    (U64, 42, b"\x2a\x00\x00\x00\x00\x00\x00\x00"),
])
def test_a_variable_starts_out_holding_its_value(ty: object, value: int,
                                                 expected: bytes) -> None:
    """The bytes in the image are the value the program gave."""
    module = Module("t")
    var = GlobalVar("v", ty, module.types.ptr_type(ty),  # type: ignore[arg-type]
                    module.int_const(ty, value))  # type: ignore[arg-type]
    assert initial_bytes(var, LAYOUT) == expected


def test_a_variable_is_named_by_its_own_name() -> None:
    """Nothing distinguishes two variables but their names, so nothing is added."""
    module = Module("t")
    var = GlobalVar("counter", U8, module.types.ptr_type(U8))
    assert symbol_of(var) == "counter"
    var.module = "text.utf8"
    assert symbol_of(var) == "text.utf8.counter"


SOURCE = """let counter: u8 = 42u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    counter
"""


@pytest.fixture(scope="module", params=compiler_targets())
def image(request: pytest.FixtureRequest,
          tmp_path_factory: pytest.TempPathFactory) -> tuple[str, elfcheck.Image, object]:
    """Compile a program with a variable, for one target."""
    triple = str(request.param)
    directory = tmp_path_factory.mktemp("vars")
    source = directory / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = directory / "out"
    proc = run_compiler(["-o", str(output), "-O1", "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    return triple, elfcheck.parse(output.read_bytes()), output


def test_the_variable_is_in_a_writable_section(image: tuple) -> None:
    """A variable is writable even while nothing can yet assign to one."""
    _, parsed, _ = image
    data = parsed.section(".data")
    assert data is not None
    assert data.sh_flags & 0x1, "the section holding a variable is not writable"
    assert data.sh_flags & 0x2, "the section holding a variable is not mapped"


def test_the_writable_segment_is_separate_and_not_executable(image: tuple) -> None:
    """Two segments sharing a page would have to share its permissions."""
    _, parsed, _ = image
    loads = [s for s in parsed.segments if s.p_type == elfcheck.PT_LOAD]
    assert len(loads) == 2
    text, data = loads
    assert text.p_flags & elfcheck.PF_X and not text.p_flags & elfcheck.PF_W
    assert data.p_flags & elfcheck.PF_W and not data.p_flags & elfcheck.PF_X
    page = text.p_align
    assert text.p_vaddr // page != data.p_vaddr // page, "they share a page"
    assert data.p_offset % data.p_align == data.p_vaddr % data.p_align


def test_the_value_is_in_the_image(image: tuple) -> None:
    """The byte the program named is the byte the file holds."""
    _, parsed, _ = image
    data = parsed.section(".data")
    assert data is not None
    assert parsed.data[data.sh_offset:data.sh_offset + 1] == b"\x2a"


def test_the_variable_has_a_symbol_of_object_kind(image: tuple) -> None:
    """A debugger and a later incremental build both find it by name."""
    _, parsed, _ = image
    symbol = parsed.symbol("counter")
    assert symbol is not None
    assert symbol.kind == 1, "the symbol does not say it names an object"
    assert symbol.size == 1


def test_the_program_reads_it(image: tuple) -> None:
    """Reading a variable gives what it holds, on every target."""
    triple, _, output = image
    runner = runner_for(triple)
    proc = subprocess.run([*runner, str(output)], capture_output=True, timeout=60)
    assert proc.returncode == 42, describe(proc)


def test_more_than_one_value_at_a_time_is_refused(tmp_path) -> None:  # noqa: ANN001
    """Without a register allocator, a function needing two at once is reported.

    It is reported rather than compiled wrongly, which is the only honest thing
    to do while every value the compiler produces goes to the same register.
    """
    source = tmp_path / "t.pl4g"
    source.write_text("let a: u8 = 1u8\nlet b: u8 = 2u8\n"
                      "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n"
                      "    let first: u8 = a\n    let second: u8 = b\n    first\n",
                      encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(source)])
    assert proc.returncode != 0
    assert "[PL4G-8501]" in proc.stderr, proc.stderr
    assert "register allocator" in proc.stderr


# -- nothing narrows a value without saying so ---------------------------------

def test_encoding_a_value_that_does_not_fit_is_refused() -> None:
    """A wrapped value would make a program start with a number it never named."""
    from pypl4g.ir.layout import ValueOutOfRangeError, encode_scalar

    assert encode_scalar(255, U8, LAYOUT) == b"\xff"
    assert encode_scalar(-128, I32, LAYOUT) == b"\x80\xff\xff\xff"
    with pytest.raises(ValueOutOfRangeError):
        encode_scalar(256, U8, LAYOUT)
    with pytest.raises(ValueOutOfRangeError):
        encode_scalar(-129, U8, LAYOUT)


def test_a_variable_whose_value_does_not_fit_is_refused() -> None:
    """The same holds for the bytes a variable starts out with."""
    from pypl4g.ir.layout import ValueOutOfRangeError

    module = Module("t")
    var = GlobalVar("v", U8, module.types.ptr_type(U8), module.int_const(U8, 300))
    with pytest.raises(ValueOutOfRangeError):
        initial_bytes(var, LAYOUT)


def test_an_immediate_that_does_not_fit_its_field_is_refused() -> None:
    """An instruction must not come to mean a number the program never named."""
    from pypl4g.mc.desc import InstrTable
    from pypl4g.mc.inst import MCInst
    from pypl4g.mc.operand import MCImm, MCReg
    from pypl4g.target.x86_64.encoder import EncodingError, encode
    from pypl4g.target.x86_64.opcodes import X86_INSTRS
    from pypl4g.target.x86_64.regs import reg

    table = InstrTable(X86_INSTRS)
    operands = (MCReg(reg("eax")), MCImm(1 << 33, 32, signed=False))
    with pytest.raises(EncodingError, match="does not fit"):
        encode(MCInst(table.select("mov", operands), operands))


def test_a_displacement_that_does_not_fit_is_refused() -> None:
    """A branch stored with its upper bits dropped would go somewhere else."""
    from pypl4g.mc.fixup import (PCREL32, FixupRangeError, MCFixup,
                                 apply_little_endian)
    from pypl4g.mc.operand import ConstExpr

    data = bytearray(4)
    fixup = MCFixup(offset=0, kind=PCREL32, target=ConstExpr(0))
    apply_little_endian(data, 0, fixup, -1)
    assert bytes(data) == b"\xff\xff\xff\xff"
    with pytest.raises(FixupRangeError):
        apply_little_endian(data, 0, fixup, 1 << 33)


@pytest.mark.parametrize(("declared", "value"), [
    ("u8", "256u8"), ("u8", "300"), ("i8", "128i8"), ("u16", "65536u16"),
    ("i16", "32768i16"), ("u32", "4294967296u32"),
])
def test_an_oversized_initializer_is_refused(compile_source, declared: str,  # noqa: ANN001
                                             value: str) -> None:
    """No value is quietly narrowed to fit the variable it is given to."""
    proc, _ = compile_source("".join((
        "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n    let v: ", declared,
        " = ", value, "\n    1u8\n")))
    assert proc.returncode != 0, proc.stdout
    assert "[PL4G-2006]" in proc.stderr, proc.stderr
    assert "does not fit" in proc.stderr


@pytest.mark.parametrize(("declared", "value"), [
    ("u8", "255u8"), ("i8", "127i8"), ("u16", "65535u16"),
    ("u64", "18446744073709551615u64"),
])
def test_the_largest_value_of_a_type_is_accepted(compile_source, declared: str,  # noqa: ANN001
                                                 value: str) -> None:
    """The boundary is where the type says, not one short of it."""
    proc, _ = compile_source("".join((
        "let v: ", declared, " = ", value,
        "\n@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n    1u8\n")))
    assert proc.returncode == 0, describe(proc)
