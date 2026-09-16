"""Rendering diagnostics for a terminal or for a program.

The JSON form carries the same content as the text form.  It is what makes the
requirement of the specification -- that code can react to the errors and
warnings the compiler emits -- reachable from outside the compiler.
"""

from __future__ import annotations

import json
from typing import Any, TextIO

from ..source.location import Span
from ..source.manager import SourceManager
from .engine import DiagEngine, Diagnostic
from .highlight import Highlighter
from .style import ColourWhen, Palette


def _caret_line(line: str, column: int, width: int) -> str:
    """Build the line of carets underlining a span.

    Characters before the span are replaced by spaces, keeping any tab so that
    the carets line up with the source as the terminal shows it.
    """
    prefix = "".join(c if c == "\t" else " " for c in line[: column - 1])
    return "".join((prefix, "^", "~" * max(0, width - 1)))


class TextRenderer:
    """Renders diagnostics in the customary ``file:line:column: severity:`` form."""

    def __init__(self, sources: SourceManager, stream: TextIO,
                 engine_ref: list[DiagEngine] | None = None,
                 when: ColourWhen = ColourWhen.AUTO) -> None:
        self._sources = sources
        self._stream = stream
        self._engine_ref = engine_ref
        self._palette = Palette.chosen(when, stream)
        self._highlighter = Highlighter() if self._palette.on else None

    def recolour(self, when: ColourWhen) -> None:
        """Say again when to write colour, the command line having been read.

        The first diagnostics a run can make are about the command line itself,
        and they are made before it has been read -- so the renderer is built
        looking, and told once the option is known.
        """
        self._palette = Palette.chosen(when, self._stream)
        self._highlighter = Highlighter() if self._palette.on else None

    def _severity(self, diag: Diagnostic) -> str:
        """The severity to print, honouring -Werror when an engine is known."""
        if self._engine_ref:
            return self._engine_ref[0].effective_severity(diag.info)
        return diag.info.severity

    def _render_one(self, diag: Diagnostic, indent: str = "") -> None:
        """Render one diagnostic.  Notes arrive as separate calls."""
        head: list[str] = [indent]
        position = self._sources.position(diag.span.start) if diag.span.is_valid else None
        if position is not None:
            head.append(self._palette.where(
                "".join((position.path, ":", str(position.line), ":",
                         str(position.column), ":"))))
            head.append(" ")
        head.append("".join((
            self._palette.severity(self._severity(diag)), ": ",
            self._palette.message(diag.text), " ",
            self._palette.number("".join(("[PL4G-", str(diag.info.number),
                                          "]"))))))
        print("".join(head), file=self._stream)
        if position is not None:
            self._render_snippet(diag.span, position.line, position.column)

    def _render_snippet(self, span: Span, line: int, column: int) -> None:
        """Print the offending source line with the span underlined."""
        text = self._sources.line_text(span.start)
        if text is None:
            return
        number = str(line)
        gutter = " " * len(number)
        width = max(1, min(span.end - span.start, len(text) - column + 1))
        print("".join((self._palette.gutter("".join((" ", number, " |"))), " ",
                       self._coloured(span, text))), file=self._stream)
        print("".join((self._palette.gutter("".join((" ", gutter, " |"))), " ",
                       self._palette.caret(_caret_line(text, column, width)))),
              file=self._stream)

    def _coloured(self, span: Span, text: str) -> str:
        """The source line, written as the grammar says its pieces are.

        Where there is no highlighter -- no colour asked for, or no grammar to
        load -- the line is what it was, which is what every other renderer of
        this kind falls back to and loses nothing by.
        """
        if self._highlighter is None:
            return text
        found = self._sources.line_within(span.start)
        if found is None:
            return text
        whole, start, length = found
        runs = self._highlighter.of_line(whole, start, min(length, len(text)))
        if not runs:
            return text
        pieces: list[str] = []
        at = 0
        for begins, ends, name in runs:
            pieces.append(text[at:begins])
            pieces.append(self._palette.capture(name, text[begins:ends]))
            at = ends
        pieces.append(text[at:])
        return "".join(pieces)

    def __call__(self, diag: Diagnostic) -> None:
        """Render *diag* and everything attached to it."""
        self._render_one(diag)


class JSONRenderer:
    """Collects diagnostics and writes them as one JSON array at the end."""

    def __init__(self, sources: SourceManager, stream: TextIO) -> None:
        self._sources = sources
        self._stream = stream
        self._collected: list[Diagnostic] = []

    def __call__(self, diag: Diagnostic) -> None:
        """Collect *diag*; nothing is written until ``finish`` is called.

        A note is skipped here because it is written inside the diagnostic it
        was attached to.
        """
        if diag.info.severity != "note":
            self._collected.append(diag)

    def _to_object(self, diag: Diagnostic) -> dict[str, Any]:
        """Convert one diagnostic, and its notes, to a JSON-compatible object."""
        obj: dict[str, Any] = {
            "number": diag.info.number,
            "name": diag.info.name,
            "severity": diag.info.severity,
            "message": diag.text,
        }
        position = self._sources.position(diag.span.start) if diag.span.is_valid else None
        if position is not None:
            obj["file"] = position.path
            obj["line"] = position.line
            obj["column"] = position.column
            obj["span"] = {"start": diag.span.start, "end": diag.span.end}
        if diag.info.option is not None:
            obj["option"] = diag.info.option
        if diag.notes:
            obj["notes"] = [self._to_object(n) for n in diag.notes]
        return obj

    def finish(self) -> None:
        """Write everything collected so far."""
        json.dump([self._to_object(d) for d in self._collected], self._stream, indent=2,
                  ensure_ascii=False)
        self._stream.write("\n")
