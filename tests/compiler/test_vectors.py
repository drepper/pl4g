"""Operations over a whole run of elements: the type, the instructions, and
bringing them down to what a machine has."""

from __future__ import annotations

import pytest

from pypl4g.ir.inst import BinaryInst, BinOp, CmpInst, LoadInst, SplatInst, StoreInst
from pypl4g.ir.layout import DataLayout, align_of, size_of
from pypl4g.ir.printer import render_module
from pypl4g.ir.reader import read_module
from pypl4g.ir.types import (BOOL, PtrType, TypeContext, U8, U16, U32, U64,
                             VecType)
from pypl4g.diag.engine import InternalError
from pypl4g.ir.verify import verify
from pypl4g.target.vectors import Vectors, settle

LAYOUT = DataLayout(pointer_size=8)


def test_a_run_is_its_elements_and_nothing_else() -> None:
    """A vector takes the room its lanes take, packed, aligned as one lane is."""
    types = TypeContext()
    assert size_of(types.vec_type(U8, 16), LAYOUT) == 16
    assert size_of(types.vec_type(U32, 4), LAYOUT) == 16
    assert size_of(types.vec_type(U16, 3), LAYOUT) == 6
    # No more than one lane asks for: what it reads is an array laid out before
    # anything knew a whole one would be read at once.
    assert align_of(types.vec_type(U8, 16), LAYOUT) == 1
    assert align_of(types.vec_type(U32, 4), LAYOUT) == 4


def test_the_type_is_interned() -> None:
    """Two asks for the same run answer with the one type."""
    types = TypeContext()
    assert types.vec_type(U8, 4) is types.vec_type(U8, 4)
    assert types.vec_type(U8, 4) is not types.vec_type(U8, 8)
    assert types.vec_type(U8, 4).render() == "u8\N{MULTIPLICATION SIGN}4"
    assert types.vec_type(U8, 4).mangled() == "vec<u8,4>"


RUNS = """; pl4g-ir 1
module "runs.pl4g" triple "x86_64-linux-none"

fn @f(ptr<mut u8>, ptr<mut u8>, u8) \N{RIGHTWARDS ARROW} u8 internal cconv(pl4g) {
block0(%0: ptr<mut u8>, %1: ptr<mut u8>, %2: u8):
  %3 = mem.start
  %4 = bitcast.ptr<mut u8\N{MULTIPLICATION SIGN}4> %0
  %5 = load.u8\N{MULTIPLICATION SIGN}4 %3, %4
  %6 = splat.u8\N{MULTIPLICATION SIGN}4 %2
  %7 = add.u8\N{MULTIPLICATION SIGN}4 %5, %6
  %8 = bitcast.ptr<mut u8\N{MULTIPLICATION SIGN}4> %1
  %9 = store.u8\N{MULTIPLICATION SIGN}4 %3, %8, %7
  ret.u8 %2
}
"""


def test_the_textual_form_reads_back() -> None:
    """A run of elements survives being written down and read again."""
    module = read_module(RUNS)
    verify(module)
    assert render_module(module) == RUNS


def test_a_comparison_of_runs_answers_a_lane_apiece() -> None:
    """The verifier holds the answer of a comparison to the shape of what it
    compared."""
    module = read_module(RUNS.replace(
        "  %7 = add.u8\N{MULTIPLICATION SIGN}4 %5, %6",
        "  %7 = icmp.eq.u8\N{MULTIPLICATION SIGN}4 %5, %6").replace(
        "  %9 = store.u8\N{MULTIPLICATION SIGN}4 %3, %8, %7\n", ""))
    verify(module)
    found = [i for b in module.functions["f"].blocks for i in b.insts
             if isinstance(i, CmpInst)]
    assert found[0].ty == module.types.vec_type(BOOL, 4)


def test_spreading_the_wrong_type_is_refused() -> None:
    """One value in every lane has to be a value of what a lane holds."""
    module = read_module(RUNS)
    func = module.functions["f"]
    block = func.blocks[0]
    splat = next(i for i in block.insts if isinstance(i, SplatInst))
    splat.operands[0] = block.params[0]
    with pytest.raises(InternalError):
        verify(module)


def _instructions(module: object) -> list[object]:
    """Every instruction of the one function, in order."""
    return [inst for block in module.functions["f"].blocks  # type: ignore[attr-defined]
            for inst in block.insts]


def _additions(found: list[object]) -> list[object]:
    """The additions of values, and not the ones that move an address along."""
    return [i for i in found if isinstance(i, BinaryInst) and i.op is BinOp.ADD
            and not isinstance(i.ty, PtrType)]


def test_a_machine_with_nothing_does_an_element_at_a_time() -> None:
    """Every run comes apart where the target says it has no such registers."""
    module = read_module(RUNS)
    settle(module, Vectors(), LAYOUT)
    verify(module)
    found = _instructions(module)
    assert not any(isinstance(i.ty, VecType) for i in found)  # type: ignore[attr-defined]
    assert len([i for i in found if isinstance(i, LoadInst)]) == 4
    assert len([i for i in found if isinstance(i, StoreInst)]) == 4
    assert len(_additions(found)) == 4
    # The one value that goes in every lane is used by each of them as it
    # stands: spreading it over nothing is nothing to do.
    assert not any(isinstance(i, SplatInst) for i in found)


def test_a_run_that_fits_is_left_alone() -> None:
    """A machine that can add a whole register of them adds it in one."""
    module = read_module(RUNS)
    settle(module, Vectors(bits=128, widest=64, binary=frozenset((BinOp.ADD,))),
           LAYOUT)
    verify(module)
    found = _instructions(module)
    assert len(_additions(found)) == 1
    assert len([i for i in found if isinstance(i, LoadInst)]) == 1
    assert len([i for i in found if isinstance(i, SplatInst)]) == 1


def test_an_operation_it_does_not_have_comes_apart() -> None:
    """Registers it has and this operation it has not: an element at a time."""
    module = read_module(RUNS)
    settle(module, Vectors(bits=128, widest=64, binary=frozenset((BinOp.SUB,))),
           LAYOUT)
    verify(module)
    assert len(_additions(_instructions(module))) == 4


LONG = RUNS.replace("\N{MULTIPLICATION SIGN}4", "\N{MULTIPLICATION SIGN}19")


def test_a_longer_run_goes_in_whole_registers_and_then_one_at_a_time() -> None:
    """Nineteen bytes in registers of sixteen: one register and three left over."""
    module = read_module(LONG)
    settle(module, Vectors(bits=128, widest=64, binary=frozenset((BinOp.ADD,))),
           LAYOUT)
    verify(module)
    found = _instructions(module)
    adds = _additions(found)
    assert len(adds) == 4
    assert [isinstance(i.ty, VecType) for i in adds] == [True, False, False, False]
    assert len([i for i in found if isinstance(i, LoadInst)]) == 4
    assert len([i for i in found if isinstance(i, StoreInst)]) == 4


def test_a_wider_element_holds_fewer_lanes() -> None:
    """Four bytes to a lane is four lanes to a sixteen-byte register."""
    able = Vectors(bits=128, widest=64, binary=frozenset((BinOp.ADD,)))
    assert able.lanes_at_once(U8, LAYOUT) == 16
    assert able.lanes_at_once(U32, LAYOUT) == 4
    assert able.lanes_at_once(U64, LAYOUT) == 2
    # A lane wider than the arithmetic reaches is no lane at all.
    narrow = Vectors(bits=128, widest=32, binary=frozenset((BinOp.ADD,)))
    assert narrow.lanes_at_once(U64, LAYOUT) == 0
    assert narrow.lanes_at_once(U32, LAYOUT) == 4
    # And a machine with no such registers holds none of anything.
    assert Vectors().lanes_at_once(U8, LAYOUT) == 0
