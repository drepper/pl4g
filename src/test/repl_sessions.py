"""Check that the interactive session answers as it should.

Run as ``repl_sessions.py PL4G-REPL ROOT``.  Each ``NAME.in`` under
``src/test/repl`` is typed into a session, its lines as they are, and what the
session writes is held to ``NAME.out`` and what it says to ``NAME.err``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    run, root = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2])
    failures = 0
    cases = sorted((root / "src" / "test" / "repl").glob("*.in"))
    env = {**os.environ, "PL4G_HASH_SEED": "1"}
    if run.endswith("-asan"):
        # Its shadow memory is more than any data limit allows.
        limited = ["prlimit", "--cpu=900", "--", run]
        env["ASAN_OPTIONS"] = "hard_rss_limit_mb=4096:detect_leaks=0"
    else:
        limited = ["prlimit", "--data=4294967296", "--cpu=120", "--", run]
    for case in cases:
        proc = subprocess.run(limited, stdin=case.open("rb"), capture_output=True, check=False, timeout=1800,
                              cwd=str(case.parent), env=env)
        for stream, wanted in ((proc.stdout, case.with_suffix(".out")), (proc.stderr, case.with_suffix(".err"))):
            expected = wanted.read_bytes() if wanted.exists() else b""
            if stream != expected:
                failures += 1
                print("".join((case.name, ": ", wanted.suffix, " differs\n--- expected\n",
                               expected.decode("utf-8", "replace"), "--- found\n",
                               stream.decode("utf-8", "replace"))))
        if proc.returncode != 0:
            failures += 1
            print("".join((case.name, ": exit status ", str(proc.returncode))))
    print("".join((str(len(cases) - failures), " of ", str(len(cases)), " sessions answer as they should")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
