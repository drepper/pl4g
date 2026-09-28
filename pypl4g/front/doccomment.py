"""What a documentation comment says, beyond saying it.

A `\N{REFERENCE MARK}\N{REFERENCE MARK}` comment belongs to what follows it, and
what is in one is prose -- except where it is written the way Doxygen writes it:
a line beginning with `\\param`, `\\return` or one of the few others is about a
particular part of the thing being documented, and something reading it can put
those parts where they belong.  An editor shows the parameters as a list; the
compiler checks that the names in them are names the function has.

**Doxygen's spelling and not a new one.**  The commands are the ones a reader
already knows and already types, with either sigil -- `\\param` and `@param` are
one thing, which is Doxygen's own rule.  `@` is the language's attribute sigil,
but an attribute is never inside a comment, so nothing is ambiguous.  What this
is not is all of Doxygen: there is no markup, no `\\code`, no grouping, and an
unknown command is reported rather than passed through.

**Nothing here knows what it is documenting.**  It reads a comment into its
parts; whether a `\\param` names a parameter is a question about a definition and
is asked where definitions are checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Mapping

#: What each command written is called here, so that the several spellings
#: Doxygen accepts for one thing are one thing.  The value is the name the rest
#: of the compiler uses; the key is what a comment may be written with.
COMMANDS: Final[Mapping[str, str]] = {
    "brief": "brief", "short": "brief",
    "details": "details", "detail": "details",
    "param": "param", "arg": "param", "argument": "param",
    "return": "return", "returns": "return", "result": "return",
    "raises": "raises", "raise": "raises", "throws": "raises", "throw": "raises",
    "exception": "raises",
    "note": "note", "remark": "note", "remarks": "note",
    "warning": "warning", "attention": "warning",
    "see": "see", "sa": "see",
    "pre": "pre", "post": "post", "invariant": "invariant",
    "since": "since", "deprecated": "deprecated", "todo": "todo",
    "author": "author", "file": "file", "example": "example",
}

#: The order an editor shows them in, which is the order a reader wants them:
#: what it is for, what it takes, what it answers with, what it may refuse with,
#: and then everything that is a remark about it.
ORDER: Final[tuple[str, ...]] = (
    "details", "param", "return", "raises", "pre", "post", "invariant",
    "note", "warning", "example", "see", "since", "deprecated", "todo",
    "author", "file")

#: What a command looks like: a sigil, a word, and then whatever follows it.
_COMMAND = re.compile(r"^[ \t]*[\\@]([A-Za-z]+)[ \t]*(.*)$")

#: What Doxygen allows between `\param` and the name, saying which way the
#: parameter is passed.  This language says that in the type, so it is read and
#: dropped rather than refused: a comment moved here from C keeps working.
_DIRECTION = re.compile(r"^\[[ \t]*(?:in|out)(?:[ \t]*,[ \t]*(?:in|out))?[ \t]*\]")


@dataclass(frozen=True, slots=True)
class Part:
    """One command of a documentation comment, and what it carries."""

    #: The word as it was written, without the sigil.
    word: str
    #: And the sigil it was written with, which is either of the two Doxygen
    #: takes -- kept so that what a diagnostic quotes is what the comment says.
    sigil: str
    #: What that word means here, or nothing where it means nothing.
    command: str
    #: What it is about: the name a `\\param` names, and nothing for the rest.
    subject: str
    #: The text that followed, with its continuation lines joined.
    text: str
    #: Where the command was written, as a line of the comment counted from
    #: nought and a column within that line, so that whoever has the comment's
    #: place in the file can work out the place of this.
    line: int
    column: int

    @property
    def written(self) -> str:
        """The command as the comment has it, for quoting back at a reader."""
        return "".join((self.sigil, self.word))

    @property
    def known(self) -> bool:
        """Whether it is a command this understands."""
        return bool(self.command)


@dataclass(frozen=True, slots=True)
class Doc:
    """A documentation comment, taken apart."""

    #: The prose before the first command, or what `\\brief` said where there was
    #: none of it.  One paragraph: what a thing is, said first and said shortly.
    summary: str
    #: Every command, in the order they were written.
    parts: tuple[Part, ...] = ()

    @property
    def brief(self) -> str:
        """The first line of the summary, for somewhere a line is all there is."""
        return self.summary.splitlines()[0].strip() if self.summary else ""

    def of(self, command: str) -> tuple[Part, ...]:
        """Every part with the given meaning, in the order they were written."""
        return tuple(one for one in self.parts if one.command == command)

    def first(self, command: str) -> Part | None:
        """The first part with that meaning, where there is one."""
        found = self.of(command)
        return found[0] if found else None

    def params(self) -> tuple[Part, ...]:
        """What it says about the parameters, which is what most of them say."""
        return self.of("param")


def parse(text: str) -> Doc:
    """Read a documentation comment into its parts.

    The text is what the parser collected: the lines of the comment with their
    markers taken off and joined with newlines, so a line here is a line there
    and a place in one is a place in the other.
    """
    summary: list[str] = []
    parts: list[_Building] = []
    for number, line in enumerate(text.splitlines()):
        found = _COMMAND.match(line)
        if found is None:
            if parts:
                parts[-1].more.append(line.strip())
            else:
                summary.append(line.rstrip())
            continue
        parts.append(_started(number, line, found))
    made = tuple(one.settled() for one in parts)
    said = "\n".join(summary).strip()
    if not said:
        # A comment that opens with `\brief` and nothing before it: what it says
        # is the summary, wherever it was written.
        first = next((one for one in made if one.command == "brief"), None)
        said = first.text if first is not None else ""
    return Doc(summary=said,
               parts=tuple(one for one in made if one.command != "brief"
                           or one.text != said))


@dataclass(slots=True)
class _Building:
    """A part being read, which is not finished until the next one begins."""

    word: str
    sigil: str
    command: str
    subject: str
    text: str
    line: int
    column: int
    more: list[str]

    def settled(self) -> Part:
        """The part, with its continuation lines joined onto its text."""
        lines = [self.text, *self.more]
        while lines and not lines[-1]:
            lines.pop()
        return Part(word=self.word, sigil=self.sigil, command=self.command,
                    subject=self.subject, text="\n".join(lines).strip(),
                    line=self.line, column=self.column)


def _started(number: int, line: str, found: re.Match[str]) -> _Building:
    """Begin a part from the command line *found* on."""
    word = found.group(1)
    rest = found.group(2)
    command = COMMANDS.get(word.lower(), "")
    subject = ""
    if command == "param":
        rest = _DIRECTION.sub("", rest).lstrip()
        name, _, after = rest.partition(" ")
        subject, rest = name.strip(), after.lstrip()
    # Where the sigil is: the first character after whatever indentation the
    # line has, which is what the pattern matched past.
    column = len(line) - len(line.lstrip())
    return _Building(word=word, sigil=line[column], command=command,
                     subject=subject, text=rest.strip(), line=number,
                     column=column, more=[])
