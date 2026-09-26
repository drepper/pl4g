"""Running the compiler over what the editor is holding.

Nothing here compiles anything itself: it builds the same `Driver` a command line
builds and asks it for the same stages, over a source manager that hands out the
editor's text where it has any and reads the disk where it has not.  So a
diagnostic in a buffer is the diagnostic the build will give, word for word and
number for number, and a feature that reads the tree reads the tree the compiler
checked.

**How far to go depends on what happened.**  Typing gets the front end -- the
lexer, the parser and the checker -- which is where all but a handful of the
diagnostics are and which costs a few milliseconds.  Saving gets the whole thing,
code generation included, so that a program the back end refuses (8501, 9901)
says so at the moment there is a file to be refused.  The two are the same driver
asked for different amounts of work.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from ..diag.engine import DiagEngine, Diagnostic, InternalError
from ..ir.reports import ReportLog
from ..driver.main import Driver
from ..driver.options import EmitKind, Options
from ..front import ast
from ..source.manager import SourceManager, SourceReadError


class BufferSources(SourceManager):
    """A source manager that knows what the editor has not saved yet.

    Every file the compiler reads comes through here, so a module imported from
    a buffer with unsaved changes is read as the buffer has it -- which is the
    whole reason this exists.  Anything the editor is not holding is read from
    the disk, as always.
    """

    def __init__(self, held: Mapping[Path, str]) -> None:
        super().__init__()
        self._held = held

    def read(self, path: Path) -> object:
        """The editor's text where there is some, and the file's where not."""
        for where, text in self._held.items():
            if where == path or where.resolve() == path.resolve():
                return self.add(path, text)
        return super().read(path)


@dataclass(slots=True)
class Analysis:
    """What one run of the compiler over one file came to."""

    path: Path
    #: Every diagnostic, in the order the compiler made them.
    diagnostics: list[Diagnostic] = field(default_factory=list)
    #: The syntax tree, where the file parsed at all.
    unit: ast.SourceUnit | None = None
    #: The source manager that placed them, which is what turns a span into a
    #: line and a column.
    sources: SourceManager | None = None
    #: Whether this went as far as code generation.
    whole: bool = False


def analyse(path: Path, held: Mapping[Path, str], *, whole: bool = False,
            module_path: list[Path] | None = None) -> Analysis:
    """Compile *path* out of *held*, as far as *whole* says.

    Every kind of failure is one of the compiler's own, so nothing here raises:
    what a caller gets back is an analysis, with whatever diagnostics there were.
    """
    collected: list[Diagnostic] = []
    reports = ReportLog()
    diags = DiagEngine(collected.append, log=reports)
    sources = BufferSources(held)
    found = Analysis(path=path, diagnostics=collected, sources=sources,
                     whole=whole)
    with TemporaryDirectory(prefix="pl4g-lsp-") as room:
        # The default target, whatever it is: what is wanted is the
        # diagnostics, and the only ones that differ between targets are the
        # ones about the target.
        options = Options(inputs=[path], output=Path(room) / "out",
                          emit=EmitKind.ELF,
                          module_path=list(module_path or ()))
        # Nothing of this writes to the standard output, which carries the
        # protocol; what the driver would say about its stages goes to the
        # standard error, which is the log an editor shows.
        driver = Driver(options=options, diags=diags, sources=sources,
                        stderr=sys.stderr, reports=reports)
        try:
            if whole:
                driver.run()
            else:
                driver.front_end()
        except (InternalError, SourceReadError, OSError):
            # An internal error is a diagnostic like any other and is already in
            # the list; anything else is the disk, and what is lost is one
            # analysis rather than the session.
            pass
    # What the parser made of this file, which the manager records for every
    # file it read: the tree is what the features that are not diagnostics read.
    for read in sources.read_units:
        if read.path == path and isinstance(read.unit, ast.SourceUnit):
            found.unit = read.unit
    return found
