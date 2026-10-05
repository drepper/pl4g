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
    #: What became of something a loop can walk: walked where it was made,
    #: built into an array where one was asked for, or handed on to a call
    #: with its description in the caller's frame -- and what it borrows.
    ITERATOR = "iterator"
    #: A value of a type marked `@[closed_by(f)]` moved from one variable to
    #: another, into a call, or out as an answer -- never copied, so the one it
    #: came from holds nothing from here on.
    MOVE = "move"
    #: Where such a variable's lifetime ended other than by a move, which is
    #: logged as `move` alone: the parameter of the function that closes it.
    LIFETIME_ENDED = "lifetime-ended"
    #: Where a variable's scope ended: after its last use -- a use in a loop
    #: being one at the loop's end, one in a deferred statement one at the end
    #: of its block -- or where a name holding a reference to it ends.  Each
    #: is on the line after the one the scope ends on.
    SCOPE_ENDED = "scope-ended"
    #: A variable whose type the program did not write in full -- `let x :=`,
    #: `let x: mut =`, `⟦⟧` alone, a shape stated in part, a name a loop or a
    #: taking apart binds -- and the type it was given.  A reader asking "what
    #: is this" of a name the program never said a type for is asking this.
    TYPE_INFERRED = "type-inferred"


#: What the severities of the diagnostic catalog are called here.  One name for
#: one thing: a reader asking the log for the errors asks for the same word the
#: compiler printed.
OF_SEVERITY: Final[dict[str, ReportKind]] = {
    "fatal": ReportKind.FATAL,
    "error": ReportKind.ERROR,
    "warning": ReportKind.WARNING,
    "note": ReportKind.NOTE,
}


#: The kinds that are something the compiler said rather than chose.
_SAID: Final[frozenset[ReportKind]] = frozenset(OF_SEVERITY.values())


class ReportClass(StrEnum):
    """How much a report asks of a reader, least first.

    An error stopped the program being built and a warning is something it
    should not have done; everything else is a note -- a note the compiler said,
    and every choice it made, which is what the program did not state and is
    nothing wrong.
    """

    NOTE = "note"
    WARNING = "warning"
    ERROR = "error"


#: The kinds that are not notes.
_CLASS_OF: Final[dict[ReportKind, ReportClass]] = {
    ReportKind.FATAL: ReportClass.ERROR,
    ReportKind.ERROR: ReportClass.ERROR,
    ReportKind.WARNING: ReportClass.WARNING,
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

    @property
    def report_class(self) -> ReportClass:
        """Whether this is an error, a warning or a note."""
        return _CLASS_OF.get(self.kind, ReportClass.NOTE)


@dataclass(slots=True)
class ReportLog:
    """Everything said and chosen about one compilation, in the order it happened."""

    entries: list[Report] = field(default_factory=list)
    #: Where the files marked `@[system]` are, as the range of locations each
    #: covers.  What the compiler chose or worked out about something in one is
    #: not recorded -- a reader of the program did not write a system module and
    #: cannot change it, as nothing is said of a system header in C -- while what
    #: the compiler *said*, an error above all, is.
    system_files: list[tuple[int, int]] = field(default_factory=list)

    def record(self, kind: ReportKind, subject: str, reason: str,
               span: Span = INVALID_SPAN, number: int | None = None) -> Report:
        """Record one report and return it -- not kept where it is a choice
        about something in a system module."""
        entry = Report(kind=kind, subject=subject, reason=reason, span=span,
                       number=number)
        if kind in _SAID or not self.in_system(span):
            self.entries.append(entry)
        return entry

    def in_system(self, span: Span) -> bool:
        """Whether *span* begins in a file marked `@[system]`."""
        return span.is_valid and any(a <= span.start < b
                                     for a, b in self.system_files)

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
