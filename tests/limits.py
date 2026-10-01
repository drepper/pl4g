"""How much a program run by the tests, or by hand through `bin/pl4g-run`, may take.

A compiled program that runs away -- a loop that never ends, a join in a loop whose
old strings nothing gives back -- would otherwise take the machine with it: nothing
in a pl4g program stops it asking the system for more, the allocator stopping only
where the system says no.  So every program the tests start is started under limits,
which turn a runaway into the program's own `out of memory` stop, or a signal for
CPU time.

- **Data**, `RLIMIT_DATA`: what the heap and the arenas map counts against it, and
  what `qemu-user` reserves for the guest's address space does not, so the one limit
  holds natively and under emulation alike.
- **CPU time**, `RLIMIT_CPU`: a loop that never ends ends.

Each test worker is limited as well, more generously, so that anything it starts
that the limits below were not applied to is still bounded: the limits a process
has, its children inherit.
"""

from __future__ import annotations

import resource
from typing import Final

#: What one program may map, and how long it may compute.  The largest program the
#: suite runs takes a few megabytes.
PROGRAM_DATA: Final[int] = 512 * 1024 * 1024
PROGRAM_CPU_SECONDS: Final[int] = 120

#: What a test worker and everything it starts may map, the compiler included.
WORKER_DATA: Final[int] = 2 * 1024 * 1024 * 1024


def limit_program() -> None:
    """Put the program limits on the process about to start one (a `preexec_fn`)."""
    resource.setrlimit(resource.RLIMIT_DATA, (PROGRAM_DATA, PROGRAM_DATA))
    resource.setrlimit(resource.RLIMIT_CPU,
                       (PROGRAM_CPU_SECONDS, PROGRAM_CPU_SECONDS))


def limit_worker() -> None:
    """Put the worker limit on this process, and so on all it starts."""
    soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    wanted = WORKER_DATA if hard == resource.RLIM_INFINITY else min(WORKER_DATA,
                                                                    hard)
    if soft == resource.RLIM_INFINITY or soft > wanted:
        resource.setrlimit(resource.RLIMIT_DATA, (wanted, hard))


def prefix() -> list[str]:
    """A command prefix applying the program limits, for a command run as a list."""
    return ["prlimit", "".join(("--data=", str(PROGRAM_DATA))),
            "".join(("--cpu=", str(PROGRAM_CPU_SECONDS))), "--"]
