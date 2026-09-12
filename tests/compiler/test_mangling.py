"""Function name mangling.

The mangled name is the signature written out.  Nothing is encoded, so these
tests can simply state the expected string, and so can every tool that shows a
symbol.
"""

import subprocess

import pytest

from conftest import describe, run_compiler
from pypl4g.diag.engine import InternalError
from pypl4g.ir.function import FuncAttrs, Function
from pypl4g.ir.inst import RetInst
from pypl4g.ir.mangle import mangle, symbol_name
from pypl4g.ir.module import Module
from pypl4g.ir.types import (BOOL, F32, F64, I32, I64, ProductType, SumType,
                             TypeContext, U8, U64, VOID)
from pypl4g.ir.verify import verify

TYPES = TypeContext()


def function(name: str, params: tuple, ret: object, *, module: str = "",
             abi: str | None = None) -> Function:
    """Build a function with the given signature."""
    func = Function(name, TYPES.func_type(params, ret),  # type: ignore[arg-type]
                    FuncAttrs(abi=abi))
    func.module = module
    return func


@pytest.mark.parametrize(("params", "ret", "expected"), [
    ((), U8, "f()u8"),
    ((I32,), I32, "f(i32)i32"),
    ((I32, I32), I32, "f(i32,i32)i32"),
    ((), VOID, "f()void"),
    ((BOOL, F32, F64, I64, U64), VOID, "f(bool,f32,f64,i64,u64)void"),
    ((TYPES.ptr_type(U8), U64), VOID, "f(ptr<u8>,u64)void"),
    ((TYPES.ptr_type(TYPES.ptr_type(U8)),), VOID, "f(ptr<ptr<u8>>)void"),
    ((TYPES.func_type((I32,), I32), I32), I32, "f(fn(i32)i32,i32)i32"),
    ((), TYPES.ptr_type(I32), "f()ptr<i32>"),
    ((ProductType((("x", I32), ("y", I32))),), VOID, "f({x:i32,y:i32})void"),
    ((SumType((("ok", I32), ("err", U8))),), VOID, "f(<ok:i32|err:u8>)void"),
])
def test_the_symbol_is_the_signature(params: tuple, ret: object,
                                     expected: str) -> None:
    """A symbol reads as the signature it stands for."""
    assert symbol_name(function("f", params, ret)) == expected


def test_a_module_name_prefixes_the_symbol() -> None:
    """A function is named within the module that holds it."""
    assert symbol_name(function("helper", (I32,), VOID, module="text.utf8")) == \
        "text.utf8.helper(i32)void"


def test_the_return_type_is_part_of_the_symbol() -> None:
    """Two functions differing only in result are different symbols.

    This is what will let a result take part in overload resolution; nothing
    depends on it yet, but the name already distinguishes them.
    """
    assert symbol_name(function("f", (I32,), I32)) != \
        symbol_name(function("f", (I32,), U8))


def test_a_foreign_function_keeps_its_name() -> None:
    """Declaring a foreign convention is declaring how others already know it."""
    assert symbol_name(function("write", (I32,), I64, abi="sysv64")) == "write"
    assert symbol_name(function("write", (I32,), I64, abi="sysv64", module="io")) == \
        "write"


def test_nothing_is_encoded() -> None:
    """There is no substitution or compression to undo, so no demangler exists."""
    symbol = symbol_name(function("f", (TYPES.ptr_type(U8), TYPES.ptr_type(U8)), VOID))
    assert symbol == "f(ptr<u8>,ptr<u8>)void"
    assert "ptr<u8>" in symbol, "a repeated type is written out, not referred back to"


def test_mangling_is_a_pure_function_of_the_signature() -> None:
    """Every stage computes the same symbol without anything being stored."""
    ty = TYPES.func_type((I32,), U8)
    assert mangle("f", ty) == symbol_name(function("f", (I32,), U8))
    assert mangle("f", ty, "m") == symbol_name(function("f", (I32,), U8, module="m"))


def test_the_verifier_catches_two_functions_under_one_symbol() -> None:
    """Two functions cannot share a symbol; the mangling is what prevents it."""
    module = Module("t")
    for key in ("first", "second"):
        func = Function("write", module.types.func_type((), VOID),
                        FuncAttrs(abi="sysv64"))
        func.add_block().append(RetInst())
        module.functions[key] = func
    with pytest.raises(InternalError, match="symbol"):
        verify(module)


SOURCE = """@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    0
"""


def test_the_symbol_reaches_the_image(tmp_path) -> None:  # noqa: ANN001
    """The generated program carries the mangled name, readable as it stands."""
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), str(source)])
    assert proc.returncode == 0, describe(proc)
    listing = subprocess.run(["eu-readelf", "-s", str(output)], capture_output=True,
                             text=True, timeout=60)
    assert "main()u8" in listing.stdout, listing.stdout
    assert " main\n" not in listing.stdout, "the unmangled name is in the image"


def test_the_entry_point_is_not_mangled(tmp_path) -> None:  # noqa: ANN001
    """The entry point is not a function of the language, so it keeps its name.

    The kernel is told where to start by the header rather than by a name, but
    every tool that inspects a binary expects to find this one.
    """
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out"
    assert run_compiler(["-o", str(output), str(source)]).returncode == 0
    listing = subprocess.run(["eu-readelf", "-s", str(output)], capture_output=True,
                             text=True, timeout=60)
    assert " _start\n" in listing.stdout, listing.stdout
