"""The manual's graph holds: bin/pl4g-manual checks it as it makes it.

What is checked is in the script's own description: every link names a node,
every node is reached from `help`, none is more than four kilobytes, and every
builtin, attribute and keyword of the language is mentioned somewhere -- so
that a change to the language or the library that the manual does not follow
fails here and not in front of a reader.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_the_manual_is_whole() -> None:
    """The graph is checked, and holds."""
    proc = subprocess.run([sys.executable, str(ROOT / "bin" / "pl4g-manual"), "--check"],
                          capture_output=True, text=True, timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr
