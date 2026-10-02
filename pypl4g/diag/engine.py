"""Emitting diagnostics.

The call site supplies only the identity, the source span and the message
arguments.  Severity, text and controllability come from the shared catalog, so
that the numbers a program can react to mean the same thing in every
implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from ..ir.reports import ReportLog
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
    notes: list[Diagnostic] = field(default_factory=list)
    #: Whether this diagnostic was actually handed to the renderer.
    reported: bool = False
    _engine: DiagEngine | None = None

    @property
    def text(self) -> str:
        """The message with its arguments substituted."""
        return self.info.message.format_map(_Args(self.args, self.info))

    def note(self, ident: DiagID, span: Span = INVALID_SPAN, /,
             **args: object) -> Diagnostic:
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
class Expectation:
    """A set of diagnostics a construct says it raises.

    While one is in force the diagnostics it names are not reported, and it
    records which of them were raised -- so that an expectation nothing meets
    can be reported in its turn, rather than quietly hiding nothing.
    """

    numbers: frozenset[DiagID]
    #: The subset that the construct asserts is raised, rather than merely
    #: allowing.  Only these are worth reporting when nothing meets them.
    required: frozenset[DiagID] = frozenset()
    raised: set[DiagID] = field(default_factory=set)
    #: Whether anything it absorbed prevents a construct from being compiled.
    #: An error about how code is written does not: the construct is whole, and
    #: saying the rule is meant to be broken here leaves it standing.
    saw_error: bool = False

    @property
    def unmet(self) -> list[DiagID]:
        """The numbers that were asserted and not raised."""
        return sorted(self.required - self.raised)


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
                 cat: Catalog | None = None,
                 log: ReportLog | None = None) -> None:
        self._sink = sink
        #: Where everything reported is written down beside what the compiler
        #: chose, so that the log a run leaves is in the order things happened.
        #: Nothing here needs it: without one the compiler says the same things.
        self._log = log
        self._control = control if control is not None else WarningControl()
        self._catalog = cat
        self.error_count: int = 0
        #: Notes to hang on every error raised just now, innermost last.  What
        #: it is for is a thing compiled because something else asked for it:
        #: the message points at what is wrong and this says what asked.
        self._because: list[tuple[DiagID, Span, dict[str, object],
                                  frozenset[DiagID] | None]] = []
        self.warning_count: int = 0
        #: The expectations in force, innermost last.
        self._expectations: list[Expectation] = []
        #: Whether warnings are being left unsaid for now: a generic function is
        #: checked where it is written and again for each set of types, and a
        #: warning is about what was compiled, which is the second.
        self.errors_only: bool = False

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

    def expect(self, numbers: frozenset[DiagID],
               required: frozenset[DiagID] = frozenset()) -> Expectation:
        """Put a new expectation in force until ``release`` is called with it."""
        expectation = Expectation(numbers, required)
        self._expectations.append(expectation)
        return expectation

    def resume(self, expectation: Expectation) -> Expectation:
        """Put an expectation back in force, keeping what it has absorbed.

        A definition is checked in two passes, and what it says it raises holds
        for both of them.
        """
        self._expectations.append(expectation)
        return expectation

    def release(self, expectation: Expectation) -> Expectation:
        """Take an expectation out of force and return what it absorbed."""
        self._expectations.remove(expectation)
        return expectation

    def _absorb(self, info: DiagInfo) -> bool:
        """Whether an expectation takes this diagnostic instead of reporting it."""
        for expectation in reversed(self._expectations):
            if info.number in expectation.numbers:
                expectation.raised.add(info.number)
                expectation.saw_error = (expectation.saw_error
                                         or info.spoils_the_construct)
                return True
        return False

    def emit(self, ident: DiagID, span: Span = INVALID_SPAN, /, **args: object) -> Diagnostic:
        """Report the diagnostic *ident* and return it, so notes can be attached."""
        diag = self.make(ident, span, **args)
        info = diag.info
        if self._absorb(info):
            return diag
        if self._because and info.is_error:
            # Something is being compiled because something else asked for it,
            # and what is wrong with it is only wrong for what was asked.  The
            # note says what asked, which is the thing a reader cannot see from
            # where the message points.
            for ident_of, span_of, args_of, family in self._because:
                if family is not None and ident not in family:
                    # A note for one family of errors and not for every error
                    # raised while it is in force.  What it says is true of that
                    # family, and hanging it on an unrelated message would be
                    # explaining something the reader did not ask about.
                    continue
                note = self.make(ident_of, span_of, **args_of)
                diag.notes.append(note)
        if info.severity == "warning" and (self.errors_only
                                           or not self._control.is_enabled(info)):
            return diag
        if info.is_error or (info.severity == "warning" and self._control.warnings_are_errors):
            self.error_count += 1
        elif info.severity == "warning":
            self.warning_count += 1
        diag.reported = True
        self._record(diag)
        self._sink(diag)
        for note in diag.notes:
            self.report_note(note)
        return diag

    def write_into(self, log: ReportLog) -> None:
        """Say where to write down what is reported from here on.

        For the times the log is not in hand when the engine is made: the driver
        has one before either, and a test builds the two the other way round.
        """
        self._log = log

    def _record(self, diag: Diagnostic) -> None:
        """Write one reported diagnostic into the log, where there is one.

        What goes down is the severity after `-Werror` has had its say, since
        that is what was printed and what the run was decided by.
        """
        if self._log is None:
            return
        self._log.said(self.effective_severity(diag.info), diag.info.name,
                       diag.text, diag.info.number, diag.span)

    def because(self, ident: DiagID, span: Span,
                family: frozenset[DiagID] | None = None,
                **args: object) -> object:
        """Hang a note on every error raised until this is given back.

        Used where something is compiled because something else asked for it --
        a generic function instantiated by a call -- so that what is wrong with
        it says what asked, which is the one thing a reader cannot see from
        where the message points.

        *family* narrows it to the errors it names, for a note that explains one
        family of mistakes rather than the context of all of them: a rule that
        holds of a condition's purity says nothing useful about a condition's
        spelling, and a note is only worth having where it answers the question
        the message raises.
        """
        self._because.append((ident, span, dict(args), family))
        return len(self._because)

    def and_no_longer(self, mark: object) -> None:
        """Take back what `because` put on, and everything after it."""
        assert isinstance(mark, int)
        del self._because[mark - 1:]

    def report_note(self, note: Diagnostic) -> None:
        """Hand a note that was attached to an already reported diagnostic on."""
        note.reported = True
        self._record(note)
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
