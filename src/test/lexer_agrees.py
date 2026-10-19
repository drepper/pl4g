"""Check that the C++ lexer makes of every test program what pypl4g makes of it.

Run as ``lexer_agrees.py PL4G-LEXDUMP ROOT``.  Every ``.pl4g`` file under
``tests/language``, ``modules`` and the lexer's fuzzing corpus is lexed by both, plainly and lazily as a
module is, and the tokens and diagnostics are compared one line at a time.
Offsets are compared in bytes: the C++ side counts them so, and the Python
side's offsets in characters are converted here.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _escaped(text: str) -> str:
    """The text as the dump writes it: bytes outside printable ASCII escaped."""
    out: list[str] = []
    for byte in text.encode("utf-8", "surrogatepass"):
        if byte == 0x5c or byte < 0x20 or byte >= 0x7f:
            out.append("".join(("\\x", format(byte, "02x"))))
        else:
            out.append(chr(byte))
    return "".join(out)


#: The C++ name of each kind of token where it is not the Python name lowered.
_RENAMED: dict[str, str] = {
    "INT": "int_lit", "FLOAT": "float_lit", "STRING": "string_lit", "CHAR": "char_lit",
    "OPERATOR": "op", "OPEN_OPERATOR": "open_op", "CLOSE_OPERATOR": "close_op",
}


def python_dump(root: Path, path: Path, lazy: bool) -> list[str]:
    """The dump of *path* as pypl4g lexes it."""
    sys.path.insert(0, str(root))
    from pypl4g.diag.engine import collecting_engine
    from pypl4g.front.lexer import tokenize
    from pypl4g.front.token import TokKind
    from pypl4g.source.manager import SourceManager

    sources = SourceManager()
    source = sources.read(path)
    engine, collected = collecting_engine()
    tokens = tokenize(source, engine, lazy=lazy)
    text = source.text
    # Where each character begins in bytes, and one past the end.
    offsets = [0]
    for ch in text:
        offsets.append(offsets[-1] + len(ch.encode("utf-8", "surrogatepass")))

    def byte(loc: int) -> int:
        return offsets[min(loc - source.base, len(text))]

    lines = ["".join(("file ", str(path)))]
    for t in tokens:
        kind = _RENAMED.get(t.kind.name, t.kind.name.lower())
        parts = [kind, str(byte(t.span.start)), str(byte(t.span.end)), _escaped(t.text)]
        if t.kind in (TokKind.INT, TokKind.CHAR, TokKind.EXPONENT):
            value = t.int_value or 0
            parts.append("big" if abs(value) >= 2 ** 127 else str(value))
        elif t.kind is TokKind.FLOAT:
            parts.append(repr(t.float_value))
        elif t.kind is TokKind.STRING:
            parts.append(_escaped(t.str_value or ""))
        suffix = t.int_type or t.float_type
        if suffix:
            parts.append("".join((":", suffix)))
        lines.append(" ".join(parts))
    for d in collected:
        lines.append(" ".join(("diag", str(d.info.number), str(byte(d.span.start)) if d.span.is_valid else "-1")))
    return lines


def same(python: str, cpp: str) -> bool:
    """Whether two lines say the same, floats compared as numbers."""
    if python == cpp:
        return True
    a, b = python.split(" "), cpp.split(" ")
    if len(a) != len(b) or a[0] != "float_lit" or a[0] != b[0]:
        return False
    return a[:4] == b[:4] and float(a[4]) == float(b[4]) and a[5:] == b[5:]


def main() -> int:
    """Compare every file, and say where the first difference of each is."""
    dump, root = sys.argv[1], Path(sys.argv[2])
    files = sorted([*(root / "tests" / "language").rglob("*.pl4g"), *(root / "modules").glob("*.pl4g"),
                    *(root / "src" / "fuzz" / "corpus" / "lexer").glob("*.pl4g")])
    failures = 0
    for lazy in (False, True):
        flag = ["--lazy"] if lazy else []
        proc = subprocess.run([dump, *flag, *map(str, files)], capture_output=True, text=True,
                              errors="surrogateescape", check=False)
        if proc.returncode != 0:
            print("".join((dump, " failed: ", proc.stderr)))
            return 1
        cpp: dict[str, list[str]] = {}
        current: list[str] = []
        for line in proc.stdout.splitlines():
            if line.startswith("file "):
                current = cpp.setdefault(line[5:], [line])
            else:
                current.append(line)
        for path in files:
            mine = python_dump(root, path, lazy)
            theirs = cpp.get(str(path), [])
            for at, (a, b) in enumerate(zip(mine, theirs)):
                if not same(a, b):
                    print("".join((str(path), " (lazy)" if lazy else "", " line ", str(at),
                                   ":\n  pypl4g: ", a, "\n  C++:    ", b)))
                    failures += 1
                    break
            else:
                if len(mine) != len(theirs):
                    print("".join((str(path), " (lazy)" if lazy else "", ": ", str(len(mine)),
                                   " lines from pypl4g, ", str(len(theirs)), " from C++")))
                    failures += 1
    print("".join((str(len(files) * 2 - failures), " of ", str(len(files) * 2), " agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
