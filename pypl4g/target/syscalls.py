"""The number each system call has, per architecture.

The compiler holds this because the compiler is what knows which architecture it
is building for, and a module has no other way to ask: `\N{APL FUNCTIONAL SYMBOL QUAD}sc@write` is 1 on
x86-64 and 64 on the other two, and a standard library written once has to be
able to say `write` and mean the right one.

It is a table of somebody else's numbers, which is a thing to keep small and
honest: what is here is what the runtime and the standard library ask for, each
number checked against the kernel's own table for that architecture, and a call
nothing asks for is not here.  Adding one is a line per architecture.

Two of the three share the numbering the kernel calls generic, so they are
written once and the third is written out.  `io_uring` came after the generic
table was frozen and has the same numbers everywhere.
"""

from __future__ import annotations

from typing import Final, Mapping

#: What the two architectures on the kernel's generic numbering call things.
_GENERIC: Final[Mapping[str, int]] = {
    "openat": 56,
    "close": 57,
    "pipe2": 59,
    "read": 63,
    "write": 64,
    "readv": 65,
    "writev": 66,
    "exit": 93,
    "exit_group": 94,
    "waitid": 95,
    "munmap": 215,
    "mmap": 222,
    "wait4": 260,
    "io_uring_setup": 425,
    "io_uring_enter": 426,
    "io_uring_register": 427,
    # One the other way round: the generic table has it and x86-64 does not,
    # which is what makes a call missing from *this* architecture a thing a
    # module has to ask about rather than a thing that cannot happen.
    "fadvise64_64": 223,
}

#: And what x86-64, which was numbered before that table existed, calls them.
#: It has calls the generic table left out, `open` among them -- the newer
#: architectures took only `openat`, which is why a name here and not above is a
#: thing a module has to ask about inside an arm the compiler settles.
_X86_64: Final[Mapping[str, int]] = {
    "read": 0,
    "write": 1,
    "close": 3,
    "open": 2,
    "pipe2": 293,
    "readv": 19,
    "writev": 20,
    "mmap": 9,
    "munmap": 11,
    "wait4": 61,
    "exit": 60,
    "exit_group": 231,
    "waitid": 247,
    "openat": 257,
    "io_uring_setup": 425,
    "io_uring_enter": 426,
    "io_uring_register": 427,
}

#: Every architecture this compiler generates for, by the name a triple gives it.
NUMBERS: Final[Mapping[str, Mapping[str, int]]] = {
    "x86_64": _X86_64,
    "aarch64": _GENERIC,
    "riscv64": _GENERIC,
}

#: Every call any of them names, for saying what a mistyped one could have been.
KNOWN: Final[tuple[str, ...]] = tuple(sorted(
    {name for table in NUMBERS.values() for name in table}))


def number_of(architecture: str, call: str) -> int | None:
    """The number *call* has on *architecture*, where it has one."""
    return NUMBERS.get(architecture, {}).get(call)
