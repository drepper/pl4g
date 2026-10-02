"""Everything the compiler has to say about one compilation.

Two kinds of thing go in here.  A *diagnostic* says something is wrong, or worth
saying, about what the program wrote.  A *choice* says the compiler settled
something the program did not state: what it dropped, what it placed where, how
long it worked out that a reference lives.  They are different things and the
``kind`` field keeps them apart, but they belong in one log and in one order,
because the question a reader has -- what happened to my program? -- is not a
question about only one of them.

The log is written by ``--report-log``, which a build can keep beside the
binary.  It is machine-readable and its ``kind`` fields are stable, so that
something reading it can ask a question -- which of my functions were dropped,
what did the compiler warn about -- without matching on prose.  The prose is
there too, for a person.

Recording costs a small record per report and happens whether or not anyone
asked for the log, because a report recorded only when someone is watching is
a report that cannot be checked in a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from ..source.location import INVALID_SPAN, Span


class ReportKind(StrEnum):
    """What kind of report this is.

    These strings are the stable part: something reading the log matches on
    them, so one is never renamed and a new kind is a new member.  The first
    four are what the compiler said about the program, and carry the number the
    diagnostic catalog gives them; the rest are what it chose.
    """

    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"

    #: How a value's allocator is known: while compiling, so that the word it
    #: carries is never read, or only by asking the value.  One entry per name
    #: that holds something that points somewhere.
    ALLOCATOR = "allocator"
    #: A value copied into another allocator, because the one it was made in is
    #: not provably the one wanted, or not provably long enough lived.
    COPY_INTO_ALLOCATOR = "copy-into-allocator"
    #: A string or a list answered without its allocator, because every answer
    #: is made in one the caller names and adds back.
    ANSWER_THIN = "answer-thin"
    #: A string or a list kept two words wide where values of it join, its
    #: allocator held by the compiler; and one that carries its own there, with
    #: why.  One entry per block parameter of either kind.
    LEAN_VALUE = "lean-value"
    #: A call of a function to itself in tail position, turned into a jump back
    #: to its start; and one that is not, with why, the stack growing by a frame.
    TAIL_CALL = "tail-call"
    SELF_CALL = "self-call"
    FAT_VALUE = "fat-value"
    DROP_FUNCTION = "drop-function"
    DROP_VARIABLE = "drop-variable"
    DROP_LOCAL = "drop-local"
    DROP_CALL = "drop-call"
    ANSWER_IN_STORAGE = "answer-in-storage"
    #: A condition the compiler settled, so that nothing of it reaches the
    #: binary.  A reader who wrote one wants to know it was free.
    CONDITION_HOLDS = "condition-holds"
    #: A condition this build asked not to emit.  One entry per clause, so that
    #: "which checks are in this binary" is a question the log answers rather
    #: than one a reader works out from the command line.
    CONDITION_DROPPED = "condition-dropped"
    #: A name a lambda brought in that its capture list did not write down,
    #: which is what `[=]` and `[&]` leave to the compiler.  One entry per
    #: name, so that "which variables were brought in" is a question the log
    #: answers without anything reading prose.
    CAPTURE = "capture"
    #: The name a lambda's code was given, so that a symbol in the binary can
    #: be matched back to the place it was written.
    NAME_LAMBDA = "name-lambda"
    #: A variable put in storage of its own rather than kept in a register,
    #: because something takes its address.
    PLACE_LOCAL = "place-local"
    #: A generic function compiled for one set of types.  The program wrote it
    #: once and said nothing about which types; which ones it was built for is
    #: what the calls turned out to ask for.
    INSTANTIATE = "instantiate"
    #: A call of a pure function that reads no memory, made where an earlier one
    #: with the same arguments was: it stands for that one.
    PURE_CALL_REUSED = "pure-call-reused"
    #: One moved out of a loop, nothing it is handed changing round the loop.
    PURE_CALL_HOISTED = "pure-call-hoisted"
    #: One worked out while compiling, every argument being a constant.
    PURE_CALL_FOLDED = "pure-call-folded"
    #: A generic function checked where it is written, against its requirements
    #: alone, so that no instantiation has anything left to find.
    GENERIC_CHECKED = "generic-checked"
    #: One checked there except for what asks what its types are -- a `comptime`
    #: construct -- which is checked where a call says.
    GENERIC_DEFERRED = "generic-deferred"
    #: A callee put where a caller called it.  What the log says is which
    #: function went where, and why it was worth it: a reader asking "where did
    #: my function go" is asking this and the drop below it.
    INLINE = "inline"
    #: How long the answer of one call lives.  The function promised only its
    #: parameter's lifetime, and which one that came to is worked out at the
    #: call; the program says it in neither place.
    LIFETIME = "lifetime"


#: What the severities of the diagnostic catalog are called here.  One name for
#: one thing: a reader asking the log for the errors asks for the same word the
#: compiler printed.
OF_SEVERITY: Final[dict[str, ReportKind]] = {
    "fatal": ReportKind.FATAL,
    "error": ReportKind.ERROR,
    "warning": ReportKind.WARNING,
    "note": ReportKind.NOTE,
}


@dataclass(frozen=True, slots=True)
class Report:
    """One thing the compiler said or chose, and what it was about."""

    kind: ReportKind
    #: What the report was about: the name the program gives it for a choice,
    #: and the diagnostic's own symbolic name for something the compiler said.
    #: Either way it is the stable half, and the reason is the readable one.
    subject: str
    #: Why, in a sentence a person can read.  For a diagnostic it is the message
    #: as it was printed, which is the same sentence and not a second one.
    reason: str
    #: Where the subject is written, so that a reader can be pointed at it.
    span: Span = INVALID_SPAN
    #: The number the catalog gives it, where this is something the compiler
    #: said.  A choice has none: nothing is wrong, so there is nothing to look
    #: up, quiet or turn into an error.
    number: int | None = None


@dataclass(slots=True)
class ReportLog:
    """Everything said and chosen about one compilation, in the order it happened."""

    entries: list[Report] = field(default_factory=list)

    def record(self, kind: ReportKind, subject: str, reason: str,
               span: Span = INVALID_SPAN, number: int | None = None) -> Report:
        """Record one report and return it."""
        entry = Report(kind=kind, subject=subject, reason=reason, span=span,
                       number=number)
        self.entries.append(entry)
        return entry

    def said(self, severity: str, name: str, message: str, number: int,
             span: Span = INVALID_SPAN) -> Report | None:
        """Record something the compiler said, where it is one of the four.

        A severity the catalog gains and this does not know is not recorded
        rather than recorded as something it is not, since what a reader matches
        on is the kind.
        """
        kind = OF_SEVERITY.get(severity)
        if kind is None:
            return None
        return self.record(kind, name, message, span, number)

    def of_kind(self, kind: ReportKind) -> list[Report]:
        """Every report of one kind, for a test or a report to ask about."""
        return [e for e in self.entries if e.kind is kind]
