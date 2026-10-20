"""Check that the C++ compiler's driver answers as pypl4g's does, where no
code is generated.

Run as ``driver_agrees.py PL4G ROOT WORK [NAME...]``.  Every test program
under ``tests/language`` is compiled by both with ``--emit`` asking for the
tokens, the tree, the tree after the macros and the IR, with the options the
test asks for; what each writes, says and exits with must be the same -- the
name a compiler gives itself apart.  A list of command lines that are wrong
is held to the same.
"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DIRECTIVE = "\N{REFERENCE MARK} pl4g-test:"
KINDS = ("tokens", "ast", "expanded", "ir")

#: Command lines that are wrong, each with nothing to compile behind it.
BAD = (
    [], ["-o", "out"], ["test"], ["-o", "out", "x.txt"], ["-o", "out", "--nonsense", "x.pl4g"],
    ["-o", "a", "-o", "b", "x.pl4g"], ["-o", "out", "--incremental", "x.pl4g"], ["-o"],
    ["-o", "out", "--emit=nonsense", "x.pl4g"], ["--verbose=3", "x.pl4g"], ["-O", "1", "x.pl4g"],
    ["-O12", "x.pl4g"], ["--jobs=0", "x.pl4g"], ["--stack-size=12q", "x.pl4g"], ["-vgx", "x.pl4g"],
    ["--color=sometimes", "x.pl4g"], ["--diag-format=xml", "x.pl4g"], ["--conditions=never", "x.pl4g"],
    ["lsp", "x.pl4g"], ["-Werror=1", "x.pl4g"], ["--target"], ["-o", "x", "--", "-y.pl4g"],
    ["--emi=ir", "x.pl4g"], ["-vofile", "x.pl4g"], ["-v=1", "x.pl4g"], ["-v-o", "x.pl4g"],
    ["-o=", "x.pl4g"], ["-o", "-x", "x.pl4g"], ["-Dname=1", "-j", "3x", "x.pl4g"],
    ["a.pl4g", "--emit=ir", "b.pl4g"], ["-O", "1", "x.pl4g"], ["-1", "x.pl4g"], ["-", "x.pl4g"],
    ["--module-path", "--", "x.pl4g"], ["-Wno-foo", "-Wbar", "x.pl4g"],
)


SANITIZED = False


def bounded() -> None:
    """What one compiler run may take: a sanitized one's shadow memory is more
    than any address-space limit allows, and what it may take is said to the
    sanitizer instead."""
    if not SANITIZED:
        resource.setrlimit(resource.RLIMIT_AS, (8 << 30, 8 << 30))
    resource.setrlimit(resource.RLIMIT_CPU, (900, 900))


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


def both(cpp: str, args: list[str], cwd: Path) -> tuple[subprocess.CompletedProcess[bytes], ...]:
    """Each compiler's run over *args*."""
    env = {**os.environ, "PL4G_HASH_SEED": "1"}
    root = Path(__file__).resolve().parent.parent.parent
    python = subprocess.run([str(root / "bin" / "pypl4g"), *args], capture_output=True,
                            check=False, cwd=cwd, env=env, preexec_fn=bounded)
    mine = subprocess.run([cpp, *args], capture_output=True, check=False, cwd=cwd, env=env, preexec_fn=bounded)
    return python, mine


def differs(python: subprocess.CompletedProcess[bytes], mine: subprocess.CompletedProcess[bytes]) -> str | None:
    """What differs between two runs, the compilers' names apart."""
    def own(data: bytes) -> bytes:
        return data.replace(b"pypl4g", b"pl4g")
    if python.returncode != mine.returncode:
        return "".join(("exit ", str(python.returncode), " against ", str(mine.returncode), "\n",
                        mine.stderr.decode("utf-8", "replace")[-800:]))
    for name, a, b in (("stdout", python.stdout, mine.stdout), ("stderr", python.stderr, mine.stderr)):
        if own(a) != own(b):
            return "".join((name, " differs\n  pypl4g: ", a.decode("utf-8", "replace")[:600],
                            "\n  C++:    ", b.decode("utf-8", "replace")[:600]))
    return None


def one(cpp: str, path: Path, work: Path) -> list[str]:
    """What differs on *path*, for every kind."""
    own, extra = directives(path)
    if not own:
        return []
    found = []
    for kind in KINDS:
        stem = "".join((path.parent.name, "--", path.stem, ".", kind))
        outputs = (work / "".join((stem, ".py")), work / "".join((stem, ".cc")))
        for stale in outputs:
            stale.unlink(missing_ok=True)
        runs = []
        for out, compiler in zip(outputs, ("python", "cpp")):
            args = ["".join(("--emit=", kind)), "-o", str(out), *extra, str(path)]
            runs.append(args)
        env = {**os.environ, "PL4G_HASH_SEED": "1"}
        root = Path(__file__).resolve().parent.parent.parent
        a = subprocess.run([str(root / "bin" / "pypl4g"), *runs[0]], capture_output=True, check=False,
                           cwd=path.parent, env=env, preexec_fn=bounded)
        b = subprocess.run([cpp, *runs[1]], capture_output=True, check=False, cwd=path.parent, env=env,
                           preexec_fn=bounded)
        why = differs(a, b)
        if why is None and outputs[0].exists() != outputs[1].exists():
            why = "an output written by one only"
        if why is None and outputs[0].exists() and outputs[0].read_bytes() != outputs[1].read_bytes():
            x, y = outputs[0].read_text("utf-8").splitlines(), outputs[1].read_text("utf-8").splitlines()
            at = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
            why = "".join(("the output differs at line ", str(at + 1), "\n  pypl4g: ",
                           x[at][:400] if at < len(x) else "<end>", "\n  C++:    ",
                           y[at][:400] if at < len(y) else "<end>"))
        if why is not None:
            found.append("".join((str(path), " --emit=", kind, ": ", why)))
    return found


def main() -> int:
    global SANITIZED
    SANITIZED = sys.argv[1].endswith("-asan")
    if SANITIZED:
        os.environ["ASAN_OPTIONS"] = "hard_rss_limit_mb=4096:detect_leaks=0"
    cpp, root, work = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    names = set(sys.argv[4:])
    work.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for args in BAD:
        python, mine = both(cpp, list(args), work)
        why = differs(python, mine)
        if why is not None:
            failures.append("".join(("pl4g ", " ".join(args), ": ", why)))
    files = [p for p in sorted((root / "tests" / "language").rglob("*.pl4g"))
             if directives(p)[0] and (not names or p.parent.name in names)]
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        for found in pool.map(lambda p: one(cpp, p, work), files):
            failures.extend(found)
    for line in failures:
        print(line)
    total = len(BAD) + len(KINDS) * len(files)
    print("".join((str(total - len(failures)), " of ", str(total), " agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
