"""Check that a C++ back end generates for every test program what pypl4g's does.

Run as ``codegen_agrees.py PL4G-CODEGEN ROOT WORK TRIPLE [NAME...]``.  Each test
program that compiles is compiled by pypl4g for TRIPLE at every level its test
asks for (``opt=``, both levels where it says nothing): once to the module its
passes made, written as JSON (``irjson.py``), and once to what ``--emit asm``
writes.  The C++ back end generates from the JSON and writes the same dump,
and the two must be the same.  Names after TRIPLE pick tests by directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIRECTIVE = "\N{REFERENCE MARK} pl4g-test:"


def directives(path: Path) -> tuple[bool, list[int], list[str]]:
    """Whether the test compiles, the levels it is compiled at, and its options."""
    compiles, levels, extra = True, [0, 1], []
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.strip()
        if not body.startswith(DIRECTIVE):
            continue
        body = body[len(DIRECTIVE):].strip()
        if body == "module":
            return False, [], []
        if body.startswith("args "):
            extra.extend(body[5:].split())
            continue
        if body.startswith(("expect-diag", "setenv", "run-args")):
            continue
        for item in body.split():
            key, _, value = item.partition("=")
            if key == "compile" and value != "ok":
                compiles = False
            elif key == "opt":
                levels = [int(one) for one in value.split(",") if one.isdigit()]
    return compiles, levels, extra


def one(run: str, path: Path, work: Path, triple: str, level: int, extra: list[str]) -> str | None:
    """What differs on *path* at *level*, or nothing."""
    stem = "".join((path.parent.name, "--", path.stem, "-O", str(level), "-", triple))
    json_out, theirs = work / "".join((stem, ".json")), work / "".join((stem, ".s"))
    common = ["--target", triple, "".join(("-O", str(level))), *extra]
    env = {**os.environ, "PL4G_HASH_SEED": "1"}
    made = subprocess.run([sys.executable, str(HERE / "irjson.py"), *common, "-o", str(json_out), str(path)],
                          capture_output=True, text=True, check=False, env=env, cwd=path.parent)
    if made.returncode != 0:
        return None
    dumped = subprocess.run([sys.executable, "-m", "pypl4g", "--emit", "asm", *common, "-o", str(theirs), str(path)],
                            capture_output=True, text=True, check=False, cwd=path.parent,
                            env={**env, "PYTHONPATH": str(HERE.parent.parent)})
    if dumped.returncode != 0:
        return None
    mine = subprocess.run(["prlimit", "--as=8589934592", "--cpu=120", "--", run, "--target", triple,
                           "".join(("-O", str(level))), str(json_out)],
                          capture_output=True, text=True, check=False, cwd=path.parent)
    if mine.returncode != 0:
        return "".join(("the C++ back end failed (", str(mine.returncode), "): ", mine.stderr[-1500:]))
    a, b = theirs.read_text(encoding="utf-8").splitlines(), mine.stdout.splitlines()
    if a == b:
        return None
    at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return "".join(("differs at line ", str(at + 1), "\n  pypl4g: ", a[at] if at < len(a) else "<end>",
                    "\n  C++:    ", b[at] if at < len(b) else "<end>"))


def main() -> int:
    run, root, work, triple = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2]).resolve(), \
        Path(sys.argv[3]).resolve(), sys.argv[4]
    picked = set(sys.argv[5:])
    work.mkdir(parents=True, exist_ok=True)
    jobs = []
    for path in sorted((root / "tests" / "language").rglob("*.pl4g")):
        if picked and path.parent.name not in picked:
            continue
        compiles, levels, extra = directives(path)
        if compiles:
            jobs.extend((path, level, extra) for level in levels)
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        found = list(pool.map(lambda job: (job, one(run, job[0], work, triple, job[1], job[2])), jobs))
    failures = [(job, why) for job, why in found if why is not None]
    for (path, level, _), why in failures:
        print("".join((str(path.relative_to(root)), " -O", str(level), ": ", why)))
    print("".join((str(len(jobs) - len(failures)), " of ", str(len(jobs)), " generate as pypl4g generates for ",
                   triple)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
