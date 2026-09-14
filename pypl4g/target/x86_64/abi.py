"""The calling conventions the x86-64 backend knows.

``pl4g`` is the language's own convention and the one every function has that
does not ask for another.  It is *not* shaped like the system's, and the
specification says in so many words that it need not be: what a function does
with its arguments is the compiler's business as long as every caller agrees,
and the caller is always this compiler.

``sysv64`` is the system's, and ``cdecl`` is the name a program writes for it
without having to know what this architecture calls it.  A function marked
`@[cdecl]` gets that one and keeps its plain name, because the point of asking
for it is to be called by something that has never heard of this language.
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
#: The vector registers, which is where a floating-point value lives.  Every one
#: of them is destroyed by a call on this architecture, so the order is simply
#: the order they are numbered in.
_FLOAT_ORDER = tuple(reg("".join(("xmm", str(n)))).unit for n in range(16))
_FLOAT_ARGS = tuple(reg("".join(("xmm", str(n)))) for n in range(8))

_ALLOCATION_ORDER = tuple(reg(n).unit for n in (
    "rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11",
    "rbx", "r12", "r13", "r14", "r15"))

#: Where the language's own convention passes its arguments.  It begins at the
#: registers an answer comes back in, which the system's convention does not:
#: a function that answers with what it was given -- which is a great deal of
#: what a generated program's small functions do -- then has the value in the
#: right register already and is a bare `ret`, and its caller has no move to
#: make either.  The rest follow in the order the allocator prefers them, so
#: that an argument is usually already where it is wanted.
_PL4G_ARGS = (RAX, RDX, RCX, RSI, RDI, reg("r8"), reg("r9"), reg("r10"))

CC_PL4G: Final[CallConvDesc] = CallConvDesc(
    name="pl4g",
    int_arg_regs=_PL4G_ARGS,
    int_ret_regs=(RAX, RDX),
    float_arg_regs=_FLOAT_ARGS,
    float_ret_regs=(reg("xmm0"),),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=128,
)

CC_SYSV: Final[CallConvDesc] = CallConvDesc(
    name="sysv64",
    int_arg_regs=(RDI, RSI, RDX, RCX, reg("r8"), reg("r9")),
    int_ret_regs=(RAX, RDX),
    float_arg_regs=_FLOAT_ARGS,
    float_ret_regs=(reg("xmm0"),),
    float_allocation_order=_FLOAT_ORDER,
    callee_saved=_CALLEE_SAVED,
    caller_saved=_CALLER_SAVED,
    stack_align=16,
    allocation_order=_ALLOCATION_ORDER,
    red_zone=128,
)

CONVENTIONS: Final[dict[str, CallConvDesc]] = {
    CC_PL4G.name: CC_PL4G,
    CC_SYSV.name: CC_SYSV,
    # What a program writes when it means "the one this system uses", without
    # having to know what this architecture calls it.
    "cdecl": CC_SYSV,
}


def lookup(name: str) -> CallConvDesc:
    """Return the convention called *name*, falling back to the language's own."""
    return CONVENTIONS.get(name, CC_PL4G)
