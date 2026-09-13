"""What a program lets out, and to whom.

There are two questions and they are not the same one.  `@[visible]` decides
whether the finished image offers a symbol at all; `@[export]` decides whether a
file importing this module may name the definition.  A definition may be offered
to the outside without its module letting it in, and a module may let something
in that no binary ever names.

Within the image, `@[visible]` then decides two further things: how widely the
symbol is bound and how far it is visible.  Those are different questions too,
and the second is the one that still says so if something later makes the symbol
global.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler, runner_for

ARROW = "\N{RIGHTWARDS ARROW}"

SOURCE = """@[visible]
let shared: u8 = 7u8

let private: mut u8 = 8u8

@[visible]
fn reachable() \N{RIGHTWARDS ARROW} u8:
    1u8

\N{REFERENCE MARK} Not exported, but the entry point calls it, so it is in the image
\N{REFERENCE MARK} and this says what a name that is kept in looks like.
@[constructor]
fn prepare() \N{RIGHTWARDS ARROW} void:
    private \N{LEFTWARDS ARROW} 8u8

\N{REFERENCE MARK} Neither exported nor reached from anywhere, so nothing can call it.
fn unreached() \N{RIGHTWARDS ARROW} u8:
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
def test_what_is_visible_is_bound_globally_and_visible(image: Built, name: str) -> None:
    """A variable and a function alike: the attribute applies to both."""
    symbol = image.image.symbol(name)
    assert symbol is not None, "".join((name, " is not in the symbol table"))
    assert symbol.binding == STB_GLOBAL
    assert image.image.visibility_of(symbol) == STV_DEFAULT


@pytest.mark.parametrize("name", ["private", "prepare()void", "main()u8"])
def test_what_is_not_exported_is_kept_in(image: Built, name: str) -> None:
    """Twice over: bound locally, and marked as not visible."""
    symbol = image.image.symbol(name)
    assert symbol is not None, "".join((name, " is not in the symbol table"))
    assert symbol.binding == STB_LOCAL
    assert image.image.visibility_of(symbol) == STV_HIDDEN


def test_what_nothing_reaches_is_not_there_at_all(image: Built) -> None:
    """Not exported and called from nowhere means callable from nowhere.

    Compilation covers the whole program, so this is not a guess about what the
    program might do later: nothing can ever reach the function, and bytes no
    program can run do not belong in the image.
    """
    assert image.image.symbol("unreached()u8") is None, \
        "a function nothing can reach is still in the image"


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


def test_visible_is_the_only_thing_that_changes_it(compile_source) -> None:  # noqa: ANN001
    """Without the attribute a definition is kept in, whatever else it says."""
    proc, output = compile_source("".join((
        "let g: mut u8 = 0u8\n\n",
        "@[align(16), constructor]\nfn helper() ", ARROW, " void:\n",
        "    g \N{LEFTWARDS ARROW} 1u8\n\n",
        "@[startup]\nfn main() ", ARROW, " u8:\n    1u8\n")))
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    symbol = parsed.symbol("helper()void")
    assert symbol is not None and symbol.binding == STB_LOCAL


# -- the two questions are not one ----------------------------------------------

EITHER = """@[export]
let lent: u8 = 1u8

@[visible]
let offered: u8 = 2u8

@[export, visible]
let both: u8 = 3u8

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    lent
"""


def test_exporting_a_definition_does_not_offer_its_symbol(compile_source) -> None:  # noqa: ANN001
    """`@[export]` says a file importing this module may name it, which is
    nothing to do with what the finished image offers."""
    proc, output = compile_source(EITHER)
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    lent = parsed.symbol("lent")
    assert lent is not None, "it was dropped, although the program reads it"
    assert lent.binding == STB_LOCAL
    assert parsed.visibility_of(lent) == STV_HIDDEN


def test_offering_a_symbol_does_not_export_the_definition(tmp_path) -> None:  # noqa: ANN001
    """And the other way round: a module that only makes a symbol visible has
    not let anything in."""
    (tmp_path / "lib.pl4g").write_text("@[visible]\nlet v: u8 = 1u8\n",
                                       encoding="utf-8")
    source = tmp_path / "main.pl4g"
    source.write_text("".join((
        'let l := import("lib")\n\n@[startup]\nfn main() ', ARROW,
        " u8:\n    l.v\n")), encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(source)])
    assert proc.returncode != 0
    assert "[PL4G-4104]" in proc.stderr, proc.stderr


def test_both_may_be_said_of_one_definition(compile_source) -> None:  # noqa: ANN001
    """They are independent, so saying both says both."""
    proc, output = compile_source(EITHER)
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    both = parsed.symbol("both")
    assert both is not None and both.binding == STB_GLOBAL
    assert parsed.visibility_of(both) == STV_DEFAULT


def test_a_definition_a_module_only_lends_is_dropped_where_nothing_takes_it(
        compile_source) -> None:  # noqa: ANN001
    """Which is the whole point of separating the two.

    Within one program, what a module offers and nothing imports is something
    nothing reaches, so it is not in the image.  Before the two were told apart
    a library module kept everything.
    """
    proc, output = compile_source(EITHER.replace("    lent\n", "    3u8\n"))
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    assert parsed.symbol("lent") is None, "a definition nothing reaches was kept"
    assert parsed.symbol("offered") is not None, \
        "a definition the image offers was dropped"

