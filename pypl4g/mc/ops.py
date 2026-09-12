"""The operations the assembler builder accepts.

An operation is a value, not a member of a closed enumeration, so a target adds
its own without touching this module: ``X86Op.SYSCALL`` is built the same way
``PLUS`` is.  ``Assembler.op`` accepts any of them and asks the target to select
an encoding.
"""

from dataclasses import dataclass
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
COMPARE: Final[Op] = register(Op("compare", 2, has_result=False))
CALL: Final[Op] = register(Op("call", 1, has_result=False))
RETURN: Final[Op] = register(Op("return", 0, has_result=False, is_terminator=True))
TRAP: Final[Op] = register(Op("trap", 0, has_result=False, is_terminator=True))
