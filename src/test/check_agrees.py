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
    mine = subprocess.run([dump, "".join(("--ir=", str(mine_ir))), *extra, str(path)], capture_output=True,
                          text=True, check=False, env=env, cwd=path.parent, preexec_fn=bounded)
    if mine.returncode < 0 or mine.returncode > 2:
        return "".join(("crashed (", str(mine.returncode), "): ", mine.stderr[-1500:]))
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
