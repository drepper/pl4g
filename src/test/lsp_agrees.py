"""Check that the C++ language server answers as pypl4g's does.

Run as ``lsp_agrees.py PL4G-LSP ROOT``.  Each conversation is the one a test of
``tests/compiler/test_lsp.py`` has with ``pypl4g lsp``: written to both servers
whole, the ``shutdown`` and ``exit`` that end it included, and what each writes
back is read as JSON and compared message for message.  What the two may say
differently is their names -- ``serverInfo`` and every diagnostic's ``source``
-- and nothing else.

One conversation of that test is not here: the one about saving, which asks
for what only pypl4g's code generator says (8501).  The C++ implementation has
no code generator, and saving gets its whole compiler, which stops where the
front end stops.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

#: The programs the conversations are about, as test_lsp.py writes them.
WRONG = """\
\N{REFERENCE MARK} a program with one error
@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let a: u8 = 300u8
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(a, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""

RIGHT = WRONG.replace("300u8", "200u8")

WIDE = "".join((
    "\N{REFERENCE MARK} two errors, one after a run of glyphs\n",
    "@[startup]\n",
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n",
    "    let s: str = \"a\N{POUND SIGN}\N{EURO SIGN}\N{LINEAR B SYLLABLE B008 A}\" ; let a: u8 = 300u8\n",
    "    0u6\n",
    "\nlet std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"))

OUTLINE = """\
\N{REFERENCE MARK} everything a file can define
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

type Pair = first : u8 ; second : u8

enum Colour:
    red ;
    green

let count: u8 = 3u8

\N{REFERENCE MARK}\N{REFERENCE MARK} what this one is for
@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    0u6
"""

NAMES = """\
let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

\N{REFERENCE MARK}\N{REFERENCE MARK} two numbers that belong together
type Pair = first : u8 ; second : u8

fn total(p: Pair) \N{RIGHTWARDS ARROW} u8:
    p.first

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let both: Pair = Pair(.first \N{LEFTWARDS ARROW} 1u8, .second \N{LEFTWARDS ARROW} 2u8)
    let sum: u8 = total(both)
    match std.write_sync(&mut init.io.error,
                         \N{APL FUNCTIONAL SYMBOL QUAD}bytes("hi\\n")):
        u64 \N{CURRENCY SIGN}size: \N{APL FUNCTIONAL SYMBOL QUAD}narrow(sum, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6
        \N{UP TACK}: 1u6
"""

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
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow(total(1u8, 2u8), \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6

let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")
"""


class Conversation:
    """The messages of one session, numbered as test_lsp.py numbers them."""

    def __init__(self, encodings: list[str] | None) -> None:
        self.messages: list[dict[str, Any]] = []
        self._next = 0
        general = {} if encodings is None else {"positionEncodings": encodings}
        self.request("initialize", {"processId": None, "rootUri": None, "capabilities": {"general": general}})
        self.notify("initialized", {})

    def request(self, method: str, params: dict[str, Any]) -> None:
        self._next += 1
        self.messages.append({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params})

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self.messages.append({"jsonrpc": "2.0", "method": method, "params": params})

    def open(self, path: Path, text: str) -> None:
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri_of(path), "languageId": "pl4g", "version": 1, "text": text}})

    def change(self, path: Path, text: str, version: int = 2) -> None:
        self.notify("textDocument/didChange", {"textDocument": {"uri": uri_of(path), "version": version},
                                               "contentChanges": [{"text": text}]})

    def at(self, method: str, path: Path, text: str, line: int, needle: str) -> None:
        found = text.splitlines()[line]
        self.request(method, {"textDocument": {"uri": uri_of(path)},
                              "position": {"line": line, "character": found.index(needle)}})

    def framed(self) -> bytes:
        """All of it, ended as an editor ends a session."""
        self.request("shutdown", {})
        self.notify("exit", {})
        out = []
        for one in self.messages:
            body = json.dumps(one).encode("utf-8")
            out.append(b"".join((b"Content-Length: ", str(len(body)).encode("ascii"), b"\r\n\r\n", body)))
        return b"".join(out)


def uri_of(path: Path) -> str:
    return "".join(("file://", str(path.resolve())))


def conversations(room: Path) -> dict[str, Conversation]:
    """Every conversation, by the test it comes from."""
    made: dict[str, Conversation] = {}
    made["handshake"] = Conversation(["utf-8", "utf-16"])
    made["nothing-said"] = Conversation(None)
    wrong = room / "wrong.pl4g"
    wrong.write_text(WRONG, encoding="utf-8")
    one = made["error"] = Conversation(["utf-32"])
    one.open(wrong, WRONG)
    wide = room / "wide.pl4g"
    wide.write_text(WIDE, encoding="utf-8")
    for encoding in ("utf-32", "utf-8", "utf-16"):
        one = made["".join(("wide-", encoding))] = Conversation([encoding])
        one.open(wide, WIDE)
    one = made["change"] = Conversation(["utf-32"])
    one.open(wrong, WRONG)
    one.change(wrong, RIGHT)
    one.change(wrong, WRONG, version=3)
    one = made["close"] = Conversation(["utf-32"])
    one.open(wrong, WRONG)
    one.notify("textDocument/didClose", {"textDocument": {"uri": uri_of(wrong)}})
    outline = room / "outline.pl4g"
    outline.write_text(OUTLINE, encoding="utf-8")
    one = made["outline"] = Conversation(["utf-32"])
    one.open(outline, OUTLINE)
    one.request("textDocument/documentSymbol", {"textDocument": {"uri": uri_of(outline)}})
    one = made["refused"] = Conversation(None)
    one.messages.append({"jsonrpc": "2.0", "id": 99, "method": "textDocument/rename", "params": {}})
    names = room / "names.pl4g"
    names.write_text(NAMES, encoding="utf-8")
    one = made["names"] = Conversation(["utf-32"])
    one.open(names, NAMES)
    for line, needle in ((14, "sum"), (12, "init"), (10, "Pair"), (11, "total"), (12, "write_sync"), (12, "std")):
        one.at("textDocument/hover", names, NAMES, line, needle)
        one.at("textDocument/definition", names, NAMES, line, needle)
    one.at("textDocument/definition", names, NAMES, 5, "total")
    one.at("textDocument/hover", names, NAMES, 5, "total")
    for method in ("textDocument/hover", "textDocument/definition"):
        one.request(method, {"textDocument": {"uri": uri_of(names)}, "position": {"line": 1, "character": 0}})
    documented = room / "documented.pl4g"
    documented.write_text(DOCUMENTED, encoding="utf-8")
    one = made["documented"] = Conversation(["utf-32"])
    one.open(documented, DOCUMENTED)
    one.at("textDocument/hover", documented, DOCUMENTED, 12, "total")
    one.request("textDocument/documentSymbol", {"textDocument": {"uri": uri_of(documented)}})
    misnamed = room / "misnamed.pl4g"
    text = DOCUMENTED.replace("\\param left the number", "\\param lft the number")
    misnamed.write_text(text, encoding="utf-8")
    one = made["misnamed"] = Conversation(["utf-32"])
    one.open(misnamed, text)
    return made


def answers(command: list[str], conversation: Conversation, root: Path) -> list[Any]:
    """What a server writes back over the whole of *conversation*."""
    env = {**os.environ, "PL4G_HASH_SEED": "1"}
    if command[-1].endswith("-asan"):
        env["ASAN_OPTIONS"] = "hard_rss_limit_mb=4096:detect_leaks=0"
    proc = subprocess.run(command, input=conversation.framed(), capture_output=True, check=False, timeout=1200,
                          cwd=str(root), env=env)
    data = proc.stdout
    found: list[Any] = []
    while data:
        head, _, rest = data.partition(b"\r\n\r\n")
        length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                      if line.lower().startswith(b"content-length"))
        found.append(json.loads(rest[:length].decode("utf-8")))
        data = rest[length:]
    found.append({"status": proc.returncode})
    return found


def normalized(value: Any) -> Any:
    """What two servers may say differently taken out: their names."""
    if isinstance(value, dict):
        out = {k: normalized(v) for k, v in value.items()}
        if "serverInfo" in out:
            out["serverInfo"] = "-"
        if "source" in out and "code" in out:
            out["source"] = "-"
        return out
    if isinstance(value, list):
        return [normalized(one) for one in value]
    return value


def main() -> int:
    server, root = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2]).resolve()
    if server.endswith("-asan"):
        mine = ["prlimit", "--cpu=900", "--", server]
    else:
        mine = ["prlimit", "--data=4294967296", "--cpu=300", "--", server]
    theirs = [sys.executable, "-m", "pypl4g", "lsp"]
    failures = 0
    with tempfile.TemporaryDirectory(prefix="lsp-agrees-") as room:
        made = conversations(Path(room))
        for name, conversation in made.items():
            a = normalized(answers(theirs, conversation, root))
            b = normalized(answers(mine, conversation, root))
            if a != b:
                failures += 1
                print("".join((name, ": the answers differ")))
                for i, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        print("".join(("  message ", str(i), "\n    pypl4g: ", json.dumps(x, ensure_ascii=False),
                                       "\n    C++:    ", json.dumps(y, ensure_ascii=False))))
                        break
                else:
                    print("".join(("  ", str(len(a)), " messages against ", str(len(b)))))
    print("".join((str(len(made) - failures), " of ", str(len(made)), " conversations agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
