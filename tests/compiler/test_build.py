"""The build function: a program that says how it is built.

A function marked `@[build]` is run by the compiler rather than compiled into
anything, and what it leaves in the object it was handed is what gets built.  So
every test here is a small project in a directory of its own, built by running
the compiler in it, and what is checked is what came out.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import describe, limited, run_compiler

#: A program to build, which answers with the status the tests look for.
PROGRAM = """\
\N{REFERENCE MARK} A program that exits with the status in its name.
@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    {status}u6
"""


def _project(where: Path, build: str, **programs: int) -> None:
    """Write a build file and the programs it names."""
    (where / "build.pl4g").write_text(build, encoding="utf-8")
    for name, status in programs.items():
        (where / "".join((name, ".pl4g"))).write_text(
            PROGRAM.format(status=status), encoding="utf-8")


def _build(where: Path, *arguments: str,
           env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the compiler in *where*, the way somebody in that directory would."""
    return run_compiler(list(arguments), cwd=where, env=env)


def test_a_build_file_names_what_to_build(tmp_path: Path) -> None:
    """The whole of it: no source on the command line, and two programs out.

    What the command line says is nothing at all; what says which files to
    compile, what to call them and where to put them is the build function.
    """
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

\N{REFERENCE MARK}\N{REFERENCE MARK} What this project builds.
@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} "bin"
    std.add_executable(b, "first", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
    std.add_executable(b, "second", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"two.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=3, two=4)
    proc = _build(tmp_path)
    assert proc.returncode == 0, describe(proc)
    for name, status in (("first", 3), ("second", 4)):
        built = tmp_path / "bin" / name
        assert built.is_file(), describe(proc)
        ran = subprocess.run([*limited(), str(built)], capture_output=True,
                             timeout=60)
        assert ran.returncode == status


def test_the_build_file_may_be_named_on_the_command_line(tmp_path: Path) -> None:
    """A file holding a build function is one whatever it is called.

    Which is the other way in: `build.pl4g` is what is looked for when nothing
    is named, and a named file that holds one is a build file all the same.
    """
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    std.add_executable(b, "only", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=5)
    (tmp_path / "make-it.pl4g").write_text(
        (tmp_path / "build.pl4g").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "build.pl4g").unlink()
    proc = _build(tmp_path, "make-it.pl4g")
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "only").is_file()


def test_the_build_function_may_work_things_out(tmp_path: Path) -> None:
    """A loop, a name joined together, and a question asked of the command line.

    Which is what makes this a function rather than a table: what to build is
    worked out, and the compiler works it out.
    """
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} std.option(b, "out", "out")
    if std.option_flag(b, "small", false):
        b\N{POSITION INDICATOR}.stack_size \N{LEFTWARDS ARROW} 65536u64
    foreach name := \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one", "two"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:
        std.add_executable(b, name, \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}name \N{DOUBLE PLUS}".pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=1, two=2)
    proc = _build(tmp_path)
    assert proc.returncode == 0, describe(proc)
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["one", "two"]
    # And the command line reaches it: another directory, and a smaller stack.
    proc = _build(tmp_path, "-Dout=elsewhere", "-Dsmall")
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "elsewhere" / "one").is_file()
    assert _stack_of(tmp_path / "elsewhere" / "one") == 65536
    assert _stack_of(tmp_path / "out" / "one") == 1 << 20


def _stack_of(path: Path) -> int:
    """How much stack the image asks for, which is where a setting shows."""
    import elfcheck
    image = elfcheck.parse(path.read_bytes())
    stack = next(s for s in image.segments if s.p_type == elfcheck.PT_GNU_STACK)
    return stack.p_memsz


def test_a_command_line_with_no_build_file_says_so(tmp_path: Path) -> None:
    """The one that used to say there was no input, said more precisely."""
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-1018]" in proc.stderr, describe(proc)
    assert "build.pl4g" in proc.stderr


def test_what_the_compiler_cannot_work_out_is_refused(tmp_path: Path) -> None:
    """With the construct named, since what to do about it depends on which."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

fn narrowed(n: u64) \N{RIGHTWARDS ARROW} u8:
    match \N{APL FUNCTIONAL SYMBOL QUAD}narrow(n, \N{TOP LEFT CORNER}u8\N{TOP RIGHT CORNER}):
        u8(v): v
        \N{UP TACK}: 0u8

@[build]
fn build(b: &mut std.Build):
    if narrowed(3u64) = 3u8:
        std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=0)
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-7000]" in proc.stderr, describe(proc)
    assert "a match" in proc.stderr


def test_a_build_function_that_does_not_finish_is_given_up_on(
        tmp_path: Path) -> None:
    """A compiler that hung on a loop with no end would say nothing at all."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    let n: mut u64 = 0u64
    while n < 10u64:
        std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=0)
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-7001]" in proc.stderr, describe(proc)


def test_a_build_that_asks_for_nothing_says_so(tmp_path: Path) -> None:
    """A build file that builds nothing is one somebody is still writing."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} "out"
""")
    proc = _build(tmp_path)
    assert "[PL4G-7005]" in proc.stderr, describe(proc)


def test_only_one_function_may_be_the_build_function(tmp_path: Path) -> None:
    """Two descriptions of one build, with nothing to say which is meant."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn one(b: &mut std.Build):
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})

@[build]
fn two(b: &mut std.Build):
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=0)
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-7002]" in proc.stderr and "[PL4G-7003]" in proc.stderr, \
        describe(proc)


def test_the_build_function_has_one_shape(tmp_path: Path) -> None:
    """It is handed the object and answers with nothing."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(n: u8) \N{RIGHTWARDS ARROW} u8:
    n
""")
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-7004]" in proc.stderr, describe(proc)


def test_what_the_compiler_provides_is_not_in_a_program(tmp_path: Path) -> None:
    """A program calling one would be calling something that is not there."""
    source = tmp_path / "wrong.pl4g"
    source.write_text("""\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    let b: mut std.Build = std.Build(.output_dir \N{LEFTWARDS ARROW} "", .target \N{LEFTWARDS ARROW} "",
                                     .opt_level \N{LEFTWARDS ARROW} 0u8, .mclevel \N{LEFTWARDS ARROW} "",
                                     .stack_size \N{LEFTWARDS ARROW} 0u64, .guard_size \N{LEFTWARDS ARROW} 0u64)
    std.add_executable(&mut b, "x", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"x.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
    0u6
""", encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(source)])
    assert proc.returncode != 0
    assert "[PL4G-7006]" in proc.stderr, describe(proc)


def test_an_executable_needs_a_source(tmp_path: Path) -> None:
    """A run of sources that is empty is a mistake rather than a program."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    let none: str\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in \N{APL FUNCTIONAL SYMBOL QUAD}static
    std.add_executable(b, "empty", none)
""")
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-7007]" in proc.stderr, describe(proc)


def test_the_build_file_is_not_itself_compiled(tmp_path: Path) -> None:
    """It describes a build; there is nothing in it to run.

    So it needs no startup function, and nothing of it is written anywhere.
    """
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=0)
    proc = _build(tmp_path)
    assert proc.returncode == 0, describe(proc)
    assert sorted(p.name for p in tmp_path.iterdir()) == \
        ["build.pl4g", "one", "one.pl4g"]


def test_a_build_reads_the_environment(tmp_path: Path) -> None:
    """`std.Build.env` is what the compiler was run with, as a dictionary.

    The same name a running program reads its own under, holding the same kind
    of thing, so that a build file asking where to write and a program asking
    where its data is ask the one question the one way.
    """
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} b\N{POSITION INDICATOR}.env\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_OUT"\N{RIGHT DOUBLE PARENTHESIS} ?? "elsewhere"
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=5)
    proc = _build(tmp_path, env={"PL4G_TEST_OUT": "here"})
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "here" / "one").is_file(), describe(proc)


def test_a_build_reads_a_name_nothing_set(tmp_path: Path) -> None:
    """What is not there is what `??` answers with, and using it is refused.

    A lookup that found nothing is a value nothing but `??` may read, so a
    build file that forgets to say what to do without it is told where.
    """
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.output_dir \N{LEFTWARDS ARROW} b\N{POSITION INDICATOR}.env\N{LEFT DOUBLE PARENTHESIS}"PL4G_NOTHING_SETS_THIS"\N{RIGHT DOUBLE PARENTHESIS} ?? "fallback"
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=6)
    proc = _build(tmp_path)
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "fallback" / "one").is_file(), describe(proc)


def test_a_build_counts_what_it_was_given(tmp_path: Path) -> None:
    """`#` answers how many there are, as it does of anything else."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    if #b\N{POSITION INDICATOR}.env > 0:
        std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=7)
    proc = _build(tmp_path)
    assert proc.returncode == 0, describe(proc)
    assert (tmp_path / "one").is_file(), describe(proc)


def test_a_build_cannot_write_the_environment(tmp_path: Path) -> None:
    """It is read-only by its type, at build time as at run time."""
    _project(tmp_path, """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[build]
fn build(b: &mut std.Build):
    b\N{POSITION INDICATOR}.env\N{LEFT DOUBLE PARENTHESIS}"PL4G_TEST_OUT"\N{RIGHT DOUBLE PARENTHESIS} \N{LEFTWARDS ARROW} "no"
    std.add_executable(b, "one", \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"one.pl4g"\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})
""", one=8)
    proc = _build(tmp_path)
    assert proc.returncode != 0
    assert "[PL4G-4598]" in proc.stderr, describe(proc)
