"""The editor configuration, and the one grammar it and the compiler both read.

`editors/nvim` holds no copy of anything: the parser and the queries in it are
links to `tree-sitter-pl4g`, so what an editor colours and what a diagnostic
colours come from one file.  A link is a thing that can be left pointing at a
name nothing has any more, which is what the first test here is for.

The rest are about the queries themselves: that every one of them compiles, that
every token of every program in the suite is something they colour, that every
name they capture by is one an editor knows -- and, where Neovim is installed,
that opening a program in it really does arrive at the captures the queries say.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, describe

GRAMMAR = ROOT / "tree-sitter-pl4g"
QUERIES = GRAMMAR / "queries"
PACKAGE = ROOT / "editors" / "nvim"

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
    }
    for link, target in wanted.items():
        assert link.is_symlink(), "".join((str(link), " is not a link"))
        found = (link.parent / link.readlink()).resolve()
        assert found == target.resolve(), "".join((
            str(link), " points at ", str(found), " and not at ", str(target)))
    for name in ("ftdetect/pl4g.lua", "ftplugin/pl4g.lua", "README.md"):
        assert (PACKAGE / name).is_file(), name


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
    found = sorted(QUERIES.glob("*.scm"))
    assert found, "there are no queries"
    for path in found:
        Query(language, path.read_text(encoding="utf-8"))  # type: ignore[arg-type]


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
