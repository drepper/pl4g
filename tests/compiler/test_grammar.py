"""The tree-sitter grammar, checked against the compiler it describes.

A grammar in a second language is a second statement of what a program is, and
two statements of one thing can disagree.  What keeps them together is this: for
every program in the language test suite, the grammar and the compiler must
agree on whether it parses.  A syntax the language gains and the grammar does
not is then a failing test rather than something noticed months later in an
editor.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import run_compiler

ROOT = Path(__file__).resolve().parent.parent.parent
GRAMMAR = ROOT / "tree-sitter-pl4g"
TREE_SITTER = "tree-sitter"

pytestmark = pytest.mark.skipif(not shutil.which(TREE_SITTER),
                                reason="the tree-sitter command is not installed")


def sources() -> list[Path]:
    """Every program in the language test suite and the examples."""
    found = sorted((ROOT / "tests" / "language").glob("*/*.pl4g"))
    found += sorted((ROOT / "examples").glob("*/*.pl4g"))
    return found


#: What `tree-sitter` says when it could not get the grammar loaded at all,
#: which is a different answer from "this program does not parse" and must not
#: be read as one.  It builds the grammar into a shared library on first use and
#: again whenever the generated parser is newer; where many of these run at once
#: -- a suite over many cores -- one of them can find that library part way
#: through being written.
_NOT_LOADED = "Failed to load language"


def grammar_parses(path: Path) -> bool:
    """Whether the grammar reads *path* without an error node.

    A run that could not load the grammar is asked again rather than counted as
    a refusal: what it reports is nothing about the program, and reporting it as
    a disagreement between the grammar and the compiler would send a reader
    looking at a file that is perfectly all right.
    """
    for last in (False, True):
        proc = subprocess.run([TREE_SITTER, "parse", "--quiet", str(path)],
                              cwd=GRAMMAR, capture_output=True, text=True,
                              timeout=60)
        if proc.returncode == 0 or _NOT_LOADED not in proc.stderr:
            return proc.returncode == 0
        if last:
            raise AssertionError("".join((
                "tree-sitter could not load the grammar: ", proc.stderr.strip())))
    raise AssertionError("unreachable")


def compiler_parses(path: Path, tmp_path: Path) -> bool:
    """Whether the compiler gets past parsing *path*.

    The syntax tree is asked for rather than a binary: a program may be
    perfectly well formed and still not compile, and it is the syntax the two
    have to agree about.
    """
    proc = run_compiler(["-o", str(tmp_path / "out"), "--emit=ast", str(path)])
    if proc.returncode == 0:
        return True
    # Anything in the lexical or syntactic blocks is the parser refusing; a
    # number outside them means the program parsed and something later objected.
    for number in _diagnostic_numbers(proc.stderr):
        if 2000 <= number < 4000:
            return False
    return True


def _diagnostic_numbers(text: str) -> list[int]:
    """Every diagnostic number a run reported."""
    found: list[int] = []
    for piece in text.split("[PL4G-")[1:]:
        digits = piece.split("]")[0]
        if digits.isdigit():
            found.append(int(digits))
    return found


@pytest.mark.parametrize("path", sources(), ids=lambda p: p.parent.name)
def test_the_grammar_and_the_compiler_agree(path: Path, tmp_path: Path) -> None:
    """One says a program parses exactly when the other does.

    Both directions matter.  A program the grammar refuses and the compiler
    accepts is a syntax the grammar has not been told about; one the grammar
    accepts and the compiler refuses is a grammar that is too loose, which shows
    up as an editor offering to complete something that cannot be written.
    """
    assert grammar_parses(path) == compiler_parses(path, tmp_path), \
        "".join(("the grammar says ", str(grammar_parses(path)),
                 " and the compiler says ", str(compiler_parses(path, tmp_path)),
                 " about ", path.name))


def test_the_corpus_of_the_grammar_passes() -> None:
    """The grammar's own tests, run from here so that one command runs both."""
    proc = subprocess.run([TREE_SITTER, "test"], cwd=GRAMMAR,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_generated_parser_is_current() -> None:
    """The parser checked in is the one the grammar produces.

    It is generated and committed so that anything reading the grammar needs no
    tree-sitter command; a grammar changed without regenerating would leave the
    two disagreeing with nothing to say so.
    """
    before = (GRAMMAR / "src" / "parser.c").read_bytes()
    proc = subprocess.run([TREE_SITTER, "generate"], cwd=GRAMMAR,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    after = (GRAMMAR / "src" / "parser.c").read_bytes()
    assert before == after, "run tree-sitter generate after changing grammar.js"


def test_every_highlight_query_is_valid() -> None:
    """A query naming a node the grammar does not have is silently no highlight."""
    for last in (False, True):
        proc = subprocess.run(
            [TREE_SITTER, "query", "queries/highlights.scm",
             str(ROOT / "tests" / "language" / "exit0" / "exit0.pl4g")],
            cwd=GRAMMAR, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0 or _NOT_LOADED not in proc.stderr or last:
            break
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "@comment" not in proc.stderr
