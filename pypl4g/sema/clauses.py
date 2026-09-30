"""What a condition written in a signature does in this build.

A clause says what must be true and a build says what to do about it, which is
the division g++ draws with `-fcontract-evaluation-semantic` and the one this
follows.  The choice is per build and not per clause: a clause that carried its
own semantic would be a clause a reader cannot price without knowing how the
binary was made, and a build that mixed them would report some violations and not
others with nothing in the program to say which.

**Only the condition half is a choice.**  A requirement -- a clause whose
operands are types -- is what makes the program type-check, so it is never
evaluated and never removable.  A build that could turn one off would be a build
in which a different program compiles, which is the one thing a switch here must
not be able to do.

**Nothing compiles only because the checks are off.**  Every semantic asks the
same questions of the clause: that it is a `bool`, that it is pure, that its
names mean something, that a `post` alone may write `⎕answer`.  What differs is
what reaches the binary.  That is what makes the switch safe to have, and it is
where Python's `assert` went wrong -- removable enough that nobody could rely on
it, so everybody wrote something else and the feature died.
"""

from __future__ import annotations

from enum import Enum


class Conditions(Enum):
    """The three things a build may ask a condition to do."""

    #: Evaluate it and stop the program where it does not hold, with the status
    #: the kind of condition has: 89 for a `pre` and 90 for a `post`.  The
    #: default, and what a program ships with.
    CHECK = "check"

    #: Evaluate nothing and emit nothing.  What the clause said is still checked
    #: for being sayable, and the report log says which conditions were dropped.
    IGNORE = "ignore"

    #: Evaluate it, say so where it does not hold, and go on.  For a build that
    #: wants every violation of a run rather than the first -- which is what the
    #: run of a binary's own tests already does, and for the same reason: a run
    #: that ended at the first would make a reader fix one thing and run again to
    #: be told the next.
    #:
    #: It costs more than the others and not only the write.  The helper it calls
    #: comes back, so the call destroys what the convention lets it destroy and
    #: the register allocator keeps nothing live across it -- where the stopping
    #: path calls something that never returns and so costs nothing to call.  A
    #: build that observes is a build that asked for that.
    OBSERVE = "observe"


#: What `assume` would be, and why it is not here.  A fourth semantic the C++
#: papers have: evaluate nothing and let the compiler act as though the condition
#: held.  It is the only one of the four that can make a program go wrong
#: silently -- a condition that does not hold would license a conclusion that is
#: false -- and this language has no undefined behaviour anywhere else: a program
#: that cannot answer stops and says so.  Adding a build flag that introduces it
#: is a larger decision than choosing what to do about a check, so it is written
#: down here and not offered.  Nothing about the three above forecloses it.
NOT_OFFERED: tuple[str, ...] = ("assume",)
