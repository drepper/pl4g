"""The program that shows a source with the compiler's decisions in it."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_compiler

ROOT = Path(__file__).resolve().parent.parent.parent
VIEWER = ROOT / "bin" / "pl4g-decisions"

SOURCE = """\N{REFERENCE MARK} A program with things to decide about.
let used: u8 = 7u8

let only_by_dropped: u8 = 9u8

fn unreached() \N{RIGHTWARDS ARROW} u8:
    only_by_dropped

@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    @[ignore(4006)]
    let unread: u8 = used
    used
"""


@pytest.fixture
def compiled(tmp_path: Path) -> tuple[Path, Path]:
    """Compile the program above and return its source and decision log."""
    source = tmp_path / "show.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    log = tmp_path / "decisions.json"
    proc = run_compiler(["-o", str(tmp_path / "out"), "-O1",
                         "".join(("--decision-log=", str(log))), str(source)])
    assert proc.returncode == 0, proc.stderr
    return source, log


def view(log: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the viewer over *log*."""
    return subprocess.run([sys.executable, str(VIEWER), str(log), *args],
                          capture_output=True, text=True, timeout=120,
                          cwd=str(cwd) if cwd is not None else None)


def test_each_decision_stands_under_the_line_it_is_about(compiled) -> None:  # noqa: ANN001
    """Putting it back where it belongs is the whole point of the program."""
    _, log = compiled
    proc = view(log)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    for source_line, subject in (("fn unreached()", "unreached"),
                                 ("let only_by_dropped", "only_by_dropped"),
                                 ("let unread", "unread")):
        index = next(i for i, line in enumerate(lines) if source_line in line)
        assert subject in lines[index + 1], \
            "".join((subject, " does not follow the line it is about"))


def test_the_whole_source_is_shown_not_only_the_lines_with_records(compiled) -> None:  # noqa: ANN001
    """A record without the code around it is what the log already was."""
    _, log = compiled
    out = view(log).stdout
    assert "A program with things to decide about" in out, "the comment is missing"
    assert "@[startup]" in out, "a line with no decision on it is missing"


def test_a_pattern_chooses_which_files_are_shown(compiled) -> None:  # noqa: ANN001
    """The filter matches the path as the log states it or the name alone."""
    _, log = compiled
    assert "unreached" in view(log, "show.pl4g").stdout
    assert "unreached" in view(log, "*.pl4g").stdout
    assert "unreached" not in view(log, "other.pl4g").stdout


def test_a_pattern_matching_nothing_says_so(compiled) -> None:  # noqa: ANN001
    """Silence would read as "nothing was decided", which is a different fact."""
    _, log = compiled
    proc = view(log, "nosuch.pl4g")
    assert proc.returncode == 0
    assert "no decisions" in proc.stderr


def test_the_records_come_out_in_the_order_of_the_file(compiled) -> None:  # noqa: ANN001
    """The compiler records them in the order of its passes, which is not the
    order anyone reads a file in."""
    _, log = compiled
    out = view(log, "--only-decisions").stdout
    numbers = [int(line.split(":")[1]) for line in out.splitlines() if ":" in line]
    assert numbers == sorted(numbers), out


# -- finding the sources --------------------------------------------------------

def test_the_source_is_found_from_another_directory(tmp_path: Path) -> None:
    """The log records where the compiler ran, so a path in it that is relative
    can be resolved from anywhere -- which is what makes the log portable."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "show.pl4g").write_text(SOURCE, encoding="utf-8")
    log = work / "decisions.json"
    proc = subprocess.run(
        [sys.executable, "-m", "pypl4g", "-o", str(work / "out"), "-O1",
         "--decision-log=decisions.json", "show.pl4g"],
        cwd=str(work), capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT)})
    assert proc.returncode == 0, proc.stderr
    document = json.loads(log.read_text(encoding="utf-8"))
    assert document["directory"] == work.as_posix()
    assert document["inputs"] == ["show.pl4g"], "the path was not kept as given"

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shown = view(log, cwd=elsewhere)
    assert shown.returncode == 0, shown.stderr
    assert "A program with things to decide about" in shown.stdout, \
        "the source was not found from another directory"


def test_a_log_of_the_older_format_is_still_read(tmp_path: Path) -> None:
    """One written before the directory was recorded resolves against itself."""
    (tmp_path / "show.pl4g").write_text(SOURCE, encoding="utf-8")
    log = tmp_path / "old.json"
    log.write_text(json.dumps({
        "format_version": 1,
        "compiler": "pypl4g 0.1",
        "inputs": ["show.pl4g"],
        "decisions": [{"kind": "drop-function", "subject": "unreached",
                       "reason": "nothing reaches it",
                       "where": {"file": "show.pl4g", "line": 6, "column": 1}}],
    }), encoding="utf-8")
    proc = view(log)
    assert proc.returncode == 0, proc.stderr
    assert "unreached" in proc.stdout


def test_a_format_it_does_not_know_is_refused(tmp_path: Path) -> None:
    """Guessing at a format it has not been told about would show nonsense."""
    log = tmp_path / "future.json"
    log.write_text(json.dumps({"format_version": 99, "decisions": []}),
                   encoding="utf-8")
    proc = view(log)
    assert proc.returncode != 0
    assert "format version" in proc.stderr


# -- highlighting ---------------------------------------------------------------

def test_a_pipe_gets_no_escape_sequences(compiled) -> None:  # noqa: ANN001
    """Output that is not a terminal is read by something, and something reading
    it should not have to strip colour out of it."""
    _, log = compiled
    assert "\033[" not in view(log).stdout


@pytest.mark.skipif(shutil.which("cc") is None, reason="no compiler for the grammar")
def test_colour_can_be_asked_for_and_comes_from_the_grammar(compiled) -> None:  # noqa: ANN001
    """The same queries an editor would use drive this, so a syntax the grammar
    learns is a syntax this colours without being told twice."""
    pytest.importorskip("tree_sitter")
    _, log = compiled
    out = view(log, "--color=always").stdout
    assert "\033[" in out, "asking for colour got none"
    # The keyword style, on the word the grammar calls a keyword.
    assert "".join(("\033[1;34m", "let", "\033[0m")) in out, \
        "the grammar did not drive the highlighting"


def test_colour_can_be_refused(compiled) -> None:  # noqa: ANN001
    """Forcing it off matters where the output is a terminal but is being read
    by something else."""
    _, log = compiled
    assert "\033[" not in view(log, "--color=never").stdout
