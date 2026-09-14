"""The command line.

Both compiler implementations accept the same options, so every way the command
line can be wrong maps onto a numbered diagnostic rather than onto text of some
library's choosing.
"""

import json
from pathlib import Path

import pytest

from conftest import run_compiler
from pypl4g.driver.options import ExitCode, load_option_table

GOOD = """@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u8:
    0
"""


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A source file that compiles."""
    path = tmp_path / "t.pl4g"
    path.write_text(GOOD, encoding="utf-8")
    return path


def test_version_and_help() -> None:
    """The informational options succeed and write to standard output."""
    for option in ("--version", "--help"):
        proc = run_compiler([option])
        assert proc.returncode == ExitCode.SUCCESS
        assert proc.stdout.strip() != ""


def test_print_targets_lists_one_triple_per_target() -> None:
    """A build system asks the compiler which targets exist, rather than guessing.

    Only canonical triples are listed, so building one binary per line does not
    build the same binary several times under its abbreviations.
    """
    from pypl4g.target.registry import canonical_triples, lookup

    proc = run_compiler(["--print-targets"])
    assert proc.returncode == ExitCode.SUCCESS
    listed = proc.stdout.split()
    assert listed == canonical_triples()
    assert listed, "no target has a backend"
    assert len(set(listed)) == len(listed)
    for triple in listed:
        assert lookup(triple) is not None


def test_help_json_round_trips() -> None:
    """The option table can be read back by a tool, which is why it is shared."""
    proc = run_compiler(["--help-json"])
    assert proc.returncode == ExitCode.SUCCESS
    table = json.loads(proc.stdout)
    assert table["source_suffix"] == ".pl4g"
    assert table == load_option_table()
    declared = {o["long"] for o in table["options"] if o["long"]}
    assert "--print-targets" in declared, "the shared option table is out of date"


@pytest.mark.parametrize(("argv", "number"), [
    ([], 1001),
    (["-o", "out"], 1002),
    (["-o", "out", "x.txt"], 1003),
    (["-o", "out", "--nonsense", "x.pl4g"], 1004),
    (["-o", "a", "-o", "b", "x.pl4g"], 1005),
    (["-o", "out", "--incremental", "x.pl4g"], 1006),
    (["-o"], 1007),
    (["-o", "out", "--emit=nonsense", "x.pl4g"], 1008),
])
def test_usage_errors_have_their_numbers(argv: list[str], number: int) -> None:
    """Every way the command line can be wrong names its diagnostic."""
    proc = run_compiler(argv)
    assert proc.returncode == ExitCode.USAGE
    assert "".join(("[PL4G-", str(number), "]")) in proc.stderr


def test_unknown_target_is_reported(source: Path, tmp_path: Path) -> None:
    """A target with no backend is named, and the known ones are listed."""
    proc = run_compiler(["-o", str(tmp_path / "out"), "--target=vax-unix", str(source)])
    assert proc.returncode == ExitCode.USAGE
    assert "[PL4G-1009]" in proc.stderr
    assert "x86_64-linux-none" in proc.stderr


def test_unreadable_input_is_reported(tmp_path: Path) -> None:
    """A source file that cannot be read is a fatal, numbered diagnostic."""
    proc = run_compiler(["-o", str(tmp_path / "out"), str(tmp_path / "absent.pl4g")])
    assert proc.returncode == ExitCode.ERRORS
    assert "[PL4G-1101]" in proc.stderr


def test_unknown_warning_name_is_reported(source: Path, tmp_path: Path) -> None:
    """-W names come from the shared catalog, so a typo is caught."""
    proc = run_compiler(["-o", str(tmp_path / "out"), "-Wno-nonsense", str(source)])
    assert proc.returncode != ExitCode.SUCCESS
    assert "[PL4G-1010]" in proc.stderr


def test_warnings_can_be_turned_off_and_made_errors(tmp_path: Path) -> None:
    """A warning is controlled by the option its catalog entry declares."""
    source = tmp_path / "t.pl4g"
    source.write_text("@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u8:\n    return 0\n",
                      encoding="utf-8")
    output = tmp_path / "out"
    plain = run_compiler(["-o", str(output), str(source)])
    assert plain.returncode == ExitCode.SUCCESS
    assert "[PL4G-5002]" in plain.stderr

    quiet = run_compiler(["-o", str(output), "-Wno-redundant-return", str(source)])
    assert quiet.returncode == ExitCode.SUCCESS
    assert "[PL4G-5002]" not in quiet.stderr

    strict = run_compiler(["-o", str(output), "-Werror", str(source)])
    assert strict.returncode == ExitCode.ERRORS
    assert "error:" in strict.stderr


def test_diagnostics_can_be_read_by_a_program(tmp_path: Path) -> None:
    """The JSON form is what makes the numbers reachable from outside."""
    source = tmp_path / "t.pl4g"
    source.write_text("fn main() \N{RIGHTWARDS ARROW} i32:\n    0\n", encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), "--diag-format=json", str(source)])
    assert proc.returncode == ExitCode.ERRORS
    reported = json.loads(proc.stderr)
    assert [d["number"] for d in reported] == [4401]
    assert reported[0]["name"] == "LANG_FUNCDEF_SPECIAL_NO_STARTUP"


def test_emit_modes_write_to_the_output_file(source: Path, tmp_path: Path) -> None:
    """Every stage writes where -o says, so nothing goes to standard output."""
    for kind, needle in (("ir", "; pl4g-ir"), ("asm", "section .text"),
                         ("tokens", "KW_FN"), ("ast", "FuncDef")):
        output = tmp_path / "".join(("out.", kind))
        proc = run_compiler(["-o", str(output), "".join(("--emit=", kind)), str(source)])
        assert proc.returncode == ExitCode.SUCCESS, proc.stderr
        assert needle in output.read_text(encoding="utf-8")


def test_decision_log_is_written(source: Path, tmp_path: Path) -> None:
    """A program that gave the compiler nothing to decide has an empty log."""
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS
    document = json.loads(log.read_text(encoding="utf-8"))
    assert document["format_version"] == 2
    assert document["decisions"] == []
    # Where the compiler ran, so that a path in the log can be found from
    # anywhere -- the arrangement DWARF uses for a compilation unit.
    assert Path(document["directory"]).is_absolute()


DROPPED = """let used: u8 = 7u8
let only_by_dropped: u8 = 9u8

fn unreached() \N{RIGHTWARDS ARROW} u8:
    only_by_dropped

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u8:
    used
"""


def test_what_is_left_out_of_the_binary_is_logged(tmp_path: Path) -> None:
    """Leaving something out is a decision, and a reader is entitled to ask.

    "I wrote that function, where is it?" has an answer; this is where it is
    kept.  It is not a warning: nothing is wrong, and a program that is meant to
    be generated will have plenty of these.
    """
    source = tmp_path / "t.pl4g"
    source.write_text(DROPPED, encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS, proc.stderr
    decisions = json.loads(log.read_text(encoding="utf-8"))["decisions"]
    by_kind = {(d["kind"], d["subject"]): d for d in decisions}
    assert ("drop-function", "unreached") in by_kind
    assert ("drop-variable", "only_by_dropped") in by_kind
    assert ("drop-variable", "used") not in by_kind, "a variable in use was logged"


def test_a_logged_decision_points_at_what_it_is_about(tmp_path: Path) -> None:
    """Being told a function went without being told which line it was on would
    leave the reader to find it."""
    source = tmp_path / "t.pl4g"
    source.write_text(DROPPED, encoding="utf-8")
    log = tmp_path / "decisions.json"
    run_compiler(["-o", str(tmp_path / "out"),
                  "".join(("--decision-log=", str(log))), str(source)])
    decisions = json.loads(log.read_text(encoding="utf-8"))["decisions"]
    dropped = next(d for d in decisions if d["subject"] == "unreached")
    assert dropped["where"]["file"] == source.as_posix()
    assert dropped["where"]["line"] == 4, dropped
    assert dropped["reason"], "a decision with no reason says only half of it"


def test_nothing_the_image_offers_is_ever_logged_as_dropped(tmp_path: Path) -> None:
    """What the image offers is reachable from outside, so it is kept.

    `@[export]` alone would not do: that says a file importing this module may
    name it, and nothing here imports it.
    """
    source = tmp_path / "t.pl4g"
    source.write_text("".join((
        "@[visible]\nlet shared: u8 = 1u8\n\n",
        "@[visible]\nfn reachable() \N{RIGHTWARDS ARROW} u8:\n    1u8\n\n",
        "@[startup, impure]\nfn main() \N{RIGHTWARDS ARROW} u8:\n    1u8\n")),
        encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS, proc.stderr
    assert json.loads(log.read_text(encoding="utf-8"))["decisions"] == []


def test_time_report(source: Path, tmp_path: Path) -> None:
    """The stage timings go to standard error, not into the output."""
    proc = run_compiler(["-o", str(tmp_path / "out"), "--time-report", str(source)])
    assert proc.returncode == ExitCode.SUCCESS
    assert "stage timings" in proc.stderr
    assert "image generation" in proc.stderr


DROPPED_LOCAL = """let g: u8 = 3u8

@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u8:
    @[ignore(4006)]
    let unread: u8 = g
    let kept: u8 = g
    kept
"""


def test_a_local_that_is_dropped_is_logged(tmp_path: Path) -> None:
    """Dropping it is an optimization, so it happens from -O1 and is logged there.

    The warning that nothing reads it and the record that it is therefore not in
    the binary are different facts: one is a possible mistake, the other is what
    became of it.
    """
    source = tmp_path / "t.pl4g"
    source.write_text(DROPPED_LOCAL, encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"), "-O1",
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS, proc.stderr
    decisions = json.loads(log.read_text(encoding="utf-8"))["decisions"]
    locals_ = [d for d in decisions if d["kind"] == "drop-local"]
    assert [d["subject"] for d in locals_] == ["unread"], decisions
    # At the name, not at the initializer and not at the indentation: a reader
    # following the log to a line wants to be put on the thing that went.
    assert (locals_[0]["where"]["line"], locals_[0]["where"]["column"]) \
        == (6, 9), locals_


def test_nothing_is_dropped_where_nothing_asked_for_it(tmp_path: Path) -> None:
    """An unoptimized build keeps what the program wrote, so it decides nothing."""
    source = tmp_path / "t.pl4g"
    source.write_text(DROPPED_LOCAL, encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS, proc.stderr
    decisions = json.loads(log.read_text(encoding="utf-8"))["decisions"]
    assert [d for d in decisions if d["kind"] == "drop-local"] == [], decisions



DROPPED_CALL = "".join((
    "fn worked_out(n: u8) \N{RIGHTWARDS ARROW} u8:\n    n + n\n\n",
    "@[startup]\nfn main() \N{RIGHTWARDS ARROW} u8:\n",
    "    _ \N{LEFTWARDS ARROW} worked_out(3u8)\n    0u8\n"))


def test_a_call_that_is_not_made_reaches_the_log(tmp_path: Path) -> None:
    """The whole way through: the attribute, the pass, and the file a reader reads.

    A reader who wrote the call and cannot find it in the output is who this is
    for, and what the entry tells them is both that it went and why -- the why
    being a property of the function they wrote, which they can change.
    """
    source = tmp_path / "t.pl4g"
    source.write_text(DROPPED_CALL, encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"), "-O1",
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS, proc.stderr
    decisions = json.loads(log.read_text(encoding="utf-8"))["decisions"]
    calls = [d for d in decisions if d["kind"] == "drop-call"]
    assert [d["subject"] for d in calls] == ["worked_out"], decisions
    # At the call, which is neither where the line begins nor where the
    # statement does: `_ \N{LEFTWARDS ARROW} ` stands before it.
    assert (calls[0]["where"]["line"], calls[0]["where"]["column"]) \
        == (6, 9), calls


def test_a_call_that_is_not_made_goes_at_every_level(tmp_path: Path) -> None:
    """`_ \N{LEFTWARDS ARROW} f()` is the program speaking, so it does not wait for a flag.

    Dropping a local nothing reads is the compiler noticing something, and waits
    for `-O1`; this is the program saying that an answer is not wanted, and what
    it says means the same thing however the compiler was asked to build it.
    """
    source = tmp_path / "t.pl4g"
    source.write_text(DROPPED_CALL, encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS, proc.stderr
    decisions = json.loads(log.read_text(encoding="utf-8"))["decisions"]
    assert [d["subject"] for d in decisions if d["kind"] == "drop-call"] \
        == ["worked_out"], decisions
