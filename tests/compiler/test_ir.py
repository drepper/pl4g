"""The IR: its textual form, its round trip and its verifier."""

import pytest

from pypl4g.diag.engine import InternalError
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import (BinaryInst, BinOp, BlockTarget, CmpInst, CmpPred,
                            CondBrInst, RetInst)
from pypl4g.ir.module import Module
from pypl4g.ir.printer import render_module
from pypl4g.ir.reader import IRSyntaxError, read_module
from pypl4g.ir.types import BOOL, I32, U8
from pypl4g.ir.verify import verify

EXIT0 = """; pl4g-ir 1
module "exit0.pl4g" triple "x86_64-linux-none"

fn @main() \N{RIGHTWARDS ARROW} u8 internal cconv(pl4g.v0) special(startup) {
block0:
  ret.u8 0
}
"""

BLOCK_PARAMS = """; pl4g-ir 1
module "absdiff.pl4g" triple "x86_64-linux-none"

fn @absdiff(i32, i32) \N{RIGHTWARDS ARROW} i32 internal cconv(pl4g.v0) {
block0(%0: i32, %1: i32):
  %2 = icmp.slt.i32 %0, %1
  condbr %2, block1(%1, %0), block1(%0, %1)
block1(%3: i32, %4: i32):
  %5 = sub.i32 %3, %4
  ret.i32 %5
}
"""


PLACES = """; pl4g-ir 1
module "places.pl4g" triple "x86_64-linux-none"

let @v: mut u64 internal = 0

fn @main() \N{RIGHTWARDS ARROW} u8 internal cconv(pl4g.v0) special(startup) {
block0:
  %0 = mem.start
  %1 = address.ptr<mut u64> @v
  %2 = bitcast.ptr<mut u8> %1
  %3 = add.ptr<mut u8> %2, 3
  %4 = store.u8 %0, %3, 9
  %5 = load.u8 %4, %3
  ret.u8 %5
}
"""


def build_exit0() -> Module:
    """A module holding the smallest conforming program."""
    module = Module("exit0.pl4g")
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    func.add_block().append(RetInst(module.int_const(U8, 0)))
    module.add_function(func)
    module.startup = func
    return module


def build_absdiff() -> Module:
    """A module exercising block parameters, which take the place of phis."""
    module = Module("absdiff.pl4g")
    func = Function("absdiff", module.types.func_type((I32, I32), I32))
    entry = func.add_block()
    left = entry.add_param(I32)
    right = entry.add_param(I32)
    merge = func.add_block()
    cond = entry.append(CmpInst(CmpPred.SLT, left, right, BOOL))
    entry.append(CondBrInst(cond, BlockTarget(merge, (right, left)),
                            BlockTarget(merge, (left, right))))
    high = merge.add_param(I32)
    low = merge.add_param(I32)
    difference = merge.append(BinaryInst(BinOp.SUB, high, low))
    merge.append(RetInst(difference))
    module.add_function(func)
    return module


@pytest.mark.parametrize(("build", "expected"),
                         [(build_exit0, EXIT0), (build_absdiff, BLOCK_PARAMS)])
def test_printing_is_canonical(build, expected: str) -> None:  # noqa: ANN001
    """A module always prints the same text, numbered by position."""
    assert render_module(build()) == expected


@pytest.mark.parametrize("text", [EXIT0, BLOCK_PARAMS, PLACES])
def test_round_trip_is_a_fixed_point(text: str) -> None:
    """Reading printed IR and printing it again gives the same text."""
    assert render_module(read_module(text)) == text


def test_reading_rejects_another_version() -> None:
    """A textual IR of another version is refused rather than misread."""
    with pytest.raises(IRSyntaxError):
        read_module(EXIT0.replace("pl4g-ir 1", "pl4g-ir 99"))


def test_reading_restores_the_special_function_caches() -> None:
    """The module's caches agree with the attributes after a round trip."""
    module = read_module(EXIT0)
    assert module.startup is not None and module.startup.name == "main"
    verify(module)


@pytest.mark.parametrize("build", [build_exit0, build_absdiff])
def test_verifier_accepts_well_formed_modules(build) -> None:  # noqa: ANN001
    """Nothing is wrong with the modules the compiler builds."""
    verify(build())


def test_verifier_rejects_two_terminators() -> None:
    """A block ends in exactly one terminator."""
    module = build_exit0()
    block = module.functions["main"].blocks[0]
    block.append(RetInst(module.int_const(U8, 0)))
    with pytest.raises(InternalError, match="terminator"):
        verify(module)


def test_verifier_rejects_a_wrong_return_type() -> None:
    """The returned value must have the declared return type."""
    module = build_exit0()
    block = module.functions["main"].blocks[0]
    block.insts = [RetInst(module.bool_const(BOOL, True))]
    with pytest.raises(InternalError, match="returning"):
        verify(module)


def test_verifier_rejects_a_branch_with_the_wrong_arguments() -> None:
    """A branch supplies exactly the arguments its destination declares."""
    module = build_absdiff()
    func = module.functions["absdiff"]
    terminator = func.blocks[0].insts[-1]
    assert isinstance(terminator, CondBrInst)
    terminator.true_target.args.pop()
    with pytest.raises(InternalError, match="arguments"):
        verify(module)


def test_verifier_rejects_a_use_that_is_not_dominated() -> None:
    """A value may only be used where its definition is guaranteed to have run."""
    module = build_absdiff()
    func = module.functions["absdiff"]
    difference = func.blocks[1].insts[0]
    func.blocks[0].insts[-1] = RetInst(difference)
    with pytest.raises(InternalError, match="dominate"):
        verify(module)


def test_verifier_rejects_a_constant_out_of_range() -> None:
    """A constant that its type cannot represent is a defect, not a wrap-around."""
    module = build_exit0()
    block = module.functions["main"].blocks[0]
    block.insts = [RetInst(module.int_const(U8, 1 << 40))]
    with pytest.raises(InternalError, match="does not fit"):
        verify(module)


def test_types_carry_no_layout() -> None:
    """No type knows a size, an alignment or a field offset.

    The specification lets the compiler reorder the fields of a product type, so
    layout has to stay outside the type.
    """
    from pypl4g.ir import types

    for name in dir(types):
        candidate = getattr(types, name)
        if isinstance(candidate, type) and issubclass(candidate, types.Type):
            fields = getattr(candidate, "__dataclass_fields__", {})
            assert not ({"size", "align", "alignment", "offset"} & set(fields)), name
