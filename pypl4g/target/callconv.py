"""Calling conventions.

The specification says explicitly that the calling conventions need not match the
system's, and that they may differ between the functions of a single
compilation.  A convention is therefore a value attached to a function, not a
property of the target, and a pass that later synthesizes a bespoke convention
for one function has somewhere to put the result.
"""

from dataclasses import dataclass, field
from typing import Sequence

from ..ir.types import FloatType, Type, parts_of
from ..mc.reg import PhysReg, RegUnit


@dataclass(frozen=True, slots=True)
class CallConvDesc:
    """One calling convention."""

    name: str
    int_arg_regs: tuple[PhysReg, ...] = ()
    int_ret_regs: tuple[PhysReg, ...] = ()
    callee_saved: frozenset[RegUnit] = field(default_factory=frozenset)
    caller_saved: frozenset[RegUnit] = field(default_factory=frozenset)
    #: The same three things for values that are not integers.  A convention
    #: says where every kind of value goes, and a target with more than one kind
    #: of register has more than one set of answers.
    float_arg_regs: tuple[PhysReg, ...] = ()
    float_ret_regs: tuple[PhysReg, ...] = ()
    float_allocation_order: tuple[RegUnit, ...] = ()
    #: The units the allocator may give out, in the order it prefers them.
    #: Which registers are free to use is what a convention is about, so the
    #: convention states it rather than the allocator working it out: the order
    #: puts the ones a call would destroy first, since using one of those costs
    #: a leaf function nothing and using a callee-saved one would cost it a save.
    allocation_order: tuple[RegUnit, ...] = ()
    stack_align: int = 16
    #: Bytes below the stack pointer a leaf function may use without adjusting it.
    red_zone: int = 0
    variadic: bool = False

    def orders(self, integers: str, floats: str) -> dict[str, tuple[RegUnit, ...]]:
        """The allocation orders by the name of the class each belongs to.

        A target with nothing to say about floating point says nothing here
        either: the mapping then holds one entry and the allocator behaves as it
        did before there was a second kind of register.
        """
        found = {integers: self.allocation_order}
        if self.float_allocation_order:
            found[floats] = self.float_allocation_order
        return found


class TooManyArguments(Exception):
    """More arguments than a convention passes in registers.

    Nothing yet puts one on the stack, so this is the limit of what can be
    called rather than the point where a second route begins.
    """


def argument_places(cconv: CallConvDesc,
                    types: Sequence[Type]) -> list[list[PhysReg]]:
    """Which registers each argument is passed in, in order.

    A register is taken from the list its *kind* comes out of, so a
    floating-point argument does not use up an integer register and the other
    way round -- which is what every one of these conventions says and what
    counting by position alone would get wrong the moment the two are mixed.

    A value of several parts takes one register per part: a result its answer
    and the truth value beside it, a tuple its members.  That is what the ABIs
    already do with a two-word aggregate.
    """
    return _places(cconv.int_arg_regs, cconv.float_arg_regs, types)


def result_places(cconv: CallConvDesc, ty: Type) -> list[PhysReg]:
    """Which registers a value of *ty* is answered with, one per part."""
    return _places(cconv.int_ret_regs, cconv.float_ret_regs, (ty,))[0]


def _places(integers: "Sequence[PhysReg]", floats: "Sequence[PhysReg]",
            types: Sequence[Type]) -> list[list[PhysReg]]:
    """The registers each of *types* occupies, counted per kind."""
    found: list[list[PhysReg]] = []
    used = {True: 0, False: 0}

    def _take(part: Type) -> PhysReg:
        floating = isinstance(part, FloatType)
        bank = floats if floating else integers
        if used[floating] >= len(bank):
            raise TooManyArguments()
        used[floating] += 1
        return bank[used[floating] - 1]

    for ty in types:
        found.append([_take(part) for part in parts_of(ty)])
    return found
