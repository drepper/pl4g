"""Check that the C++ passes make of a module what pypl4g's make of it.

Run as ``passes_agree.py [--level=N] PL4G-IRDUMP ROOT WORK [NAME...]``.  Every
test program that compiles is compiled by pypl4g at optimization level N (nought
where not said), the module written as JSON as the checker left it, before any
pass ran, and as text once the passes of that level have run; the C++ side reads
the first, runs its own passes of that level over it, and prints it, and the two
texts must be the same.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ir_agrees import directives  # noqa: E402


#: The optimization level both sides run the passes of.
LEVEL = 0


def one(path: Path, work: Path) -> tuple[Path, Path, Path] | None:
    compiles, extra = directives(path)
    if not compiles:
        return None
    stem = "".join((path.parent.name, "--", path.stem))
    before, after = work / "".join((stem, ".before.json")), work / "".join((stem, ".ir"))
    proc = subprocess.run([sys.executable, "-I", str(HERE / "irjson.py"), "".join(("--text=", str(after))),
                           "".join(("--before=", str(before))), "".join(("-O", str(LEVEL))), *extra, "-o",
                           os.devnull, str(path)],
                          capture_output=True, text=True, check=False, env={**os.environ, "PL4G_HASH_SEED": "1"})
    if proc.returncode != 0:
        print("".join(("pypl4g failed on ", str(path), ":\n", proc.stderr[-2000:])))
        return None
    return path, before, after


def main() -> int:
    global LEVEL
    if sys.argv[1].startswith("--level="):
        LEVEL = int(sys.argv.pop(1)[len("--level="):])
    dump, root, work = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    files = sorted((root / "tests" / "language").rglob("*.pl4g"))
    picked = set(sys.argv[4:])
    if picked:
        files = [p for p in files if p.parent.name in picked]
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        made = [m for m in pool.map(lambda p: one(p, work), files) if m is not None]
    failures = 0
    for path, before, after in made:
        proc = subprocess.run([dump, "--passes", "".join(("--level=", str(LEVEL))), str(before)],
                              capture_output=True, text=True, check=False)
        expected = after.read_text(encoding="utf-8")
        if proc.returncode != 0 or proc.stdout != expected:
            failures += 1
            mine, theirs = expected.splitlines(), proc.stdout.splitlines()
            at = next((i for i, (a, b) in enumerate(zip(mine, theirs)) if a != b), min(len(mine), len(theirs)))
            print("".join((str(path), ": line ", str(at + 1), " ", proc.stderr[-500:], "\n  pypl4g: ",
                           mine[at] if at < len(mine) else "<end>", "\n  C++:    ",
                           theirs[at] if at < len(theirs) else "<end>")))
    print("".join((str(len(made) - failures), " of ", str(len(made)), " agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
