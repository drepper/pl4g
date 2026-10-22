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

#: A floating-point register is one no function hands back as it found it -- a
#: prologue saves only the ordinary registers -- so a call whose callee has not
#: said what it destroys destroys every one of them.
_FLOATS_DESTROYED = frozenset(_FLOAT_ORDER)
#: What the system's convention says of the floating-point registers: a callee
#: hands back the low halves of v8 to v15 as it found them, and may destroy the
#: rest.  So a function of that convention saves and restores eight bytes of
#: each of those it uses, which is what a C caller counts on.  A call is still
#: taken to destroy every one of them: the upper half of v8 to v15 is not kept,
#: and a run of lanes is held in the whole register.  The language's own
#: convention keeps none of them.
_FLOATS_KEPT = frozenset(reg("".join(("d", str(n)))).unit for n in range(8, 16))
_FLOATS = frozenset(reg("".join(("d", str(n)))).unit for n in range(32))

CC_PL4G: Final[CallConvDesc] = CallConvDesc(
    name="pl4g",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("x0"), reg("x1"), reg("x2")),
    float_arg_regs=_FLOAT_ARGS,
    # As many as it answers values in, so that an answer of floating-point
    # numbers is answered in registers wherever one of whole numbers is.
    float_ret_regs=(reg("d0"), reg("d1"), reg("d2")),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED | _FLOATS_DESTROYED,
    stack_align=16,
    # The standard defines no area below the stack pointer that a leaf function
    # may use, unlike the one on x86-64.
    allocation_order=_ALLOCATION_ORDER,
    red_zone=0,
    # Not x18, GCC's: this backend leaves x18 alone, a platform being free to
    # claim it.  x15 is a temporary no argument uses, and x16 and x17 are the
    # linker's.
    chain_reg=reg("x15")
)

CC_AAPCS64: Final[CallConvDesc] = CallConvDesc(
    name="aapcs64",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("x0"), reg("x1")),
    float_arg_regs=_FLOAT_ARGS,
    # What the system answers a pair of `double`s in, the first two of the
    # four a homogeneous aggregate may take.
    float_ret_regs=(reg("d0"), reg("d1")),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED | _FLOATS_KEPT,
    caller_saved=_CALLER_SAVED | _FLOATS,
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
