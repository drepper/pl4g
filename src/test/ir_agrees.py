"""Check that a module read from JSON prints as pypl4g prints it.

Run as ``ir_agrees.py PL4G-IRDUMP ROOT WORK``.  Every test program that compiles
is compiled by pypl4g to its IR, written both as text and as JSON (by
``irjson.py``); the C++ side reads the JSON and prints it, and the two texts
must be the same.  What that checks is the reader, the types and the printer
-- and that the JSON leaves out nothing the text says -- and, through the layout
pypl4g writes beside every type, that each is laid out alike.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent


def directives(path: Path) -> tuple[bool, list[str]]:
    """Whether the test compiles, and the options it asks for."""
    compiles, extra = True, []
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.strip()
        if not body.startswith("\N{REFERENCE MARK} pl4g-test:"):
            continue
        body = body[len("\N{REFERENCE MARK} pl4g-test:"):].strip()
        if body == "module":
            return False, []
        if body.startswith("args "):
            extra.extend(body[5:].split())
        elif any(one.startswith("compile=") and one != "compile=ok" for one in body.split()):
            compiles = False
    return compiles, extra


def one(path: Path, work: Path) -> tuple[Path, Path, Path] | None:
    """Compile *path* to JSON and text; nothing where pypl4g would not."""
    compiles, extra = directives(path)
    if not compiles:
        return None
    stem = "".join((path.parent.name, "--", path.stem))
    json_out, text_out = work / "".join((stem, ".json")), work / "".join((stem, ".ir"))
    proc = subprocess.run([sys.executable, "-I", str(HERE / "irjson.py"), "".join(("--text=", str(text_out))),
                           *extra, "-o", str(json_out), str(path)], capture_output=True, text=True, check=False,
                          env={**os.environ, "PL4G_HASH_SEED": "1"})
    if proc.returncode != 0:
        print("".join(("pypl4g failed on ", str(path), ":\n", proc.stderr[-2000:])))
        return None
    return path, json_out, text_out


def main() -> int:
    dump, root, work = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    files = sorted((root / "tests" / "language").rglob("*.pl4g"))
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        made = [m for m in pool.map(lambda p: one(p, work), files) if m is not None]
    failures = 0
    for path, json_out, text_out in made:
        proc = subprocess.run([dump, str(json_out)], capture_output=True, text=True, check=False)
        expected = text_out.read_text(encoding="utf-8")
        if proc.returncode != 0 or proc.stdout != expected:
            failures += 1
            mine, theirs = expected.splitlines(), proc.stdout.splitlines()
            at = next((i for i, (a, b) in enumerate(zip(mine, theirs)) if a != b), min(len(mine), len(theirs)))
            print("".join((str(path), ": line ", str(at + 1), proc.stderr[-500:], "\n  pypl4g: ",
                           mine[at] if at < len(mine) else "<end>", "\n  C++:    ",
                           theirs[at] if at < len(theirs) else "<end>")))
    print("".join((str(len(made) - failures), " of ", str(len(made)), " agree")))
    # And every type of every one of them laid out as pypl4g lays it out.
    proc = subprocess.run([dump, "--check-layout", *(str(j) for _, j, _ in made)], capture_output=True, text=True,
                          check=False)
    if proc.returncode != 0:
        print(proc.stdout[-4000:], proc.stderr[-2000:])
        failures += 1
    else:
        print("every type laid out alike")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
