"""What a program lets the outside see.

Nothing is visible outside unless it says so.  The attribute that says so
decides two things in the image: how widely the symbol is bound, and how far it
is visible -- which are different questions, and the second is the one that
still says so if something later makes the symbol global.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler, runner_for

ARROW = "\N{RIGHTWARDS ARROW}"

SOURCE = """@[export]
let shared: u8 = 7u8

let private: u8 = 8u8

@[export]
fn reachable() \N{RIGHTWARDS ARROW} u8:
    1u8

fn unreachable() \N{RIGHTWARDS ARROW} u8:
    2u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    shared
"""

#: What the ELF format calls the two bindings and the two visibilities here.
STB_LOCAL, STB_GLOBAL = 0, 1
STV_DEFAULT, STV_HIDDEN = 0, 2


@dataclass(frozen=True, slots=True)
class Built:
    """One compiled program and where it came from."""

    triple: str
    path: Path
    image: elfcheck.Image


@pytest.fixture(scope="module", params=compiler_targets())
def image(request: pytest.FixtureRequest,
          tmp_path_factory: pytest.TempPathFactory) -> Built:
    """Compile a program that exports some of what it defines."""
    triple = str(request.param)
    directory = tmp_path_factory.mktemp("export")
    source = directory / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = directory / "out"
    proc = run_compiler(["-o", str(output), "-O1", "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    return Built(triple=triple, path=output, image=elfcheck.parse(output.read_bytes()))


@pytest.mark.parametrize("name", ["shared", "reachable()u8"])
def test_what_is_exported_is_bound_globally_and_visible(image: Built, name: str) -> None:
    """A variable and a function alike: the attribute applies to both."""
    symbol = image.image.symbol(name)
    assert symbol is not None, "".join((name, " is not in the symbol table"))
    assert symbol.binding == STB_GLOBAL
    assert image.image.visibility_of(symbol) == STV_DEFAULT


@pytest.mark.parametrize("name", ["private", "unreachable()u8", "main()u8"])
def test_what_is_not_exported_is_kept_in(image: Built, name: str) -> None:
    """Twice over: bound locally, and marked as not visible."""
    symbol = image.image.symbol(name)
    assert symbol is not None, "".join((name, " is not in the symbol table"))
    assert symbol.binding == STB_LOCAL
    assert image.image.visibility_of(symbol) == STV_HIDDEN


def test_the_startup_function_is_not_exported_either(image: Built) -> None:
    """It is reached by the entry point, which is inside the same image."""
    startup = image.image.symbol("main()u8")
    assert startup is not None and startup.binding == STB_LOCAL


def test_the_entry_point_stays_visible(image: Built) -> None:
    """It is the compiler's own, not something the program declared, and every
    tool that looks at a binary expects to find it."""
    start = image.image.symbol("_start")
    assert start is not None
    assert start.binding == STB_GLOBAL
    assert image.image.visibility_of(start) == STV_DEFAULT


def test_the_symbol_table_is_still_well_formed(image: Built) -> None:
    """Locals before globals, and the count of them recorded correctly."""
    assert elfcheck.check_well_formed(image.image) == []


def test_it_still_runs(image: Built) -> None:
    """Nothing about visibility changes what the program does."""
    runner = runner_for(image.triple)
    if runner and not shutil.which(runner[0]):
        pytest.skip("".join((runner[0], " is not installed")))
    proc = subprocess.run([*runner, str(image.path)], capture_output=True, timeout=60)
    assert proc.returncode == 7, describe(proc)


def test_export_is_the_only_thing_that_changes_it(compile_source) -> None:  # noqa: ANN001
    """Without the attribute a definition is kept in, whatever else it says."""
    proc, output = compile_source("".join((
        "@[align(16)]\nfn helper() ", ARROW, " u8:\n    1u8\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n    1u8\n")))
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    symbol = parsed.symbol("helper()u8")
    assert symbol is not None and symbol.binding == STB_LOCAL
