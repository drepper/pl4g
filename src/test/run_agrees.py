"""Check that every test program, interpreted, exits as its test says.

Run as ``run_agrees.py PL4G-RUNJSON ROOT WORK [NAME...]``.  Each test program
that runs is compiled by pypl4g to its IR, written as JSON by ``irjson.py``; the
C++ interpreter reads that and runs it, under the limits every program the
tests run is held to, with the arguments and the environment the test asks for;
and the status it exits with is held to the one the test names.  Names after
WORK pick the tests to run, by the name of their directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The tests whose subject is what happens where the system will give no more
#: memory, which the data limit is what makes happen: a sanitized interpreter
#: cannot run under one, its shadow memory being beyond any.
NEEDS_A_DATA_LIMIT = frozenset(("memory-runs-out",))
DIRECTIVE = "\N{REFERENCE MARK} pl4g-test:"


@dataclass(slots=True)
class Expected:
    """What a test asks of a run."""

    runs: bool = False
    compiles: bool = True
    status: int = 0
    module: bool = False
    args: list[str] = field(default_factory=list)
    run_args: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    xfail: str | None = None


def directives(path: Path) -> Expected:
    found = Expected()
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.strip()
        if not body.startswith(DIRECTIVE):
            continue
        body = body[len(DIRECTIVE):].strip()
        if body == "module":
            found.module = True
        elif body.startswith("run-args "):
            found.run_args.extend(body[len("run-args "):].split())
        elif body.startswith("args "):
            found.args.extend(body[len("args "):].split())
        elif body.startswith("setenv "):
            name, _, value = body[len("setenv "):].partition("=")
            found.environment[name.strip()] = value
        elif not body.startswith("expect-diag"):
            for item in body.split():
                key, _, value = item.partition("=")
                if key == "compile":
                    found.compiles = value == "ok"
                elif key == "exit":
                    found.status = int(value)
                elif key == "run":
                    found.runs = "native" in value or "qemu" in value
                elif key == "xfail":
                    found.xfail = value
    return found


def one(path: Path, run: str, work: Path) -> str | None:
    """Compile and run *path*; what went wrong, or nothing."""
    expected = directives(path)
    stem = "".join((path.parent.name, "--", path.stem))
    out = work / "".join((stem, ".json"))
    env = {**os.environ, "PL4G_HASH_SEED": "1"}
    proc = subprocess.run([sys.executable, "-I", str(HERE / "irjson.py"), *expected.args, "-o", str(out),
                           str(path)], capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        return "".join(("pypl4g did not compile it:\n", proc.stderr[-1500:]))
    # A sanitized interpreter reserves its shadow memory beyond any data limit,
    # so its memory is bounded by the sanitizer's own limit instead.
    if run.endswith("-asan"):
        limited = ["prlimit", "--cpu=900", "--"]
        env = {**env, "ASAN_OPTIONS": "hard_rss_limit_mb=4096:detect_leaks=0:quarantine_size_mb=64"}
    else:
        limited = ["prlimit", "--data=536870912", "--cpu=120", "--"]
    try:
        ran = subprocess.run([*limited, run, str(out), *expected.run_args], capture_output=True, timeout=1000,
                             check=False, env={**env, **expected.environment}, cwd=str(path.parent))
    except subprocess.TimeoutExpired:
        return "it did not finish"
    if ran.returncode != expected.status:
        return "".join(("exit status ", str(ran.returncode), ", expected ", str(expected.status), "\n",
                        ran.stderr.decode("utf-8", "replace")[-1500:]))
    return None


def main() -> int:
    run, root, work = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    picked = set(sys.argv[4:])
    work.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted((root / "tests" / "language").rglob("*.pl4g")):
        expected = directives(path)
        if expected.module or not expected.compiles or not expected.runs or expected.xfail is not None:
            continue
        if picked and path.parent.name not in picked:
            continue
        if run.endswith("-asan") and path.parent.name in NEEDS_A_DATA_LIMIT:
            continue
        files.append(path)
    with ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) // 2)) as pool:
        results = list(pool.map(lambda p: (p, one(p, run, work)), files))
    failures = [(p, why) for p, why in results if why is not None]
    for path, why in failures:
        print("".join((str(path.relative_to(root)), ": ", why)))
    print("".join((str(len(files) - len(failures)), " of ", str(len(files)), " run as their tests say")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
