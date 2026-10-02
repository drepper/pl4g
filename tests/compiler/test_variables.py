"""Variables: how they are written, what their type is, and where they live."""

from __future__ import annotations

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


def test_mutability_is_part_of_the_pointer_type() -> None:
    """A value is a value; it is the place that is writable or not."""
    module = Module("t")
    plain = GlobalVar("a", U8, module.types.ptr_type(U8))
    writable = GlobalVar("b", U8, module.types.ptr_type(U8, mutable=True))
    assert not plain.mutable and writable.mutable
    assert plain.ty.render() == "ptr<u8>"
    assert writable.ty.render() == "ptr<mut u8>"
    assert plain.value_type is writable.value_type, "the value's type is the same"


def test_a_variable_is_named_by_its_own_name() -> None:
    """Nothing distinguishes two variables but their names, so nothing is added."""
    module = Module("t")
    var = GlobalVar("counter", U8, module.types.ptr_type(U8))
    assert symbol_of(var) == "counter"
    var.module = "text.utf8"
    assert symbol_of(var) == "text.utf8.counter"


SOURCE = """let counter: u6 = 42u6
let touched: mut u6 = 7u6

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    touched \N{LEFTWARDS ARROW} counter
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
    """A variable that can be assigned to needs a section that can be written."""
    _, parsed, _ = image
    data = parsed.section(".data")
    assert data is not None
    assert data.sh_flags & 0x1, "the section holding a variable is not writable"
    assert data.sh_flags & 0x2, "the section holding a variable is not mapped"


def test_a_constant_is_in_a_read_only_section(image: tuple) -> None:
    """Not being assignable is a guarantee the image itself can keep.

    A variable that is not ``mut`` cannot change while the program runs, so it
    is put where the hardware refuses a write rather than where only the type
    checker does.
    """
    _, parsed, _ = image
    rodata = parsed.section(".rodata")
    assert rodata is not None, "a constant was not put in a read-only section"
    assert not rodata.sh_flags & 0x1, "a constant sits in a writable section"
    assert rodata.sh_flags & 0x2, "the section holding a constant is not mapped"


def test_each_kind_of_section_gets_a_segment_of_its_own(image: tuple) -> None:
    """Two segments sharing a page would have to share its permissions."""
    _, parsed, _ = image
    loads = [s for s in parsed.segments if s.p_type == elfcheck.PT_LOAD]
    assert len(loads) == 3
    rodata, text, data = loads
    assert rodata.p_flags == elfcheck.PF_R, "the constants are writable or executable"
    assert text.p_flags & elfcheck.PF_X and not text.p_flags & elfcheck.PF_W
    assert data.p_flags & elfcheck.PF_W and not data.p_flags & elfcheck.PF_X
    page = loads[0].p_align
    pages = [s.p_vaddr // page for s in loads]
    assert len(set(pages)) == len(pages), "two segments share a page"
    for segment in loads:
        assert segment.p_offset % segment.p_align == segment.p_vaddr % segment.p_align


def test_the_value_is_in_the_image(image: tuple) -> None:
    """The byte the program named is the byte the file holds."""
    _, parsed, _ = image
    rodata = parsed.section(".rodata")
    assert rodata is not None
    assert parsed.data[rodata.sh_offset:rodata.sh_offset + 1] == b"\x2a"
    data = parsed.section(".data")
    assert data is not None
    assert parsed.data[data.sh_offset:data.sh_offset + 1] == b"\x07"


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


@pytest.mark.parametrize("triple", compiler_targets())
def test_more_than_one_value_at_a_time_now_works(triple: str, tmp_path) -> None:  # noqa: ANN001
    """What the single-register limitation used to refuse, on every target.

    Every value the compiler produced went to the register a result is returned
    in, so a function wanting two at once was reported rather than compiled
    wrongly.  The allocator is what lifted that, and this is the case it lifted.
    """
    source = tmp_path / "t.pl4g"
    source.write_text("let a: u6 = 1u6\nlet b: u6 = 2u6\n"
                      "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
                      "    @[ignore(4006)]\n    let first: u6 = a\n"
                      "    @[ignore(4006)]\n    let second: u6 = b\n    first\n",
                      encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)), str(source)])
    assert proc.returncode == 0, describe(proc)
    ran = subprocess.run([*runner_for(triple), str(output)], capture_output=True,
                         timeout=60)
    assert ran.returncode == 1, describe(ran)


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
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    let v: ", declared,
        " = ", value, "\n    1u6\n")))
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
        "\n@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    1u6\n")))
    assert proc.returncode == 0, describe(proc)


# -- changing a variable -------------------------------------------------------

ASSIGN = """let counter: mut u6 = 1u6

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    counter \N{LEFTWARDS ARROW} 7u6
    counter
"""


def test_a_local_assignment_writes_nothing(compile_source, tmp_path) -> None:  # noqa: ANN001
    """A local is a value, so changing it binds the name to a new one.

    Nothing reaches memory, and the whole function folds to its result.
    """
    proc, output = compile_source(
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    let x: mut u6 = 3u6\n    x \N{LEFTWARDS ARROW} 5u6\n    x\n",
        "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "ret.u6 5" in text, text
    assert "store" not in text and "mem.start" not in text, text


def test_a_global_assignment_is_a_store_the_token_orders(compile_source,  # noqa: ANN001
                                                         tmp_path) -> None:  # noqa: ANN001
    """A variable at the top level is an address, so changing it writes memory."""
    proc, output = compile_source(ASSIGN, "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "let @counter: mut u6" in text, text
    assert "%0 = mem.start" in text, text
    assert "%1 = store.u6 %0, @counter, 7" in text, text
    assert "%2 = load.u6 %1, @counter" in text, "the read is not ordered after the write"


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_change_is_visible_when_it_is_read_back(triple: str, tmp_path) -> None:  # noqa: ANN001
    """What was written is what is read, on every target."""
    source = tmp_path / "t.pl4g"
    source.write_text(ASSIGN, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "-O1", "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    runner = runner_for(triple)
    ran = subprocess.run([*runner, str(output)], capture_output=True, timeout=60)
    assert ran.returncode == 7, describe(ran)


def test_the_verifier_refuses_a_store_into_something_immutable() -> None:
    """The rule holds in the representation too, not only in the source."""
    from pypl4g.diag.engine import InternalError
    from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
    from pypl4g.ir.inst import MemStartInst, RetInst, StoreInst
    from pypl4g.ir.verify import verify

    module = Module("t")
    var = GlobalVar("c", U8, module.types.ptr_type(U8), module.int_const(U8, 1))
    module.add_global(var)
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    block = func.add_block()
    token = block.append(MemStartInst())
    block.append(StoreInst(token, var, module.int_const(U8, 7)))
    block.append(RetInst(module.int_const(U8, 0)))
    module.add_function(func)
    module.startup = func
    with pytest.raises(InternalError, match="does not allow it"):
        verify(module)

    # The same module with the variable's pointer allowing the write.
    var.ty = module.types.ptr_type(U8, mutable=True)
    verify(module)


# -- an assignment stands for the variable it changed ---------------------------

def test_an_assignment_is_the_result_when_it_is_last(compile_source) -> None:  # noqa: ANN001
    """A read follows the write, and the token is what puts it after."""
    proc, output = compile_source(
        "let counter: mut u6 = 1u6\n@[startup, impure]\n"
        "fn main() \N{RIGHTWARDS ARROW} u6:\n    counter \N{LEFTWARDS ARROW} 42u6\n",
        "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "%1 = store.u6 %0, @counter, 42" in text, text
    assert "%2 = load.u6 %1, @counter" in text, text
    assert "ret.u6 %2" in text, text


def test_a_discarded_assignment_reads_nothing_back(compile_source) -> None:  # noqa: ANN001
    """Reading a place nothing looks at would be an instruction nobody asked for."""
    proc, output = compile_source(
        "let counter: mut u6 = 1u6\n@[startup, impure]\n"
        "fn main() \N{RIGHTWARDS ARROW} u6:\n    counter \N{LEFTWARDS ARROW} 42u6\n"
        "    3u6\n", "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "store.u6" in text, text
    assert "load" not in text, text


def test_a_local_assignment_as_the_result_touches_no_memory(compile_source) -> None:  # noqa: ANN001
    """A local is a value, so there is nothing to read back."""
    proc, output = compile_source(
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    let status: mut u6 = 3u6\n    status \N{LEFTWARDS ARROW} 9u6\n",
        "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "ret.u6 9" in text, text
    assert "store" not in text and "load" not in text, text


@pytest.mark.parametrize("source", [
    "let v: mut u6 = 1u6",
    "let v: mut = 1u6",
])
def test_mut_stands_where_the_type_does(compile_source, source: str) -> None:  # noqa: ANN001
    """Either part after the colon may be left out; the qualifier has a place."""
    proc, _ = compile_source("".join((
        source, "\n@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    v \N{LEFTWARDS ARROW} 5u6\n")))
    assert proc.returncode == 0, describe(proc)


def test_mut_before_the_name_is_no_longer_the_syntax(compile_source) -> None:  # noqa: ANN001
    """It qualifies the type, so it does not stand before the name."""
    proc, _ = compile_source(
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    let mut v: u6 = 1u6\n    v\n")
    assert proc.returncode != 0


# -- a variable the whole program never reads -----------------------------------

WRITTEN_ONLY = """let counter: mut u6 = 1u6

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    counter \N{LEFTWARDS ARROW} 7u6
    3u6
"""


def test_a_variable_the_program_only_writes_is_reported(compile_source) -> None:  # noqa: ANN001
    """The rule that catches an unread value in a function, asked of the program."""
    proc, _ = compile_source(WRITTEN_ONLY)
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4007]" in proc.stderr, proc.stderr
    assert "never reads it" in proc.stderr


def test_reading_it_anywhere_is_enough(compile_source) -> None:  # noqa: ANN001
    """A variable at the top level can be named from any function, so one read
    from anywhere answers the question for the whole program."""
    proc, _ = compile_source(
        "let counter: mut u6 = 1u6\n\n"
        "@[export]\nfn peek() \N{RIGHTWARDS ARROW} u6:\n    counter\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    counter \N{LEFTWARDS ARROW} 7u6\n    3u6\n")
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4007]" not in proc.stderr, proc.stderr


def test_an_exported_variable_is_never_reported(compile_source) -> None:  # noqa: ANN001
    """Something outside this compilation may read it, so nothing here can say
    that nothing does."""
    proc, _ = compile_source(
        "@[export]\nlet counter: mut u6 = 1u6\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    counter \N{LEFTWARDS ARROW} 7u6\n    3u6\n")
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4007]" not in proc.stderr, proc.stderr


def test_a_variable_nothing_writes_either_is_not_reported(compile_source) -> None:  # noqa: ANN001
    """It is dropped rather than reported: there is no write to call pointless."""
    proc, output = compile_source(
        "let unused: u6 = 1u6\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    3u6\n", "--emit=ir")
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4007]" not in proc.stderr, proc.stderr
    assert "@unused" not in output.read_text(encoding="utf-8")


def test_the_definition_may_say_it_expects_it(compile_source) -> None:  # noqa: ANN001
    """The attribute stands where a reader would write it, on the definition,
    although the diagnostic is only discovered once the program is whole."""
    proc, _ = compile_source("".join(("@[expect(4007)]\n", WRITTEN_ONLY)))
    assert proc.returncode == 0, describe(proc)
    assert "[PL4G-4007]" not in proc.stderr, proc.stderr
    assert "[PL4G-3206]" not in proc.stderr, "the assertion was called stale"


def test_an_assertion_nothing_meets_is_still_reported(compile_source) -> None:  # noqa: ANN001
    """Carrying the expectation that far must not make it impossible to fail."""
    proc, _ = compile_source(
        "@[expect(4007)]\nlet counter: mut u6 = 1u6\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    counter \N{LEFTWARDS ARROW} 7u6\n")
    assert proc.returncode != 0
    assert "[PL4G-3206]" in proc.stderr, proc.stderr


# -- truth values ---------------------------------------------------------------

def test_a_boolean_holds_one_byte_which_is_one_or_zero() -> None:
    """The language says the type has two values; the width is the compiler's."""
    module = Module("t")
    yes = GlobalVar("y", BOOL, module.types.ptr_type(BOOL), module.bool_const(BOOL, True))
    no = GlobalVar("n", BOOL, module.types.ptr_type(BOOL), module.bool_const(BOOL, False))
    assert size_of(BOOL, LAYOUT) == 1 and align_of(BOOL, LAYOUT) == 1
    assert initial_bytes(yes, LAYOUT) == b"\x01"
    assert initial_bytes(no, LAYOUT) == b"\x00"


@pytest.mark.parametrize(("source", "found"), [
    ("let flag: bool = 1u8\n", "u8"),
    ("let flag: bool = 1\n", "integer"),
    ("let n: u8 = true\n", "bool"),
])
def test_nothing_but_true_and_false_is_a_boolean(compile_source, source: str,  # noqa: ANN001
                                                 found: str) -> None:
    """A number is not a truth value spelled differently, in either direction.

    It is reported as the mismatch it is.  A literal the declared type has no use
    for used to fall through to "not implemented", which said the compiler was
    unfinished where the program was simply wrong.
    """
    proc, _ = compile_source("".join((
        source, "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    1u6\n")))
    assert proc.returncode != 0, proc.stdout
    assert "[PL4G-4203]" in proc.stderr, proc.stderr
    assert "".join(("of type '", found, "'")) in proc.stderr, proc.stderr


def test_a_type_already_reported_says_nothing_more_about_the_value(compile_source) -> None:  # noqa: ANN001
    """One mistake, one message: the unknown type is not also a bad initializer."""
    proc, _ = compile_source(
        "let v: nosuchtype = 1u6\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    1u6\n")
    assert proc.returncode != 0
    assert proc.stderr.count("[PL4G-") == 1, proc.stderr
    assert "[PL4G-4201]" in proc.stderr, proc.stderr


def test_a_boolean_constant_is_read_only_and_a_mutable_one_is_not(tmp_path) -> None:  # noqa: ANN001
    """A truth value is placed by the same rule as any other constant."""
    source = tmp_path / "t.pl4g"
    source.write_text("let ready: bool = true\n"
                      "let seen: mut bool = false\n"
                      "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
                      "    seen \N{LEFTWARDS ARROW} ready\n    1u6\n", encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), str(source)])
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    rodata = parsed.section(".rodata")
    data = parsed.section(".data")
    assert rodata is not None and data is not None
    assert parsed.data[rodata.sh_offset] == 1, "true is not one in the image"
    assert parsed.data[data.sh_offset] == 0, "false is not zero in the image"



def test_the_memory_chain_starts_in_the_entry_block(compile_source,  # noqa: ANN001
                                                    tmp_path) -> None:  # noqa: ANN001
    """`mem.start` goes where the function begins, not where memory is first asked for.

    It says nothing and depends on nothing, so where it belongs is the top of the
    entry block.  Asked for inside an arm of an `if` and left there, it would be a
    value the other arm does not reach -- and every later use of memory, the branch
    into a loop among them, would be reading something that does not dominate it.
    """
    proc, output = compile_source(
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    let k: u6 = if true { 5u6 } else { 6u6 }\n"
        "    let a: u6? = while \N{SECTION SIGN}x true:\n"
        "        break \N{SECTION SIGN}x 1u6\n"
        "    k + (a ?? 0u6)\n",
        "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    entry = text.split("block0:", 1)[1].split("\n\n", 1)[0]
    assert "mem.start" in entry, text


def test_an_answer_of_four_parts_goes_through_the_callers_storage(  # noqa: ANN001
        compile_source, tmp_path) -> None:  # noqa: ANN001
    """The pass gives the function a place to write into and the caller makes it.

    The style is the function's own property, so what decides this is the callee
    and nothing else: the signature grows a pointer, the answer becomes nothing,
    and the call hands over a place it made for the purpose.
    """
    proc, output = compile_source(
        "fn three() \N{RIGHTWARDS ARROW} \N{LEFT ANGLE BRACKET}u8, u8, u8, u8\N{RIGHT ANGLE BRACKET}:\n"
        "    \N{LEFT ANGLE BRACKET}1u8, 2u8, 3u8, 4u8\N{RIGHT ANGLE BRACKET}\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    let t: \N{LEFT ANGLE BRACKET}u8, u8, u8, u8\N{RIGHT ANGLE BRACKET} = three()\n"
        "    if t\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 1u8:\n        0u6\n    else:\n        1u6\n",
        "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "fn @three(ptr<mut \N{LEFT ANGLE BRACKET}u8, u8, u8, u8\N{RIGHT ANGLE BRACKET}>) \N{RIGHTWARDS ARROW} void" in text, text
    # It writes through the pointer now, which the textual form says: a call to
    # it changes memory that outlives it, so a form that left it out would read
    # back as a module where the call may be moved and repeated.  What it does
    # not say is impure -- what the function writes is the place it was handed
    # and nothing else, and a caller that reads none of that place may still
    # drop the call.
    assert "\N{RIGHTWARDS ARROW} void internal cconv(pl4g) answer-in-storage" \
        in text, text
    assert " impure" not in text.split("fn @main")[0], text
    assert "frame.ptr<mut \N{LEFT ANGLE BRACKET}u8, u8, u8, u8\N{RIGHT ANGLE BRACKET}>" in text, text
    assert "ret.\N{LEFT ANGLE BRACKET}" not in text, text


def test_an_answer_of_two_parts_stays_in_registers(compile_source,  # noqa: ANN001
                                                   tmp_path) -> None:  # noqa: ANN001
    """Two is what every style answers in registers, so nothing is rewritten."""
    proc, output = compile_source(
        "fn two() \N{RIGHTWARDS ARROW} \N{LEFT ANGLE BRACKET}u8, u8\N{RIGHT ANGLE BRACKET}:\n"
        "    \N{LEFT ANGLE BRACKET}1u8, 2u8\N{RIGHT ANGLE BRACKET}\n\n"
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    let t: \N{LEFT ANGLE BRACKET}u8, u8\N{RIGHT ANGLE BRACKET} = two()\n"
        "    if t\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = 1u8:\n        0u6\n    else:\n        1u6\n",
        "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "fn @two() \N{RIGHTWARDS ARROW} \N{LEFT ANGLE BRACKET}u8, u8\N{RIGHT ANGLE BRACKET}" in text, text
    assert "frame.ptr" not in text, text


def test_a_string_answer_stays_in_registers(compile_source,  # noqa: ANN001
                                           tmp_path) -> None:  # noqa: ANN001
    """A string is three words, and the language's own convention answers three.

    Where, how many, and the allocator: what a great many small functions answer,
    so it travels as it is made and not through the caller's storage.
    """
    proc, output = compile_source(
        "fn named(c: bool) \N{RIGHTWARDS ARROW} str:\n"
        "    if c: \"x\" else: \"y\" \N{DOUBLE PLUS} \"z\"\n\n"
        "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n"
        "    if named(true) = \"x\": 0u6 else: 1u6\n",
        "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "fn @named(bool) \N{RIGHTWARDS ARROW} str" in text, text
    assert "answer-in-storage" not in text, text


#: A call whose answer goes through the caller's storage and whose answer the
#: folder then makes nobody's.  `three` is held away from the inliner, so what
#: is left is a call; `pick` is not, so the condition it branches on is settled
#: once it stands in `main` and the arm that reads the answer goes with it.
UNREAD = """\
@[inline(never)]
fn three() \N{RIGHTWARDS ARROW} \N{LEFT ANGLE BRACKET}u8, u8, u8, u8\N{RIGHT ANGLE BRACKET}:
    \N{LEFT ANGLE BRACKET}1u8, 2u8, 3u8, 4u8\N{RIGHT ANGLE BRACKET}

fn pick(c: bool) \N{RIGHTWARDS ARROW} u8:
    let t: \N{LEFT ANGLE BRACKET}u8, u8, u8, u8\N{RIGHT ANGLE BRACKET} = three()
    if c:
        t\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}
    else:
        7u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(pick({0}), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 1u6
"""


def test_a_call_answering_through_storage_goes_where_nothing_reads_it(
        compile_source) -> None:  # noqa: ANN001
    """The room, the call and the function itself.

    What the function writes is the place it was handed and nothing else, so a
    caller that reads none of that place is a caller for whom the call changes
    nothing anyone can see -- and the room goes with the call, having then no
    user at all.
    """
    proc, output = compile_source(UNREAD.format("false"), "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "frame." not in text, text
    assert "call " not in text, text
    assert "fn @three" not in text, text


def test_and_stays_where_something_does(compile_source) -> None:  # noqa: ANN001
    """The other half of it, which is what makes the first half more than a
    pass that removes calls."""
    proc, output = compile_source(UNREAD.format("true"), "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "frame." in text, text
    assert "call " in text, text
    assert "fn @three" in text, text


def test_a_function_the_program_called_impure_keeps_its_call(
        compile_source) -> None:  # noqa: ANN001
    """The rewrite says what the function does with the place it was handed; it
    says nothing about what the program already said the function does."""
    source = UNREAD.format("false").replace(
        "@[inline(never)]\nfn three", "@[inline(never), impure]\nfn three")
    # A pure function may not call one that changes what outlives it, so the
    # two that call this one say so as well.
    source = source.replace("fn pick(", "@[impure]\nfn pick(")
    source = source.replace("@[startup]", "@[startup, impure]")
    proc, output = compile_source(source, "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "call " in text, text
    assert "fn @three" in text, text
