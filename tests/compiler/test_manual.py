"""The manual's graph holds, and every program it shows runs.

What is checked of the graph is in the script's own description: every link
names a node, every node is reached from `help`, none is more than four
kilobytes, and every builtin, attribute and keyword of the language is mentioned
somewhere -- so that a change to the language or the library that the manual
does not follow fails here and not in front of a reader.

**An example is a program, and a program is compiled.**  A node shows a whole
program under a line `Program:`, indented, and what it prints under a line
`Output:` after it; each is compiled for x86-64, run through `bin/pl4g-run`, and
held to the status nought, to that output, and to no diagnostic at all.  So an example cannot go stale: a
change to the language that breaks one fails here, naming the node.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

#: What the examples are compiled for, and how a binary for it is run here.
TARGET = "x86_64-linux-none"
RUNNER: list[str] = [] if platform.machine() == "x86_64" else ["qemu-x86_64"]


def manual() -> ModuleType:
    """The script that makes the manual, loaded as a module: it has no `.py`."""
    path = ROOT / "bin" / "pl4g-manual"
    loader = importlib.machinery.SourceFileLoader("pl4g_manual", str(path))
    spec = importlib.util.spec_from_loader("pl4g_manual", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up by name, so the module has to have one.
    sys.modules["pl4g_manual"] = module
    loader.exec_module(module)
    return module


def examples() -> list[tuple[str, str, str | None]]:
    """Every program the manual shows: the node, the program, what it prints."""
    script = manual()
    found: list[tuple[str, str, str | None]] = []
    for name, node in sorted(script.made().items()):
        for program, output in script.programs(node.body):
            found.append((name, program, output))
    return found


EXAMPLES = examples()


def test_the_manual_is_whole() -> None:
    """The graph is checked, and holds."""
    proc = subprocess.run([sys.executable, str(ROOT / "bin" / "pl4g-manual"), "--check"],
                          capture_output=True, text=True, timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr


def test_the_manual_has_examples() -> None:
    """There are programs to run: a convention nothing follows checks nothing."""
    assert len(EXAMPLES) >= 20, [one[0] for one in EXAMPLES]


@pytest.mark.parametrize(("node", "program", "output"), EXAMPLES,
                         ids=["".join((one[0], "-", str(at))) for at, one in enumerate(EXAMPLES)])
def test_example_runs(node: str, program: str, output: str | None, tmp_path: Path) -> None:
    """A program the manual shows compiles, exits with nought, and prints what
    the node says it prints."""
    source = tmp_path / "example.pl4g"
    source.write_text(program, encoding="utf-8")
    binary = tmp_path / "example"
    built = subprocess.run([sys.executable, "-m", "pypl4g", "".join(("--target=", TARGET)),
                            "-o", str(binary), str(source)],
                           cwd=ROOT, capture_output=True, text=True, timeout=180,
                           env={**os.environ, "PYTHONPATH": str(ROOT)}, check=False)
    assert built.returncode == 0, "".join(("the program in '", node, "' does not compile:\n",
                                           built.stderr, "\n", program))
    # An example is what a reader copies, so it is one the compiler has nothing
    # to say about.
    assert not built.stderr.strip(), "".join(("the program in '", node, "' is warned about:\n",
                                              built.stderr, "\n", program))
    ran = subprocess.run([str(ROOT / "bin" / "pl4g-run"), *RUNNER, str(binary)],
                         capture_output=True, text=True, timeout=120, check=False)
    assert ran.returncode == 0, "".join(("the program in '", node, "' exits with ",
                                         str(ran.returncode), ":\n", ran.stderr, "\n", program))
    if output is not None:
        assert ran.stdout == output, "".join(("the program in '", node, "' prints\n", ran.stdout,
                                              "where the node says\n", output))
