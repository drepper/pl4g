"""Operations that only AArch64 has.

They are built exactly like the architecture-neutral ones, which is what makes
the set of operations open: a target adds what it needs without touching the
builder or the shared operation list.
"""

from __future__ import annotations

from typing import Final

from ...mc.ops import Op, register

#: Enters the kernel.  The call number is in x8 and the arguments in x0 onwards,
#: which is a different convention from the one an ordinary call uses.
SUPERVISOR_CALL: Final[Op] = register(Op("aarch64.svc", 0, has_result=False))
