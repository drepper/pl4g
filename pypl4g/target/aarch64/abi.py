"""The calling conventions the AArch64 backend knows.

``pl4g`` is the language's own convention.  It is shaped like the standard
one for now, which costs nothing and keeps generated code readable in a
debugger; the specification leaves the compiler free to change it, and the fact
that it is a value rather than a rule is what will make that change local.
"""

from __future__ import annotations

from typing import Final

from ..callconv import CallConvDesc
from .regs import CALLEE_SAVED_NAMES, CALLER_SAVED_NAMES, reg

_CALLEE_SAVED = frozenset(reg(n).unit for n in CALLEE_SAVED_NAMES)
_CALLER_SAVED = frozenset(reg(n).unit for n in CALLER_SAVED_NAMES)
_ARG_REGS = tuple(reg("".join(("x", str(n)))) for n in range(8))

#: The registers the allocator may give out, the ones a call would destroy
#: first.  x18 is left alone because a platform may claim it, x29 is the frame
#: pointer and x30 holds the return address; the stack pointer and the zero
#: register are not general registers at all here.
_ALLOCATION_ORDER = tuple(reg("".join(("x", str(n)))).unit
                          for n in (*range(18), *range(19, 29)))

#: The floating-point registers.  The first eight carry arguments and the first
#: carries the answer; the eight after them are ones a function hands back as it
#: found them, so they come last in the order.
_FLOAT_ARGS = tuple(reg("".join(("d", str(n)))) for n in range(8))
_FLOAT_ORDER = tuple(reg("".join(("d", str(n)))).unit
                     for n in (*range(0, 8), *range(16, 32), *range(8, 16)))

CC_PL4G: Final[CallConvDesc] = CallConvDesc(
    name="pl4g",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("x0"), reg("x1"), reg("x2")),
    float_arg_regs=_FLOAT_ARGS,
    float_ret_regs=(reg("d0"),),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    # The standard defines no area below the stack pointer that a leaf function
    # may use, unlike the one on x86-64.
    allocation_order=_ALLOCATION_ORDER,
    red_zone=0,
)

CC_AAPCS64: Final[CallConvDesc] = CallConvDesc(
    name="aapcs64",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("x0"), reg("x1")),
    float_arg_regs=_FLOAT_ARGS,
    float_ret_regs=(reg("d0"),),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=0,
)

CONVENTIONS: Final[dict[str, CallConvDesc]] = {
    CC_PL4G.name: CC_PL4G,
    CC_AAPCS64.name: CC_AAPCS64,
    # What a program writes when it means "the one this system uses", without
    # having to know what this architecture calls it.
    "cdecl": CC_AAPCS64,
}


def lookup(name: str) -> CallConvDesc:
    """Return the convention called *name*, falling back to the language's own."""
    return CONVENTIONS.get(name, CC_PL4G)
