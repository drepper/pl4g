"""Check that the C++ back end settles runs of elements as pypl4g's does.

Run as ``vectors_agree.py PL4G-VECDUMP ROOT WORK``.  Every test program pypl4g
compiles is compiled at each level its test asks for; the module the passes
leave is written as JSON, and then settled by pypl4g's ``target.vectors`` for
each target's vector registers -- none, x86-64 at its first and third levels,
AArch64 -- and printed.  The C++ side reads the JSON, settles it the same way
and prints it, and the two texts must be the same.

Run as ``vectors_agree.py --one CONFIG JSON TEXT [PYPL4G-OPTION...] SOURCE`` it
is the pypl4g side of one comparison.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIGS = ("none", "x86-v1", "x86-v3", "aarch64")
DIRECTIVE = "\N{REFERENCE MARK} pl4g-test:"


def configured(name: str) -> object:
    """What each target the test names can do to a run at once."""
    from pypl4g.ir.inst import BinOp, UnOp
    from pypl4g.target.vectors import EVERY, Vectors
    if name == "none":
        return Vectors()
    narrow = frozenset((8, 16))
    aarch64 = name == "aarch64"
    wrapped = frozenset((8, 16, 32)) if aarch64 else (
        frozenset((16,)) if name == "x86-v1" else frozenset((16, 32)))
    return Vectors(
        bits=256 if name == "x86-v3" else 128,
        binary={BinOp.AND: EVERY, BinOp.OR: EVERY, BinOp.XOR: EVERY,
                BinOp.ADD: EVERY, BinOp.SUB: EVERY,
                BinOp.SAT_ADD: EVERY if aarch64 else narrow,
                BinOp.SAT_SUB: EVERY if aarch64 else narrow,
                BinOp.WRAP_ADD: EVERY, BinOp.WRAP_SUB: EVERY,
                BinOp.WRAP_MUL: wrapped},
        unary={UnOp.NOT: EVERY})


def one(args: list[str]) -> int:
    """The pypl4g side: compile, write the JSON, settle, write the text."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(HERE))
    import irjson
    import pypl4g.driver.main as driver
    from pypl4g.ir.layout import DataLayout
    from pypl4g.ir.printer import render_module
    from pypl4g.target.vectors import settle
    config, json_out, text_out, *rest = args

    def captured(module: object) -> str:
        Path(json_out).write_text(irjson.render(module), encoding="utf-8")
        settle(module, configured(config), DataLayout(pointer_size=8))
        Path(text_out).write_text(render_module(module), encoding="utf-8")
        return ""

    driver.render_module = captured  # type: ignore[assignment]
    return driver.main(["--emit", "ir", "-o", os.devnull, *rest])


def directives(path: Path) -> tuple[bool, list[str], list[str]]:
    """Whether the test compiles, its options, and the levels it asks for."""
    compiles, extra, levels = True, [], ["0"]
    for line in path.read_text(encoding="utf-8").splitlines():
        body = line.strip()
        if not body.startswith(DIRECTIVE):
            continue
        body = body[len(DIRECTIVE):].strip()
        if body == "module":
            return False, [], []
        if body.startswith("args "):
            extra.extend(body[5:].split())
        for item in body.split():
            key, _, value = item.partition("=")
            if key == "compile" and value != "ok":
                compiles = False
            elif key == "opt":
                levels = value.split(",")
    return compiles, extra, levels


def compare(dump: str, path: Path, work: Path) -> list[str]:
    """What differs for one test program, at each level and for each target."""
    compiles, extra, levels = directives(path)
    if not compiles:
        return []
    wrong: list[str] = []
    for level in levels:
        for config in CONFIGS:
            stem = "".join((path.parent.name, "--", path.stem, "-O", level, "-", config))
            json_out, text_out = work / (stem + ".json"), work / (stem + ".ir")
            made = subprocess.run([sys.executable, str(HERE / "vectors_agree.py"), "--one", config, str(json_out),
                                   str(text_out), "-O" + level, *extra, str(path)], capture_output=True, text=True,
                                  check=False, env={**os.environ, "PL4G_HASH_SEED": "1"})
            if made.returncode != 0 or not json_out.exists():
                return []
            if '"vec' not in json_out.read_text(encoding="utf-8") and config != "none":
                continue
            mine = subprocess.run([dump, config, str(json_out)], capture_output=True, text=True, check=False)
            expected = text_out.read_text(encoding="utf-8")
            if mine.returncode != 0 or mine.stdout != expected:
                a, b = expected.splitlines(), mine.stdout.splitlines()
                at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                wrong.append("".join((str(path.relative_to(ROOT)), " -O", level, " ", config, ": line ",
                                      str(at + 1), mine.stderr[-300:], "\n  pypl4g: ",
                                      a[at] if at < len(a) else "<end>", "\n  C++:    ",
                                      b[at] if at < len(b) else "<end>")))
    return wrong


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--one":
        return one(sys.argv[2:])
    dump, root, work = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2]), Path(sys.argv[3])
    work.mkdir(parents=True, exist_ok=True)
    files = sorted((root / "tests" / "language").rglob("*.pl4g"))
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        found = list(pool.map(lambda p: compare(dump, p, work), files))
    wrong = [one for some in found for one in some]
    for line in wrong:
        print(line)
    print("".join((str(len(files)), " test programs, ", str(len(wrong)), " differences")))
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
