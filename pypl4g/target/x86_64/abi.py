"""The calling conventions the x86-64 backend knows.

``pl4g.v0`` is the language's own convention.  It is deliberately shaped like the
system's for now, which costs nothing and makes generated code readable in a
debugger; the specification leaves the compiler free to change it, and the fact
that it is a value rather than a hard-coded rule is what will make that change
local.
"""

from typing import Final

from ..callconv import CallConvDesc
from .regs import RAX, RCX, RDI, RDX, RSI, reg

_CALLEE_SAVED = frozenset(reg(n).unit for n in ("rbx", "rbp", "r12", "r13", "r14", "r15"))
_CALLER_SAVED = frozenset(reg(n).unit for n in
                          ("rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11"))

#: The registers the allocator may give out, the ones a call would destroy
#: first: using one of those costs a leaf function nothing, while using a
#: callee-saved one would cost it a save and a restore.  The stack pointer is
#: not among them, and neither is the frame pointer, which is left free so that
#: a frame and the unwinder that will walk it have somewhere to stand.
_ALLOCATION_ORDER = tuple(reg(n).unit for n in (
    "rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11",
    "rbx", "r12", "r13", "r14", "r15"))

CC_PL4G_V0: Final[CallConvDesc] = CallConvDesc(
    name="pl4g.v0",
    int_arg_regs=(RDI, RSI, RDX, RCX, reg("r8"), reg("r9")),
    int_ret_regs=(RAX, RDX),
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=128,
)

CC_SYSV: Final[CallConvDesc] = CallConvDesc(
    name="sysv",
    int_arg_regs=(RDI, RSI, RDX, RCX, reg("r8"), reg("r9")),
    int_ret_regs=(RAX, RDX),
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=128,
)

CONVENTIONS: Final[dict[str, CallConvDesc]] = {
    CC_PL4G_V0.name: CC_PL4G_V0,
    CC_SYSV.name: CC_SYSV,
}


def lookup(name: str) -> CallConvDesc:
    """Return the convention called *name*, falling back to the language's own."""
    return CONVENTIONS.get(name, CC_PL4G_V0)
