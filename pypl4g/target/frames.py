"""Where every function's return address is, so that a fault can say who called.

A fault says what went wrong and in which function, which is what the compiler
knew when it emitted the check.  What it cannot know is how that function was
reached, and that is what the stack holds -- but a stack is only bytes until
something says which of them is a return address.  This is what says so: one row
per function, giving where the function's code is, where its return address lies
once its frame stands, how far above its caller's stack pointer is, and the line
to print for it.

**A table rather than a chain of frame pointers.**  The other way to walk is to
have every function keep a pointer to its caller's frame, which costs a register
and two instructions in every function that calls, in every program, forever.
This costs nothing in any function: the rows are bytes in the image, read only
by a program that is already stopping.  It is also the thing a debugger and a
profiler would want next, where a chain is of no use to either.

**The rows are in address order**, which is the order the functions are emitted
in, so a walk finds the row for an address by taking the last row that begins at
or before it.  The last row is a sentinel: it begins where the last function
ends and names nothing, so an address past every function falls in it and the
walk stops rather than reading a row that is not there.

**The line is built whole at compile time**, the way a fault message is.  What
runs at the moment of the fault is a write of bytes that were already there:
no formatting, no number to turn into text, nothing that could itself fail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from ..mc.asmbuilder import Assembler
from ..mc.fixup import ABS64, MCFixup
from ..mc.operand import SymExpr
from ..mc.symbol import SymBinding, SymKind, SymVisibility

#: Where the table goes.  It is read only by the walk, and only once.
SECTION = ".rodata"

#: What the table is called, which is the one name the walk has to know.
SYMBOL = "__pl4g_frames"

#: What the label after the last function is called, which is where the
#: sentinel row begins.
END_SYMBOL = ".Lpl4g.frames.end"

#: How many bytes one row is.  Every field is a whole number of them and the
#: first is an address, so the row is eight-byte aligned and the walk steps by
#: a constant.
ROW_BYTES = 24

#: What a row's *return address* field holds where there is none: a function
#: that calls nothing keeps its return address in the register the call left it
#: in, and can therefore only ever be the innermost frame.
#:
#: It is the largest value four bytes hold *as a positive number*, and not the
#: largest they hold, because of what reads it: two of the three architectures
#: widen a four-byte load by copying nought into the rest and the third copies
#: the sign, so a value with the top bit set would be two different numbers
#: depending on which machine read it.  Every number in a row is therefore kept
#: below that bit, and the one that is a marker rather than a measurement is
#: this.
NO_RETURN_ADDRESS = 0x7FFFFFFF

def line_for(name: str) -> str:
    """What is printed for a frame of the function called *name*.

    Whole, with its indentation and its newline, because building it here is
    building it once and at compile time -- and because the alternative is
    three writes at the moment of a fault instead of one.
    """
    return "".join(("  called from '", name, "'\n"))


@dataclass(slots=True)
class Frame:
    """One function's row, as it stands before the addresses are known."""

    #: The symbol the function's code begins at.
    symbol: str
    #: Where its return address lies once its frame stands, measured from its
    #: stack pointer, or nothing where it keeps it in a register.
    return_at: int | None
    #: How far above its stack pointer its caller's is.
    caller_at: int
    #: What is printed for it.
    line: str


@dataclass(slots=True)
class Frames:
    """The rows a compilation collects, and the table they are emitted as."""

    rows: list[Frame] = field(default_factory=list)
    #: Whether anything will walk, which is whether a fault can happen at all.
    #: A program that cannot fault carries no table.
    wanted: bool = False

    def record(self, symbol: str, name: str, return_at: int | None,
               caller_at: int) -> None:
        """Note where the function called *name* keeps its return address.

        A function that keeps it nowhere gets no row.  That is a function that
        calls nothing, and no walk can ever meet one: every frame standing when
        a fault is reported has an outstanding call, the report itself being
        one.  So the rows are only for the functions that can be walked through,
        which is what keeps the table down to what it is for.
        """
        if return_at is None:
            return
        self.rows.append(Frame(symbol=symbol, return_at=return_at,
                               caller_at=caller_at, line=line_for(name)))

    def emit(self, asm: Assembler) -> None:
        """Put the table and the lines it names into the image.

        The lines follow the rows in the same section, and a row names its line
        by how far into the table it is rather than by its address -- which is
        one relocation per function instead of two, the walk having the table's
        own address in hand already.
        """
        if not self.wanted:
            return
        asm.section(SECTION, writable=False, alignment=8)
        asm.align(8)
        table = asm.label(SYMBOL, binding=SymBinding.LOCAL, kind=SymKind.OBJECT,
                          visibility=SymVisibility.HIDDEN)
        lines = _lines_of(self.rows)
        at = (len(self.rows) + 1) * ROW_BYTES
        for row in self.rows:
            offset, length = lines[row.line]
            asm.bytes(_row(row, at + offset, length),
                      (MCFixup(offset=0, kind=ABS64,
                               target=SymExpr(asm.symbol_named(row.symbol))),))
        # The sentinel: it begins where the last function ends and names no
        # line, which is what says the rows are at an end.  It is emitted even
        # where there are no rows at all, since what asks for the table is that
        # the program can stop with a message and not that it has anything to
        # say about who called.
        asm.bytes(_row(Frame(symbol=END_SYMBOL, return_at=None, caller_at=0,
                             line=""), 0, 0),
                  (MCFixup(offset=0, kind=ABS64,
                           target=SymExpr(asm.symbol_named(END_SYMBOL))),))
        for text in lines:
            asm.bytes(text.encode("utf-8"))
        asm.end_label(table)


def _lines_of(rows: Sequence[Frame]) -> dict[str, tuple[int, int]]:
    """Where each distinct line goes and how long it is.

    One copy per distinct text, so a program with two functions of one name --
    which a module system makes possible -- carries the line once.
    """
    found: dict[str, tuple[int, int]] = {}
    at = 0
    for row in rows:
        if row.line in found:
            continue
        length = len(row.line.encode("utf-8"))
        found[row.line] = (at, length)
        at += length
    return found


def _row(row: Frame, line_at: int, line_length: int) -> bytes:
    """One row's bytes, with the address left for the fixup to fill in."""
    assert row.return_at is None or row.return_at < NO_RETURN_ADDRESS
    assert row.caller_at < NO_RETURN_ADDRESS
    return b"".join((
        (0).to_bytes(8, "little"),
        (NO_RETURN_ADDRESS if row.return_at is None
         else row.return_at).to_bytes(4, "little"),
        row.caller_at.to_bytes(4, "little"),
        line_at.to_bytes(4, "little"),
        line_length.to_bytes(4, "little")))
