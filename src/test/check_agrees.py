"""Check that the C++ checker makes of every test program what pypl4g's makes.

Run as ``check_agrees.py PL4G-CHECKDUMP ROOT WORK [NAME...]``.  Every test
program under ``tests/language`` is checked by both, with the options the test
asks for: what each says on standard error must be the same, word for word,
and so must its exit status and, where the program compiles, the text of the
IR as the checker left it, before any pass ran.  Naming tests checks only
those; a summary line says how many agreed.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIRECTIVE = "\N{REFERENCE MARK} pl4g-test:"

#: The tests pypl4g itself fails, each in TODO-pypl4g.md: what the C++ side
#: says of them is held to what the test expects instead.
PYPL4G_FAILS = frozenset(("unit-put-on-a-unit",))


def as_the_test_says(path: Path, status: int, said: str) -> str | None:
    """What is wrong with a run of *path*, held to the test's own directives."""
    compiles, wanted = True, []
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.strip()
        if body.startswith(DIRECTIVE):
            for item in body[len(DIRECTIVE):].split():
                key, _, value = item.partition("=")
                if key == "compile":
                    compiles = value == "ok"
                elif key == "diag" and value != "none":
                    wanted.extend(value.split(","))
    if (status == 0) != compiles:
        return "".join(("exit ", str(status), " where the test says compile=", "ok" if compiles else "error"))
    missing = [one for one in wanted if "".join(("[PL4G-", one, "]")) not in said]
    return "".join(("does not say ", ", ".join(missing))) if missing else None


def directives(path: Path) -> tuple[bool, list[str]]:
    """Whether the file is a program of its own, and the options it asks for."""
    extra: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.strip()
        if not body.startswith(DIRECTIVE):
            continue
        body = body[len(DIRECTIVE):].strip()
        if body == "module":
            return False, []
        if body.startswith("args "):
            extra.extend(body[5:].split())
    return True, extra


def bounded() -> None:
    """What a checker run may take: a runaway stops rather than takes the machine."""
    resource.setrlimit(resource.RLIMIT_AS, (8 << 30, 8 << 30))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))


def bounded_sanitized() -> None:
    """The same for a sanitized build, whose shadow memory no address-space limit
    allows: what it may take is said to the sanitizer instead."""
    resource.setrlimit(resource.RLIMIT_CPU, (900, 900))


def one(dump: str, path: Path, work: Path) -> str | None:
    """What differs between the two on *path*, or nothing."""
    own, extra = directives(path)
    if not own:
        return None
    stem = "".join((path.parent.name, "--", path.stem))
    theirs_ir, mine_ir = work / "".join((stem, ".py.ir")), work / "".join((stem, ".cc.ir"))
    for stale in (theirs_ir, mine_ir):
        stale.unlink(missing_ok=True)
    env = {**os.environ, "PL4G_HASH_SEED": "1"}
    theirs = subprocess.run([sys.executable, "-I", str(HERE / "checkout.py"), "".join(("--ir=", str(theirs_ir))),
                             *extra, str(path)], capture_output=True, text=True, check=False, env=env,
                            cwd=path.parent, preexec_fn=bounded)
    sanitized = dump.endswith("-asan")
    mine = subprocess.run([dump, "".join(("--ir=", str(mine_ir))), *extra, str(path)], capture_output=True,
                          text=True, check=False, cwd=path.parent,
                          env={**env, "ASAN_OPTIONS": "hard_rss_limit_mb=4096:detect_leaks=0"} if sanitized else env,
                          preexec_fn=bounded_sanitized if sanitized else bounded)
    if mine.returncode < 0 or mine.returncode > 2:
        return "".join(("crashed (", str(mine.returncode), "): ", mine.stderr[-1500:]))
    if path.parent.name in PYPL4G_FAILS:
        return as_the_test_says(path, mine.returncode, mine.stderr)
    if theirs.stderr != mine.stderr:
        a, b = theirs.stderr.splitlines(), mine.stderr.splitlines()
        at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        return "".join(("diagnostics differ at line ", str(at + 1), "\n  pypl4g: ", a[at] if at < len(a) else "<end>",
                        "\n  C++:    ", b[at] if at < len(b) else "<end>"))
    if theirs.returncode != mine.returncode:
        return "".join(("exit ", str(theirs.returncode), " against ", str(mine.returncode)))
    if theirs_ir.exists() != mine_ir.exists():
        return "IR written by one only"
    if theirs_ir.exists():
        a = theirs_ir.read_text(encoding="utf-8").splitlines()
        b = mine_ir.read_text(encoding="utf-8").splitlines()
        if a != b:
            at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
            return "".join(("IR differs at line ", str(at + 1), "\n  pypl4g: ", a[at] if at < len(a) else "<end>",
                            "\n  C++:    ", b[at] if at < len(b) else "<end>"))
    return None


def main() -> int:
    dump, root, work = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    names = set(sys.argv[4:])
    work.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted((root / "tests" / "language").rglob("*.pl4g"))
             if not names or p.parent.name in names or p.stem in names]
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        found = list(pool.map(lambda p: (p, one(dump, p, work)), files))
    checked = [(p, why) for p, why in found if directives(p)[0]]
    failures = [(p, why) for p, why in checked if why is not None]
    for path, why in failures:
        print("".join((str(path.relative_to(root)), ": ", why)))
    print("".join((str(len(checked) - len(failures)), " of ", str(len(checked)), " agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
