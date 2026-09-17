"""The exit statuses the runtime reserves.

A program the runtime stops leaves through exit with a status, never through a
signal.  A signal is not a status: a shell reports one as 128 plus the number,
which collides with whatever the program might have chosen to exit with, and a
caller has to know to look for it.  A program that dies of a signal really did
die of one, and that is worth being able to believe.

What makes a status enough to say it with is that a range of them is reserved:

| Range   | Whose                                                      |
|---------|------------------------------------------------------------|
| 0-63    | the program's own, from the startup function's result       |
| 64-127  | the runtime's, for a stop it reports                         |
| 128-255 | a signal the program really died of, as the shell reports it |

So a caller can tell a program that chose to fail from one that was stopped,
which is the thing a signal could not be made to say without also lying about
how the program ended.
"""

from __future__ import annotations

from typing import Final

#: The lowest and highest of the reserved range, for whatever has to say so.
FIRST: Final[int] = 64
LAST: Final[int] = 127

#: A stop the runtime has no more particular number for yet.  Everything a fault
#: reports leaves through this today: what went wrong is in the message, which
#: names the operation, the function and the line, and a number could say less.
GENERAL: Final[int] = FIRST

#: A test the binary runs answered that it did not pass.  Every one of them is
#: run and every failure named before this is reached: a run that stopped at the
#: first would make a reader fix one thing and run again to be told the next.
TESTS_FAILED: Final[int] = FIRST + 2

#: The program ran off the bottom of its stack.  It has a number of its own
#: because what to do about it -- build with a larger stack, or find the
#: recursion that does not end -- is a different thing to do, and because it is
#: the one stop a program cannot report for itself: there is no room left to
#: report it in, which is why the handler that does runs on a stack of its own.
#: `runtime/io.c` says the same number; the two are one table in two languages.
STACK_OVERFLOW: Final[int] = FIRST + 3

#: The processor is not the one the program was built for.  It has a number of
#: its own because it is the one stop that happens before the program has run at
#: all, and because what to do about it -- build for an older level, or find a
#: newer machine -- is a different thing to do.
WRONG_PROCESSOR: Final[int] = FIRST + 1
