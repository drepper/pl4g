"""Check that every row of the C++ AArch64 encoding table encodes as pypl4g's.

Run as ``encoding_agrees_aarch64.py PL4G-ENCDUMP ROOT``.  The samples are the
ones ``tests/compiler/test_encoding_aarch64.py`` holds pypl4g's table to the
GNU assembler with, one per row of the table; each is encoded by pypl4g and by
``pl4g-encdump``, and the bytes and the fixups they leave must be the same.
The codegen comparison covers what the test programs reach; this covers every
row, the ones nothing reaches yet among them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def samples(root: Path) -> None:
    """Write each sample as the tool reads it and as pypl4g encodes it, as JSON.

    In a process of its own: the module the samples are in brings the test
    suite's limits with it, and a sanitized tool cannot start under them.
    """
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "tests"))
    sys.path.insert(0, str(root / "tests" / "compiler"))
    from pypl4g.mc.desc import InstrTable
    from pypl4g.mc.inst import MCInst
    from pypl4g.mc.operand import MCImm, MCReg, MCSymRef
    from pypl4g.target.aarch64.encoder import encode
    from pypl4g.target.aarch64.opcodes import AARCH64_INSTRS
    from test_encoding_aarch64 import SAMPLES

    table = InstrTable(AARCH64_INSTRS)

    def written(operand: object) -> str:
        if isinstance(operand, MCReg):
            return "".join(("r:", operand.reg.name, "" if operand.bits is None else ":" + str(operand.bits)))
        if isinstance(operand, MCImm):
            return "".join(("i:", str(operand.value), ":", str(operand.bits), ":", "1" if operand.signed else "0"))
        assert isinstance(operand, MCSymRef)
        return "".join(("s:", operand.expr.symbol.name))

    found = []
    for sample in SAMPLES:
        mnemonic, operands = sample[1], sample[2:]
        data, fixups = encode(MCInst(table.select(mnemonic, operands), operands))
        found.append((" ".join((mnemonic, *(written(one) for one in operands))),
                      "".join((data.hex(), *("".join((" ", f.kind.name, "@", str(f.offset))) for f in fixups)))))
    json.dump({"samples": found, "rows": len(AARCH64_INSTRS)}, sys.stdout)


def main() -> int:
    if sys.argv[1] == "--samples":
        samples(Path(sys.argv[2]))
        return 0
    run, root = sys.argv[1], Path(sys.argv[2])
    taken = json.loads(subprocess.run([sys.executable, __file__, "--samples", str(root)], capture_output=True,
                                      text=True, check=True).stdout)
    lines = [line for line, _ in taken["samples"]]
    expected = [want for _, want in taken["samples"]]
    found = subprocess.run([run, "--target", "aarch64-linux-none"], input="\n".join(lines) + "\n",
                           capture_output=True, text=True, check=False).stdout.splitlines()
    failures = 0
    for line, want, got in zip(lines, expected, found + [""] * (len(expected) - len(found))):
        if want != got:
            failures += 1
            print("".join((line, "\n  pypl4g: ", want, "\n  C++:    ", got)))
    print("".join((str(len(lines) - failures), " of ", str(len(lines)), " samples, for ", str(taken["rows"]),
                   " rows, encode alike")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
