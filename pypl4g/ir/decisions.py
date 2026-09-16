"""What the compiler decided about a program, as opposed to what it reported.

A diagnostic says something is wrong.  A decision says the compiler chose
something the program did not state: what it dropped, what it placed where, what
it decided not to do.  Neither is the other, which is why they are kept apart --
a decision is not a warning, and burying "this function is not in your binary"
in a stream of warnings would either be noise or be missed.

The log this makes is written by ``--decision-log``, which a build can keep
beside the binary.  It is machine-readable and its ``kind`` fields are stable,
so that something reading it can ask a question -- which of my functions were
dropped? -- without matching on prose.  The prose is there too, for a person.

Recording costs a small record per decision and happens whether or not anyone
asked for the log, because a decision recorded only when someone is watching is
a decision that cannot be checked in a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..source.location import INVALID_SPAN, Span


class DecisionKind(StrEnum):
    """What kind of decision was made.

    These strings are the stable part: something reading the log matches on
    them, so one is never renamed and a new kind is a new member.
    """

    DROP_FUNCTION = "drop-function"
    DROP_VARIABLE = "drop-variable"
    DROP_LOCAL = "drop-local"
    DROP_CALL = "drop-call"
    ANSWER_IN_STORAGE = "answer-in-storage"
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


@dataclass(frozen=True, slots=True)
class Decision:
    """One choice the compiler made, and what it was about."""

    kind: DecisionKind
    #: What the decision was about, named as the program names it.
    subject: str
    #: Why, in a sentence a person can read.
    reason: str
    #: Where the subject is written, so that a reader can be pointed at it.
    span: Span = INVALID_SPAN


@dataclass(slots=True)
class DecisionLog:
    """Everything decided about one compilation, in the order it was decided."""

    entries: list[Decision] = field(default_factory=list)

    def record(self, kind: DecisionKind, subject: str, reason: str,
               span: Span = INVALID_SPAN) -> Decision:
        """Record one decision and return it."""
        entry = Decision(kind=kind, subject=subject, reason=reason, span=span)
        self.entries.append(entry)
        return entry

    def of_kind(self, kind: DecisionKind) -> list[Decision]:
        """Every decision of one kind, for a test or a report to ask about."""
        return [e for e in self.entries if e.kind is kind]
