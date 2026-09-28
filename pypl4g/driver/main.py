"""The compilation driver.

It owns the diagnostic engine and the source manager, sequences the stages, and
is the one place that turns an internal failure into a report rather than a
traceback.
"""

from __future__ import annotations

import json
import sys
from os import environ
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
import platform
import subprocess
import tempfile
from dataclasses import replace
from typing import Final, Sequence, TextIO

#: How long a test binary is given.  A test that runs longer than this is one
#: that will not finish, and a build waiting for ever on it is worse than a
#: build that says so.
TEST_TIMEOUT: Final[float] = 120.0

from .. import VERSION
from ..diag import ids as D
from ..diag.catalog import catalog
from ..diag.engine import DiagEngine, InternalError, WarningControl
from ..diag.render import JSONRenderer, TextRenderer
from ..front import ast
from ..front.lexer import tokenize
from ..front.parser import parse
from ..ir.reports import Report, ReportLog
from ..ir.function import Function, SpecialKind
from ..ir.module import Module
from ..ir.printer import render_module
from ..ir.verify import verify
from ..mc.asmbuilder import RegisterAssignmentError
from ..mc.dump import dump_sections
from ..mc.streamer import MCStreamer
from ..opt.pass_ import build_manager
from ..sema.check import check
from ..sema.modules import (ModuleRegistry, SearchPath,
                            system_modules)
from ..sema.notes import Notes
from ..source.manager import (SourceDecodeError, SourceManager, SourceReadError)
from ..target.registry import (canonical_triples, known_triples,
                               lookup as lookup_target)
from ..comptime.build import (Plan, builtins as build_builtins, named,
                              sources_of)
from ..sema.check import STD_MODULE_NAME as STD_MODULE
from ..comptime.evaluate import CannotEvaluate, Cell, Evaluator, Reference
from .cli import parse_command_line
from .cli import LSP, TEST
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
    #: Which kinds of test the binary this run writes is built to run.  Empty
    #: for the program itself, whose entry calls its startup function; the
    #: `always` tests are in it either way, being what says it is fit to start.
    wanted_tests: tuple[SpecialKind, ...] = ()
    #: What the run compiled, kept so that what it holds can be asked about
    #: after the fact -- whether there are tests to run, above all.
    module: Module | None = None
    #: Everything the compiler said about the program and chose about it, in the
    #: order it happened.  The diagnostics are written here by the engine as
    #: they are reported, and the module writes its choices into the same log --
    #: so a compilation that failed early still leaves one rather than none.
    reports: ReportLog = field(default_factory=ReportLog)
    #: Where to write down what each name in the program turned out to be, for
    #: whoever will be asked about one later.  Nothing for a build, which is
    #: never asked; the language server hands one in.
    notes: Notes | None = None

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
        self.module = module
        if module.build is not None and not self.options.from_build:
            # What was named describes a build rather than being one: the
            # compiler runs the function and then does what it asked for.
            return self._run_build(units)
        if self.options.emit is EmitKind.IR:
            return self._write_text(render_module(module))
        return self._generate(module)

    def _run_build(self, units: Sequence[ast.SourceUnit]) -> int:
        """Run the build function and build what it asked for.

        Nothing of the file that describes the build is compiled: what it is for
        is what it leaves in the object it was handed, and that is a list of
        things to build with the settings to build them under.  Each of them is
        then an ordinary compilation, run the way a command line naming it would
        have run it.
        """
        start = perf_counter()
        found = _build_function(units)
        if found is None:
            # The module says there is one and the tree does not: the two are
            # built from each other, so this cannot happen.
            self.diags.internal("the build function is not in the syntax tree")
            return ExitCode.INTERNAL
        # What the compiler was run with, read under two names that answer with
        # the one dictionary: `⎕environ`, which is what a program reads its own
        # environment under, and `std.Build.env`, which is the field of the
        # object the build function is handed.  A copy, so that nothing a build
        # does could change what the compiler itself sees.
        settled = dict(environ)
        plan = Plan(output_dir="", target=self.options.triple,
                    opt_level=self.options.opt_level,
                    mclevel=self.options.mclevel or "",
                    stack_size=self.options.stack_size,
                    guard_size=self.options.guard_size,
                    env=settled)
        record = plan.record()
        evaluator = Evaluator(units, {STD_MODULE: build_builtins(
            plan, self.options.defines, self.diags)}, environ=settled)
        try:
            evaluator.call(found, [Reference(Cell(record), mutable=True)])
        except CannotEvaluate as exc:
            if exc.ran_out:
                self.diags.emit(D.LANG_COMPTIME_ENDLESS, exc.span,
                                detail=exc.detail)
            else:
                self.diags.emit(D.LANG_COMPTIME_CANNOT, exc.span,
                                construct=exc.detail)
            return ExitCode.ERRORS
        plan.settle(record)
        self._timed("the build function", start)
        if self.diags.failed:
            return ExitCode.ERRORS
        if not plan.artifacts:
            self.diags.emit(D.LANG_BUILD_NOTHING)
            return ExitCode.SUCCESS
        return self._build_artifacts(plan)

    def _build_artifacts(self, plan: Plan) -> int:
        """Compile each thing the build asked for, in the order it asked.

        One driver each, sharing this one's diagnostics and its source manager:
        what a reader sees is one run reporting what went wrong wherever it was,
        and a file named by two artifacts is read twice because each compilation
        is a compilation of its own.
        """
        beside = self.options.inputs[0].resolve().parent
        status = ExitCode.SUCCESS
        for artifact in plan.artifacts:
            output = named(artifact, plan)
            options = replace(
                self.options, inputs=sources_of(artifact, beside), output=output,
                triple=plan.target, opt_level=plan.opt_level,
                mclevel=plan.mclevel or None, stack_size=plan.stack_size,
                guard_size=plan.guard_size, from_build=True)
            if output.parent != Path():
                try:
                    output.parent.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    self.diags.emit(D.IMPL_OUTPUT_UNWRITABLE,
                                    path=output.as_posix(),
                                    reason=exc.strerror or str(exc))
                    return ExitCode.ERRORS
            if self.options.verbose:
                print("".join(("pypl4g: building ", artifact.name)),
                      file=self.stderr)
            made = Driver(options=options, diags=self.diags, sources=self.sources,
                          stderr=self.stderr, reports=self.reports)
            answered = made.run()
            self.timings.extend(made.timings)
            if answered != ExitCode.SUCCESS:
                status = answered
        return status

    def front_end(self) -> Module | None:
        """Read, parse and check, and stop there.

        Every diagnostic but the back end's is made by these stages, and they
        are the fast ones: what asks for this rather than for `run` is something
        that wants to know what is wrong with a program rather than to have it
        compiled -- the language server, which does this on every keystroke.
        """
        return self._analyze(self._read_and_parse())

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
            unit = parse(tokens, path.as_posix(), self.diags)
            self.sources.record(path, tokens, unit)
            units.append(unit)
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
        module = Module(name=name, triple=self.options.triple,
                        stack_size=self.options.stack_size,
                        guard_size=self.options.guard_size,
                        reports=self.reports)
        registry = ModuleRegistry(search=SearchPath(
            given=list(self.options.module_path), system=system_modules()))
        check(module, units, self.diags, registry, self.sources, self.notes)
        # Before anything is dropped: which tests this binary runs is what
        # decides which of them are reachable at all.
        module.test_plan.extend(_planned(module, self.wanted_tests))
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

    def _settle_mclevel(self, target: object) -> bool:
        """Tell the target which of its microarchitecture levels to generate for.

        What may be asked for is each architecture's own business -- x86-64 has
        four named levels and RISC-V has a string naming extensions or a profile
        naming a published set of them -- so the name is handed to the target
        and the target says what is wrong with it.  A target that can be built
        for only one thing says so by having no such method at all, and asking
        for a level of it is asking for something with no meaning rather than
        for the only thing there is.
        """
        wanted = self.options.mclevel
        if wanted is None:
            return True
        settle = getattr(target, "use_mclevel", None)
        if settle is None:
            self.diags.emit(D.IMPL_CLI_NO_MCLEVELS, triple=self.options.triple)
            return False
        try:
            settle(wanted)
        except ValueError as exc:
            self.diags.emit(D.IMPL_CLI_UNKNOWN_MCLEVEL, level=wanted,
                            triple=self.options.triple, detail=str(exc))
            return False
        return True

    def _generate(self, module: Module) -> int:
        """Generate code and write the image."""
        target = lookup_target(self.options.triple)
        if target is None:
            self.diags.emit(D.IMPL_CLI_UNKNOWN_TARGET, triple=self.options.triple)
            return ExitCode.ERRORS
        if not self._settle_mclevel(target):
            return ExitCode.ERRORS
        start = perf_counter()
        streamer = MCStreamer(encode=target.encode)
        asm = target.new_assembler(streamer, self.options.opt_level)
        try:
            target.generate(module, asm, self.diags, self.options.opt_level,
                            self.sources)
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
        # After the dump above and not before it: the dump is for reading what
        # the code generator produced, and this is about the file rather than
        # about the code -- it names the sources by the paths they were read
        # from, which are one machine's and not a program's.
        self._emit_sbom(asm)
        start = perf_counter()
        defaults = target.image_defaults()
        settings = ImageSettings(machine=defaults.machine, base_vaddr=defaults.base_vaddr,
                                 page_size=defaults.page_size,
                                 entry_symbol=target.entry_symbol,
                                 header_flags=defaults.header_flags,
                                 kind=ImageKind.EXECUTABLE,
                                 stack_size=self.options.stack_size)
        try:
            image, _ = write_image(settings, list(streamer.sections.values()),
                                   list(streamer.symbols.values()), module.source_paths,
                                   target.apply_fixup)
        except ImageError as exc:
            if exc.symbol is not None:
                self.diags.emit(D.IMPL_IMAGE_UNDEFINED_SYMBOL, name=exc.symbol)
            elif exc.out_of_range is not None:
                value, field = exc.out_of_range
                self.diags.emit(D.IMPL_IMAGE_VALUE_OUT_OF_RANGE, value=value, field=field)
            else:
                self.diags.internal(exc.detail)
            return ExitCode.ERRORS
        self._timed("image generation", start)
        return self._write_binary(image)

    # -- output ----------------------------------------------------------------

    def _emit_sbom(self, asm: object) -> None:
        """Write what the image was built from into the image.

        Always, and not behind a flag: a bill of materials that a flag turns off
        is one nobody can rely on being there, and the question it answers --
        what is this built from -- is asked of binaries nobody thought to ask
        about at the time.

        The table is loaded and read-only, and so are its strings: the table
        holds offsets into them, so one without the other would be a table a
        running program could not read.  A tool reading the file finds them
        through the section's link either way.
        """
        from ..elf.const import SHT_PROGBITS, SHT_STRTAB
        from ..sbom import ROW_SIZE, SECTION, STRINGS, build, entries_for

        table = build(entries_for(self.sources.read_units), little_endian=True)
        asm.section(SECTION, alloc=True, alignment=4, sh_type=SHT_PROGBITS,
                    sh_link_to=STRINGS, sh_entsize=ROW_SIZE)
        asm.bytes(table.rows)
        asm.section(STRINGS, alloc=True, alignment=1, sh_type=SHT_STRTAB)
        asm.bytes(table.strings)

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

    def write_report_log(self) -> None:
        """Write the log of everything the compiler said and chose.

        Two kinds of thing are in it.  What the compiler *said* is the
        diagnostics, with the number the catalog gives them.  What it *chose* is
        what the program did not state -- what was left out, where something was
        put, how long a reference turned out to live -- and has no number,
        nothing being wrong with any of it.  They are in one log and in one
        order because the question a reader has, what happened to my program, is
        not a question about only one of them.

        The log records the directory the compiler ran in, so that the paths in
        it can be resolved from anywhere.  It is written as JSON so that a build
        can keep it beside the binary and something can ask it a question later.
        """
        if self.options.report_log is None:
            return
        document = {
            "format_version": 3,
            "compiler": "".join(("pypl4g ", VERSION)),
            # Where the compiler ran, so that every path below can be found from
            # anywhere: a path that is not absolute is relative to this.  It is
            # the arrangement DWARF uses, where a compilation unit records the
            # directory it was compiled in beside the name it was compiled from,
            # and it is what lets a log be read on another machine or from
            # another directory without guessing what the paths were relative to.
            "directory": Path.cwd().as_posix(),
            "inputs": [p.as_posix() for p in self.options.inputs],
            "reports": [self._rendered_report(one)
                        for one in self.reports.entries],
        }
        self.options.report_log.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _rendered_report(self, report: Report) -> dict[str, object]:
        """One report, as the log records it.

        The kind is the stable part, so that a reader can ask which functions
        were dropped without matching on prose; the reason is there for a person
        and may be reworded.
        """
        entry: dict[str, object] = {
            "kind": report.kind.value,
            "subject": report.subject,
            "reason": report.reason,
        }
        if report.number is not None:
            # Something the compiler said, which the catalog numbers.  A choice
            # has no number: nothing is wrong, so there is nothing to look up.
            entry["number"] = report.number
        position = (self.sources.position(report.span.start)
                    if report.span.is_valid else None)
        if position is not None:
            entry["where"] = {"file": position.path, "line": position.line,
                              "column": position.column}
        return entry

    def report_timings(self) -> None:
        """Print how long each stage took."""
        if not self.options.time_report:
            return
        print("stage timings:", file=self.stderr)
        for timing in self.timings:
            print("".join(("  ", format(timing.seconds * 1000, "8.3f"), " ms  ",
                           timing.name)), file=self.stderr)


#: What `pypl4g test` runs: the tests that say the program is fit to start and
#: the ones written for a suite run.  A `build` test is not among them -- it runs
#: when a build finishes, which is a different question asked at a different
#: time.
SUITE_RUN: Final[tuple[SpecialKind, ...]] = (SpecialKind.TEST_ALWAYS,
                                             SpecialKind.TEST_SUITE)

#: What a finished build runs.
AFTER_A_BUILD: Final[tuple[SpecialKind, ...]] = (SpecialKind.TEST_BUILD,)


def _build_function(units: Sequence[ast.SourceUnit]) -> ast.FuncDef | None:
    """The function marked `@[build]`, where the program has one.

    Out of the syntax tree, because that is what the compiler runs: the module
    holds the checked form of it, which is what says there is one and what says
    there is only one.
    """
    for unit in units:
        for item in unit.items:
            if isinstance(item, ast.FuncDef) \
                    and any(attr.name == "build" for attr in item.attrs):
                return item
    return None


def _planned(module: Module, wanted: Sequence[SpecialKind]) -> list[Function]:
    """The tests a binary built for *wanted* runs, in the order they are written.

    In the order they are written and not grouped by kind: a run that reported
    them in an order nobody wrote would be one whose output moved when something
    unrelated was added.
    """
    if not wanted:
        return []
    return [one for one in module.tests if one.attrs.special in wanted]


def _built_for_this_machine(triple: str) -> bool:
    """Whether a binary for *triple* is one this machine runs.

    The architecture and nothing else: the binaries depend on nothing from the
    system, so what decides it is whether the processor knows the instructions.
    """
    wanted = triple.split("-")[0]
    here = platform.machine()
    return wanted == here or (wanted, here) in (("x86_64", "amd64"),
                                                ("aarch64", "arm64"))


def _ran_the_tests(options: Options, diags: DiagEngine, binary: Path,
                   kind: str, stderr: TextIO) -> bool:
    """Run a test binary and answer whether every test in it passed.

    What it writes goes where the compiler's own messages go: the binary names
    the test that did not pass, and that name is the thing worth reading.
    """
    if options.test_runner:
        words = [*options.test_runner.split(), str(binary)]
    elif _built_for_this_machine(options.triple):
        words = [str(binary)]
    else:
        diags.emit(D.IMPL_TESTS_NOT_RUN, kind=kind, triple=options.triple)
        return True
    try:
        done = subprocess.run(words, stderr=subprocess.PIPE, text=True,
                              timeout=TEST_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        diags.emit(D.IMPL_TESTS_NOT_RUN, kind=kind, triple=str(exc))
        return True
    if done.stderr:
        stderr.write(done.stderr)
    if done.returncode == 0:
        return True
    diags.emit(D.IMPL_TESTS_FAILED, kind=kind)
    return False


def _test_binary(options: Options, diags: DiagEngine, sources: SourceManager,
                 reports: ReportLog, stderr: TextIO,
                 wanted: tuple[SpecialKind, ...], kind: str) -> int:
    """Build a binary that runs *wanted* and run it.

    Built from the sources over again rather than from the module in hand: the
    passes have already left out of that one everything the program does not
    reach, and a test the program does not call is exactly what they left out.
    """
    with tempfile.TemporaryDirectory() as room:
        where = replace(options, output=Path(room) / "tests",
                        emit=EmitKind.ELF, report_log=None)
        driver = Driver(options=where, diags=diags, sources=sources,
                        stderr=stderr, reports=reports, wanted_tests=wanted)
        status = driver.run()
        if status != ExitCode.SUCCESS or diags.failed:
            return status
        if driver.module is not None and not driver.module.test_plan:
            return ExitCode.SUCCESS
        if not _ran_the_tests(where, diags, where.output, kind, stderr):
            return ExitCode.ERRORS
    return ExitCode.SUCCESS


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
    # One log for the run, written into from both ends: the engine puts down
    # what it reports and the module what it chooses, so that what comes out is
    # in the order it happened rather than in two heaps.
    reports = ReportLog()
    diags = DiagEngine(renderer, log=reports)
    collected.append(diags)

    options = parse_command_line(argv, diags)
    # Now that the command line has been read, and not before: the diagnostics
    # a bad command line makes are made by the renderer built above.
    renderer.recolour(options.colour)
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
        diags = DiagEngine(json_renderer, diags.control, log=reports)
        collected[0] = diags
    if diags.failed:
        return ExitCode.USAGE
    if lookup_target(options.triple) is None:
        diags.emit(D.IMPL_CLI_UNKNOWN_TARGET, triple=options.triple)
        print("".join(("known targets: ", ", ".join(known_triples()))), file=err)
        if json_renderer is not None:
            json_renderer.finish()
        return ExitCode.USAGE

    driver = Driver(options=options, diags=diags, sources=sources, stderr=err,
                    reports=reports)
    if options.command == LSP:
        # Nothing of the command line is compiled: what the server compiles is
        # what an editor sends it, and it goes on until the editor says to stop.
        # Imported here rather than above because the server is built on this
        # module -- it drives the same driver -- and because a build has no use
        # for it.
        from ..lsp import serve
        return serve()
    try:
        if options.command == TEST:
            # Nothing else is built: what was asked for is the tests, and the
            # program itself is not what runs.
            status = _test_binary(options, diags, sources, reports, err,
                                  SUITE_RUN, "suite")
        else:
            status = driver.run()
            if status == ExitCode.SUCCESS and not diags.failed \
                    and options.emit is EmitKind.ELF \
                    and driver.module is not None \
                    and any(one.attrs.special is SpecialKind.TEST_BUILD
                            for one in driver.module.tests):
                status = _test_binary(options, diags, sources, reports, err,
                                      AFTER_A_BUILD, "build")
    except InternalError as exc:
        diags.internal(str(exc))
        status = ExitCode.INTERNAL
    driver.write_report_log()
    driver.report_timings()
    if json_renderer is not None:
        json_renderer.finish()
    if diags.failed and status == ExitCode.SUCCESS:
        return ExitCode.ERRORS
    return status


def _usage_hint(stderr: TextIO) -> None:
    """Point at the help after a malformed command line."""
    print("try 'pypl4g --help' for the accepted options", file=stderr)
