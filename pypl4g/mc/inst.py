"""An instruction of the symbolic assembler."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..source.location import INVALID_SPAN, Span
from .desc import InstDesc
from .operand import MCOperand
from .reg import Reg


@dataclass(slots=True, eq=False)
class MCInst:
    """One selected instruction: a table row plus its operands.

    The span is carried through to the encoder so that a constraint the hardware
    imposes can be reported against the source that asked for it.  That is what
    will let inline assembly produce real diagnostics rather than assertions.
    """

    desc: InstDesc
    operands: tuple[MCOperand, ...] = ()
    span: Span = INVALID_SPAN
    prefixes: frozenset[str] = field(default_factory=frozenset)
    #: Registers this instruction destroys beyond what its row says.  A call is
    #: the one that has any: which registers it destroys is the callee's to say
    #: and not the instruction's, and two functions of one compilation may say
    #: different things.  They are here rather than in the row for that reason,
    #: and kept apart from the row's own so that a pass asking what *this*
    #: function used does not count what something it called used.
    clobbers: tuple[Reg, ...] = ()
    #: Registers this instruction reads beyond what its row says.  A call is
    #: again the one that has any: the arguments were put in registers by the
    #: instructions before it, and what says those registers are still wanted
    #: when it happens is the call itself.  Without this a register holding an
    #: argument would look dead from the moment it was written.
    reads: tuple[Reg, ...] = ()

    @property
    def mnemonic(self) -> str:
        """The name of the instruction in the table."""
        return self.desc.mnemonic

    def render(self) -> str:
        """A readable form, for the debugging dump."""
        if not self.operands:
            return self.desc.mnemonic
        return "".join((self.desc.mnemonic, " ",
                        ", ".join(o.render() for o in self.operands)))
