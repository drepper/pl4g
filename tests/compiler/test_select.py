"""A choice of one of two values by a truth value, with no branch.

The representation's `select`: written down and read back, folded where what
chooses is known, and held by the verifier to choosing between two values of
one type that fit in one register.
"""

from __future__ import annotations

import pytest

from pypl4g.diag.engine import InternalError
from pypl4g.ir.inst import SelectInst
from pypl4g.ir.printer import render_module
from pypl4g.ir.reader import read_module
from pypl4g.ir.verify import verify
from pypl4g.opt.passes.constfold import ConstantFolding

CHOSEN = """; pl4g-ir 1
module "chosen.pl4g" triple "x86_64-linux-none"

fn @f(bool, f64, f64) \N{RIGHTWARDS ARROW} f64 internal cconv(pl4g) {
block0(%0: bool, %1: f64, %2: f64):
  %3 = select.f64 %0, %1, %2
  ret.f64 %3
}
"""


def test_the_textual_form_reads_back() -> None:
    """Written down and read again, it is the same instruction."""
    module = read_module(CHOSEN)
    verify(module)
    assert render_module(module) == CHOSEN


def test_a_known_choice_folds() -> None:
    """A truth value that is known chooses while compiling."""
    module = read_module(CHOSEN.replace("select.f64 %0, %1, %2",
                                        "select.f64 true, %1, %2"))
    ConstantFolding().run(module)
    block = module.functions["f"].blocks[0]
    assert not any(isinstance(one, SelectInst) for one in block.insts)
    assert block.insts[-1].operands[0] is block.params[1]


def test_choosing_by_a_number_is_refused() -> None:
    """What chooses is a truth value and nothing else."""
    module = read_module(CHOSEN)
    block = module.functions["f"].blocks[0]
    choice = next(one for one in block.insts if isinstance(one, SelectInst))
    choice.operands[0] = block.params[1]
    with pytest.raises(InternalError):
        verify(module)
