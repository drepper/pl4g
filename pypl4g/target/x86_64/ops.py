"""Operations that only x86-64 has.

They are built exactly like the architecture-neutral ones, which is what makes
the set of operations open: a target adds what it needs without touching the
builder or the shared operation list.
"""

from typing import Final

from ...mc.ops import Op, register

#: Enters the kernel.  No destination, and the operands it reads and writes are
#: fixed by the calling convention of the system call interface.
SYSCALL: Final[Op] = register(Op("x86.syscall", 0, has_result=False))
