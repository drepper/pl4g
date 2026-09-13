"""Constants that have to be in memory rather than in an instruction.

Every integer a program writes can be built in a register: an instruction
carries it, or two or three instructions build it a piece at a time.  A
floating-point number cannot.  None of the three architectures has an
instruction that carries one, and building one bit by bit in an integer register
and moving it across costs more than reading it from memory -- so a
floating-point constant is put in the image beside the program's other constants
and loaded.

That is not a constant *pool* in the usual sense: nothing is shared between
functions beyond what sharing the same bytes gives, there is no per-function
island, and nothing is placed near the code that reads it.  The address is
computed from the program counter like any other, which is what keeps the image
free of anything a loader would have to patch.

Two constants with the same bytes are one constant.  The bytes and not the
number: the two zeroes of floating point have different bits and compare equal,
and a program that wrote both wrote two different things.
"""

from dataclasses import dataclass, field

from ..mc.asmbuilder import Assembler
from ..mc.symbol import SymBinding, SymKind, SymVisibility

#: Where they go.  They are constants in the fullest sense -- nothing writes
#: them and nothing can -- so the section that cannot be written is the one.
SECTION = ".rodata"


@dataclass(slots=True)
class Constants:
    """The constants a compilation had to put in memory, by their bytes."""

    by_bytes: dict[bytes, tuple[str, int]] = field(default_factory=dict)

    def symbol(self, data: bytes, alignment: int) -> str:
        """The symbol *data* is stored under, adding it if it is new."""
        found = self.by_bytes.get(data)
        if found is not None:
            return found[0]
        name = "".join((".Lconst.", str(len(self.by_bytes))))
        self.by_bytes[data] = (name, alignment)
        return name

    def emit(self, asm: Assembler) -> None:
        """Put every one of them into the image."""
        if not self.by_bytes:
            return
        widest = max(alignment for _, alignment in self.by_bytes.values())
        asm.section(SECTION, writable=False, alignment=widest)
        for data, (name, alignment) in self.by_bytes.items():
            asm.align(alignment)
            symbol = asm.label(name, binding=SymBinding.LOCAL, kind=SymKind.OBJECT,
                               visibility=SymVisibility.HIDDEN)
            asm.bytes(data)
            asm.end_label(symbol)
