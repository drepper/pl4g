"""Modules: where they are found, how often they are read, and what they are called."""

import subprocess
import sys
from pathlib import Path

import pytest

import elfcheck
from conftest import compiler_targets, describe, run_compiler, runner_for
from pypl4g.sema.modules import (SearchPath, base_name, parse_search_path,
                                 path_hash)

ARROW = "\N{RIGHTWARDS ARROW}"


def write(directory: Path, name: str, text: str) -> Path:
    """Put a source file where a test wants it."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build(tmp_path: Path, main: str, *args: str):  # noqa: ANN201
    """Compile the file called `main.pl4g` in *tmp_path*."""
    write(tmp_path, "main.pl4g", main)
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), *args, str(tmp_path / "main.pl4g")])
    return proc, output


def ran(output: Path) -> int:
    """Run a compiled program and return its status."""
    proc = subprocess.run([str(output)], capture_output=True, timeout=60)
    assert proc.returncode >= 0, describe(proc)
    return proc.returncode


# -- where a module is found ----------------------------------------------------

def test_the_directory_of_the_importing_file_is_looked_at_first(tmp_path: Path) -> None:
    """A program and the modules beside it need no path telling the compiler so."""
    write(tmp_path, "lib/limits.pl4g", "@[export]\nlet ceiling: u8 = 42u8\n")
    proc, output = build(tmp_path, "".join((
        'let limits := import("lib/limits")\n\n@[startup, impure]\nfn main() ', ARROW,
        " u8:\n    limits.ceiling\n")))
    assert proc.returncode == 0, describe(proc)
    assert ran(output) == 42


def test_a_name_given_on_the_command_line_is_looked_at_next(tmp_path: Path) -> None:
    """Which is how a build says where the modules of a project are."""
    write(tmp_path, "elsewhere/found.pl4g", "@[export]\nlet v: u8 = 5u8\n")
    proc, output = build(tmp_path, "".join((
        'let f := import("found")\n\n@[startup, impure]\nfn main() ', ARROW,
        " u8:\n    f.v\n")), "--module-path=elsewhere")
    assert proc.returncode == 0, describe(proc)
    assert ran(output) == 5


def test_a_name_that_is_a_path_names_one_file(tmp_path: Path) -> None:
    """Nothing is searched for: an absolute name is an answer, not a question."""
    absolute = write(tmp_path, "away/exact.pl4g", "@[export]\nlet v: u8 = 9u8\n")
    proc, output = build(tmp_path, "".join((
        'let f := import("', absolute.as_posix(), '")\n\n@[startup, impure]\nfn main() ',
        ARROW, " u8:\n    f.v\n")))
    assert proc.returncode == 0, describe(proc)
    assert ran(output) == 9


def test_the_extension_is_added_where_it_is_not_written(tmp_path: Path) -> None:
    """Writing it or leaving it out names the same file."""
    write(tmp_path, "with.pl4g", "@[export]\nlet v: u8 = 3u8\n")
    for written in ("with", "with.pl4g"):
        proc, output = build(tmp_path, "".join((
            'let f := import("', written, '")\n\n@[startup, impure]\nfn main() ', ARROW,
            " u8:\n    f.v\n")))
        assert proc.returncode == 0, "".join((written, ": ", describe(proc)))
        assert ran(output) == 3


def test_a_name_found_nowhere_says_how_many_places_were_looked(tmp_path: Path) -> None:
    """Saying only that it failed would leave the reader to guess where to put it."""
    proc, _ = build(tmp_path, "".join((
        'let z := import("nowhere")\n\n@[startup, impure]\nfn main() ', ARROW,
        " u8:\n    1u8\n")))
    assert proc.returncode != 0
    assert "[PL4G-4100]" in proc.stderr, proc.stderr
    assert "places looked" in proc.stderr


def test_the_system_directories_are_skipped_for_a_name_with_a_slash() -> None:
    """A name with a slash is a path, and a path is not something to go looking
    for where the program knows nothing about."""
    search = SearchPath(given=[], system=[Path("/usr/share/pl4g")],
                        working=Path("/tmp"))
    plain = search.places("thing", Path("/src/main.pl4g"))
    pathed = search.places("sub/thing", Path("/src/main.pl4g"))
    assert any("/usr/share/pl4g" in str(p) for p in plain)
    assert not any("/usr/share/pl4g" in str(p) for p in pathed)


def test_a_relative_entry_is_tried_against_the_file_then_the_directory() -> None:
    """In that order, which is what someone building a tree of sources expects."""
    search = SearchPath(given=[Path("lib")], working=Path("/where/run"))
    places = [p.as_posix() for p in search.places("thing", Path("/src/main.pl4g"))]
    assert places.index("/src/lib/thing.pl4g") < places.index("/where/run/lib/thing.pl4g")


# -- read once ------------------------------------------------------------------

SHARED = "@[export, visible]\nlet value: u8 = 7u8\n"
MIDDLE = 'let s := import("shared")\n\n@[export]\nlet echo: u8 = 1u8\n'


def test_one_file_reached_two_ways_is_one_module(tmp_path: Path) -> None:
    """The definitions are in the image once, not once per route to them."""
    write(tmp_path, "shared.pl4g", SHARED)
    write(tmp_path, "middle.pl4g", MIDDLE)
    proc, output = build(tmp_path, "".join((
        'let direct := import("shared")\nlet mid := import("middle")\n\n',
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    direct.value\n")))
    assert proc.returncode == 0, describe(proc)
    assert ran(output) == 7
    parsed = elfcheck.parse(output.read_bytes())
    holding = [s.name for s in parsed.symbols if s.name.endswith("value")]
    assert holding == ["shared.value"], holding


def test_the_shortest_of_a_module_s_names_is_the_one_used(tmp_path: Path) -> None:
    """A program should not be made to carry the longest way of reaching something."""
    write(tmp_path, "shared.pl4g", SHARED)
    write(tmp_path, "middle.pl4g", MIDDLE)
    proc, output = build(tmp_path, "".join((
        'let mid := import("middle")\nlet direct := import("shared")\n\n',
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    direct.value\n")))
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    assert parsed.symbol("shared.value") is not None, \
        "the longer name was used although a shorter one reaches it"


def test_two_files_of_one_name_are_told_apart(tmp_path: Path) -> None:
    """Both get the hash of their path, since neither has a better claim to the
    name they share."""
    write(tmp_path, "one/util.pl4g", "@[export, visible]\nlet v: u8 = 1u8\n")
    write(tmp_path, "two/util.pl4g", "@[export, visible]\nlet v: u8 = 2u8\n")
    proc, output = build(tmp_path, "".join((
        'let a := import("one/util")\nlet b := import("two/util")\n\n',
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    a.v | b.v\n")))
    assert proc.returncode == 0, describe(proc)
    assert ran(output) == 3
    parsed = elfcheck.parse(output.read_bytes())
    named = sorted(s.name for s in parsed.symbols if s.name.endswith(".v"))
    assert len(named) == 2 and named[0] != named[1], named
    assert all(n.startswith("util-") for n in named), named


def test_the_hash_is_of_the_path_and_not_the_contents(tmp_path: Path) -> None:
    """Two files with the same contents are still two modules, and a file that
    changes is still the same module."""
    assert path_hash(Path("/a/util.pl4g")) != path_hash(Path("/b/util.pl4g"))
    assert path_hash(Path("/a/util.pl4g")) == path_hash(Path("/a/util.pl4g"))


# -- what a module lets out -----------------------------------------------------

def test_only_what_a_module_exports_can_be_named(tmp_path: Path) -> None:
    """Everything else is the module's own."""
    write(tmp_path, "lib.pl4g", "@[export]\nlet open: u8 = 1u8\n\nlet shut: u8 = 2u8\n")
    proc, _ = build(tmp_path, "".join((
        'let l := import("lib")\n\n@[startup, impure]\nfn main() ', ARROW,
        " u8:\n    l.shut\n")))
    assert proc.returncode != 0
    assert "[PL4G-4104]" in proc.stderr, proc.stderr


def test_two_modules_may_each_define_a_name(tmp_path: Path) -> None:
    """A file's top-level names are its own; neither sees the other's."""
    write(tmp_path, "a.pl4g", "@[export]\nlet counter: u8 = 1u8\n")
    write(tmp_path, "b.pl4g", "@[export]\nlet counter: u8 = 2u8\n")
    proc, output = build(tmp_path, "".join((
        'let a := import("a")\nlet b := import("b")\n\n@[startup, impure]\nfn main() ',
        ARROW, " u8:\n    a.counter | b.counter\n")))
    assert proc.returncode == 0, describe(proc)
    assert ran(output) == 3


def test_a_module_is_not_a_value(tmp_path: Path) -> None:
    """Nothing can be computed from one and nothing of it reaches the program."""
    write(tmp_path, "lib.pl4g", "@[export]\nlet v: u8 = 1u8\n")
    proc, _ = build(tmp_path, "".join((
        'let l := import("lib")\n\n@[startup, impure]\nfn main() ', ARROW,
        " u8:\n    l\n")))
    assert proc.returncode != 0
    assert "[PL4G-4105]" in proc.stderr, proc.stderr


def test_a_dot_after_something_that_is_not_a_module_is_reported(tmp_path: Path) -> None:
    """Only a name bound by an import is a module."""
    proc, _ = build(tmp_path, "".join((
        "let v: u8 = 1u8\n\n@[startup, impure]\nfn main() ", ARROW, " u8:\n    v.field\n")))
    assert proc.returncode != 0
    assert "[PL4G-4103]" in proc.stderr, proc.stderr


# -- what is refused ------------------------------------------------------------

def test_a_ring_of_imports_is_refused_and_shown(tmp_path: Path) -> None:
    """A module is read while the file importing it is being read, so a ring has
    no beginning: neither can be finished before the other."""
    write(tmp_path, "a.pl4g", 'let b := import("b")\n\n@[export]\nlet x: u8 = 1u8\n')
    write(tmp_path, "b.pl4g", 'let a := import("a")\n\n@[export]\nlet y: u8 = 2u8\n')
    proc, _ = build(tmp_path, "".join((
        'let a := import("a")\n\n@[startup, impure]\nfn main() ', ARROW, " u8:\n    a.x\n")))
    assert proc.returncode != 0
    assert "[PL4G-4102]" in proc.stderr, proc.stderr
    assert "a.pl4g -> b.pl4g -> a.pl4g" in proc.stderr, proc.stderr


def test_a_module_importing_itself_is_a_ring_of_one(tmp_path: Path) -> None:
    """The shortest ring there is, and the one easiest to write by accident."""
    write(tmp_path, "self.pl4g", 'let me := import("self")\n\n@[export]\nlet v: u8 = 1u8\n')
    proc, _ = build(tmp_path, "".join((
        'let s := import("self")\n\n@[startup, impure]\nfn main() ', ARROW, " u8:\n    s.v\n")))
    assert proc.returncode != 0
    assert "[PL4G-4102]" in proc.stderr, proc.stderr


def test_an_import_inside_a_function_is_refused(tmp_path: Path) -> None:
    """What a module holds belongs to the program, not to one function."""
    write(tmp_path, "lib.pl4g", "@[export]\nlet v: u8 = 1u8\n")
    proc, _ = build(tmp_path, "".join((
        "@[startup, impure]\nfn main() ", ARROW,
        ' u8:\n    let m := import("lib")\n    1u8\n')))
    assert proc.returncode != 0
    assert "[PL4G-3013]" in proc.stderr, proc.stderr


@pytest.mark.parametrize("written", ["let m: mut = import(\"lib\")",
                                     "let m: u8 = import(\"lib\")"])
def test_a_module_cannot_be_qualified(tmp_path: Path, written: str) -> None:
    """There is nothing to change and nothing to give a type to."""
    write(tmp_path, "lib.pl4g", "@[export]\nlet v: u8 = 1u8\n")
    proc, _ = build(tmp_path, "".join((
        written, "\n\n@[startup, impure]\nfn main() ", ARROW, " u8:\n    1u8\n")))
    assert proc.returncode != 0
    assert "[PL4G-3012]" in proc.stderr, proc.stderr


# -- on every target ------------------------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_a_program_with_modules_runs_everywhere(triple: str, tmp_path: Path) -> None:
    """Nothing about a module is a property of one architecture."""
    write(tmp_path, "lib/one.pl4g", "@[export]\nlet a: u8 = 0b1100u8\n")
    write(tmp_path, "lib/two.pl4g",
          'let one := import("one")\n\n@[export]\nlet b: u8 = 0b1010u8\n')
    write(tmp_path, "main.pl4g", "".join((
        'let one := import("lib/one")\nlet two := import("lib/two")\n\n',
        "@[startup, impure]\nfn main() ", ARROW, " u8:\n    one.a & two.b\n")))
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)),
                         str(tmp_path / "main.pl4g")])
    assert proc.returncode == 0, describe(proc)
    result = subprocess.run([*runner_for(triple), str(output)],
                            capture_output=True, timeout=60)
    assert result.returncode == 0b1000, describe(result)


# -- the pieces on their own ----------------------------------------------------

def test_a_search_path_is_split_on_colons() -> None:
    """Empty entries are dropped rather than naming the current directory."""
    assert parse_search_path("a:b::c") == [Path("a"), Path("b"), Path("c")]
    assert parse_search_path("") == []


def test_a_module_is_named_after_its_file_without_the_extension() -> None:
    """Which is why two files of one base name have to be told apart."""
    assert base_name(Path("/a/b/limits.pl4g")) == "limits"
    assert base_name(Path("/a/b/limits")) == "limits"


def test_the_source_of_a_module_is_recorded(tmp_path: Path) -> None:
    """A debugger and an incremental build both want to know what went in."""
    write(tmp_path, "lib.pl4g", "@[export]\nlet v: u8 = 1u8\n")
    proc, output = build(tmp_path, "".join((
        'let l := import("lib")\n\n@[startup, impure]\nfn main() ', ARROW,
        " u8:\n    l.v\n")))
    assert proc.returncode == 0, describe(proc)
    parsed = elfcheck.parse(output.read_bytes())
    files = [s.name for s in parsed.symbols if s.kind == 4]
    assert any(name.endswith("lib.pl4g") for name in files), files


def _unused() -> None:
    """Referenced so the import of sys is not decoration."""
    assert sys.executable
