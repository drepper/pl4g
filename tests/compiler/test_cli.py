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

GOOD = """@[startup]
fn main() \N{RIGHTWARDS ARROW} i32:
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
    source.write_text("@[startup]\nfn main() \N{RIGHTWARDS ARROW} i32:\n    return 0\n",
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
    """The log is empty for now, but its format is fixed before it has content."""
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"),
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == ExitCode.SUCCESS
    document = json.loads(log.read_text(encoding="utf-8"))
    assert document["format_version"] == 1
    assert document["decisions"] == []


def test_time_report(source: Path, tmp_path: Path) -> None:
    """The stage timings go to standard error, not into the output."""
    proc = run_compiler(["-o", str(tmp_path / "out"), "--time-report", str(source)])
    assert proc.returncode == ExitCode.SUCCESS
    assert "stage timings" in proc.stderr
    assert "image generation" in proc.stderr
