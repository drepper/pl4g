"""Building a constant that no single instruction can carry.

Every value in a program has to get into a register somehow, and on two of the
three architectures an instruction carries at most a part of a wide one: AArch64
sets sixteen bits at a time, RISC-V twelve or twenty.  So a constant becomes a
sequence, and a sequence that is wrong by one bit is a program that is wrong in
a way nothing else would notice.

What makes these tests worth trusting is where the answer comes from.  The
program compares a constant it *built* against the same constant as the image
writer *wrote* it into memory -- two entirely separate paths from the same
number -- and exits with whether they agree.  A defect in either one is a
disagreement, and there is nowhere for both to be wrong in the same way.
"""

import pytest

from conftest import compiler_targets
from pypl4g.ir.function import FuncAttrs, Function, SpecialKind
from pypl4g.ir.inst import CmpInst, CmpPred, LoadInst, MemStartInst, RetInst
from pypl4g.ir.module import GlobalVar, Module
from pypl4g.ir.types import BOOL, I64, U64
from pypl4g.ir.verify import verify
from test_branches import build, run

#: Values chosen for where they fall in each architecture's encoding: the
#: boundaries of what one instruction can carry, a value with a hole in the
#: middle, one made of all ones, and the ends of both sixty-four bit types.
VALUES = [
    ("zero", U64, 0),
    ("one", U64, 1),
    ("just past twelve bits", U64, 2048),
    ("just past sixteen bits", U64, 0x10000),
    ("just past twenty bits", U64, 0x100000),
    ("the whole of thirty-two bits", U64, 0xFFFFFFFF),
    ("just past thirty-two bits", U64, 0x100000000),
    ("a power of two high up", U64, 1 << 48),
    ("every quarter different", U64, 0x0123456789ABCDEF),
    ("every bit set", U64, 0xFFFFFFFFFFFFFFFF),
    ("the largest unsigned", U64, (1 << 64) - 1),
    ("minus one", I64, -1),
    ("just past the negative twelve", I64, -2049),
    ("a wide negative", I64, -1234567890123),
    ("the smallest signed", I64, -(1 << 63)),
    ("the largest signed", I64, (1 << 63) - 1),
]


def compares_a_built_constant_with_a_written_one(triple: str, ty, value: int) -> Module:  # noqa: ANN001
    """A program that answers whether the two ways of producing *value* agree."""
    module = Module("t", triple=triple)
    written = GlobalVar("written", ty, module.types.ptr_type(ty),
                        initializer=module.int_const(ty, value))
    module.add_global(written)
    func = Function("main", module.types.func_type((), BOOL),
                    FuncAttrs(special=SpecialKind.STARTUP))
    entry = func.add_block()
    token = entry.append(MemStartInst())
    loaded = entry.append(LoadInst(ty, (token, written)))
    entry.append(RetInst(entry.append(
        CmpInst(CmpPred.EQ, loaded, module.int_const(ty, value), BOOL))))
    module.add_function(func)
    module.startup = func
    verify(module)
    return module


@pytest.mark.parametrize("triple", compiler_targets())
@pytest.mark.parametrize(("name", "ty", "value"), VALUES, ids=[v[0] for v in VALUES])
def test_a_built_constant_is_the_constant(triple: str, name: str, ty,  # noqa: ANN001
                                          value: int, tmp_path) -> None:  # noqa: ANN001
    """The program exits with 1 where the two agree, which is the whole test."""
    path = tmp_path / "out"
    build(compares_a_built_constant_with_a_written_one(triple, ty, value),
          triple, path)
    assert run(triple, path) == 1, "".join((name, " came out wrong"))


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_small_constant_is_still_one_instruction(triple: str, tmp_path) -> None:  # noqa: ANN001
    """Nothing above made the common case cost more than it did.

    A sequence that always ran to four instructions would pass every test in
    this file and make every program larger.
    """
    mnemonics = build(compares_a_built_constant_with_a_written_one(triple, U64, 1),
                      triple, tmp_path / "out")
    building = {"x86_64-linux-none": "mov", "aarch64-linux-none": "movz",
                "riscv64-linux-none": "li"}[triple]
    assert mnemonics.count(building) <= 2, mnemonics
