"""Calling conventions.

The specification says explicitly that the calling conventions need not match the
system's, and that they may differ between the functions of a single
compilation.  A convention is therefore a value attached to a function, not a
property of the target, and a pass that later synthesizes a bespoke convention
for one function has somewhere to put the result.
"""

from dataclasses import dataclass, field

from ..mc.reg import PhysReg, RegUnit


@dataclass(frozen=True, slots=True)
class CallConvDesc:
    """One calling convention."""

    name: str
    int_arg_regs: tuple[PhysReg, ...] = ()
    int_ret_regs: tuple[PhysReg, ...] = ()
    callee_saved: frozenset[RegUnit] = field(default_factory=frozenset)
    caller_saved: frozenset[RegUnit] = field(default_factory=frozenset)
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
