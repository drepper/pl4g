"""What a build function leaves behind, and what it is given to leave it in.

The object a build function writes into is an ordinary record of the `std`
module as far as the program is concerned -- it has a type, its fields have types,
and the checker checks what is written into them.  While the compiler runs the
function, that record is the `Record` here and its fields are read back
afterwards; the things to build are added by functions the compiler provides,
there being no way yet to write a growing run of records in the language.

**The defaults are what the command line said.**  A build function that says
nothing about the target builds for the target the command line named, which is
what makes a build file something to add settings to rather than something that
has to say everything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Mapping, Sequence

from ..diag import ids as D
from ..diag.engine import DiagEngine
from ..source.location import Span
from .evaluate import Builtin, Record

#: What the record is called, and what each of its fields is called.  The names
#: are the module's; nothing here invents one, so a field added to `std.Build`
#: is added here and nowhere else.
TYPE_NAME: Final[str] = "Build"
OUTPUT_DIR: Final[str] = "output_dir"
TARGET: Final[str] = "target"
OPT_LEVEL: Final[str] = "opt_level"
MCLEVEL: Final[str] = "mclevel"
STACK_SIZE: Final[str] = "stack_size"
GUARD_SIZE: Final[str] = "guard_size"

#: What `-Dname` on its own means, and what counts as saying so.
SAID_SO: Final[frozenset[str]] = frozenset(("true", "yes", "on", "1"))


@dataclass(frozen=True, slots=True)
class Artifact:
    """One thing to build, and where it was asked for."""

    name: str
    sources: tuple[str, ...]
    span: Span


@dataclass(slots=True)
class Plan:
    """Everything a build function asked for, once it has finished."""

    output_dir: str = ""
    target: str = ""
    opt_level: int = 0
    mclevel: str = ""
    stack_size: int = 0
    guard_size: int = 0
    artifacts: list[Artifact] = field(default_factory=list)

    def record(self) -> Record:
        """The object to hand the build function, holding these as its fields."""
        return Record(TYPE_NAME, {
            OUTPUT_DIR: self.output_dir, TARGET: self.target,
            OPT_LEVEL: self.opt_level, MCLEVEL: self.mclevel,
            STACK_SIZE: self.stack_size, GUARD_SIZE: self.guard_size})

    def settle(self, record: Record) -> None:
        """Read back what the build function wrote into the object.

        Only the fields this knows about: a field the module gained and this did
        not is one nothing reads, which is a thing to notice here rather than
        somewhere a build quietly does the wrong thing.
        """
        self.output_dir = _as_text(record, OUTPUT_DIR, self.output_dir)
        self.target = _as_text(record, TARGET, self.target)
        self.mclevel = _as_text(record, MCLEVEL, self.mclevel)
        self.opt_level = _as_number(record, OPT_LEVEL, self.opt_level)
        self.stack_size = _as_number(record, STACK_SIZE, self.stack_size)
        self.guard_size = _as_number(record, GUARD_SIZE, self.guard_size)


def _as_text(record: Record, name: str, fallback: str) -> str:
    """One field of the object, where it holds text."""
    found = record.fields.get(name, fallback)
    return found if isinstance(found, str) else fallback


def _as_number(record: Record, name: str, fallback: int) -> int:
    """And one where it holds a number."""
    found = record.fields.get(name, fallback)
    return found if isinstance(found, int) and not isinstance(found, bool) \
        else fallback


def builtins(plan: Plan, given: Mapping[str, str],
             diags: DiagEngine) -> Mapping[str, Builtin]:
    """The functions the compiler provides while a build function runs.

    Each is handed the place it was called from before its arguments, so that
    what it refuses it refuses where the call is written.
    """

    def add_executable(span: Span, held: object, name: object,
                       sources: object) -> object:
        """`add_executable(b, name, sources)`: one more thing to build."""
        del held
        wanted = tuple(str(one) for one in sources) \
            if isinstance(sources, list) else ()
        if not wanted:
            diags.emit(D.LANG_BUILD_NO_SOURCES, span, name=str(name))
            return None
        plan.artifacts.append(Artifact(name=str(name), sources=wanted, span=span))
        return None

    def option(span: Span, held: object, name: object, fallback: object) -> object:
        """`option(b, name, fallback)`: what the command line said, as text."""
        del span, held
        return given.get(str(name), str(fallback))

    def option_flag(span: Span, held: object, name: object,
                    fallback: object) -> object:
        """And the same question asked of something that is either so or not."""
        del span, held
        found = given.get(str(name))
        if found is None:
            return bool(fallback)
        return found.strip().lower() in SAID_SO

    return {one.name: one for one in (
        Builtin("add_executable", add_executable),
        Builtin("option", option),
        Builtin("option_flag", option_flag))}


def sources_of(artifact: Artifact, beside: Path) -> list[Path]:
    """Where an artifact's sources are: beside the build file that named them.

    Which is what makes a build file something a project can be entered through
    from anywhere -- the names in it are about the project and not about where
    the compiler happened to be run.
    """
    return [one if (one := Path(text)).is_absolute() else beside / one
            for text in artifact.sources]


def named(artifact: Artifact, plan: Plan) -> Path:
    """Where what is built is written."""
    return (Path(plan.output_dir) if plan.output_dir else Path()) / artifact.name


def summary(plan: Plan) -> Sequence[str]:
    """What was built, one line each, for a run that says what it did."""
    return [" ".join((artifact.name, "".join(("(", ", ".join(artifact.sources),
                                              ")"))))
            for artifact in plan.artifacts]
