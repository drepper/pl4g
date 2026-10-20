"""Check that every row of the C++ RISC-V encoding table encodes as pypl4g's.

Run as ``encoding_agrees_riscv64.py PL4G-ENCDUMP ROOT``.  The samples are the
ones ``tests/compiler/test_encoding_riscv64.py`` holds pypl4g's table to the
GNU assembler with, one per row of the table, and the immediates that test
refuses; each is encoded by pypl4g and by ``pl4g-encdump``, and the bytes and
the fixups they leave, or the refusal, must be the same.  So must every fixup
the same test stores: the jump and the branch, whose offsets are scattered
through their words, and the two halves of an address, at values in range and
out of it.
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
    from pypl4g.mc.fixup import MCFixup, fixup_value
    from pypl4g.mc.inst import MCInst
    from pypl4g.mc.operand import ConstExpr, MCImm, MCReg, MCSymRef, SymExpr
    from pypl4g.mc.symbol import MCSymbol
    from pypl4g.target.riscv64 import fixups
    from pypl4g.target.riscv64.encoder import encode
    from pypl4g.target.riscv64.opcodes import RISCV_INSTRS
    from pypl4g.target.riscv64.regs import reg
    from test_encoding_riscv64 import JAL_CASES, SAMPLES

    table = InstrTable(RISCV_INSTRS)

    def written(operand: object) -> str:
        if isinstance(operand, MCReg):
            return "".join(("r:", operand.reg.name, "" if operand.bits is None else ":" + str(operand.bits)))
        if isinstance(operand, MCImm):
            return "".join(("i:", str(operand.value), ":", str(operand.bits), ":", "1" if operand.signed else "0"))
        assert isinstance(operand, MCSymRef)
        return "".join(("s:", operand.expr.symbol.name))

    found = []

    def encoded(mnemonic: str, *operands: object) -> None:
        line = " ".join((mnemonic, *(written(one) for one in operands)))
        try:
            data, made = encode(MCInst(table.select(mnemonic, operands), operands))  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 -- the refusal is what is compared
            found.append((line, "".join(("refused ", str(exc)))))
            return
        found.append((line, "".join((data.hex(), *("".join((" ", f.kind.name, "@", str(f.offset))) for f in made)))))

    def stored(kind: object, value: int, data: bytes) -> None:
        line = " ".join(("".join(("@", kind.name)), str(value), data.hex()))  # type: ignore[attr-defined]
        patched = bytearray(data)
        fixup = MCFixup(offset=0, kind=kind, target=SymExpr(MCSymbol("s")))  # type: ignore[arg-type]
        try:
            fixups.apply_fixup(patched, 0, fixup, value)
        except Exception as exc:  # noqa: BLE001 -- the refusal is what is compared
            found.append((line, "".join(("refused ", str(exc)))))
            return
        found.append((line, bytes(patched).hex()))

    for sample in SAMPLES:
        encoded(sample[1], *sample[2:])
    a0, a1 = MCReg(reg("a0")), MCReg(reg("a1"))
    for value in (2047, -2048, 2048, -2049):
        encoded("li", a0, MCImm(value, 12 if -2048 <= value <= 2047 else 32))
        encoded("sb", a0, a1, MCImm(value, 12 if -2048 <= value <= 2047 else 32))
    encoded("addi", a0, a1, MCImm(-1, 12))
    encoded("sd", a0, a1, MCImm(-8, 12))
    encoded("mv", MCReg(reg("x10")), MCReg(reg("x11")))
    jal = encode(MCInst(table.select("jal", (MCSymRef(SymExpr(MCSymbol("s"))),)),
                        (MCSymRef(SymExpr(MCSymbol("s"))),)))[0]
    for offset, _ in JAL_CASES:
        stored(fixups.JAL, offset, jal)
    for offset in (0x100, 1 << 21, 1, -(1 << 20), (1 << 20) - 2, -(1 << 20) - 2):
        stored(fixups.JAL, offset, jal)
    for offset in (-4, 0, 4094, -4096, 4096, -4098, 3):
        stored(fixups.BRANCH, offset, bytes(4))
    # The address pair the linker test links, at its own values and at the ends
    # of what each half reaches.
    pc, target = 0x400FFC, 0x411008
    high = MCFixup(offset=0, kind=fixups.PCREL_HI20, target=ConstExpr(target))
    low = MCFixup(offset=0, kind=fixups.PCREL_LO12_I, target=ConstExpr(target),
                  base_adjust=-fixups.PCREL_PAIR_DISTANCE)
    auipc = encode(MCInst(table.select("auipc.hi20", (a0, MCSymRef(SymExpr(MCSymbol("s"))))),
                          (a0, MCSymRef(SymExpr(MCSymbol("s"))))))[0]
    addi = encode(MCInst(table.select("addi.lo12", (a0, a0, MCSymRef(SymExpr(MCSymbol("s"))))),
                         (a0, a0, MCSymRef(SymExpr(MCSymbol("s"))))))[0]
    stored(fixups.PCREL_HI20, fixup_value(high, pc), auipc)
    stored(fixups.PCREL_LO12_I, fixup_value(low, pc + 4), addi)
    for value in (0, 0x7FF, 0x800, -0x800, -0x801, (1 << 31) - 0x801, (1 << 31) - 0x800, -(1 << 31) - 0x800,
                  -(1 << 31) - 0x801):
        stored(fixups.PCREL_HI20, value, auipc)
        stored(fixups.PCREL_LO12_I, value, addi)
        stored(fixups.PCREL_LO12_S, value, bytes(4))
    for value in (0, 0x1000, -8, (1 << 31) - 0x801, -(1 << 31) - 0x801):
        stored(fixups.CALL, value, bytes.fromhex("9700000067800000"))
    json.dump({"samples": found, "rows": len(RISCV_INSTRS)}, sys.stdout)


def main() -> int:
    if sys.argv[1] == "--samples":
        samples(Path(sys.argv[2]))
        return 0
    run, root = sys.argv[1], Path(sys.argv[2])
    taken = json.loads(subprocess.run([sys.executable, __file__, "--samples", str(root)], capture_output=True,
                                      text=True, check=True).stdout)
    lines = [line for line, _ in taken["samples"]]
    expected = [want for _, want in taken["samples"]]
    found = subprocess.run([run, "--target", "riscv64-linux-none"], input="\n".join(lines) + "\n",
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
