"""Shared test fixtures and the collector for language tests.

A language test is a ``.pl4g`` file that carries its own expectations as
directives in its comments, so that a test and what it expects cannot drift
apart.  Those tests drive the compiler only through its command line, so they
will still be valid once the final compiler replaces this one.
"""

from __future__ import annotations

import fcntl
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIRECTIVE = "\N{REFERENCE MARK} pl4g-test:"

#: The user's objdump dispatches on the architecture of its argument, which it
#: cannot determine for a raw instruction blob, so the tests name the one they
#: need directly.
OBJDUMP = "/usr/bin/objdump"

#: The disassembler and the emulator for each architecture.  A test that needs
#: one is skipped where it is not installed rather than failing.
ARCH_TOOLS: dict[str, dict[str, str]] = {
    "x86_64": {"objdump": "/usr/bin/objdump", "machine": "i386:x86-64",
               "qemu": "qemu-x86_64", "flavour": "intel"},
    "aarch64": {"objdump": "/usr/bin/aarch64-linux-gnu-objdump", "machine": "aarch64",
                "qemu": "qemu-aarch64", "flavour": ""},
    "riscv64": {"objdump": "/usr/bin/riscv64-linux-gnu-objdump", "machine": "riscv:rv64",
                "qemu": "qemu-riscv64", "flavour": "",
                # This architecture puts what an image was built for in a
                # section of its own, and reading one back wants the reader
                # that knows the format.
                "readelf": "/usr/bin/riscv64-linux-gnu-readelf"},
}

HOST_ARCH = platform.machine()

#: Checks an ELF file against what the format requires.  With --strict it also
#: reports what is merely allowed by common practice rather than by the standard,
#: which is the level worth holding a compiler that writes the file itself to.
ELFLINT = "eu-elflint"


def check_conformance(path: Path) -> None:
    """Check a generated binary against the format, and say what is wrong.

    Every binary the tests produce goes through this.  The compiler writes the
    image itself, with no assembler or linker between it and the file, so there
    is nothing else that would notice a field it filled in wrongly.
    """
    if not shutil.which(ELFLINT):
        return
    proc = subprocess.run([ELFLINT, "--strict", str(path)], capture_output=True,
                          text=True, timeout=60)
    complaints = proc.stdout.strip()
    assert proc.returncode == 0 and complaints == "No errors", "".join((
        ELFLINT, " --strict rejects ", str(path), ":\n", complaints, proc.stderr))


def architecture_of(triple: str) -> str:
    """The architecture a triple names, which is its first component."""
    return triple.split("-", 1)[0]


def runner_for(triple: str) -> list[str]:
    """How to run a binary built for *triple*: directly, or through an emulator."""
    arch = architecture_of(triple)
    if arch == HOST_ARCH:
        return []
    return [ARCH_TOOLS.get(arch, {}).get("qemu", "".join(("qemu-", arch)))]


def compiler_targets() -> list[str]:
    """The targets the compiler reports, asked once per session."""
    global _TARGETS
    if _TARGETS is None:
        proc = run_compiler(["--print-targets"])
        assert proc.returncode == 0, describe(proc)
        _TARGETS = proc.stdout.split()
    return _TARGETS


_TARGETS: list[str] | None = None


@dataclass(slots=True)
class Expectations:
    """What a language test expects of the compiler."""

    compiles: bool = True
    exit_status: int = 0
    run_native: bool = False
    run_qemu: bool = False
    no_diagnostics: bool = False
    expected_diags: list[tuple[int, str]] = field(default_factory=list)
    opt_levels: list[int] = field(default_factory=lambda: [0])
    #: Extra options to pass to the compiler, for the test to ask for a warning
    #: that is off by default, for instance.
    extra_args: list[str] = field(default_factory=list)
    xfail: str | None = None
    #: Whether the file is a module another test imports rather than a test of
    #: its own.  One has no startup function, so compiling it alone would fail
    #: for a reason that says nothing about the compiler.
    is_module: bool = False


def parse_directives(text: str) -> Expectations:
    """Read the expectations a test file states in its comments."""
    result = Expectations()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(DIRECTIVE):
            continue
        body = stripped[len(DIRECTIVE):].strip()
        if body == "module":
            result.is_module = True
            continue
        if body.startswith("args "):
            result.extra_args.extend(body[len("args "):].split())
            continue
        if body.startswith("expect-diag"):
            parts = body.split()
            result.expected_diags.append((int(parts[1]), parts[2]))
            continue
        for item in body.split():
            key, _, value = item.partition("=")
            match key:
                case "compile":
                    result.compiles = value == "ok"
                case "exit":
                    result.exit_status = int(value)
                case "run":
                    result.run_native = "native" in value
                    result.run_qemu = "qemu" in value
                case "diag":
                    result.no_diagnostics = value == "none"
                case "opt":
                    result.opt_levels = [int(v) for v in value.split(",")]
                case "xfail":
                    result.xfail = value
    return result



@pytest.fixture(scope="session", autouse=True)
def grammar_is_built() -> None:
    """Have the grammar's parser built before anything tries to parse with it.

    `tree-sitter parse` compiles the grammar into a shared library the first
    time it is asked to, and again whenever the generated parser is newer --
    which it is, every time the grammar changes.  Run the suite over many cores
    and dozens of processes arrive at that at once; what they get is a library
    half written, reported as a file that suddenly does not parse or as a
    language that cannot be loaded.

    So one process does it and the rest wait.  The lock is a file rather than
    anything pytest provides, because the processes that have to agree are
    separate interpreters that know nothing about one another -- which is what
    `-n auto` makes them.
    """
    if not shutil.which("tree-sitter"):
        return
    grammar = ROOT / "tree-sitter-pl4g"
    if not (grammar / "src" / "parser.c").exists():
        return
    lock = ROOT / ".pytest_cache" / "grammar-build.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    # A program of this language and not just any file: which grammar gets
    # built is decided by what the file is, so asking it to parse something
    # else builds something else and leaves this one to be raced for after all.
    warm = lock.with_name("grammar-build.pl4g")
    warm.write_text("fn nothing():\n    ()\n", encoding="utf-8")
    with open(lock, "w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            subprocess.run(["tree-sitter", "parse", "--quiet", str(warm)],
                           cwd=grammar, capture_output=True, timeout=300,
                           check=False)
        except (OSError, subprocess.SubprocessError):
            # Whatever is wrong with it is the grammar tests' to report; this
            # only exists so that they do not all find it out at once.
            pass


def run_compiler(args: Sequence[str],
                 env: Mapping[str, str] | None = None,
                 cwd: Path | None = None
                 ) -> subprocess.CompletedProcess[str]:
    """Invoke the compiler as a separate process, the way a user would.

    *env* adds to what this process has rather than replacing it, since what a
    test is saying with it is one variable and not a whole environment.  *cwd* is
    where to run it, for the tests that are about what a command line means where
    it was typed -- a build file is looked for beside the caller.
    """
    return subprocess.run(
        [sys.executable, "-m", "pypl4g", *args],
        cwd=str(ROOT if cwd is None else cwd), capture_output=True, text=True,
        timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT), **(env or {})})


def describe(proc: subprocess.CompletedProcess[Any]) -> str:
    """Explain a process result, distinguishing a signal from a status."""
    if proc.returncode < 0:
        return "".join(("killed by signal ", str(-proc.returncode),
                        "\nstdout: ", str(proc.stdout),
                        "\nstderr: ", str(proc.stderr)))
    return "".join(("exit status ", str(proc.returncode),
                    "\nstdout: ", str(proc.stdout), "\nstderr: ", str(proc.stderr)))


class PL4GFile(pytest.File):
    """A ``.pl4g`` file, collected as one test."""

    def collect(self):  # noqa: ANN201
        """Yield one item per optimization level, and per target where it runs.

        A program that is expected to run is built and run for every target, so
        a backend that miscompiles it fails here.  A program that is expected
        not to compile is checked once: a diagnostic does not depend on the
        target.
        """
        text = self.path.read_text(encoding="utf-8")
        expectations = parse_directives(text)
        runs = expectations.compiles and (expectations.run_native
                                          or expectations.run_qemu)
        triples = compiler_targets() if runs else [compiler_targets()[0]]
        for level in expectations.opt_levels:
            for triple in triples:
                suffix = "".join(("-O", str(level)))
                if runs:
                    suffix = "".join((suffix, "-", architecture_of(triple)))
                yield PL4GItem.from_parent(
                    self, name="".join((self.path.stem, suffix)),
                    expectations=expectations, opt_level=level, triple=triple)


class PL4GItem(pytest.Item):
    """One run of the compiler over one language test file."""

    def __init__(self, *, expectations: Expectations, opt_level: int, triple: str,
                 **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.expectations = expectations
        self.opt_level = opt_level
        self.triple = triple

    def runtest(self) -> None:
        """Compile the file and check everything it expects."""
        if self.expectations.xfail is not None:
            pytest.xfail(self.expectations.xfail)
        source = Path(str(self.path))
        output = Path(str(self.config.rootpath)) / ".pytest_cache" / "bin" / \
            self.triple / "".join((source.stem, "-O", str(self.opt_level)))
        output.parent.mkdir(parents=True, exist_ok=True)
        proc = run_compiler(["-o", str(output), "".join(("-O", str(self.opt_level))),
                             "".join(("--target=", self.triple)),
                             *self.expectations.extra_args, str(source)])
        self._check_compile(proc)
        if not self.expectations.compiles:
            return
        check_conformance(output)
        runner = runner_for(self.triple)
        if runner and not shutil.which(runner[0]):
            pytest.skip("".join((runner[0], " is not installed")))
        how = "natively" if not runner else "".join(("under ", runner[0]))
        self._check_run([*runner, str(output)], how)

    def _check_compile(self, proc: subprocess.CompletedProcess[str]) -> None:
        """Check the compilation itself against what the file expects."""
        if self.expectations.compiles:
            assert proc.returncode == 0, describe(proc)
        else:
            assert proc.returncode != 0, "".join(("compilation was expected to fail\n",
                                                  describe(proc)))
        if self.expectations.no_diagnostics:
            assert proc.stderr.strip() == "", describe(proc)
        for number, name in self.expectations.expected_diags:
            marker = "".join(("[PL4G-", str(number), "]"))
            assert marker in proc.stderr, "".join((
                "expected diagnostic ", str(number), " (", name, ")\n", describe(proc)))

    def _check_run(self, command: Sequence[str], how: str) -> None:
        """Run the generated binary and check the status it exits with."""
        proc = subprocess.run(list(command), capture_output=True, timeout=60)
        assert proc.returncode == self.expectations.exit_status, "".join((
            "running ", how, ": ", describe(proc)))

    def repr_failure(self, excinfo: object, style: object = None) -> str:  # noqa: ANN001
        """Report a failure without a Python traceback, which adds nothing here."""
        return str(getattr(excinfo, "value", excinfo))

    def reportinfo(self) -> tuple[Path, int, str]:
        """Where the failure is, for the summary line."""
        return Path(str(self.path)), 0, self.name


def pytest_collect_file(parent: pytest.Collector, file_path: Path):  # noqa: ANN201
    """Collect every ``.pl4g`` file under ``tests/language`` as a test."""
    if file_path.suffix != ".pl4g" or "language" not in file_path.parts:
        return None
    # A module belongs to the test that imports it and is not one itself.
    if parse_directives(file_path.read_text(encoding="utf-8")).is_module:
        return None
    return PL4GFile.from_parent(parent, path=file_path)


@pytest.fixture(scope="session")
def root() -> Path:
    """The repository root."""
    return ROOT


@pytest.fixture
def compile_source(tmp_path: Path):  # noqa: ANN201
    """Compile a source string and return the process result and output path."""
    def run(text: str, *extra: str, name: str = "t.pl4g"):  # noqa: ANN202
        source = tmp_path / name
        source.write_text(text, encoding="utf-8")
        output = tmp_path / "out"
        proc = run_compiler(["-o", str(output), *extra, str(source)])
        return proc, output
    return run
