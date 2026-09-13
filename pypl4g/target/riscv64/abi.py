"""The calling conventions the RISC-V backend knows.

``pl4g.v0`` is the language's own convention, shaped like the standard one for
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

CC_PL4G_V0: Final[CallConvDesc] = CallConvDesc(
    name="pl4g.v0",
    int_arg_regs=_ARG_REGS,
    int_ret_regs=(reg("a0"), reg("a1")),
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
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=0,
)

CONVENTIONS: Final[dict[str, CallConvDesc]] = {
    CC_PL4G_V0.name: CC_PL4G_V0,
    CC_LP64.name: CC_LP64,
    # A foreign function declared to follow "the system's" convention gets this
    # architecture's one.
    "sysv": CC_LP64,
}


def lookup(name: str) -> CallConvDesc:
    """Return the convention called *name*, falling back to the language's own."""
    return CONVENTIONS.get(name, CC_PL4G_V0)
