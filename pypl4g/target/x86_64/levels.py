"""The microarchitecture levels x86-64 defines, and what asking for one means.

The architecture's own documentation names four sets of features and calls them
`x86-64-v1` through `x86-64-v4`, each holding the one before it.  They exist
because "x86-64" has meant several quite different machines over twenty-five
years, and a program built for the newest of them runs on none of the others.

What this compiler does with a level has two halves.  The one that matters later
is that code generation may use what the level allows; nothing does yet, every
instruction this compiler emits being in the oldest level.  The one that matters
now is that a program says at its own entry point whether the processor it has
been started on can run it, and stops if it cannot -- which is the difference
between a clear line of text and an illegal instruction in the middle of
somebody's data.

**The features are asked of the processor and not of the system.**  `CPUID` is
the instruction the architecture provides for exactly this question, it is
answered the same way on every operating system, and it cannot be out of date
with respect to the processor the program is actually running on.  Reading
`/proc/cpuinfo` would be a file that may not be mounted, a format that has
changed, and a question asked of the kernel about a processor rather than of the
processor.
"""

from dataclasses import dataclass
from typing import Final, Sequence


@dataclass(frozen=True, slots=True)
class Requirement:
    """One question put to `CPUID`, and the bits the answer must have set.

    *leaf* and *subleaf* go into `EAX` and `ECX`; *register* is which of the four
    answers holds the bits, named as the architecture names it.
    """

    leaf: int
    subleaf: int
    register: str
    bits: int
    #: What the bits are, for the message a program that cannot run reports.
    names: tuple[str, ...]


def _bits(*numbers: int) -> int:
    """One mask out of the bit numbers the architecture's tables give."""
    found = 0
    for number in numbers:
        found |= 1 << number
    return found


#: What each level adds, in the order the architecture's documentation lists
#: them.  A level requires its own row and every row above it.
#:
#: v1 is what every x86-64 processor has -- it is what the architecture *is* --
#: so there is nothing to ask about it and nothing to check.
_ADDED: Final[dict[str, tuple[Requirement, ...]]] = {
    "v1": (),
    "v2": (
        # SSE3, SSSE3, CMPXCHG16B, SSE4.1, SSE4.2, POPCNT
        Requirement(1, 0, "ecx", _bits(0, 9, 13, 19, 20, 23),
                    ("SSE3", "SSSE3", "CMPXCHG16B", "SSE4.1", "SSE4.2", "POPCNT")),
        # LAHF/SAHF in long mode
        Requirement(0x80000001, 0, "ecx", _bits(0), ("LAHF-SAHF",)),
    ),
    "v3": (
        # FMA, MOVBE, OSXSAVE, AVX, F16C
        Requirement(1, 0, "ecx", _bits(12, 22, 27, 28, 29),
                    ("FMA", "MOVBE", "OSXSAVE", "AVX", "F16C")),
        # BMI1, AVX2, BMI2
        Requirement(7, 0, "ebx", _bits(3, 5, 8), ("BMI1", "AVX2", "BMI2")),
        # LZCNT, which the architecture counts under ABM
        Requirement(0x80000001, 0, "ecx", _bits(5), ("LZCNT",)),
    ),
    "v4": (
        # AVX512F, AVX512DQ, AVX512CD, AVX512BW, AVX512VL
        Requirement(7, 0, "ebx", _bits(16, 17, 28, 30, 31),
                    ("AVX512F", "AVX512DQ", "AVX512CD", "AVX512BW", "AVX512VL")),
    ),
}

#: The levels, oldest first, which is the order one contains another in.
NAMES: Final[tuple[str, ...]] = ("v1", "v2", "v3", "v4")

#: What a program is built for unless it says otherwise.  The newest, because a
#: program that will not run says so the moment it is started, while one built
#: for the oldest machine quietly leaves everything on the table -- and which of
#: those two a reader would rather have found out about is not a close question.
DEFAULT: Final[str] = "v4"


def requirements(level: str) -> "Sequence[Requirement]":
    """Everything a program of this level needs, its own and what it contains."""
    found: list[Requirement] = []
    for name in NAMES:
        found.extend(_ADDED[name])
        if name == level:
            break
    return _merged(found)


def _merged(found: "Sequence[Requirement]") -> "list[Requirement]":
    """One question per leaf and register, so that `CPUID` is asked once each."""
    order: list[tuple[int, int, str]] = []
    bits: dict[tuple[int, int, str], int] = {}
    names: dict[tuple[int, int, str], tuple[str, ...]] = {}
    for one in found:
        key = (one.leaf, one.subleaf, one.register)
        if key not in bits:
            order.append(key)
            bits[key] = 0
            names[key] = ()
        bits[key] |= one.bits
        names[key] = names[key] + one.names
    return [Requirement(leaf, subleaf, register, bits[(leaf, subleaf, register)],
                        names[(leaf, subleaf, register)])
            for leaf, subleaf, register in order]


def described(level: str) -> str:
    """What a program of this level tells a processor that cannot run it."""
    return "".join(("pl4g: this program was built for x86-64-", level,
                    " and this processor does not have it\n"))
