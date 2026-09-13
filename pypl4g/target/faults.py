"""Reporting a fault, and stopping.

A fault is a thing the program asked for that cannot be done -- an addition
whose answer will not fit is the first and so far the only one.  The
specification says such a program stops and says why, and this is the whole of
how it says why.

**Everything that can be worked out beforehand is.**  The compiler knows which
operation it was, in which function, at which line, so the message is built
whole at compile time and put in the image as a string.  What runs at the moment
of the fault is a write and a trap: no formatting, no number to turn into text,
no allocation, nothing that could itself fail.  That matters more here than
anywhere else in the compiler, because this is the code that runs when something
has already gone wrong.

**It goes out through a raw system call**, standard error being the only place
a program that depends on nothing from the system can write to.  Whether the
descriptor is open is not asked: a program started with it closed would have the
message go to whatever was opened next, and that is still better than nowhere.

**It ends by trapping rather than by exiting.**  The program dies by a signal at
the point of the fault with its stack still standing, which is what a debugger
wants to be handed, and the message has already been written by the time the
signal arrives.  A status would say less and would be indistinguishable from a
program that meant to exit with it.
"""

from dataclasses import dataclass, field
from typing import Sequence

from ..mc.asmbuilder import Assembler
from ..mc.symbol import SymBinding, SymKind, SymVisibility
from ..source.location import Span
from ..source.manager import SourceManager

#: Where the messages go.  They are constants of the program in the fullest
#: sense: nothing reads them but the write that reports them.
SECTION = ".rodata"


@dataclass(slots=True)
class Messages:
    """The messages a compilation needs, and the symbols they are known by.

    One message per distinct text, so a program that faults the same way twice
    carries one string.
    """

    by_text: dict[str, str] = field(default_factory=dict)

    def symbol(self, text: str) -> str:
        """The symbol the message *text* is stored under, adding it if new."""
        found = self.by_text.get(text)
        if found is None:
            found = "".join((".Lfault.", str(len(self.by_text))))
            self.by_text[text] = found
        return found

    @property
    def wanted(self) -> bool:
        """Whether anything asked for a message, which is whether the helper a
        fault leaves through has to be emitted at all."""
        return bool(self.by_text)

    def emit(self, asm: Assembler) -> None:
        """Put every message into the image."""
        if not self.by_text:
            return
        asm.section(SECTION, writable=False, alignment=1)
        for text, name in self.by_text.items():
            symbol = asm.label(name, binding=SymBinding.LOCAL, kind=SymKind.OBJECT,
                               visibility=SymVisibility.HIDDEN)
            asm.bytes(text.encode("utf-8"))
            asm.end_label(symbol)


def describe(what: str, function: str, span: Span,
             sources: "SourceManager | None") -> str:
    """The message a fault of kind *what* in *function* reports.

    Written the way a compiler's own diagnostics are written -- the place first,
    then what happened -- so that a person reading the two together is reading
    one format and not two.
    """
    where = ""
    if sources is not None and span.is_valid:
        located = sources.position(span.start)
        if located is not None:
            where = "".join((located.path, ":", str(located.line), ":",
                             str(located.column), ": "))
    return "".join((where, "pl4g: ", what, " in '", function, "'\n"))
