"""Fuzz one entry point for a while and fail if anything crashed or hung.

Run as ``run_fuzz.py FUZZER SECONDS OUT SEED-DIR...``.  The seeds are copied
into one directory -- AFL++ takes one -- and afl-fuzz runs for SECONDS over
them; what it found is under OUT, where a crash can be replayed with the
sanitized replay driver of the same entry point.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Run the fuzzer, and say what it found."""
    fuzzer, seconds, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    if shutil.which("afl-fuzz") is None:
        print("afl-fuzz is not installed")
        return 77
    seeds = out / "seeds"
    shutil.rmtree(out, ignore_errors=True)
    seeds.mkdir(parents=True)
    count = 0
    for directory in sys.argv[4:]:
        for path in sorted(Path(directory).rglob("*")):
            # AFL++ wants small seeds; a test program is a few kilobytes.
            if path.is_file() and path.stat().st_size <= 16384:
                shutil.copy(path, seeds / "".join(("seed-", str(count))))
                count += 1
    env = {**os.environ, "AFL_SKIP_CPUFREQ": "1", "AFL_NO_UI": "1",
           "AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES": "1", "AFL_NO_AFFINITY": "1"}
    proc = subprocess.run(["afl-fuzz", "-V", seconds, "-i", str(seeds), "-o", str(out / "findings"),
                           "--", fuzzer], env=env, capture_output=True, text=True, check=False)
    stats = out / "findings" / "default" / "fuzzer_stats"
    if not stats.exists():
        print(proc.stdout[-4000:], proc.stderr[-4000:])
        return 1
    found: dict[str, str] = {}
    for line in stats.read_text().splitlines():
        key, _, value = line.partition(":")
        found[key.strip()] = value
    crashes = int(found["saved_crashes"].strip())
    hangs = int(found["saved_hangs"].strip())
    print("".join((found["execs_done"].strip(), " executions, ", str(crashes), " crashes, ",
                   str(hangs), " hangs; findings in ", str(out / "findings"))))
    return 1 if crashes or hangs else 0


if __name__ == "__main__":
    sys.exit(main())
