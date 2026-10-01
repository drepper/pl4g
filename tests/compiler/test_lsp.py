"""The compiler as a language server, driven the way an editor drives it.

Every test here starts `pypl4g lsp` as a process and talks the protocol to it
over pipes: the framing, the handshake, the notifications and the requests.  That
is the only way to check a server -- what it is, is what it says on those two
streams -- and it is cheap, a session being a few milliseconds of compiler.

What is checked is that the answers are the compiler's own.  A diagnostic has the
compiler's number, its words and its place; an outline is what the parser found.
Nothing here asks whether the message is a good message: that is the catalog's
business and is checked where the catalog is.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import ROOT, describe

#: How long to wait for an answer.  A session is milliseconds of work; this is
#: the margin over that, and what it is really for is to fail rather than hang
#: where the server has stopped answering at all.
TIMEOUT = 60.0


class Session:
    """One server, started and talked to."""

    def __init__(self) -> None:
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "pypl4g", "lsp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(ROOT))
        self._next = 0

    def send(self, message: dict[str, Any]) -> None:
        """Write one message, framed as the protocol frames it."""
        assert self._proc.stdin is not None
        body = json.dumps(message).encode("utf-8")
        self._proc.stdin.write(b"".join((b"Content-Length: ",
                                         str(len(body)).encode("ascii"),
                                         b"\r\n\r\n", body)))
        self._proc.stdin.flush()

    def read(self) -> dict[str, Any]:
        """The next message the server sends, whatever kind it is."""
        assert self._proc.stdout is not None
        length = -1
        while True:
            line = self._proc.stdout.readline()
            assert line, "".join(("the server stopped: ", self._log()))
            if not line.strip():
                break
            name, _, value = line.decode("utf-8").partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
        assert length >= 0, "a message with no length"
        found = json.loads(self._proc.stdout.read(length).decode("utf-8"))
        assert isinstance(found, dict)
        return found

    def request(self, method: str, params: dict[str, Any]) -> Any:
        """Ask something and answer with the result, skipping what arrives first.

        A server may notify while a request is outstanding -- diagnostics arrive
        whenever they are ready -- so what is waited for is the answer with this
        request's number on it.
        """
        self._next += 1
        ident = self._next
        self.send({"jsonrpc": "2.0", "id": ident, "method": method,
                   "params": params})
        while True:
            message = self.read()
            if message.get("id") == ident:
                assert "error" not in message, message
                return message.get("result")

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Say something that wants no answer."""
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def diagnostics(self) -> tuple[str, list[dict[str, Any]]]:
        """Wait for the next set of diagnostics and answer whose they are."""
        while True:
            message = self.read()
            if message.get("method") == "textDocument/publishDiagnostics":
                params = message.get("params") or {}
                return str(params.get("uri")), list(params.get("diagnostics") or [])

    def start(self, encodings: list[str] | None = None) -> dict[str, Any]:
        """The handshake, and what the server said it can do."""
        general = {} if encodings is None else {"positionEncodings": encodings}
        answered = self.request("initialize", {
            "processId": None, "rootUri": None,
            "capabilities": {"general": general}})
        self.notify("initialized", {})
        assert isinstance(answered, dict)
        return answered

    def open(self, path: Path, text: str | None = None) -> None:
        """Tell the server about a file, with its text."""
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri_of(path), "languageId": "pl4g", "version": 1,
            "text": path.read_text(encoding="utf-8") if text is None else text}})

    def change(self, path: Path, text: str, version: int = 2) -> None:
        """Tell it the text is now this."""
        self.notify("textDocument/didChange", {
            "textDocument": {"uri": uri_of(path), "version": version},
            "contentChanges": [{"text": text}]})

    def save(self, path: Path) -> None:
        """Tell it the file has been saved."""
        self.notify("textDocument/didSave",
                    {"textDocument": {"uri": uri_of(path)}})

    def close(self) -> int:
        """End the session the way an editor ends one, and answer the status."""
        self.request("shutdown", {})
        self.notify("exit", {})
        return self._proc.wait(timeout=TIMEOUT)

    def _log(self) -> str:
        """Whatever the server put on its error stream, for a failure message."""
        assert self._proc.stderr is not None
        self._proc.kill()
        return self._proc.stderr.read().decode("utf-8", "replace")

    def kill(self) -> None:
        """Stop the server however it is doing, for a test that failed."""
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(timeout=TIMEOUT)


def uri_of(path: Path) -> str:
    """The name an editor knows a file by."""
    return "".join(("file://", str(path.resolve())))


@pytest.fixture
def session():  # noqa: ANN201
    """A server for one test, stopped however the test ends."""
    made = Session()
    try:
        yield made
    finally:
        made.kill()


#: A program with one error in it, on a line whose glyphs are three bytes each so
#: that where the error is depends on how the two ends agreed to count.
WRONG = """\
\N{REFERENCE MARK} a program with one error
@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let a: u8 = 300u8
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(a, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6
"""

#: And the same program with the value in range.
RIGHT = WRONG.replace("300u8", "200u8")


def test_the_handshake_says_what_the_server_can_do(session: Session) -> None:
    """And agrees on how a position is counted, which is the first thing to settle."""
    answered = session.start(["utf-8", "utf-16"])
    capabilities = answered["capabilities"]
    assert capabilities["positionEncoding"] == "utf-8"
    assert capabilities["documentSymbolProvider"] is True
    assert capabilities["textDocumentSync"]["openClose"] is True
    assert answered["serverInfo"]["name"] == "pypl4g"
    assert session.close() == 0


def test_a_client_that_says_nothing_gets_what_the_protocol_settled(
        session: Session) -> None:
    """Sixteen-bit units, which is the default and is nobody's choice here."""
    answered = session.start(None)
    assert answered["capabilities"]["positionEncoding"] == "utf-16"
    assert session.close() == 0


def test_an_error_is_the_compiler_s_own(session: Session, tmp_path: Path) -> None:
    """The number, the words and the place, none of them invented here.

    2006 is "integer literal 300 does not fit in type 'u8'", and where it is, is
    where the compiler's own diagnostic points: line four, at the literal.
    """
    source = tmp_path / "wrong.pl4g"
    source.write_text(WRONG, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    uri, found = session.diagnostics()
    assert uri == uri_of(source)
    assert len(found) == 1, found
    one = found[0]
    assert one["code"] == 2006
    assert one["severity"] == 1
    assert one["source"] == "pypl4g"
    assert "300" in one["message"] and "u8" in one["message"]
    # Counted in characters, which is what --emit says too: the fourth line, and
    # the literal begins sixteen characters along it.
    assert one["range"] == {"start": {"line": 3, "character": 16},
                            "end": {"line": 3, "character": 21}}
    assert session.close() == 0


def test_a_glyph_is_as_wide_as_the_editor_asked(session: Session,
                                                tmp_path: Path) -> None:
    """The same error, counted three ways.

    The line before the error holds `\N{RIGHTWARDS ARROW}`, and the line the
    error is on holds nothing but ASCII -- so what differs between the three is
    not this error but the one below, which is why the second is measured here
    too: `\N{APL FUNCTIONAL SYMBOL QUAD}narrow` is one character, three bytes and
    one sixteen-bit unit, and a program that got that wrong would put a squiggle
    in the wrong place on every line of this language.
    """
    text = "".join((
        "\N{REFERENCE MARK} two errors, one after a run of glyphs\n",
        "@[startup]\n",
        "fn main() \N{RIGHTWARDS ARROW} u6:\n",
        "    let s: str = \"a\N{POUND SIGN}\N{EURO SIGN}"
        "\N{LINEAR B SYLLABLE B008 A}\" ; let a: u8 = 300u8\n",
        "    0u6\n"))
    source = tmp_path / "wide.pl4g"
    source.write_text(text, encoding="utf-8")
    # Thirty-eight characters of that line come before the literal, and among
    # them a pound sign (two bytes), a euro sign (three) and a syllable outside
    # the basic plane (four bytes, and two sixteen-bit units).  So the same place
    # is character 38, byte 44 and unit 39, and a server that answered one number
    # to all three would be wrong for two of them.
    wanted = {"utf-32": 38, "utf-8": 44, "utf-16": 39}
    for encoding, character in wanted.items():
        session = Session()
        try:
            answered = session.start([encoding])
            assert answered["capabilities"]["positionEncoding"] == encoding
            session.open(source)
            _, found = session.diagnostics()
            about = [one for one in found if one["code"] == 2006]
            assert len(about) == 1, found
            assert about[0]["range"]["start"] == {"line": 3,
                                                 "character": character}, encoding
            assert session.close() == 0
        finally:
            session.kill()


def test_a_change_is_compiled_and_a_fixed_program_says_nothing(
        session: Session, tmp_path: Path) -> None:
    """What is compiled is the text in the buffer, saved or not.

    The file on disk is the wrong one throughout: nothing is written to it, and
    the diagnostics follow what the editor says it is holding.
    """
    source = tmp_path / "wrong.pl4g"
    source.write_text(WRONG, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    _, found = session.diagnostics()
    assert [one["code"] for one in found] == [2006]
    session.change(source, RIGHT)
    _, found = session.diagnostics()
    assert found == []
    assert source.read_text(encoding="utf-8") == WRONG
    # And back again, so that it is the text and not the order of events.
    session.change(source, WRONG, version=3)
    _, found = session.diagnostics()
    assert [one["code"] for one in found] == [2006]
    assert session.close() == 0


def test_the_back_end_speaks_when_the_file_is_saved(session: Session,
                                                    tmp_path: Path) -> None:
    """Typing gets the front end, saving gets the whole compiler.

    The program here parses and checks and is refused by the code generator
    (8501), which is the one kind of diagnostic the fast path cannot find.  So it
    is absent while the text is being changed and present when the editor says
    the file was saved -- and this is what that division is for.
    """
    source = ROOT / "tests" / "language" / "type-value-not-yet" \
        / "type-value-not-yet.pl4g"
    text = source.read_text(encoding="utf-8")
    session.start(["utf-32"])
    session.open(source, text)
    _, found = session.diagnostics()
    assert [one["code"] for one in found] == [8501], found
    session.change(source, text)
    _, found = session.diagnostics()
    assert found == [], "the front end does not reach the back end"
    session.save(source)
    _, found = session.diagnostics()
    assert [one["code"] for one in found] == [8501], found
    assert session.close() == 0


def test_closing_a_file_takes_its_diagnostics_with_it(session: Session,
                                                      tmp_path: Path) -> None:
    """An editor showing nothing of a file should show none of its complaints."""
    source = tmp_path / "wrong.pl4g"
    source.write_text(WRONG, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    _, found = session.diagnostics()
    assert found
    session.notify("textDocument/didClose",
                   {"textDocument": {"uri": uri_of(source)}})
    uri, found = session.diagnostics()
    assert uri == uri_of(source) and found == []
    assert session.close() == 0


def test_the_outline_is_what_the_parser_found(session: Session,
                                              tmp_path: Path) -> None:
    """Every definition, in the order the file defines them, with what is inside.

    A record's fields and an enumeration's values are beneath the definition they
    belong to, which is where an editor's outline shows them.
    """
    text = """\
\N{REFERENCE MARK} everything a file can define
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

type Pair = first : u8 ; second : u8

enum Colour:
    red ;
    green

let count: u8 = 3u8

\N{REFERENCE MARK}\N{REFERENCE MARK} what this one is for
@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    0u6
"""
    source = tmp_path / "outline.pl4g"
    source.write_text(text, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    session.diagnostics()
    found = session.request("textDocument/documentSymbol",
                            {"textDocument": {"uri": uri_of(source)}})
    assert isinstance(found, list)
    assert [one["name"] for one in found] == \
        ["std", "Pair", "Colour", "count", "main"]
    by_name = {one["name"]: one for one in found}
    assert by_name["std"]["kind"] == 2, "a module"
    assert by_name["Pair"]["kind"] == 23, "a record"
    assert by_name["Colour"]["kind"] == 10, "an enumeration"
    assert by_name["count"]["kind"] == 13, "a variable"
    assert by_name["main"]["kind"] == 12, "a function"
    assert [one["name"] for one in by_name["Pair"]["children"]] == \
        ["first", "second"]
    assert [one["name"] for one in by_name["Colour"]["children"]] == \
        ["red", "green"]
    # The documentation comment is what an outline shows beside the name.
    assert by_name["main"]["detail"] == "what this one is for"
    # The name is inside the definition it names, which is what tells an editor
    # what to put the cursor on.
    whole = by_name["main"]["range"]
    named = by_name["main"]["selectionRange"]
    assert whole["start"]["line"] <= named["start"]["line"]
    assert named["start"]["character"] == 3
    assert session.close() == 0


def test_a_method_the_server_has_not_got_is_refused(session: Session) -> None:
    """A request has to be answered even where the answer is no.

    An editor that asked for something and was told nothing would wait for ever,
    and one that is told there is no such method simply stops asking.
    """
    session.start(None)
    session.send({"jsonrpc": "2.0", "id": 99, "method": "textDocument/rename",
                  "params": {}})
    while True:
        message = session.read()
        if message.get("id") == 99:
            break
    assert message["error"]["code"] == -32601
    assert "rename" in message["error"]["message"]
    assert session.close() == 0


#: What is asked of Neovim: wait for the server to answer, then write down what
#: the buffer's diagnostics are.  `vim.wait` rather than a sleep, so it is as
#: quick as the compiler is and only as long as it has to be.
_IN_NEOVIM = """\
local waited = vim.wait(30000, function()
  return #vim.diagnostic.get(0) > 0
end, 50)
local names = vim.tbl_map(function(c) return c.name end,
                          vim.lsp.get_clients({ bufnr = 0 }))
io.stdout:write("clients\\t", table.concat(names, ","), "\\n")
io.stdout:write("waited\\t", tostring(waited), "\\n")
for _, d in ipairs(vim.diagnostic.get(0)) do
  io.stdout:write(table.concat({ "diagnostic", d.lnum, d.col, tostring(d.code),
                                 d.message }, "\\t"), "\\n")
end
"""


@pytest.mark.skipif(not shutil.which("nvim"), reason="neovim is not installed")
def test_neovim_starts_the_server_and_shows_what_it_says(tmp_path: Path) -> None:
    """The whole way through, in the editor this was written for.

    Neovim with nothing but this project's package on its runtime path: it works
    out that the file is of this language, reads `lsp/pl4g.lua`, starts
    `bin/pypl4g lsp`, sends the text, and puts what comes back in the buffer.  So
    what this checks is every piece at once -- the filetype, the configuration,
    the process, the protocol, and the place the compiler said the error was.
    """
    source = tmp_path / "wrong.pl4g"
    source.write_text(WRONG, encoding="utf-8")
    script = tmp_path / "probe.lua"
    script.write_text(_IN_NEOVIM, encoding="utf-8")
    package = ROOT / "editors" / "nvim"
    proc = subprocess.run(
        ["nvim", "--headless", "--clean",
         "--cmd", "".join(("set runtimepath+=", str(package))),
         str(source), "-c", "".join(("luafile ", str(script))), "-c", "qa!"],
        capture_output=True, text=True, timeout=180,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert proc.returncode == 0, describe(proc)
    said = dict(line.split("\t", 1) for line in proc.stdout.splitlines()
                if "\t" in line)
    assert said.get("clients") == "pl4g", "".join((
        "the server did not attach:\n", proc.stdout, proc.stderr))
    assert said.get("waited") == "true", "".join((
        "no diagnostic arrived:\n", proc.stdout, proc.stderr))
    found = [line.split("\t") for line in proc.stdout.splitlines()
             if line.startswith("diagnostic\t")]
    assert len(found) == 1, proc.stdout
    _, line, column, code, message = found[0]
    # The same place the compiler's own diagnostic names, and the same words.
    assert (int(line), int(column), code) == (3, 16, "2006")
    assert "300" in message


#: A program whose names are worth asking about: a parameter, a variable, a type
#: the file defines, a type another module defines, a function of this file's and
#: a function of another module's.
NAMES = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

\N{REFERENCE MARK}\N{REFERENCE MARK} two numbers that belong together
type Pair = first : u8 ; second : u8

fn total(p: Pair) \N{RIGHTWARDS ARROW} u8:
    p.first

@[startup, impure]
fn main(init: mut std.Init) \N{RIGHTWARDS ARROW} u6:
    let both: Pair = Pair(.first \N{LEFTWARDS ARROW} 1u8, .second \N{LEFTWARDS ARROW} 2u8)
    let sum: u8 = total(both)
    match std.write_sync(&mut init.io.error,
                         \N{APL FUNCTIONAL SYMBOL QUAD}bytes("hi\\n")):
        u64 \N{CURRENCY SIGN}size: \N{APL FUNCTIONAL SYMBOL QUAD}narrow(sum, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6
        \N{UP TACK}: 1u6
"""


def _at(text: str, line: int, needle: str) -> dict[str, int]:
    """The position of *needle* on *line* of *text*, counted in characters."""
    found = text.splitlines()[line]
    at = found.index(needle)
    return {"line": line, "character": at}


def test_hover_says_what_a_name_is(session: Session, tmp_path: Path) -> None:
    """The checker's answer, which is the type the compiler gave the name.

    Not a guess from the syntax: `sum` is `u8` because the checker worked out
    that it is, and `init` is the record the program started with because that is
    what the signature said.
    """
    source = tmp_path / "names.pl4g"
    source.write_text(NAMES, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    uri, found = session.diagnostics()
    assert found == [], found

    def hover(line: int, needle: str) -> str:
        answered = session.request("textDocument/hover", {
            "textDocument": {"uri": uri_of(source)},
            "position": _at(NAMES, line, needle)})
        assert isinstance(answered, dict), (line, needle, answered)
        return str(answered["contents"]["value"])

    assert "variable sum : u8" in hover(14, "sum")
    # Written the way the program wrote it: a reader of the source has never
    # seen the IR's `ptr<mut Init>` and should not be shown it here.
    assert "parameter init : Init" in hover(12, "init")
    # Said once: the name of a record is what the type is called, so a hover
    # that wrote it twice would be saying `type Pair : Pair`.
    assert "type Pair\n" in hover(10, "Pair")
    assert "function total" in hover(11, "total")
    # A function of another module, with the signature the module gave it.
    said = hover(12, "write_sync")
    assert "function write_sync" in said and "u8" in said
    # A result, with the spaces a signature writes it with.
    assert "u64 \N{CURRENCY SIGN}size ? i32" in said
    # And the module itself, which says where it came from.
    assert "module std" in hover(12, "std")
    # A documentation comment is shown under what the thing is.
    assert "two numbers that belong together" in hover(10, "Pair")
    assert session.close() == 0


def test_where_a_name_was_defined(session: Session, tmp_path: Path) -> None:
    """In this file and in another, which is the same question either way.

    A module's function is defined in the module, and the compiler read that file
    too -- so the span it wrote down places itself, and the answer names a
    different file with no more work than the ones that do not.
    """
    source = tmp_path / "names.pl4g"
    source.write_text(NAMES, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    session.diagnostics()

    def defined(line: int, needle: str) -> tuple[str, int]:
        answered = session.request("textDocument/definition", {
            "textDocument": {"uri": uri_of(source)},
            "position": _at(NAMES, line, needle)})
        assert isinstance(answered, dict), (line, needle, answered)
        return str(answered["uri"]), int(answered["range"]["start"]["line"])

    here = uri_of(source)
    # A local, a type and a function of this file's: the line the name is on.
    assert defined(14, "sum") == (here, 11)
    assert defined(10, "Pair") == (here, 3)
    assert defined(11, "total") == (here, 5)
    assert defined(12, "init") == (here, 9)
    assert defined(12, "std") == (here, 0)
    # And one of the module's, which is in the module's own file.
    where, line = defined(12, "write_sync")
    assert where.endswith("/modules/std.pl4g"), where
    text = (ROOT / "modules" / "std.pl4g").read_text(encoding="utf-8")
    assert "write_sync" in text.splitlines()[line]
    assert session.close() == 0


def test_standing_on_a_definition_answers_with_itself(session: Session,
                                                      tmp_path: Path) -> None:
    """Which is what makes the same key work whichever end you are at.

    The checker records what a *use* resolved to and says nothing about a
    definition, there being nothing to resolve; what answers for one is the
    syntax tree, which is where a definition's name and its documentation are.
    """
    source = tmp_path / "names.pl4g"
    source.write_text(NAMES, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    session.diagnostics()
    on_the_definition = _at(NAMES, 5, "total")
    answered = session.request("textDocument/definition", {
        "textDocument": {"uri": uri_of(source)}, "position": on_the_definition})
    assert answered["uri"] == uri_of(source)
    assert answered["range"]["start"] == on_the_definition
    said = session.request("textDocument/hover", {
        "textDocument": {"uri": uri_of(source)}, "position": on_the_definition})
    assert "function total" in said["contents"]["value"]
    assert session.close() == 0


def test_nothing_is_said_about_a_place_that_is_not_a_name(session: Session,
                                                          tmp_path: Path) -> None:
    """A server that answered for every position would be guessing at most."""
    source = tmp_path / "names.pl4g"
    source.write_text(NAMES, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    session.diagnostics()
    empty = {"line": 1, "character": 0}
    assert session.request("textDocument/hover", {
        "textDocument": {"uri": uri_of(source)}, "position": empty}) is None
    assert session.request("textDocument/definition", {
        "textDocument": {"uri": uri_of(source)}, "position": empty}) is None
    assert session.close() == 0


#: A program whose comment is written the way Doxygen writes one.
DOCUMENTED = """\
\N{REFERENCE MARK}\N{REFERENCE MARK} Add two numbers, saturating rather than wrapping.
\N{REFERENCE MARK}\N{REFERENCE MARK}
\N{REFERENCE MARK}\N{REFERENCE MARK} \\param left the number on the left
\N{REFERENCE MARK}\N{REFERENCE MARK} @param right the number on the right
\N{REFERENCE MARK}\N{REFERENCE MARK} \\return the sum, or the largest u8 where it does not fit
\N{REFERENCE MARK}\N{REFERENCE MARK} \\note saturating is what the operator says
@[visible]
fn total(left: u8, right: u8) \N{RIGHTWARDS ARROW} u8:
    left \N{SQUARED PLUS} right

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(total(1u8, 2u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6
"""


def test_hover_shows_a_doxygen_comment_as_its_parts(session: Session,
                                                    tmp_path: Path) -> None:
    """The summary, then what it takes, then what it answers with.

    Which is the order a reader wants them in and is not the order they have to
    be written in; and the list of parameters is a list, because that is what it
    is.  Both sigils arrive at the same place, `@param` being `\\param`.
    """
    source = tmp_path / "documented.pl4g"
    source.write_text(DOCUMENTED, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    uri, found = session.diagnostics()
    assert found == [], found
    # The call on the last line, which is a use and so is answered out of what
    # the checker resolved rather than out of the tree.
    line = DOCUMENTED.splitlines()[12]
    answered = session.request("textDocument/hover", {
        "textDocument": {"uri": uri_of(source)},
        "position": {"line": 12, "character": line.index("total")}})
    said = str(answered["contents"]["value"])
    assert "function total : fn(u8, u8)" in said
    assert "Add two numbers, saturating rather than wrapping." in said
    assert "**Takes**\n- `left` the number on the left" in said
    assert "- `right` the number on the right" in said
    assert "**Answers with** the sum, or the largest u8 where it does not fit" in said
    assert "**Note** saturating is what the operator says" in said
    # The commands themselves are not shown: what is shown is what they said.
    assert "\\param" not in said and "@param" not in said
    assert session.close() == 0


def test_the_outline_shows_what_a_definition_is_for(session: Session,
                                                    tmp_path: Path) -> None:
    """One line beside the name, which is the summary and never a command."""
    source = tmp_path / "documented.pl4g"
    source.write_text(DOCUMENTED, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    session.diagnostics()
    found = session.request("textDocument/documentSymbol",
                            {"textDocument": {"uri": uri_of(source)}})
    by_name = {one["name"]: one for one in found}
    assert by_name["total"]["detail"] == \
        "Add two numbers, saturating rather than wrapping."
    assert "detail" not in by_name["main"], "nothing to say is nothing shown"
    assert session.close() == 0


def test_a_comment_that_names_nothing_is_a_diagnostic_like_any_other(
        session: Session, tmp_path: Path) -> None:
    """The server invents nothing: the warning is the compiler's own.

    4601 is "'\\param x' names nothing that 'f' takes", and where it is, is the
    line of the comment that says it -- which is what the parser kept the place
    of each line for.
    """
    text = DOCUMENTED.replace("\\param left the number", "\\param lft the number")
    source = tmp_path / "wrong.pl4g"
    source.write_text(text, encoding="utf-8")
    session.start(["utf-32"])
    session.open(source)
    _, found = session.diagnostics()
    assert [one["code"] for one in found] == [4601], found
    one = found[0]
    assert one["severity"] == 2, "a warning"
    assert one["range"]["start"]["line"] == 2, one
    # The caret covers the command and nothing else.
    assert one["range"]["start"]["character"] == 3
    assert one["range"]["end"]["character"] == 9
    assert session.close() == 0
