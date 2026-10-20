"""Run pypl4g as its command line does, writing the IR its checker made.

Run as ``checkout.py --ir=OUT.ir [PYPL4G-OPTION...] SOURCE.pl4g``: what pypl4g
says goes to standard error as it always does, and the module as the checker
left it -- before any pass ran -- is written to OUT.ir, in the form
``pl4g-checkdump --ir`` writes.  Nothing after the checker runs: the passes and
everything they lead to are put aside, being what the comparison is not about.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypl4g.driver import main as driver  # noqa: E402
from pypl4g.ir.printer import render_module  # noqa: E402
from pypl4g.opt import pass_  # noqa: E402


def main() -> int:
    """Run pypl4g on the arguments, with the passes replaced by a dump."""
    args = sys.argv[1:]
    out = next((a[len("--ir="):] for a in args if a.startswith("--ir=")), None)
    args = [a for a in args if not a.startswith("--ir=")]

    def dump(self: object, module: object) -> None:
        if out is not None:
            Path(out).write_text(render_module(module), encoding="utf-8")

    pass_.PassManager.run = dump  # type: ignore[method-assign]
    driver.render_module = lambda module: ""  # type: ignore[attr-defined]
    return driver.main(["--emit", "ir", "-o", "/dev/null", *args])


if __name__ == "__main__":
    sys.exit(main())
