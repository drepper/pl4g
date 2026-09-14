"""Reading source files and resolving offsets back to line and column."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from .location import FileId, Loc, Position, Span


class SourceReadError(Exception):
    """Raised when a source file cannot be read.

    The driver turns this into a diagnostic; the source layer does not know about
    diagnostics so that it stays usable from tools that render errors otherwise.
    """

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason


class SourceDecodeError(Exception):
    """Raised when a source file is not valid UTF-8."""

    def __init__(self, path: Path, byte_offset: int) -> None:
        super().__init__(path.as_posix())
        self.path = path
        self.byte_offset = byte_offset


@dataclass(slots=True)
class SourceFile:
    """One source file and the data needed to resolve positions inside it."""

    ident: FileId
    path: Path
    text: str
    base: Loc
    #: Offset within ``text`` of the first character of each line.
    line_starts: list[int] = field(default_factory=list)

    @property
    def end(self) -> Loc:
        """The global offset just past the last character of the file."""
        return self.base + len(self.text)

    def span_of_whole_file(self) -> Span:
        """A span covering the entire file."""
        return Span(self.base, self.end)


def _compute_line_starts(text: str) -> list[int]:
    """Return the offset of the first character of every line in *text*."""
    starts = [0]
    start = text.find("\n")
    while start >= 0:
        starts.append(start + 1)
        start = text.find("\n", start + 1)
    return starts


class SourceManager:
    """Owns every source file of one compilation.

    Files are laid out end to end in a single coordinate space, so a ``Loc`` is a
    plain integer that still identifies its file.
    """

    def __init__(self) -> None:
        self._files: list[SourceFile] = []
        self._bases: list[Loc] = []
        self._next_base: Loc = 0

    @property
    def files(self) -> list[SourceFile]:
        """Every file added so far, in the order they were added."""
        return self._files

    def add(self, path: Path, text: str) -> SourceFile:
        """Register *text* as the contents of *path* and return its record."""
        source = SourceFile(ident=len(self._files), path=path, text=text, base=self._next_base)
        source.line_starts = _compute_line_starts(text)
        self._files.append(source)
        self._bases.append(source.base)
        # One past the end, so that the end-of-file position of one file is never
        # the start position of the next.
        self._next_base = source.end + 1
        return source

    def read(self, path: Path) -> SourceFile:
        """Read *path* as UTF-8 and register it."""
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise SourceReadError(path, exc.strerror or str(exc)) from exc
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceDecodeError(path, exc.start) from exc
        return self.add(path, text)

    def file_of(self, loc: Loc) -> SourceFile | None:
        """Return the file containing *loc*, or ``None`` if there is none."""
        if loc < 0 or not self._files:
            return None
        index = bisect_right(self._bases, loc) - 1
        if index < 0:
            return None
        source = self._files[index]
        return source if loc <= source.end else None

    def position(self, loc: Loc) -> Position | None:
        """Resolve *loc* to a path, a 1-based line and a 1-based column."""
        source = self.file_of(loc)
        if source is None:
            return None
        offset = loc - source.base
        line_index = bisect_right(source.line_starts, offset) - 1
        column = offset - source.line_starts[line_index]
        return Position(source.path.as_posix(), line_index + 1, column + 1)

    def line_text(self, loc: Loc) -> str | None:
        """Return the text of the line containing *loc*, without its newline."""
        source = self.file_of(loc)
        if source is None:
            return None
        offset = loc - source.base
        line_index = bisect_right(source.line_starts, offset) - 1
        start = source.line_starts[line_index]
        end = source.text.find("\n", start)
        return source.text[start:] if end < 0 else source.text[start:end]

    def snippet(self, span: Span) -> str | None:
        """Return the source text covered by *span*."""
        source = self.file_of(span.start)
        if source is None:
            return None
        return source.text[span.start - source.base : span.end - source.base]
