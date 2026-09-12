"""The compilation driver.

It owns the diagnostic engine and the source manager, sequences the stages, and
is the one place that turns an internal failure into a report rather than a
traceback.
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Sequence, TextIO

from .. import VERSION
from ..diag import ids as D
from ..diag.catalog import catalog
from ..diag.engine import DiagEngine, InternalError, WarningControl
from ..diag.render import JSONRenderer, TextRenderer
from ..front import ast
from ..front.lexer import tokenize
from ..front.parser import parse
from ..ir.module import Module
from ..ir.printer import render_module
from ..ir.verify import verify
from ..mc.asmbuilder import RegisterAssignmentError
from ..mc.dump import dump_sections
from ..mc.streamer import MCStreamer
from ..opt.pass_ import build_manager
from ..sema.check import check
from ..source.manager import (SourceDecodeError, SourceManager, SourceReadError)
from ..target.registry import (canonical_triples, known_triples,
                               lookup as lookup_target)
from .cli import parse_command_line
from .options import (DiagFormat, EmitKind, ExitCode, Options, load_option_table,
                      render_help)


@dataclass(slots=True)
class Timing:
    """How long one stage took."""

    name: str
    seconds: float


@dataclass(slots=True)
class Driver:
    """Runs one compilation."""

    options: Options
    diags: DiagEngine
    sources: SourceManager
    stderr: TextIO
    timings: list[Timing] = field(default_factory=list)

    def _timed(self, name: str, start: float) -> None:
        """Record that a stage finished."""
        self.timings.append(Timing(name, perf_counter() - start))
        if self.options.verbose:
            print("".join(("pypl4g: ", name, " done")), file=self.stderr)

    def run(self) -> int:
        """Compile, returning the status the process should exit with."""
        units = self._read_and_parse()
        if self.options.emit is EmitKind.TOKENS or self.options.emit is EmitKind.AST:
            return self._emit_frontend(units)
        module = self._analyze(units)
        if module is None or self.diags.failed:
            return ExitCode.ERRORS
        if self.options.emit is EmitKind.IR:
            return self._write_text(render_module(module))
        return self._generate(module)

    # -- stages ----------------------------------------------------------------

    def _read_and_parse(self) -> list[ast.SourceUnit]:
        """Read every input file and parse it."""
        units: list[ast.SourceUnit] = []
        for path in self.options.inputs:
            start = perf_counter()
            try:
                source = self.sources.read(path)
            except SourceReadError as exc:
                self.diags.emit(D.IMPL_INPUT_UNREADABLE, path=exc.path.as_posix(),
                                reason=exc.reason)
                continue
            except SourceDecodeError as exc:
                self.diags.emit(D.LANG_SYNTAX_INVALID_UTF8, offset=exc.byte_offset)
                continue
            tokens = tokenize(source, self.diags)
            units.append(parse(tokens, path.as_posix(), self.diags))
            self._timed("".join(("parse ", path.as_posix())), start)
        return units

    def _emit_frontend(self, units: Sequence[ast.SourceUnit]) -> int:
        """Write the tokens or the syntax tree, for inspecting the frontend."""
        if self.diags.failed:
            return ExitCode.ERRORS
        if self.options.emit is EmitKind.TOKENS:
            lines: list[str] = []
            for source in self.sources.files:
                engine = self.diags
                for token in tokenize(source, engine):
                    position = self.sources.position(token.span.start)
                    where = "" if position is None else "".join(
                        (position.path, ":", str(position.line), ":", str(position.column)))
                    lines.append("".join((where.ljust(28), token.kind.name.ljust(14),
                                          repr(token.text))))
            return self._write_text("\n".join(lines) + "\n")
        return self._write_text("\n".join(repr(u) for u in units) + "\n")

    def _analyze(self, units: Sequence[ast.SourceUnit]) -> Module | None:
        """Check the program and lower it to the IR."""
        start = perf_counter()
        name = self.options.inputs[0].name if self.options.inputs else "<none>"
        module = Module(name=name, triple=self.options.triple)
        check(module, units, self.diags)
        self._timed("semantic analysis", start)
        if self.diags.failed:
            return module
        verify(module)
        start = perf_counter()
        manager = build_manager(self.options.opt_level)
        manager.run(module)
        for timing in manager.timings:
            self.timings.append(Timing("".join(("pass ", timing.name)), timing.seconds))
        self._timed("optimization", start)
        return module

    def _generate(self, module: Module) -> int:
        """Generate code and write the image."""
        target = lookup_target(self.options.triple)
        if target is None:
            self.diags.emit(D.IMPL_CLI_UNKNOWN_TARGET, triple=self.options.triple)
            return ExitCode.ERRORS
        start = perf_counter()
        streamer = MCStreamer(encode=target.encode)
        asm = target.new_assembler(streamer, self.options.opt_level)
        try:
            target.generate(module, asm, self.diags, self.options.opt_level)
        except RegisterAssignmentError as exc:
            self.diags.internal(str(exc))
            return ExitCode.ERRORS
        self._timed("code generation", start)
        if self.diags.failed:
            return ExitCode.ERRORS

        from ..elf.layout import ImageKind
        from ..elf.writer import ImageError, ImageSettings, write_image
        from ..mc.layout import layout_section, resolve_symbol_offsets

        if self.options.emit is EmitKind.ASM:
            symbols = list(streamer.symbols.values())
            for section in streamer.sections.values():
                layout_section(section)
                resolve_symbol_offsets(section, symbols)
            return self._write_text(dump_sections(list(streamer.sections.values()),
                                                  symbols))
        start = perf_counter()
        defaults = target.image_defaults()
        settings = ImageSettings(machine=defaults.machine, base_vaddr=defaults.base_vaddr,
                                 page_size=defaults.page_size,
                                 entry_symbol=target.entry_symbol,
                                 kind=ImageKind.EXECUTABLE)
        try:
            image, _ = write_image(settings, list(streamer.sections.values()),
                                   list(streamer.symbols.values()), module.source_paths)
        except ImageError as exc:
            if exc.symbol is not None:
                self.diags.emit(D.IMPL_IMAGE_UNDEFINED_SYMBOL, name=exc.symbol)
            else:
                self.diags.internal(exc.detail)
            return ExitCode.ERRORS
        self._timed("image generation", start)
        return self._write_binary(image)

    # -- output ----------------------------------------------------------------

    def _write_text(self, text: str) -> int:
        """Write a textual result to the output file."""
        assert self.options.output is not None
        try:
            self.options.output.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.diags.emit(D.IMPL_OUTPUT_UNWRITABLE,
                            path=self.options.output.as_posix(),
                            reason=exc.strerror or str(exc))
            return ExitCode.ERRORS
        return ExitCode.SUCCESS

    def _write_binary(self, image: bytes) -> int:
        """Write the generated executable."""
        assert self.options.output is not None
        try:
            self.options.output.write_bytes(image)
            self.options.output.chmod(0o755)
        except OSError as exc:
            self.diags.emit(D.IMPL_OUTPUT_UNWRITABLE,
                            path=self.options.output.as_posix(),
                            reason=exc.strerror or str(exc))
            return ExitCode.ERRORS
        return ExitCode.SUCCESS

    def write_decision_log(self) -> None:
        """Write the log of the decisions the compiler made.

        The compiler makes no recorded decisions yet, so the log is empty; it is
        written all the same, so that the format is fixed before there is
        anything to put in it.
        """
        if self.options.decision_log is None:
            return
        document = {
            "format_version": 1,
            "compiler": "".join(("pypl4g ", VERSION)),
            "inputs": [p.as_posix() for p in self.options.inputs],
            "decisions": [],
        }
        self.options.decision_log.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def report_timings(self) -> None:
        """Print how long each stage took."""
        if not self.options.time_report:
            return
        print("stage timings:", file=self.stderr)
        for timing in self.timings:
            print("".join(("  ", format(timing.seconds * 1000, "8.3f"), " ms  ",
                           timing.name)), file=self.stderr)


def _warning_control(options: Options, diags: DiagEngine) -> WarningControl:
    """Build the warning settings, reporting any name that is not a warning."""
    control = WarningControl(warnings_are_errors=options.warnings_are_errors)
    known = catalog().by_option
    for name, enabled in options.warnings.items():
        if name not in known:
            diags.emit(D.IMPL_CLI_UNKNOWN_WARNING, name=name,
                       option="".join(("-W", "" if enabled else "no-", name)))
            continue
        control.enabled[name] = enabled
    return control


def main(argv: Sequence[str], stdout: TextIO | None = None,
         stderr: TextIO | None = None) -> int:
    """Run the compiler over *argv* and return the process status."""
    out: TextIO = sys.stdout if stdout is None else stdout
    err: TextIO = sys.stderr if stderr is None else stderr
    sources = SourceManager()
    collected: list[DiagEngine] = []
    renderer = TextRenderer(sources, err, collected)
    diags = DiagEngine(renderer)
    collected.append(diags)

    options = parse_command_line(argv, diags)
    if options.show_version:
        print("".join(("pypl4g ", VERSION)), file=out)
        return ExitCode.SUCCESS
    if options.show_help:
        print(render_help(load_option_table()), file=out, end="")
        return ExitCode.SUCCESS
    if options.show_targets:
        for triple in canonical_triples():
            print(triple, file=out)
        return ExitCode.SUCCESS
    if options.show_help_json:
        json.dump(load_option_table(), out, indent=2, ensure_ascii=False)
        out.write("\n")
        return ExitCode.SUCCESS
    if diags.failed:
        _usage_hint(err)
        return ExitCode.USAGE

    diags.set_control(_warning_control(options, diags))
    json_renderer: JSONRenderer | None = None
    if options.diag_format is DiagFormat.JSON:
        json_renderer = JSONRenderer(sources, err)
        diags = DiagEngine(json_renderer, diags.control)
        collected[0] = diags
    if diags.failed:
        return ExitCode.USAGE
    if lookup_target(options.triple) is None:
        diags.emit(D.IMPL_CLI_UNKNOWN_TARGET, triple=options.triple)
        print("".join(("known targets: ", ", ".join(known_triples()))), file=err)
        if json_renderer is not None:
            json_renderer.finish()
        return ExitCode.USAGE

    driver = Driver(options=options, diags=diags, sources=sources, stderr=err)
    try:
        status = driver.run()
    except InternalError as exc:
        diags.internal(str(exc))
        status = ExitCode.INTERNAL
    driver.write_decision_log()
    driver.report_timings()
    if json_renderer is not None:
        json_renderer.finish()
    if diags.failed and status == ExitCode.SUCCESS:
        return ExitCode.ERRORS
    return status


def _usage_hint(stderr: TextIO) -> None:
    """Point at the help after a malformed command line."""
    print("try 'pypl4g --help' for the accepted options", file=stderr)
