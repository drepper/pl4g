"""`⎕environ`: what a program carries for it, and what it does not.

The behaviour of the name is a language test; what is here is the bargain it
strikes with the image -- a program that never names it carries no table, no
builder and nothing asked of the system -- and the shape of what is generated
for one that does.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler, runner_for

#: What the variable and the function that fills it are called in the image.
VARIABLE = "__pl4g_environ"
BUILDER = "__pl4g_environ_make"

#: A program that reads the environment, and one that does not.
READS = """\
@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    if \N{APL FUNCTIONAL SYMBOL QUAD}environ\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_NAME"\N{RIGHT DOUBLE PARENTHESIS} ?? "nothing" = "said": 0u6 else: 1u6
"""

SILENT = """\
@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    0u6
"""


def _built(source: str, where: Path, triple: str) -> Path:
    """Compile *source* for *triple* and answer where the program landed."""
    written = where / "one.pl4g"
    written.write_text(source, encoding="utf-8")
    out = where / "one"
    proc = run_compiler(["-o", str(out), "".join(("--target=", triple)),
                         str(written)])
    assert proc.returncode == 0, describe(proc)
    return out


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_names_it_carries_it(triple: str, tmp_path: Path) -> None:
    """The variable and the generated builder are both in the image."""
    image = elfcheck.parse(_built(READS, tmp_path, triple).read_bytes())
    assert image.symbol(VARIABLE) is not None
    assert image.symbol(BUILDER) is not None


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_that_does_not_carries_none_of_it(triple: str,
                                                    tmp_path: Path) -> None:
    """Neither of them, and no table runtime either.

    The variable is made on first ask, so a program that never asks has none --
    which is what makes the environment cost nothing to a program that does not
    read it, with nothing to look for in what the program wrote.
    """
    image = elfcheck.parse(_built(SILENT, tmp_path, triple).read_bytes())
    assert image.symbol(VARIABLE) is None
    assert image.symbol(BUILDER) is None
    assert not [one for one in image.symbols if one.name.startswith("__pl4g_table")]


@pytest.mark.parametrize("triple", compiler_targets())
def test_what_the_process_was_started_with(triple: str, tmp_path: Path) -> None:
    """And it says what the run said, whichever architecture reads it."""
    built = _built(READS, tmp_path, triple)
    runner = runner_for(triple)
    if runner and not shutil.which(runner[0]):
        pytest.skip("".join((runner[0], " is not installed")))
    proc = subprocess.run([*runner, str(built)], capture_output=True, timeout=60,
                          env={"PL4G_TEST_NAME": "said", "PATH": "/usr/bin"})
    assert proc.returncode == 0, describe(proc)


def test_a_build_function_reads_the_compiler_s_own(tmp_path: Path) -> None:
    """The same name while the compiler is working something out.

    A build function is run by the compiler, so the process whose environment
    it reads is the compiler's -- which is the one a person typing the build
    command has in front of them.
    """
    (tmp_path / "one.pl4g").write_text(
        "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    0u6\n", encoding="utf-8")
    (tmp_path / "build.pl4g").write_text("""\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} \N{APL FUNCTIONAL SYMBOL QUAD}environ\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_OUT"\N{RIGHT DOUBLE PARENTHESIS} ?? "elsewhere"
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", encoding="utf-8")
    proc = run_compiler([], cwd=tmp_path, env={"PL4G_TEST_OUT": "here"})
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "here" / "one").is_file(), describe(proc)


def test_the_build_object_reads_the_same_dictionary(tmp_path: Path) -> None:
    """`std.Build.env` is that same environment, under the name the field has."""
    (tmp_path / "one.pl4g").write_text(
        "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u6:\n    0u6\n", encoding="utf-8")
    (tmp_path / "build.pl4g").write_text("""\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    let said: str = \N{APL FUNCTIONAL SYMBOL QUAD}environ\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_OUT"\N{RIGHT DOUBLE PARENTHESIS} ?? "one"
    let field: str = b\N{POSITION INDICATOR}.env\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_OUT"\N{RIGHT DOUBLE PARENTHESIS} ?? "other"
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} if said = field: said else: "disagree"
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", encoding="utf-8")
    proc = run_compiler([], cwd=tmp_path, env={"PL4G_TEST_OUT": "agreed"})
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "agreed" / "one").is_file(), describe(proc)


def test_a_build_cannot_write_it(tmp_path: Path) -> None:
    """It is read-only by its type while the compiler works it out, too."""
    (tmp_path / "build.pl4g").write_text("""\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    \N{APL FUNCTIONAL SYMBOL QUAD}environ\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_OUT"\N{RIGHT DOUBLE PARENTHESIS} \N{LEFTWARDS ARROW} "no"
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", encoding="utf-8")
    proc = run_compiler([], cwd=tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-4598]" in proc.stderr, describe(proc)
