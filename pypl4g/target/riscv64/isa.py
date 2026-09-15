"""What a RISC-V program is built for, written the way the architecture writes it.

Every other architecture this compiler has names what a processor can do with a
short list of levels: `x86-64-v2` is a set somebody chose and published, and
asking for one is asking for the whole of it.  RISC-V does not work that way.
Its base is deliberately small and everything else is an extension that an
implementation may or may not have, so what a program is built for is a *list*,
and the architecture gives that list a spelling: `rv64gc`, `rv64imafd_zicsr`,
`rv64gc_zba_zbb_zbs`.  That spelling is what this module reads.

**The naming convention**, as the unprivileged specification states it:

- The strings are case insensitive.
- A string begins with the base: `rv32i`, `rv32e`, `rv64i` or `rv64e`, which
  says how wide an address is and whether the base is the reduced one.
- Each extension may carry a version, written major-`p`-minor -- `2p1` -- with
  `p0` left off where the minor number is zero.
- Single-letter extensions follow the base, in the order `imafdqlcbkjtpvh`.
  Underscores between them are allowed and mean nothing.
- Multi-letter extensions are `z` for the standard unprivileged ones, `s` for
  the privileged ones, `x` for everything non-standard, and each **must** be
  separated from what is beside it by an underscore -- which is what makes the
  string parseable at all, `zba` beside `zbb` being otherwise one long word.
- `g` is shorthand for `imafd_zicsr_zifencei`, the general-purpose set.

**Profiles** are the other half.  A list of extensions is precise and nobody
wants to type one, so the architecture also publishes named sets -- `rva23u64`
is "what a 64-bit application processor of 2023 has", and it is a name a person
can hold in their head.  The shape is a class letter (`a` for application, `b`
for bespoke, `i` for microcontroller), a year, an optional privilege level and
an optional width: `rva23`, `rva23u64`, `rva23s64`.

**What this compiler does with any of it** is a much shorter list than what it
reads, and that is deliberate.  A string says what the processor has; the
compiler looks up the few it can use today and ignores the rest, so that a
program which names an extension for the sake of a future compiler is not
refused by this one.  What is used today:

- `f` and `d`, without which floating point is refused outright -- there is no
  soft-float convention here, and quietly emitting one would be a program that
  does not run rather than one that does not build.
- `zfa`, which has the instruction that rounds a floating-point number where it
  stands.  Without it the same answer costs a round trip through an integer and
  a branch.

**Unknown extension names are refused.**  That is what every other compiler's
`-march` does, and the reason is a typo: `zfaa` is not an extension, and a
compiler that shrugged at it would silently build the slower program.  Adding an
extension this compiler learns to use, or one a profile names, is one row in the
table below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Mapping

#: The order the single-letter extensions are written in.  Nothing here depends
#: on it -- a set is a set however it was spelled -- but it is what the
#: canonical form below writes them in, and it is the architecture's own order.
SINGLE: Final[str] = "imafdqlcbkjtpvh"

#: What one extension brings with it.  Closed over, so `g` reaches `zicsr`
#: through nothing and `b` reaches `zbb` directly.
_IMPLIES: Final[dict[str, tuple[str, ...]]] = {
    # The general-purpose set, which is the one abbreviation the convention
    # gives a letter of its own.
    "g": ("i", "m", "a", "f", "d", "zicsr", "zifencei"),
    # The bit-manipulation set, which is three extensions under one letter.
    "b": ("zba", "zbb", "zbs"),
    "d": ("f",),
    "q": ("d",),
    "f": ("zicsr",),
    "zfa": ("f",),
    "zfh": ("f",),
    "zfhmin": ("f",),
    "zicntr": ("zicsr",),
    "zihpm": ("zicsr",),
    "zk": ("zkn", "zkr", "zkt"),
    "zkn": ("zbkb", "zbkc", "zbkx", "zkne", "zknd", "zknh"),
    "zks": ("zbkb", "zbkc", "zbkx", "zksed", "zksh"),
    "v": ("zve64d", "zvl128b"),
    "zve64d": ("zve64f",),
    "zve64f": ("zve64x", "zve32f"),
    "zve64x": ("zve32x", "zvl64b"),
    "zve32f": ("zve32x",),
    "zve32x": ("zvl32b",),
    "zvl128b": ("zvl64b",),
    "zvl64b": ("zvl32b",),
    "zvbb": ("zvkb",),
    "zvfhmin": ("zve32f",),
    "zvfh": ("zve32f", "zvfhmin", "zfhmin"),
    # What `Sha` comprises, which the profile document states as a list: it is
    # a name for the hypervisor half rather than an extension of its own.
    "sha": ("h", "ssstateen", "shcounterenw", "shvstvala", "shtvala",
            "shvstvecd", "shvsatpa", "shgatpa"),
    "zvkng": ("zvkb",),
    "zvksg": ("zvkb",),
    # The multiply-and-divide letter is the multiply half and the divide half,
    # and the atomic letter the two halves of what an atomic operation is.
    "m": ("zmmul",),
    "a": ("zaamo", "zalrsc"),
    # The compressed letter is the base of the compressed encoding plus
    # whichever of the two floating-point halves the program has -- which is
    # the one implication here that depends on something else being there.
    "c": ("zca",),
    "zcb": ("zca",),
    "zcd": ("zca",),
    "zcf": ("zca",),
    "zcmop": ("zca",),
    "zfhmin": ("f",),
}

#: What an extension brings only where something else is there too, and only at
#: the stated width where one is stated.  The compressed encoding covers the
#: floating-point loads and stores of whichever format the program has, and of
#: neither where it has none -- and the single-precision half of it exists only
#: at the narrower width, the wider one having spent those encodings on
#: something else.
_IMPLIES_WITH: Final[tuple[tuple[str, str, str, int | None], ...]] = (
    ("c", "d", "zcd", None),
    ("c", "f", "zcf", 32),
)

#: What version each extension is at, where it is not the first.  An extension
#: is at 1.0 until somebody revises it, and most never are, so what is written
#: down is the exceptions -- the base and the extensions old enough to have been
#: through a revision.  The numbers are the ones the architecture's own
#: documentation gives, and a test checks every one of them against what the GNU
#: assembler writes for the same string.
_VERSIONS: Final[dict[str, tuple[int, int]]] = {
    "i": (2, 1), "e": (2, 0), "m": (2, 0), "a": (2, 1), "f": (2, 2),
    "d": (2, 2), "q": (2, 2), "c": (2, 0),
    "zicsr": (2, 0), "zifencei": (2, 0), "zicntr": (2, 0), "zihpm": (2, 0),
    "zihintpause": (2, 0),
}

#: Every extension name this compiler knows.  It is not every extension there
#: is and is not meant to be: it is the single letters, everything the profiles
#: below name, and everything the code generator asks about.  A name outside it
#: is refused, because the name a program most often writes that is not an
#: extension is a misspelling of one that is.
KNOWN: Final[frozenset[str]] = frozenset((
    *SINGLE, "e", "g",
    # The unprivileged standard extensions, in the order the profiles name them.
    "zicsr", "zifencei", "zicntr", "zihpm", "ziccif", "ziccrse", "ziccamoa",
    "ziccamoc", "zicclsm", "zic64b", "zicbom", "zicbop", "zicboz", "zicond",
    "zihintpause", "zihintntl", "zimop", "zcmop", "zcb", "zcd", "zcf",
    "za64rs", "zawrs", "zabha", "zacas", "zama16b",
    "zfa", "zfh", "zfhmin", "zfbfmin", "zfinx", "zdinx",
    "zba", "zbb", "zbs", "zbc", "zbkb", "zbkc", "zbkx",
    "zk", "zkn", "zknd", "zkne", "zknh", "zkr", "zks", "zksed", "zksh", "zkt",
    "zve32x", "zve32f", "zve64x", "zve64f", "zve64d",
    "zvl32b", "zvl64b", "zvl128b", "zvl256b", "zvl512b", "zvl1024b", "zvkb",
    "zmmul", "zaamo", "zalrsc", "zca",
    "zvbb", "zvbc", "zvfh", "zvfhmin", "zvfbfmin", "zvfbfwma", "zvkt",
    "zvkng", "zvksg",
    "zicfilp", "zicfiss",
    # The privileged ones.
    "ss", "sm", "sha", "supm", "sspm", "ssnpm", "svbare", "sv32", "sv39",
    "sv48", "sv57", "svade", "svadu", "svinval", "svnapot", "svpbmt", "svvptc",
    "ssccptr", "sstvecd", "sstvala", "sscounterenw", "sstc", "sscofpmf",
    "ssu64xl", "ssstateen", "ssstrict", "sdtrig", "shcounterenw", "shvstvala",
    "shtvala", "shvstvecd", "shvsatpa", "shgatpa",
))

#: The profiles this compiler knows, and what each one guarantees.  Only the
#: mandatory extensions: an optional one is one the processor may not have, so a
#: program built for the profile cannot use it and there is nothing for the
#: compiler to learn from its name.
#:
#: The lists are the ones the profile documents give, written out rather than
#: derived, because a profile is a published set and not a formula.
_PROFILES: Final[dict[str, tuple[int, tuple[str, ...]]]] = {
    "rva23u64": (64, (
        "i", "m", "a", "f", "d", "c", "b", "v",
        "zicsr", "zicntr", "zihpm", "ziccif", "ziccrse", "ziccamoa",
        "zicclsm", "za64rs", "zihintpause", "zic64b", "zicbom", "zicbop",
        "zicboz", "zfhmin", "zkt", "zvfhmin", "zvbb", "zvkt", "zihintntl",
        "zicond", "zimop", "zcmop", "zcb", "zfa", "zawrs", "supm")),
    "rva23s64": (64, (
        # Everything the user-mode profile has, and the privileged half beside
        # it.  A program this compiler builds runs in user mode, so what the
        # privileged half adds changes nothing here -- it is read so that a
        # build script may name the profile its system is described by.
        "i", "m", "a", "f", "d", "c", "b", "v",
        "zicsr", "zicntr", "zihpm", "ziccif", "ziccrse", "ziccamoa",
        "zicclsm", "za64rs", "zihintpause", "zic64b", "zicbom", "zicbop",
        "zicboz", "zfhmin", "zkt", "zvfhmin", "zvbb", "zvkt", "zihintntl",
        "zicond", "zimop", "zcmop", "zcb", "zfa", "zawrs", "supm",
        # `Ss1p13` and `Sv39`, which the document also lists, are not here: the
        # first says which privileged specification the system follows and the
        # second which page-table mode it uses, and neither is a thing a
        # program may be built to use -- they belong to the other attributes
        # the format has for saying them.
        "zifencei", "svbare", "svade", "ssccptr", "sstvecd",
        "sstvala", "sscounterenw", "svpbmt", "svinval", "svnapot", "sstc",
        "sscofpmf", "ssnpm", "ssu64xl", "sha")),
}

#: What a profile name written without its privilege level or its width means.
#: A program is built for user mode and this compiler has one address width, so
#: the short name is the one it would have meant anyway.
_SHORT: Final[dict[str, str]] = {"rva23": "rva23u64", "rva23u": "rva23u64",
                                 "rva23s": "rva23s64"}

#: What a program is built for unless it says otherwise.  The newest application
#: profile, because that is what a processor bought to run a program like this
#: one has -- and because the alternative, the bare base, would have the
#: compiler refuse floating point by default on the architecture whose Linux ABI
#: has required it since the beginning.
DEFAULT: Final[str] = "rva23"

#: What a profile name looks like: a class, a year, and optionally which
#: privilege level and how wide an address is.
_PROFILE_SHAPE: Final[re.Pattern[str]] = re.compile(
    r"rv[abim]\d\d(?:[usm](?:32|64)?)?\Z")

#: A version, which is a major number and optionally a minor one after a `p`.
_VERSION: Final[re.Pattern[str]] = re.compile(r"(\d+)(?:p(\d+))?\Z")


def version_of(extension: str) -> tuple[int, int]:
    """What version an extension is at where nothing says otherwise."""
    return _VERSIONS.get(extension, (1, 0))


def _spelled(version: tuple[int, int]) -> str:
    """A version written the way the convention writes one."""
    return "".join((str(version[0]), "p", str(version[1])))


class BadName(ValueError):
    """A name that is neither a well-formed ISA string nor a profile.

    It carries what is wrong rather than what was written: whoever reports it
    has the name already, and what a reader needs is the part they got wrong.
    """


@dataclass(frozen=True, slots=True)
class ISA:
    """What a program is built for: how wide an address is, and every extension.

    The versions are kept beside the names although nothing yet reads them.
    They are in the string and throwing them away would make the one thing a
    version is for -- telling two incompatible forms of an extension apart --
    impossible to add later without parsing twice.
    """

    #: How wide an address is: thirty-two or sixty-four.
    bits: int
    #: Whether the base is the reduced one, which has sixteen registers.
    embedded: bool
    #: What was asked for, as it was written.
    named: str
    #: Every extension, including the ones another implies, with its version
    #: where one was written.
    versions: Mapping[str, tuple[int, int] | None]

    def has(self, extension: str) -> bool:
        """Whether a program built for this may use *extension*."""
        return extension in self.versions

    @property
    def floats(self) -> bool:
        """Whether floating point is there at all, which is what decides whether
        this compiler can build a program that uses it."""
        return self.has("d")

    def normalized(self) -> str:
        """The spelling the ELF attribute wants: every extension, in order,
        each with the version it is at.

        It is the same order as the short form and differs in two ways, both of
        which are what makes it *normal* rather than merely canonical: nothing
        is left implicit, so an extension another brings with it is written out
        beside it, and every one carries its version, so that a reader of the
        file need know nothing about which version was current when it was
        built.
        """
        base = "".join(("rv", str(self.bits), "e" if self.embedded else "i",
                        _spelled(version_of("e" if self.embedded else "i"))))
        rest = [*(letter for letter in SINGLE
                  if letter != "i" and letter in self.versions),
                *self._multi()]
        return "_".join((base, *("".join((name, _spelled(version_of(name))))
                                 for name in rest)))

    def _multi(self) -> list[str]:
        """The multi-letter extensions, in the order the convention states.

        The standard unprivileged ones first, ordered by the single letter they
        belong under -- the one right after the `z` -- and alphabetically within
        that; then the privileged ones; then whatever is nobody's standard.
        """
        found: list[str] = []
        for prefix in "zsx":
            here = [name for name in self.versions
                    if len(name) > 1 and name.startswith(prefix)]
            if prefix == "z":
                here.sort(key=lambda name: (SINGLE.find(name[1]), name))
            else:
                here.sort()
            found.extend(here)
        return found


def parse(text: str) -> ISA:
    """What *text* says a program is built for, or `BadName` where it says
    nothing.

    Two shapes are accepted and they cannot be confused: a profile names a class
    and a year, an ISA string names a base and a width, and no string is both.
    """
    written = text.strip()
    lowered = written.lower()
    if not lowered:
        raise BadName("a name was expected")
    if _PROFILE_SHAPE.match(lowered) is not None:
        return _profile(lowered, written)
    return _string(lowered, written)


def _profile(lowered: str, written: str) -> ISA:
    """A named set, which is a list somebody published rather than one to read."""
    found = _PROFILES.get(_SHORT.get(lowered, lowered))
    if found is None:
        raise BadName("".join((
            "it has the shape of a profile name and is not one this compiler "
            "knows; it knows ", ", ".join(sorted(_PROFILES)))))
    bits, extensions = found
    return ISA(bits=bits, embedded=False, named=written,
               versions=_closed({name: None for name in extensions}, bits))


def _string(lowered: str, written: str) -> ISA:
    """An ISA string, read the way the naming convention says to read one.

    The base is written as a width and a letter -- `rv64i` -- but the letter is
    an extension like any other and `g` brings it with it, which is why
    `rv64gc` is a string everyone writes and `rv64ic` is not.  So the width is
    read off the front and the letter is left to the walk, which then only has
    to say afterwards that one of the two bases was among what it found.
    """
    if len(lowered) < 5 or lowered[:4] not in ("rv32", "rv64"):
        raise BadName("".join((
            "an ISA string begins with 'rv32' or 'rv64' and a profile name "
            "with a class and a year, like 'rva23'")))
    found: dict[str, tuple[int, int] | None] = {}
    at = 4
    while at < len(lowered):
        if lowered[at] == "_":
            at += 1
            continue
        name, version, at = (_multi(lowered, at) if lowered[at] in "zsx"
                             else _single(lowered, at))
        if name in found:
            raise BadName("".join(("'", name, "' is written twice")))
        found[name] = version
    versions = _closed(found, int(lowered[2:4]))
    if "i" not in versions and "e" not in versions:
        raise BadName("an ISA string names its base, which is 'i' or 'e' -- or "
                      "'g', which brings 'i' with it")
    return ISA(bits=int(lowered[2:4]), embedded="e" in versions,
               named=written, versions=versions)


def _single(text: str, at: int) -> tuple[str, tuple[int, int] | None, int]:
    """One single-letter extension and the version written after it."""
    name = text[at]
    if name not in KNOWN or len(name) != 1:
        raise BadName("".join((
            "'", name, "' is not one of the single-letter extensions, which "
            "are ", SINGLE, ", e and g")))
    end = at + 1
    while end < len(text) and (text[end].isdigit() or text[end] == "p"):
        end += 1
    return name, _version(text[at + 1:end]), end


def _multi(text: str, at: int) -> tuple[str, tuple[int, int] | None, int]:
    """One multi-letter extension and the version written after it.

    It runs to the next underscore, which is what the convention has the
    underscore for: `zbazbb` would otherwise be a name, and there would be no
    way to tell it from two extensions written side by side.

    The version is peeled off only where what is left is a name this compiler
    knows, which is what tells `sv39` -- a name ending in digits -- from
    `zfa1p0`, a name with a version after it.  The architecture's own rule is
    that a name never ends in a digit, and `sv39` is older than the rule.
    """
    end = at
    while end < len(text) and text[end] != "_":
        end += 1
    whole = text[at:end]
    if whole in KNOWN:
        return whole, None, end
    split = _VERSION.search(whole)
    if split is not None and split.start() > 0 and whole[:split.start()] in KNOWN:
        return whole[:split.start()], _version(split.group(0)), end
    raise BadName("".join(("'", whole, "' is not an extension this compiler "
                           "knows")))


def _version(text: str) -> tuple[int, int] | None:
    """A version, which is a major number and optionally a minor one."""
    if not text:
        return None
    split = _VERSION.match(text)
    if split is None:
        raise BadName("".join((
            "'", text, "' is not a version; a version is a number, or two "
            "with a 'p' between them")))
    minor = split.group(2)
    return int(split.group(1)), int(minor) if minor else 0


def _closed(found: dict[str, tuple[int, int] | None], bits: int
            ) -> Mapping[str, tuple[int, int] | None]:
    """*found* with everything its extensions imply, and with `g` spelled out.

    An extension that another brings with it has no version of its own: what was
    written down is a version of the one that was written down, and inventing
    one for what it implies would be stating something nobody said.
    """
    waiting = list(found)
    while waiting:
        name = waiting.pop()
        for implied in _IMPLIES.get(name, ()):
            if implied not in found:
                found[implied] = None
                waiting.append(implied)
        for one, other, implied, only_at in _IMPLIES_WITH:
            if only_at is not None and only_at != bits:
                continue
            if one in found and other in found and implied not in found:
                found[implied] = None
                waiting.append(implied)
    # `g` is an abbreviation and not an extension: what it stands for is in the
    # set now, and leaving it beside them would be one thing named twice.
    found.pop("g", None)
    return found
