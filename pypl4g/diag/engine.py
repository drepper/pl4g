"""Emitting diagnostics.

The call site supplies only the identity, the source span and the message
arguments.  Severity, text and controllability come from the shared catalog, so
that the numbers a program can react to mean the same thing in every
implementation.
"""

from dataclasses import dataclass, field
from typing import Callable, Mapping

from ..source.location import INVALID_SPAN, Span
from . import ids as D
from .catalog import Catalog, DiagID, DiagInfo, catalog


class InternalError(Exception):
    """An inconsistency in the compiler's own data structures.

    Raised rather than emitted, so that every internal failure unwinds to the one
    place in the driver that reports it.
    """


@dataclass(slots=True)
class Diagnostic:
    """One emitted diagnostic and the notes attached to it."""

    info: DiagInfo
    span: Span
    args: Mapping[str, object]
    notes: list["Diagnostic"] = field(default_factory=list)
    #: Whether this diagnostic was actually handed to the renderer.
    reported: bool = False
    _engine: "DiagEngine | None" = None

    @property
    def text(self) -> str:
        """The message with its arguments substituted."""
        return self.info.message.format_map(_Args(self.args, self.info))

    def note(self, ident: DiagID, span: Span = INVALID_SPAN, /, **args: object) -> "Diagnostic":
        """Attach a note to this diagnostic and return the diagnostic.

        The note is handed to the renderer at once rather than when the parent
        was rendered, so that output stays in the order it was produced and a
        note is never lost because it was attached after the fact.
        """
        assert self._engine is not None
        note = self._engine.make(ident, span, **args)
        self.notes.append(note)
        if self.reported:
            self._engine.report_note(note)
        return self


class _Args(dict[str, object]):
    """Mapping used for substitution that reports a missing argument clearly."""

    def __init__(self, args: Mapping[str, object], info: DiagInfo) -> None:
        super().__init__(args)
        self._info = info

    def __missing__(self, key: str) -> object:
        raise InternalError("".join(
            ("diagnostic ", self._info.name, " is missing the argument '", key, "'")))


@dataclass(slots=True)
class WarningControl:
    """Which warnings are enabled, and whether they count as errors."""

    enabled: dict[str, bool] = field(default_factory=dict)
    warnings_are_errors: bool = False

    def is_enabled(self, info: DiagInfo) -> bool:
        """Whether *info* should be reported at all."""
        if info.option is None:
            return info.default_enabled
        return self.enabled.get(info.option, info.default_enabled)


class DiagEngine:
    """Collects diagnostics and hands them to a renderer."""

    def __init__(self, sink: Callable[[Diagnostic], None],
                 control: WarningControl | None = None,
                 cat: Catalog | None = None) -> None:
        self._sink = sink
        self._control = control if control is not None else WarningControl()
        self._catalog = cat
        self.error_count: int = 0
        self.warning_count: int = 0

    @property
    def catalog(self) -> Catalog:
        """The catalog in use, loaded on first need."""
        if self._catalog is None:
            self._catalog = catalog()
        return self._catalog

    @property
    def control(self) -> WarningControl:
        """The warning control settings in use."""
        return self._control

    def set_control(self, control: WarningControl) -> None:
        """Replace the warning control settings.

        The settings come from the command line, which is itself parsed with a
        working engine, so they are applied once that parse has finished.
        """
        self._control = control

    @property
    def failed(self) -> bool:
        """Whether anything was reported that prevents a successful compilation."""
        return self.error_count > 0

    def make(self, ident: DiagID, span: Span = INVALID_SPAN, /, **args: object) -> Diagnostic:
        """Build a diagnostic without reporting it."""
        diag = Diagnostic(self.catalog.get(ident), span, args)
        diag._engine = self
        return diag

    def emit(self, ident: DiagID, span: Span = INVALID_SPAN, /, **args: object) -> Diagnostic:
        """Report the diagnostic *ident* and return it, so notes can be attached."""
        diag = self.make(ident, span, **args)
        info = diag.info
        if info.severity == "warning" and not self._control.is_enabled(info):
            return diag
        if info.is_error or (info.severity == "warning" and self._control.warnings_are_errors):
            self.error_count += 1
        elif info.severity == "warning":
            self.warning_count += 1
        diag.reported = True
        self._sink(diag)
        return diag

    def report_note(self, note: Diagnostic) -> None:
        """Hand a note that was attached to an already reported diagnostic on."""
        note.reported = True
        self._sink(note)

    def internal(self, detail: str) -> Diagnostic:
        """Report an internal compiler error."""
        return self.emit(D.IMPL_INTERNAL_ERROR, detail=detail)

    def effective_severity(self, info: DiagInfo) -> str:
        """The severity as it should be rendered, honouring -Werror."""
        if info.severity == "warning" and self._control.warnings_are_errors:
            return "error"
        return info.severity


def collecting_engine(control: WarningControl | None = None) -> tuple[DiagEngine, list[Diagnostic]]:
    """Return an engine that appends to a list, together with that list.

    Used by the tests and by ``--diag-format=json``, which renders only at the end.
    """
    collected: list[Diagnostic] = []
    return DiagEngine(collected.append, control), collected
