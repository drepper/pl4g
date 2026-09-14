"""The calling conventions the RISC-V backend knows.

``pl4g`` is the language's own convention, shaped like the standard one for
now.  The return address lives in a register the callee is free to overwrite,
which is why a function that calls anything must save it -- a difference from
the other two backends worth stating where the convention is described rather
than discovering it in code generation.
"""

from typing import Final

from ..callconv import CallConvDesc
from .regs import CALLEE_SAVED_NAMES, CALLER_SAVED_NAMES, reg

_CALLEE_SAVED = frozenset(reg(n).unit for n in CALLEE_SAVED_NAMES)
_CALLER_SAVED = frozenset(reg(n).unit for n in CALLER_SAVED_NAMES)
_ARG_REGS = tuple(reg("".join(("a", str(n)))) for n in range(8))

#: The registers the allocator may give out, the ones a call would destroy
#: first.  `ra` is left alone although a call destroys it: it holds the return
#: address, which is what an unwinder follows.  `sp` and `s0`, the frame
#: pointer, are left alone for the same reason, and `zero` is not a register
#: that can hold anything.
_ALLOCATION_ORDER = tuple(reg(n).unit for n in (
    "t0", "t1", "t2", "t3", "t4", "t5", "t6",
    "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7",
    "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11"))

#: The floating-point registers.  f10 to f17 carry arguments and f10 carries the
#: answer, which is what the convention's own names fa0 to fa7 mean; f8, f9 and
#: f18 upward are ones a function hands back as it found them, so they come last.
_FLOAT_ARGS = tuple(reg("".join(("f", str(n)))) for n in range(10, 18))
_FLOAT_ORDER = tuple(reg("".join(("f", str(n)))).unit
                     for n in (*range(10, 18), *range(0, 8), *range(28, 32),
                               8, 9, *range(18, 28)))

CC_PL4G: Final[CallConvDesc] = CallConvDesc(
    name="pl4g",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("a0"), reg("a1")),
    float_arg_regs=_FLOAT_ARGS,
    float_ret_regs=(reg("f10"),),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    # The standard defines no area below the stack pointer a leaf function may
    # use, as on AArch64 and unlike x86-64.
    allocation_order=_ALLOCATION_ORDER,
    red_zone=0,
)

CC_LP64: Final[CallConvDesc] = CallConvDesc(
    name="lp64",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("a0"), reg("a1")),
    float_arg_regs=_FLOAT_ARGS,
    float_ret_regs=(reg("f10"),),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=0,
)

CONVENTIONS: Final[dict[str, CallConvDesc]] = {
    CC_PL4G.name: CC_PL4G,
    CC_LP64.name: CC_LP64,
    # What a program writes when it means "the one this system uses", without
    # having to know what this architecture calls it.
    "cdecl": CC_LP64,
}


def lookup(name: str) -> CallConvDesc:
    """Return the convention called *name*, falling back to the language's own."""
    return CONVENTIONS.get(name, CC_PL4G)
