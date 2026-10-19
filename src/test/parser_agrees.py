"""Check that the C++ parser makes of every test program the tree pypl4g makes.

Run as ``parser_agrees.py PL4G-ASTDUMP ROOT``.  Every ``.pl4g`` file under
``tests/language``, ``modules`` and the parser's fuzzing corpus is parsed by
both -- as a program's own file is, and as a module is, with bodies read when
something asks for them -- and the trees and the diagnostics are compared.
A tree is written out field by field in the form ``src/lib/front/dump.hh``
describes; offsets are bytes, the Python side's characters converted here.
"""

from __future__ import annotations

import dataclasses
import enum
import struct
import subprocess
import sys
from pathlib import Path
from typing import Callable


def _escaped(text: str) -> str:
    """A string as the dump writes one."""
    out: list[str] = ['"']
    for byte in text.encode("utf-8", "surrogatepass"):
        if byte in (0x5c, 0x22) or byte < 0x20 or byte >= 0x7f:
            out.append("".join(("\\x", format(byte, "02x"))))
        else:
            out.append(chr(byte))
    out.append('"')
    return "".join(out)


def _dumper(byte: Callable[[int], int]) -> Callable[[object], str]:
    """A function writing a tree out, offsets turned into bytes by *byte*."""
    from pypl4g.front import ast
    from pypl4g.source.location import Span

    def value(v: object) -> str:
        if v is None:
            return "None"
        if isinstance(v, Span):
            return "".join(("@", str(byte(v.start)), "-", str(byte(v.end))))
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, enum.Enum):
            return v.name.lower()
        if isinstance(v, int):
            return "big" if abs(v) >= 2 ** 127 else str(v)
        if isinstance(v, float):
            return "".join(("f:", format(struct.unpack("<Q", struct.pack("<d", v))[0], "x")))
        if isinstance(v, str):
            return _escaped(v)
        if isinstance(v, tuple):
            return "".join(("[", " ".join(value(one) for one in v), "]"))
        if isinstance(v, ast.Node):
            found = [(f.name, value(getattr(v, f.name))) for f in dataclasses.fields(v)]
            found.sort()
            name = "Block" if isinstance(v, ast.DeferredBlock) else type(v).__name__
            return "".join(("(", name, *("".join((" ", k, "=", t)) for k, t in found), ")"))
        raise TypeError(repr(v))

    return value


def python_dump(root: Path, path: Path, module: bool) -> list[str]:
    """The dump of *path* as pypl4g parses it."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from pypl4g.diag.engine import collecting_engine
    from pypl4g.front.lexer import tokenize
    from pypl4g.front.parser import parse
    from pypl4g.source.manager import SourceManager

    sources = SourceManager()
    source = sources.read(path)
    engine, collected = collecting_engine()
    text = source.text
    offsets = [0]
    for ch in text:
        offsets.append(offsets[-1] + len(ch.encode("utf-8", "surrogatepass")))

    def byte(loc: int) -> int:
        if loc < 0:
            return loc
        return offsets[min(loc - source.base, len(text))]

    tokens = tokenize(source, engine, lazy=module)
    unit = parse(tokens, str(path), engine, defer=module, source=source)
    lines = ["".join(("file ", str(path))), _dumper(byte)(unit)]
    for d in collected:
        lines.append(" ".join(("diag", str(d.info.number), str(byte(d.span.start)) if d.span.is_valid else "-1")))
    return lines


def first_difference(a: str, b: str) -> str:
    """Where two long lines part, with a little of each around it."""
    at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    begin = max(0, at - 120)
    return "".join(("  pypl4g: ...", a[begin:at + 120], "\n  C++:    ...", b[begin:at + 120]))


def main() -> int:
    """Compare every file, and say where the first difference of each is."""
    dump, root = sys.argv[1], Path(sys.argv[2])
    files = sorted([*(root / "tests" / "language").rglob("*.pl4g"), *(root / "modules").glob("*.pl4g"),
                    *(root / "src" / "fuzz" / "corpus" / "parser").glob("*.pl4g")])
    failures = 0
    for module in (False, True):
        flag = ["--module"] if module else []
        proc = subprocess.run([dump, *flag, *map(str, files)], capture_output=True, text=True,
                              errors="surrogateescape", check=False)
        if proc.returncode != 0:
            print("".join((dump, " failed: ", proc.stderr[-4000:])))
            return 1
        cpp: dict[str, list[str]] = {}
        current: list[str] = []
        for line in proc.stdout.splitlines():
            if line.startswith("file "):
                current = cpp.setdefault(line[5:], [line])
            else:
                current.append(line)
        for path in files:
            mine = python_dump(root, path, module)
            theirs = cpp.get(str(path), [])
            for at, (a, b) in enumerate(zip(mine, theirs)):
                if a != b:
                    print("".join((str(path), " (module)" if module else "", " line ", str(at), ":\n",
                                   first_difference(a, b))))
                    failures += 1
                    break
            else:
                if len(mine) != len(theirs):
                    print("".join((str(path), " (module)" if module else "", ": ", str(len(mine)),
                                   " lines from pypl4g, ", str(len(theirs)), " from C++\n  ",
                                   "\n  ".join(mine[len(theirs):] or theirs[len(mine):]))))
                    failures += 1
    print("".join((str(len(files) * 2 - failures), " of ", str(len(files) * 2), " agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
