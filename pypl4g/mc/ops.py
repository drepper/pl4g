"""The operations the assembler builder accepts.

An operation is a value, not a member of a closed enumeration, so a target adds
its own without touching this module: ``X86Op.SYSCALL`` is built the same way
``PLUS`` is.  ``Assembler.op`` accepts any of them and asks the target to select
an encoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


@dataclass(frozen=True, slots=True)
class Op:
    """One operation of the symbolic assembler."""

    name: str
    #: How many source operands the operation takes.
    arity: int
    #: Whether the operation writes a destination register.
    has_result: bool = True
    #: Whether the operation ends a block of straight-line code.
    is_terminator: bool = False

    def __repr__(self) -> str:
        return "".join(("Op(", self.name, ")"))


class Rounding(Enum):
    """Which whole number a floating-point number is rounded to.

    Four, and three of them say which way they go.  The fourth asks the
    processor what it is doing just now, which every one of these machines holds
    in a register of its own and which nothing in the language can change -- so
    it is a question about where the program is running and not about the
    program.
    """

    DOWN = "down"
    UP = "up"
    NEAREST = "nearest"
    CURRENT = "current"


class Condition(Enum):
    """What a conditional branch tests.

    Signed and unsigned orderings are different conditions rather than one
    condition read against a type, because the instruction that tests them is a
    different instruction on every architecture.  Equality is the same either
    way and is listed once.
    """

    EQ = "eq"
    NE = "ne"
    SLT = "slt"
    SLE = "sle"
    SGT = "sgt"
    SGE = "sge"
    ULT = "ult"
    ULE = "ule"
    UGT = "ugt"
    UGE = "uge"

    def inverted(self) -> Condition:
        """The condition that is true exactly when this one is not.

        Inverting is what lets a branch be turned round so that the block that
        follows it in the image is the one it falls into, which costs one jump
        less than branching over one.
        """
        return _INVERSE[self]

    def swapped(self) -> Condition:
        """The condition that holds when the two operands are exchanged.

        An architecture whose branch has no form for one ordering has the other:
        there is no "branch if less or equal" on RISC-V, and swapping the
        operands of "branch if greater or equal" is that instruction.
        """
        return _SWAPPED[self]


_INVERSE: Final[dict[Condition, Condition]] = {
    Condition.EQ: Condition.NE, Condition.NE: Condition.EQ,
    Condition.SLT: Condition.SGE, Condition.SGE: Condition.SLT,
    Condition.SLE: Condition.SGT, Condition.SGT: Condition.SLE,
    Condition.ULT: Condition.UGE, Condition.UGE: Condition.ULT,
    Condition.ULE: Condition.UGT, Condition.UGT: Condition.ULE,
}

_SWAPPED: Final[dict[Condition, Condition]] = {
    Condition.EQ: Condition.EQ, Condition.NE: Condition.NE,
    Condition.SLT: Condition.SGT, Condition.SGT: Condition.SLT,
    Condition.SLE: Condition.SGE, Condition.SGE: Condition.SLE,
    Condition.ULT: Condition.UGT, Condition.UGT: Condition.ULT,
    Condition.ULE: Condition.UGE, Condition.UGE: Condition.ULE,
}


_REGISTRY: dict[str, Op] = {}


def register(op: Op) -> Op:
    """Add *op* to the registry of known operations."""
    if op.name in _REGISTRY:
        raise ValueError("".join(("operation '", op.name, "' is already registered")))
    _REGISTRY[op.name] = op
    return op


def lookup(name: str) -> Op | None:
    """Return the operation called *name*, if one is registered."""
    return _REGISTRY.get(name)


def registered() -> list[Op]:
    """Every registered operation."""
    return list(_REGISTRY.values())


# -- the architecture-neutral operations ---------------------------------------
#
# Written in three-address form with the destination first.  A target whose
# instructions take two operands lowers the three-address form itself.

MOVE: Final[Op] = register(Op("move", 1))
PLUS: Final[Op] = register(Op("plus", 2))
MINUS: Final[Op] = register(Op("minus", 2))
TIMES: Final[Op] = register(Op("times", 2))
AND: Final[Op] = register(Op("and", 2))
OR: Final[Op] = register(Op("or", 2))
XOR: Final[Op] = register(Op("xor", 2))
NEG: Final[Op] = register(Op("neg", 1))
NOT: Final[Op] = register(Op("not", 1))
SHIFT_LEFT: Final[Op] = register(Op("shift_left", 2))
SHIFT_RIGHT: Final[Op] = register(Op("shift_right", 2))
#: Shifting right brings in copies of the sign rather than zeroes, which is what
#: a signed value wants and what an unsigned one must not have.
SHIFT_RIGHT_SIGNED: Final[Op] = register(Op("shift_right_signed", 2))
DIVIDE: Final[Op] = register(Op("divide", 2))
#: The larger and the smaller of two.  Only the floating-point formats have
#: an instruction for them on every one of these machines; an integer one is
#: a comparison and a conditional move, which is what saturating arithmetic
#: is built from already.
LARGER: Final[Op] = register(Op("larger", 2))
SMALLER: Final[Op] = register(Op("smaller", 2))
COMPARE: Final[Op] = register(Op("compare", 2, has_result=False))
CALL: Final[Op] = register(Op("call", 1, has_result=False))
RETURN: Final[Op] = register(Op("return", 0, has_result=False, is_terminator=True))
TRAP: Final[Op] = register(Op("trap", 0, has_result=False, is_terminator=True))
