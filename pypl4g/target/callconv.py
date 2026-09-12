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
    stack_align: int = 16
    #: Bytes below the stack pointer a leaf function may use without adjusting it.
    red_zone: int = 0
    variadic: bool = False
