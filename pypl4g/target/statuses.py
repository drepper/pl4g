"""The exit statuses the runtime reserves, and one per kind of stop.

A program the runtime stops leaves through exit with a status, never through a
signal.  A signal is not a status: a shell reports one as 128 plus the number,
which collides with whatever the program might have chosen to exit with, and a
caller has to know to look for it.  A program that dies of a signal really did
die of one, and that is worth being able to believe.

What makes a status enough to say it with is that a range of them is reserved:

| Range   | Whose                                                        |
|---------|--------------------------------------------------------------|
| 0-63    | the program's own, from the startup function's result         |
| 64-78   | `<sysexits.h>`, which is nobody's to take                     |
| 79-127  | the runtime's, for a stop it reports                          |
| 128-255 | a signal the program really died of, as the shell reports it  |

**The middle range is left alone.**  `<sysexits.h>` has named 64 through 78
since 4.0BSD -- `EX_USAGE`, `EX_DATAERR`, `EX_NOINPUT` and the rest -- and a
great deal of software written since reads them.  A runtime that stopped a
program with 64 would be saying "the command line was wrong" to everything that
knows the convention, which is the opposite of what it means.  So the runtime
begins at `EX__MAX` plus one and the two never meet.

**Each kind of stop has a number.**  The message says which operation, in which
function, at which line, and says it better than a number could -- but a message
is for a person and a status is for a program, and a caller that wants to retry
a temporary failure and give up on a permanent one is reading the status.  An
answer that will not fit, a division with no answer, an index outside its array
and an allocation that could not be met are four different things and a caller
may act on them differently.

The numbers are settled here and nowhere else.  Everything that stops a program
names one of these, and a program that grows a new kind of stop grows a number
here rather than reaching for the general one.
"""

from __future__ import annotations

from typing import Final

#: What `<sysexits.h>` has taken, which is `EX__BASE` through `EX__MAX`.
SYSEXITS_FIRST: Final[int] = 64
SYSEXITS_LAST: Final[int] = 78

#: The lowest and highest of the reserved range, for whatever has to say so.
FIRST: Final[int] = SYSEXITS_LAST + 1
LAST: Final[int] = 127

#: A stop the runtime has no more particular number for.  Nothing uses it today
#: -- every kind of stop there is has one of its own -- and it is here for the
#: kind that has not been thought of yet, so that reaching for it is a smaller
#: thing than inventing a number in the middle of the range.
GENERAL: Final[int] = FIRST

#: An answer that will not fit the type it was asked for: a sum, a difference, a
#: product, a shift, a rotation, and the one division whose answer is one past
#: the largest its type holds.
OVERFLOW: Final[int] = FIRST + 1

#: An operation whose answer is not a number: a floating-point one that came to
#: an infinity or to not-a-number.  It is not the one above because the two are
#: different things to a caller -- one is an answer too large to carry and the
#: other is no answer there could be.
NO_ANSWER: Final[int] = FIRST + 2

#: An index or a slice outside what it names.  What was asked for is not there,
#: which is a thing about the data and not about the arithmetic.
OUT_OF_RANGE: Final[int] = FIRST + 3

#: A number turned into a code point that is not one.  Unicode has a last code
#: point and it is not the last number, so this is the gap between the two.
NOT_A_CODE_POINT: Final[int] = FIRST + 4

#: A walk over a list used after it has ended, or moved off either end of what
#: it walks.  The walk itself is the thing that went wrong, which is why it is
#: not the index number above.
WALK_ENDED: Final[int] = FIRST + 5

#: The system would give no more memory.  A caller may well do something about
#: this one -- wait, or ask for less -- which is the argument for a number of
#: its own in one line.
OUT_OF_MEMORY: Final[int] = FIRST + 6

#: The program ran off the bottom of its stack.  It has a number of its own
#: because what to do about it -- build with a larger stack, or find the
#: recursion that does not end -- is a different thing to do, and because it is
#: the one stop a program cannot report for itself: there is no room left to
#: report it in, which is why the handler that does runs on a stack of its own.
STACK_OVERFLOW: Final[int] = FIRST + 7

#: The processor is not the one the program was built for, or the system has
#: turned off registers the program uses.  It is the one stop that happens
#: before the program has run at all.
WRONG_PROCESSOR: Final[int] = FIRST + 8

#: A test the binary runs answered that it did not pass.  Every one of them is
#: run and every failure named before this is reached: a run that stopped at the
#: first would make a reader fix one thing and run again to be told the next.
TESTS_FAILED: Final[int] = FIRST + 9

#: A condition written in a signature that did not hold on the way in.  It is
#: the *caller* that was wrong, which is why it is not the one below: a caller
#: acting on a status can tell "I called this wrongly" from "the thing I called
#: is broken", and those are two different things to do about it.
PRE_CONDITION: Final[int] = FIRST + 10

#: A condition written in a signature that did not hold on the way out.  The
#: callee is what was wrong here, whatever it was called with.
POST_CONDITION: Final[int] = FIRST + 11
