"""The editor configurations, and the one grammar they and the compiler read.

`editors/nvim` and `editors/zed` hold no copy of anything: the parser and the
queries in them are links to `tree-sitter-pl4g`, so what an editor colours and
what a diagnostic colours come from one file.  A link is a thing that can be left
pointing at a name nothing has any more, which is what the first test here is for.

Zed is the one that cannot read the working tree: it builds a grammar from a git
repository at a revision, which is a second statement of which grammar is current
and is checked here like every other second statement in this project.

The rest are about the queries themselves: that every one of them compiles, that
every token of every program in the suite is something they colour, that every
name they capture by is one an editor knows -- and, where Neovim is installed,
that opening a program in it really does arrive at the captures the queries say.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, describe

GRAMMAR = ROOT / "tree-sitter-pl4g"
QUERIES = GRAMMAR / "queries"
PACKAGE = ROOT / "editors" / "nvim"
ZED = ROOT / "editors" / "zed"
ZED_LANGUAGE = ZED / "languages" / "pl4g"

NEOVIM = "nvim"

def _capture_doc() -> Path | None:
    """Where the standard capture names are written down.

    Neovim's own documentation lists them one per line at the start of a line,
    which is what makes it readable as a list rather than as prose.  Where it is
    installed is the installation's business, so the editor is asked.
    """
    if not shutil.which(NEOVIM):
        return None
    proc = subprocess.run(
        [NEOVIM, "--headless", "--clean", "-c", "echo $VIMRUNTIME", "-c", "qa!"],
        capture_output=True, text=True, timeout=60)
    where = proc.stderr.strip() or proc.stdout.strip()
    if not where:
        return None
    found = Path(where) / "doc" / "treesitter.txt"
    return found if found.is_file() else None

#: What a query may capture by that is not a highlight: a fold query names what
#: folds, and there is no colour in it.
_NOT_A_HIGHLIGHT = frozenset(("@fold",))


def test_the_package_points_at_the_grammar() -> None:
    """Each link in the package resolves to the file it is meant to be.

    The parser is a build product and may not have been built, so what is
    checked there is where the link points and not that something is there.
    """
    wanted = {
        PACKAGE / "parser" / "pl4g.so": GRAMMAR / "pl4g.so",
        PACKAGE / "queries" / "pl4g" / "highlights.scm": QUERIES / "highlights.scm",
        PACKAGE / "queries" / "pl4g" / "folds.scm": QUERIES / "folds.scm",
        ZED_LANGUAGE / "highlights.scm": QUERIES / "highlights.scm",
    }
    for link, target in wanted.items():
        assert link.is_symlink(), "".join((str(link), " is not a link"))
        found = (link.parent / link.readlink()).resolve()
        assert found == target.resolve(), "".join((
            str(link), " points at ", str(found), " and not at ", str(target)))
    for name in ("ftdetect/pl4g.lua", "ftplugin/pl4g.lua", "lsp/pl4g.lua",
                 "plugin/pl4g.lua", "lua/pl4g/health.lua", "README.md"):
        assert (PACKAGE / name).is_file(), name
    for name in ("extension.toml", "Cargo.toml", "src/lib.rs", "README.md",
                 "languages/pl4g/config.toml", "languages/pl4g/outline.scm",
                 "languages/pl4g/brackets.scm", "languages/pl4g/overrides.scm"):
        assert (ZED / name).is_file(), name


def _language() -> object:
    """The built grammar, or nothing where it or the bindings are missing."""
    try:
        from tree_sitter import Language
    except ImportError:
        return None
    if not (GRAMMAR / "pl4g.so").is_file():
        return None
    import ctypes
    import warnings
    library = ctypes.cdll.LoadLibrary(str(GRAMMAR / "pl4g.so"))
    entry = library.tree_sitter_pl4g
    entry.restype = ctypes.c_void_p
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return Language(entry())


@pytest.fixture(scope="module")
def language() -> object:
    """The grammar, or a skip: none of this is about the compiler proper."""
    found = _language()
    if found is None:
        pytest.skip("the grammar is not built, or the bindings are not installed")
    return found


def test_every_query_compiles(language: object) -> None:
    """A query naming a node the grammar has not got is caught here.

    Nothing else would say so: the compiler's own highlighter swallows it -- what
    is lost there is colour and none of it is about the program -- and an editor
    reports it once, to whoever happens to open a file.
    """
    from tree_sitter import Query
    found = sorted(QUERIES.glob("*.scm")) + sorted(ZED_LANGUAGE.glob("*.scm"))
    assert found, "there are no queries"
    for path in found:
        try:
            Query(language, path.read_text(encoding="utf-8"))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 -- what it is, is what to report
            raise AssertionError("".join((path.name, ": ", str(exc)))) from exc


def _sources() -> list[Path]:
    """Every program in the suite, the examples and the modules."""
    return (sorted((ROOT / "tests" / "language").glob("*/*.pl4g"))
            + sorted((ROOT / "examples").glob("*/*.pl4g"))
            + sorted((ROOT / "modules").glob("*.pl4g")))


def test_every_token_of_every_program_is_coloured(language: object) -> None:
    """Nothing a program can be written with is left plain.

    Which is what makes a syntax added to the grammar and not to the queries a
    failing test rather than a glyph that quietly stays the colour of the page.
    A token inside an error node is not counted: the programs that hold one are
    there to be refused, and what the grammar makes of them is not a statement
    about the language.
    """
    from tree_sitter import Parser, Query, QueryCursor
    query = Query(language,  # type: ignore[arg-type]
                  (QUERIES / "highlights.scm").read_text(encoding="utf-8"))
    parser = Parser(language)  # type: ignore[arg-type]
    plain: dict[str, str] = {}
    for path in _sources():
        data = path.read_bytes()
        tree = parser.parse(data)
        coloured: set[int] = set()
        for _, captured in QueryCursor(query).matches(tree.root_node):
            for nodes in captured.values():
                for node in nodes:
                    coloured.update(range(node.start_byte, node.end_byte))
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.has_error and node.type == "ERROR":
                continue
            if node.child_count == 0:
                text = data[node.start_byte:node.end_byte].decode("utf-8", "replace")
                if text.strip() and node.start_byte not in coloured:
                    plain.setdefault(text, path.name)
                continue
            stack.extend(node.children)
    assert not plain, "".join((
        "nothing colours ", repr(plain)))


def test_every_capture_is_one_an_editor_knows() -> None:
    """The names are tree-sitter's own, which is what makes them worth using.

    An editor colours what it recognizes and says nothing about the rest, so a
    name misspelled here is a thing that simply has no colour -- and looks, to
    whoever is reading the file, exactly like a query that was never written.
    """
    doc = _capture_doc()
    if doc is None:
        pytest.skip("the list of standard capture names is not installed")
    known = set(re.findall(r"^@[a-z.]+", doc.read_text(encoding="utf-8"),
                           re.MULTILINE))
    assert len(known) > 50, "the list was not found where it was expected"
    for path in sorted(QUERIES.glob("*.scm")):
        used = set(re.findall(r"@[a-z][a-z.]*", path.read_text(encoding="utf-8")))
        strange = sorted(used - known - _NOT_A_HIGHLIGHT)
        assert not strange, "".join((path.name, " captures by ", repr(strange)))


#: The program the table below is about.  Written here rather than taken from the
#: suite so that a line number in the table stays a line of this program, and so
#: that every token the table names appears once on the line it is looked for on.
_PROGRAM = """\
fn deep(depth: u64) \N{RIGHTWARDS ARROW} u64:
    depth

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{REFERENCE MARK} a remark
    let found: mut u6 = 0u6
    foreach \N{SECTION SIGN}rows r := 0u6\N{HORIZONTAL ELLIPSIS}4u6:
        if deep(1u64) = 1u64:
            found \N{LEFTWARDS ARROW} r
            break \N{SECTION SIGN}rows
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(found, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6
"""

#: What opening that program in Neovim has to arrive at: the token, and the
#: capture that must be the last one over it -- the last being the one an editor
#: shows.  Both directions of the rule are here.  A general pattern winning where
#: a particular one should is what the wrong order in the query file looks like,
#: and each of these would say so.
_EXPECTED: tuple[tuple[int, str, str], ...] = (
    (1, "fn", "keyword.function"),
    (1, "deep", "function"),
    (1, "depth", "variable.parameter"),
    (1, "u64", "type"),
    (1, "\N{RIGHTWARDS ARROW}", "punctuation.delimiter"),
    (4, "@[", "punctuation.bracket"),
    (4, "startup", "attribute"),
    (6, "\N{REFERENCE MARK}", "comment"),
    (7, "let", "keyword"),
    (7, "mut", "keyword.modifier"),
    (7, "found", "variable"),
    (7, "0u6", "number"),
    (8, "foreach", "keyword.repeat"),
    (8, "\N{SECTION SIGN}", "punctuation.special"),
    (8, "rows", "label"),
    (8, "\N{HORIZONTAL ELLIPSIS}", "operator"),
    (9, "if", "keyword.conditional"),
    (9, "=", "operator"),
    (10, "\N{LEFTWARDS ARROW}", "operator"),
    (11, "break", "keyword.repeat"),
    (12, "\N{APL FUNCTIONAL SYMBOL QUAD}narrow", "function.builtin"),
    (12, "\N{TOP LEFT CORNER}", "punctuation.bracket"),
    (12, "??", "operator"),
)

#: What is asked of Neovim, once the file is open: whether the grammar read the
#: program at all, and then, for each token of the table, every capture over the
#: byte it begins at in the order the editor would apply them.
_PROBE = """\
local buf = vim.api.nvim_get_current_buf()
local trees = vim.treesitter.get_parser(buf):parse()
local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
local out = { table.concat({ 0, "read", tostring(not trees[1]:root():has_error()) },
                           "\\t") }
for _, one in ipairs(vim.json.decode(vim.env.PL4G_PROBE)) do
  local line, needle = one[1], one[2]
  local text = lines[line] or ""
  local at = string.find(text, needle, 1, true)
  local names = {}
  if at then
    for _, c in ipairs(vim.treesitter.get_captures_at_pos(buf, line - 1, at - 1)) do
      names[#names + 1] = c.capture
    end
  end
  out[#out + 1] = table.concat({ line, needle, table.concat(names, ",") }, "\\t")
end
io.stdout:write(table.concat(out, "\\n"), "\\n")
"""


@pytest.mark.skipif(not shutil.which(NEOVIM), reason="neovim is not installed")
def test_neovim_colours_a_program_as_the_queries_say(tmp_path: Path) -> None:
    """The whole way through, in the editor the configuration is for.

    A file is opened with nothing but this package on the runtime path -- no
    user's configuration, no plugin -- so what answers is the grammar, the
    queries and the four files beside them.  It is the one test that reads the
    type of the buffer, the parser, the queries and the order of the patterns in
    one go, and the only one that would notice if any of them stopped agreeing.
    """
    if not (GRAMMAR / "pl4g.so").is_file():
        pytest.skip("the grammar is not built")
    import json
    source = tmp_path / "probe.pl4g"
    source.write_text(_PROGRAM, encoding="utf-8")
    script = tmp_path / "probe.lua"
    script.write_text(_PROBE, encoding="utf-8")
    proc = subprocess.run(
        [NEOVIM, "--headless", "--clean",
         "--cmd", "".join(("set runtimepath+=", str(PACKAGE))),
         str(source), "-c", "".join(("luafile ", str(script))), "-c", "qa!"],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "PL4G_PROBE": json.dumps([[line, token]
                                       for line, token, _ in _EXPECTED])})
    assert proc.returncode == 0, describe(proc)
    answered = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            answered[(int(parts[0]), parts[1])] = parts[2].split(",") \
                if parts[2] else []
    assert answered, describe(proc)
    assert answered.get((0, "read")) == ["true"], "".join((
        "the grammar did not read the program, so a token of it is misspelled\n",
        describe(proc)))
    for line, token, capture in _EXPECTED:
        found = answered.get((line, token))
        assert found, "".join(("nothing was captured over ", repr(token),
                               " on line ", str(line), "\n", describe(proc)))
        assert found[-1] == capture, "".join((
            repr(token), " on line ", str(line), " came out as ", repr(found),
            " and the last of them is not ", capture))


@pytest.mark.skipif(not shutil.which(NEOVIM), reason="neovim is not installed")
def test_the_health_check_finds_nothing_wrong(tmp_path: Path) -> None:
    """`:checkhealth pl4g` is what a reader runs when nothing is coloured.

    Five things have to hold and each of them fails looking like the others, so
    the check says which.  Here it is run where all five do hold: every line of
    it has to be an answer of the good kind, and the two that matter most -- the
    grammar loads, the queries compile -- have to be there rather than merely not
    complained about.

    The colour scheme is not one of the five.  `--clean` leaves Neovim's own,
    which paints most of these groups as ordinary text, and the check says so as
    a warning: a configuration that works and looks as though it does not is
    exactly what it is there to tell a reader about.
    """
    if not (GRAMMAR / "pl4g.so").is_file():
        pytest.skip("the grammar is not built")
    source = tmp_path / "probe.pl4g"
    source.write_text(_PROGRAM, encoding="utf-8")
    report = tmp_path / "health.txt"
    proc = subprocess.run(
        [NEOVIM, "--headless", "--clean",
         "--cmd", "".join(("set runtimepath+=", str(PACKAGE))),
         str(source), "-c", "checkhealth pl4g",
         "-c", "".join(("write! ", str(report))), "-c", "qa!"],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert proc.returncode == 0, describe(proc)
    assert report.is_file(), describe(proc)
    text = report.read_text(encoding="utf-8")
    assert "ERROR" not in text, text
    for said in ("a `.pl4g` file is of type `pl4g`",
                 "the grammar loads",
                 "the highlights query compiles",
                 "the folds query compiles",
                 "coloured by the grammar"):
        assert said in text, "".join((said, " is not in:\n", text))


def _manifest() -> dict[str, object]:
    """What the Zed extension says it is."""
    return tomllib.loads((ZED / "extension.toml").read_text(encoding="utf-8"))


def _language_config() -> dict[str, object]:
    """And what it says a file of this language is."""
    return tomllib.loads(
        (ZED_LANGUAGE / "config.toml").read_text(encoding="utf-8"))


def test_the_zed_extension_describes_this_language() -> None:
    """The two files agree with each other and with the compiler.

    The suffix is the compiler's own, out of the table both implementations
    read; the grammar the language names is the grammar the extension builds; and
    the server it declares is the one the code beside it starts.
    """
    manifest = _manifest()
    config = _language_config()
    assert manifest["id"] == "pl4g"
    assert manifest["schema_version"] == 1
    grammars = manifest["grammars"]
    assert isinstance(grammars, dict) and list(grammars) == ["pl4g"], grammars
    assert config["grammar"] == "pl4g"
    assert manifest["languages"] == ["languages/pl4g"] \
        if "languages" in manifest else True
    servers = manifest["language_servers"]
    assert isinstance(servers, dict) and list(servers) == ["pl4g"], servers
    assert servers["pl4g"]["language"] == config["name"]
    # The suffix a source file has is said once, in the table both compilers
    # read, and what the editor looks for has to be that.
    table = json.loads((ROOT / "share" / "options.json").read_text(encoding="utf-8"))
    assert config["path_suffixes"] == [str(table["source_suffix"]).lstrip(".")]
    # Indentation is characters and never a tab, which is what 2103 is about.
    assert config["hard_tabs"] is False
    assert config["tab_size"] == 4


def test_the_zed_extension_writes_the_comment_the_grammar_reads() -> None:
    """What a commenting command writes has to be what a program writes.

    The grammar says a remark begins with `\N{REFERENCE MARK}`; an editor that
    wrote anything else would write a line the compiler refuses.
    """
    config = _language_config()
    assert config["line_comments"] == ["\N{REFERENCE MARK} "]
    grammar = (GRAMMAR / "grammar.js").read_text(encoding="utf-8")
    assert "'\N{REFERENCE MARK}'" in grammar, "the grammar's marker has moved"


def test_the_zed_extension_starts_the_compiler() -> None:
    """The command it runs is `pypl4g lsp`, which is the command that exists.

    Read out of the code rather than stated twice: what the extension will run is
    written there, and a word of it changed without the other is what this
    notices.
    """
    code = (ZED / "src" / "lib.rs").read_text(encoding="utf-8")
    assert '"bin/pypl4g"' in code and '"pypl4g"' in code
    assert '"lsp"' in code
    assert (ROOT / "bin" / "pypl4g").is_file()
    # And the crate it is built against, which decides which Zed can run it.
    cargo = tomllib.loads((ZED / "Cargo.toml").read_text(encoding="utf-8"))
    assert cargo["lib"]["crate-type"] == ["cdylib"], "Zed runs it as WebAssembly"
    assert "zed_extension_api" in cargo["dependencies"]


@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="not a git checkout")
def test_the_grammar_zed_fetches_is_the_grammar_this_tree_has() -> None:
    """Zed builds a grammar from a commit, so the commit has to be the right one.

    It clones the repository at the revision the manifest names and builds what
    it finds there, which is a second statement of which grammar is current -- and
    a second statement of one thing is what this project keeps honest with a test.
    `bin/pl4g-zed-rev` is what writes it; this is what notices that it was not run.

    The comparison is the *tree* of the grammar directory and not the revision
    itself, so a revision that lags the tip is perfectly all right as long as the
    grammar in it is the grammar here.
    """
    grammars = _manifest()["grammars"]
    assert isinstance(grammars, dict)
    entry = grammars["pl4g"]
    assert isinstance(entry, dict)
    assert entry["path"] == GRAMMAR.name, entry
    assert str(entry["repository"]).startswith("http"), entry
    rev = str(entry["rev"])
    assert re.fullmatch(r"[0-9a-f]{40}", rev), "".join(("not a revision: ", rev))
    here = _tree_of("HEAD")
    there = _tree_of(rev)
    if there is None:
        # Unknown here: a revision that was mistyped, or a clone that was made
        # shallow and does not go back that far.  The second is a reason to skip
        # and the first is not, so which it is gets asked.
        done = subprocess.run(["git", "rev-parse", "--is-shallow-repository"],
                              cwd=str(ROOT), capture_output=True, text=True,
                              timeout=60, check=False)
        if done.stdout.strip() == "true":
            pytest.skip("".join(("the revision ", rev[:12],
                                 " is not in this shallow clone")))
        raise AssertionError("".join((
            "editors/zed/extension.toml points at ", rev[:12],
            ", which is not a revision of this repository")))
    assert there == here, "".join((
        "editors/zed/extension.toml points at ", rev[:12],
        ", whose grammar is not this one; run bin/pl4g-zed-rev"))


def _tree_of(rev: str) -> str | None:
    """What the grammar directory is, at *rev*, or nothing where it is unknown."""
    done = subprocess.run(
        ["git", "rev-parse", "".join((rev, ":", GRAMMAR.name))],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False)
    return done.stdout.strip() if done.returncode == 0 else None


def test_the_grammar_directory_has_nothing_uncommitted() -> None:
    """Which is what makes the check above mean anything.

    A grammar changed and not committed is one Zed cannot fetch whatever the
    manifest says, so the revision being right says nothing while that is true.
    This is a warning in the shape of a test: it fails only where the two are
    actually out of step, which is where the editor would be wrong.
    """
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    done = subprocess.run(
        ["git", "status", "--porcelain", "--", GRAMMAR.name],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60, check=False)
    if done.returncode != 0:
        pytest.skip("git would not answer")
    changed = [line for line in done.stdout.splitlines() if line.strip()]
    if not changed:
        return
    rev = str(_manifest()["grammars"]["pl4g"]["rev"])
    assert _tree_of(rev) == _tree_of("HEAD"), "".join((
        "the grammar has uncommitted changes:\n", "\n".join(changed),
        "\nZed can only fetch a commit, so commit them and run bin/pl4g-zed-rev"))
