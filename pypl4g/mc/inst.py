"""An instruction of the symbolic assembler."""

from dataclasses import dataclass, field

from ..source.location import INVALID_SPAN, Span
from .desc import InstDesc
from .operand import MCOperand


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
