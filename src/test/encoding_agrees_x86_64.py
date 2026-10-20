"""Check that every row of the C++ x86-64 encoding table encodes as pypl4g's.

Run as ``encoding_agrees_x86_64.py PL4G-ENCDUMP ROOT``.  The samples are the
ones ``tests/compiler/test_encoding.py`` holds pypl4g's table to the
disassembler with, one per row of the table, and its hand-checked encodings
and refusals beside them; each is encoded by pypl4g and by ``pl4g-encdump``,
and the bytes and the fixups they leave, or what refuses them, must be the
same.  The codegen comparison covers what the test programs reach; this covers
every row, the ones nothing reaches yet among them.
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
    from pypl4g.mc.desc import InstrTable, SelectionError
    from pypl4g.mc.inst import MCInst
    from pypl4g.mc.operand import MCImm, MCMem, MCReg, MCSymRef
    from pypl4g.target.x86_64.encoder import EncodingError, encode
    from pypl4g.target.x86_64.opcodes import X86_INSTRS
    from pypl4g.target.x86_64.regs import reg
    from test_encoding import KNOWN, SAMPLES

    table = InstrTable(X86_INSTRS)

    def written(operand: object) -> str:
        if isinstance(operand, MCReg):
            return "".join(("r:", operand.reg.name, "" if operand.bits is None else ":" + str(operand.bits)))
        if isinstance(operand, MCImm):
            return "".join(("i:", str(operand.value), ":", str(operand.bits), ":", "1" if operand.signed else "0"))
        if isinstance(operand, MCMem):
            fields = [one for one in (
                "".join(("base=", operand.base.name)) if operand.base is not None else "",
                "".join(("index=", operand.index.name)) if operand.index is not None else "",
                "".join(("scale=", str(operand.scale))),
                "".join(("disp=", str(operand.disp))),
                "".join(("sym=", operand.disp_sym.symbol.name)) if operand.disp_sym is not None else "",
                "".join(("seg=", operand.seg.name)) if operand.seg is not None else "",
                "".join(("size=", str(operand.size_bits))) if operand.size_bits is not None else "",
                "".join(("rip=", "1" if operand.rip_relative else "0")),
                "".join(("signed=", "1" if operand.signed else "0"))) if one]
            return "".join(("m:", ",".join(fields)))
        assert isinstance(operand, MCSymRef)
        return "".join(("s:", operand.expr.symbol.name))

    # The refusals the test holds the encoder to, which the C++ one has to say
    # in the same words.
    refused = [
        ("mov", MCReg(reg("spl")), MCReg(reg("ah"))),
        ("mov", MCReg(reg("rax")), MCMem(base=reg("rbx"), index=reg("rsp"))),
        ("mov", MCReg(reg("r16d")), MCReg(reg("eax"))),
        ("mov", MCReg(reg("eax")), MCReg(reg("rax"))),
        ("mov", MCReg(reg("dil")), MCReg(reg("sil"))),
    ]
    found = []
    for sample in [*(one[1:] for one in SAMPLES), *(one[1:] for one in KNOWN), *refused]:
        mnemonic, operands = sample[0], sample[1:]
        try:
            data, fixups = encode(MCInst(table.select(mnemonic, operands), operands))
            said = "".join((data.hex(), *("".join((" ", f.kind.name, "@", str(f.offset))) for f in fixups)))
        except (EncodingError, SelectionError) as error:
            said = "".join(("refused ", str(error)))
        found.append((" ".join((mnemonic, *(written(one) for one in operands))), said))
    json.dump({"samples": found, "rows": len(X86_INSTRS)}, sys.stdout)


def main() -> int:
    if sys.argv[1] == "--samples":
        samples(Path(sys.argv[2]))
        return 0
    run, root = sys.argv[1], Path(sys.argv[2])
    taken = json.loads(subprocess.run([sys.executable, __file__, "--samples", str(root)], capture_output=True,
                                      text=True, check=True).stdout)
    lines = [line for line, _ in taken["samples"]]
    expected = [want for _, want in taken["samples"]]
    found = subprocess.run([run, "--target", "x86_64-linux-none"], input="\n".join(lines) + "\n",
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
