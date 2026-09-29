"""What a read or a write may say about what another observer sees.

The memory token already says that two accesses of this program happen in an
order.  What it does not say is what a *second* observer sees, and where memory
is shared -- a kernel reaping a ring, another thread -- that is the whole
question.  `Ordering` on the instruction is what says it; this is the one rule
about which accesses may carry one, so the three instruction selectors ask
rather than each deciding.
"""

from __future__ import annotations

from ..ir.inst import LoadInst, Ordering, StoreInst
from ..ir.types import FloatType, VecType, made_of_parts


def cannot_order(inst: object) -> str | None:
    """Why this access cannot carry the ordering it was given, or nothing.

    An ordering is about one access, and a value of several parts is several
    accesses: which of them the ordering belonged to would have no answer, and
    answering "all of them" is a different promise from the one the word makes.
    Floating point is refused for the plainer reason that the ordered forms of
    these architectures name integer registers; a program that wants one writes
    the bits.
    """
    if not isinstance(inst, (LoadInst, StoreInst)):
        return None
    if inst.ordering is Ordering.PLAIN:
        return None
    ty = inst.ty if isinstance(inst, LoadInst) else inst.operands[2].ty
    if made_of_parts(ty):
        return "".join(("an ordered access to '", ty.written(),
                        "', which is several values and not one"))
    if isinstance(ty, (FloatType, VecType)):
        return "".join(("an ordered access to '", ty.written(), "'"))
    return None
