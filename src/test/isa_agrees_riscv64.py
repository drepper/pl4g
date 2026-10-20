"""Check that the C++ back end reads a RISC-V ISA string as pypl4g reads it.

Run as ``isa_agrees_riscv64.py PL4G-ISADUMP ROOT``.  The strings are the ones
``tests/compiler/test_riscv_isa.py``, ``test_riscv_attributes.py`` and
``test_mclevel.py`` hand pypl4g, every extension it knows on its own and after
the general-purpose set, and every profile name; each is read by pypl4g and by
``pl4g-isadump``, and the width, the base, every extension with its version,
the normalized form, the attributes section made from it and what
``--mclevel`` makes of it -- or the reason it is refused -- must be the same.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

#: The strings the tests write, the ones they refuse among them.
WRITTEN = [
    "rv64i", "rv32i", "rv64e", "RV64GC_Zba", "rv64gc_zba", "rv64g", "rv64ib", "rv64id", "rv64i_zfa",
    "rv64i2p1_m2p0_zfa1p0", "rv64i2", "rv64gc_sv39_zic64b", "rv64i_m_a_f_d", "rv64imafd", "rv64gc_zbazbb",
    "rv64gc_zfaa", "rv64", "rv64c", "rv64i_m_m", "rva23", "rva23u64", "rva23s64", "rva99u64", "rv64i_zba_c_m",
    "rv64idfam", "rv64im", "rv64ia", "rv64imc", "rv64gc", "rv64iq", "rv64gh", "rv64gcv", "rv64gc_zfa",
    "rv64gc_zba_zbb_zbs", "rv64gc_zvbb", "rv64gc_zfhmin", "rv64gc_zvfhmin", "rv64gc_zvfh", "rv64gc_zcb_zcmop",
    "rv64gc_zkt_zbc", "rv64gc_zicbom_zicboz", "rv64gc_zawrs_za64rs", "rv64im_zba", "rv64i_zicsr",
    "rv64i_zicntr_zihpm", "rv64imafdc_zicond_zimop", "rv32imc", "rv32gc", "rv32gcv", "rva23u32", "RV64GC_Zba_Zbb",
    "v2", "v1", "rv64i_", "rv64i__m", "rv64i2p", "rv64ip1", "rv64i_x", "rv64i_xfoo", "rv64e_zfa", "rv128i",
    "rva22u64", "rva23u", "rva23s", "rvb23u64", "rv64gc_zba2p0_zbb", "rv64g_zicsr2p0", "rv64imafdcv_zvl256b",
    "rv64i2p1p3", "rv64gc_zfa1", "rv64gc_zfa1p", "rv64gc_sv391p0", "rv64i_zba_", "RVA23U64", "rva23u64_zfa",
    "rv64gzfa", "rv64i9999999999", "rv64m", "rv32e", "rv64ge", "rv64i_zve64d_zvl1024b",
]


def samples(root: Path) -> None:
    """Write each string and what pypl4g makes of it, as JSON.

    In a process of its own, as the encoding comparison does.
    """
    sys.path.insert(0, str(root))
    from pypl4g.target.riscv64 import attributes, isa
    from pypl4g.target.riscv64.target import RISCV64Target

    def read(text: str) -> str:
        try:
            found = isa.parse(text)
        except isa.BadName as exc:
            return "".join(("refused ", str(exc)))
        parts = [str(found.bits), "1" if found.embedded else "0", found.named]
        parts.extend(name if version is None else "".join((name, "=", str(version[0]), "p", str(version[1])))
                     for name, version in found.versions.items())
        normalized = found.normalized()
        parts.extend((normalized, attributes.build(normalized).hex()))
        return " ".join(parts)

    def level(text: str) -> str:
        try:
            RISCV64Target().use_mclevel(text)
        except ValueError as exc:
            return "".join(("refused ", str(exc)))
        return "ok"

    strings = [*WRITTEN]
    for name in sorted(isa.KNOWN):
        strings.extend(("".join(("rv64i_", name)), "".join(("rv64g_", name)), "".join(("rv32i_", name))))
        if len(name) == 1:
            strings.append("".join(("rv64i", name)))
    found = [(text, "".join((read(text), "\t", level(text)))) for text in strings]
    json.dump(found, sys.stdout)


def main() -> int:
    if sys.argv[1] == "--samples":
        samples(Path(sys.argv[2]))
        return 0
    run, root = sys.argv[1], Path(sys.argv[2])
    taken = json.loads(subprocess.run([sys.executable, __file__, "--samples", str(root)], capture_output=True,
                                      text=True, check=True).stdout)
    lines = [text for text, _ in taken]
    found = subprocess.run([run], input="\n".join(lines) + "\n", capture_output=True, text=True,
                           check=False).stdout.splitlines()
    failures = 0
    for (line, want), got in zip(taken, found + [""] * (len(taken) - len(found))):
        if want != got:
            failures += 1
            print("".join((repr(line), "\n  pypl4g: ", want, "\n  C++:    ", got)))
    print("".join((str(len(taken) - failures), " of ", str(len(taken)), " ISA strings read alike")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
