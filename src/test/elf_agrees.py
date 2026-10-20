"""Check that the C++ compiler writes for every test program the image pypl4g writes.

Run as ``elf_agrees.py PL4G ROOT WORK TRIPLE [NAME...]``.  Each test program
that compiles is compiled for TRIPLE at every level its test asks for, by
pypl4g (``python -m pypl4g`` from ROOT) and by the C++ compiler, with the same
command line in the same directory, and the two images must be the same byte
for byte.  The C++ compiler is told to write pypl4g's name into the bill of
materials, which is the one place the two differ on purpose.  ROOT is the tree
both find their modules in.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from codegen_agrees import directives  # noqa: E402


def one(run: str, root: Path, path: Path, work: Path, triple: str, level: int, extra: list[str]) -> str | None:
    """What differs on *path* at *level*, or nothing."""
    stem = "".join((path.parent.name, "--", path.stem, "-O", str(level), "-", triple))
    theirs, mine = work / "".join((stem, ".py.elf")), work / "".join((stem, ".cc.elf"))
    for stale in (theirs, mine):
        stale.unlink(missing_ok=True)
    common = ["--target", triple, "".join(("-O", str(level))), *extra, str(path)]
    env = {**os.environ, "PL4G_HASH_SEED": "1", "PYTHONPATH": str(root)}
    made = subprocess.run([sys.executable, "-m", "pypl4g", "-o", str(theirs), *common], capture_output=True,
                          text=True, check=False, env=env, cwd=path.parent)
    if made.returncode != 0:
        return None
    sanitized = run.endswith("-asan")
    limit = ["prlimit", "--cpu=900", "--"] if sanitized else ["prlimit", "--as=8589934592", "--cpu=120", "--"]
    ours = subprocess.run([*limit, run, "-o", str(mine), *common], capture_output=True, text=True, check=False,
                          cwd=path.parent, env={**env, "PL4G_SBOM_COMPILER": "pypl4g 0.1",
                                                "ASAN_OPTIONS": "hard_rss_limit_mb=4096:detect_leaks=0"})
    if ours.returncode != 0:
        return "".join(("the C++ compiler failed (", str(ours.returncode), "): ", ours.stderr[-1500:]))
    a, b = theirs.read_bytes(), mine.read_bytes()
    if a == b:
        return None
    at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return "".join(("the images differ from byte ", str(at), " (", str(len(a)), " and ", str(len(b)), " bytes)"))


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
        found = list(pool.map(lambda job: (job, one(run, root, job[0], work, triple, job[1], job[2])), jobs))
    failures = [(job, why) for job, why in found if why is not None]
    for (path, level, _), why in failures:
        print("".join((str(path.relative_to(root)), " -O", str(level), ": ", why)))
    print("".join((str(len(jobs) - len(failures)), " of ", str(len(jobs)), " images are pypl4g's for ", triple)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
