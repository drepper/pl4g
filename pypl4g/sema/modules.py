"""Finding modules, reading them once, and deciding what to call them.

A module is a source file, found while the program is being compiled.  Three
things are decided here and nowhere else.

**Where to look.**  A name beginning with a slash names a file outright and only
that place is looked at.  Otherwise the directory of the file doing the
importing comes first, then the directories given on the command line, then the
ones the installation provides -- and the last of those only where the name has
no slash in it, because a name with a slash is a path and a path is not
something to go looking for in a place the program knows nothing about.

**Reading it once.**  A file that has been read is remembered by the path it was
found at, so a module imported from two places is one module: the same
definitions, in the image once.  Two names for one file are two names for one
thing, not two things.

**What to call it.**  A module's name is the name of its file without the
extension, with the name of whatever imported it in front.  A module reached by
more than one route has more than one such name, and the shortest is the one
used -- the one that sorts first where two are the same length -- because the
name is what appears in the image and a program should not be made to carry the
longest way of reaching something.  Two different files with the same base name
would come to one name, so both get a few characters of the hash of their path
to tell them apart.
"""

from dataclasses import dataclass, field
from hashlib import blake2b
from pathlib import Path
from typing import Sequence

#: What a module file is called.  A name that does not end in this gets it.
SUFFIX = ".pl4g"

#: How much of the hash of a path is used to tell two modules of one name apart.
#: Six characters is a millionth of a chance of a second collision, which is
#: enough for a compiler that would notice one.
HASH_LENGTH = 6


class ModuleNotFound(Exception):
    """No file of that name was found anywhere that was looked."""

    def __init__(self, name: str, looked: Sequence[Path]) -> None:
        super().__init__(name)
        self.name = name
        #: Every place that was looked at, in the order they were looked at, so
        #: that a message can say where rather than only that it failed.
        self.looked = list(looked)


class ImportCycle(Exception):
    """A module imports itself, directly or round a ring."""

    def __init__(self, chain: Sequence[Path]) -> None:
        super().__init__("import cycle")
        #: The ring, beginning and ending at the file that closes it.
        self.chain = list(chain)


@dataclass(slots=True)
class LoadedModule:
    """One file that has been read, and what it turned out to hold."""

    path: Path
    #: What the file exports, by the name the file gave it.  The values are the
    #: things the representation holds -- a function or a variable.
    exports: dict[str, object] = field(default_factory=dict)
    #: Every name this module could be known by, one per route that reached it.
    candidates: set[str] = field(default_factory=set)
    #: The name finally chosen, once every route is known.
    name: str = ""
    #: The definitions whose symbol carries the module's name, so that the name
    #: can be settled after all the loading rather than while it is going on.
    owned: list[object] = field(default_factory=list)

    def add_candidate(self, prefix: str, base: str) -> str:
        """Record a name this module could go by, and return it."""
        candidate = ".".join((prefix, base)) if prefix else base
        self.candidates.add(candidate)
        return candidate


@dataclass(slots=True)
class SearchPath:
    """Where modules are looked for, in the order they are looked for."""

    #: Directories given on the command line, in the order given.
    given: list[Path] = field(default_factory=list)
    #: Directories the installation provides, looked at last and only for a name
    #: with no slash in it.
    system: list[Path] = field(default_factory=list)
    #: Where the compiler was run, which is what a relative entry of the path is
    #: relative to when the importing file's own directory did not have it.
    working: Path = field(default_factory=Path.cwd)

    def places(self, name: str, importer: Path) -> list[Path]:
        """Every file that might be the module *name*, in the order to try them.

        The list is built rather than walked so that a name found nowhere can
        say where it was looked for.
        """
        wanted = name if name.endswith(SUFFIX) else "".join((name, SUFFIX))
        candidate = Path(wanted)
        if candidate.is_absolute():
            # A name that is a path names one file and no search is done.
            return [candidate]
        found: list[Path] = [importer.parent / candidate]
        for directory in self.given:
            if directory.is_absolute():
                found.append(directory / candidate)
            else:
                # A relative entry is relative to the importing file first and
                # to where the compiler was run second, which is the order
                # someone building a tree of sources would expect.
                found.append(importer.parent / directory / candidate)
                found.append(self.working / directory / candidate)
        if "/" not in name:
            found.extend(directory / candidate for directory in self.system)
        return found


@dataclass(slots=True)
class ModuleRegistry:
    """Every module the compilation has read, by the path it was read from."""

    search: SearchPath = field(default_factory=SearchPath)
    by_path: dict[Path, LoadedModule] = field(default_factory=dict)
    #: The files being read just now, innermost last, for finding a ring.
    reading: list[Path] = field(default_factory=list)

    def resolve(self, name: str, importer: Path) -> Path:
        """Where the module *name* is, as imported from *importer*."""
        looked = self.search.places(name, importer)
        for place in looked:
            if place.is_file():
                # The same file reached two ways is one module, so what is
                # remembered is what the file system says it is.
                return place.resolve()
        raise ModuleNotFound(name, looked)

    def known(self, path: Path) -> LoadedModule | None:
        """The module already read from *path*, if there is one.

        A module still being read is not one that has been read: what it holds
        is not known yet, so handing it back would hand back a module with
        nothing in it.  Asking `cycle_through` first is what tells the two apart.
        """
        found = self.by_path.get(path)
        return None if found is not None and path in self.reading else found

    def cycle_through(self, path: Path) -> list[Path] | None:
        """The ring importing *path* would close, if it would close one."""
        if path not in self.reading:
            return None
        return [*self.reading[self.reading.index(path):], path]

    def begin(self, path: Path) -> LoadedModule:
        """Record that *path* is being read, and refuse a ring."""
        if path in self.reading:
            index = self.reading.index(path)
            raise ImportCycle([*self.reading[index:], path])
        self.reading.append(path)
        found = LoadedModule(path=path)
        self.by_path[path] = found
        return found

    def finish(self, path: Path) -> None:
        """Record that *path* has been read."""
        if self.reading and self.reading[-1] == path:
            self.reading.pop()

    def settle_names(self) -> None:
        """Give every module its name, now that every route to it is known.

        This happens at the end rather than as each module is read, because
        neither question can be answered earlier: the shortest of a module's
        names is not known until the last route to it has been found, and
        whether two modules share a base name is not known until both are read.
        """
        for module in self.by_path.values():
            module.name = min(module.candidates, key=lambda c: (len(c), c)) \
                if module.candidates else base_name(module.path)
        self._disambiguate()
        for module in self.by_path.values():
            for owned in module.owned:
                setattr(owned, "module", module.name)

    def _disambiguate(self) -> None:
        """Tell apart two modules that came to one name."""
        by_name: dict[str, list[LoadedModule]] = {}
        for module in self.by_path.values():
            by_name.setdefault(module.name, []).append(module)
        for name, sharing in by_name.items():
            if len(sharing) < 2:
                continue
            for module in sharing:
                module.name = "".join((name, "-", path_hash(module.path)))


def base_name(path: Path) -> str:
    """What a module at *path* is called before anything is put in front of it."""
    return path.name[:-len(SUFFIX)] if path.name.endswith(SUFFIX) else path.name


def path_hash(path: Path) -> str:
    """A few characters standing for a whole path.

    It is the path and not the contents: two files with the same contents are
    still two modules, and a file that changes is still the same module.
    """
    return blake2b(str(path).encode("utf-8"),
                   digest_size=HASH_LENGTH).hexdigest()[:HASH_LENGTH]


def parse_search_path(given: str) -> list[Path]:
    """The directories a colon-separated path names, in order."""
    return [Path(piece) for piece in given.split(":") if piece]


def system_modules() -> list[Path]:
    """The directories the installation provides modules in.

    One for now, beside the compiler itself, so that a checkout and an install
    behave the same.  Where a program is run from is the only thing the compiler
    can know about its own installation without being told.
    """
    return [Path(__file__).resolve().parent.parent.parent / "modules"]
