"""How a value narrower than a register is held, and what has to keep it so.

**A value of an integer type of width w is held in a register with every bit
above w equal to the zero- or sign-extension of the value, according to whether
the type is signed.**  That is the invariant the rest of code generation rests
on: a comparison, a store and an arithmetic operation all read the whole
register, so a value whose upper bits say something other than what the type
says is a value that will be read wrongly by one of them.

Nearly everything maintains it without being asked.  A load says in its own
instruction whether it widens by zero or by sign.  A constant is materialized as
the whole pattern.  A comparison answers with one or zero.  `and`, `or` and
`exclusive or` of two values that satisfy the invariant satisfy it again, since
the bits above the width are then all the same bit on both sides.

One thing does not.  The complement of an unsigned value sets every bit above
its width, where the value it stands for has zeroes there -- `~15` as a `u8` is
240, and a register holding the complement of 15 holds neither 240 nor anything
that compares equal to it.  So a complement is followed by putting those bits
back, which is what this module is for.

Arithmetic is the other place the question arises, and it answers it differently:
an operation that saturates or that faults on overflow never produces a value
outside the type, so computing it at the full width of the register and clamping
leaves the invariant holding with nothing further to do.
"""

from typing import TYPE_CHECKING

from ..mc import ops
from ..mc.operand import MCImm, MCReg
from ..source.location import Span

if TYPE_CHECKING:
    from ..ir.types import Type
    from ..mc.asmbuilder import Assembler
    from ..mc.reg import Reg


def normalize(asm: "Assembler", ty: "Type", destination: "Reg",
              register_bits: int, span: Span) -> None:
    """Put the bits above a narrow unsigned value back to zero.

    Does nothing where there are no such bits -- a signed value, or one as wide
    as the register it is in -- which is most of the time.
    """
    from ..ir.types import EnumType, IntType

    if isinstance(ty, EnumType):
        # A value of an enumeration is held the way a value of the type that
        # holds it is, so the same question and the same answer.
        ty = ty.holder
    if not isinstance(ty, IntType) or ty.signed or ty.bits >= register_bits:
        return
    asm.op(ops.AND, destination, MCReg(destination),
           MCImm((1 << ty.bits) - 1, 32 if ty.bits < 32 else 64, signed=False),
           span=span)
