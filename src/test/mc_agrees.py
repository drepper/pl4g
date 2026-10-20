"""Check that the C++ machine-code layer and ELF writer make what pypl4g's make.

Run as ``mc_agrees.py PL4G-MCDUMP [COUNT] [SEED]``.  COUNT programs of
builder calls are made up at random -- functions of a few blocks with loops,
calls and more values than registers, data with fixups -- and each is run
through the assembler builder of both sides with the same small made-up
target (`Fake` here, `src/tools/mcdump.cc` there): the register allocator,
the frame, the streamer, the layout, the debugging dump and the ELF image.
What the two print must be the same, byte for byte.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypl4g.elf.writer import ImageSettings, write_image  # noqa: E402
from pypl4g.mc import ops as O  # noqa: E402
from pypl4g.mc.asmbuilder import Assembler  # noqa: E402
from pypl4g.mc.desc import InstDesc, InstFlags, OperandRole  # noqa: E402
from pypl4g.mc.dump import dump_sections  # noqa: E402
from pypl4g.mc.fixup import ABS64, PCREL32, MCFixup, apply_little_endian  # noqa: E402
from pypl4g.mc.inst import MCInst  # noqa: E402
from pypl4g.mc.operand import MCImm, MCMem, MCReg, MCSymRef, SymExpr  # noqa: E402
from pypl4g.mc.ops import Condition  # noqa: E402
from pypl4g.mc.reg import RegisterInfo  # noqa: E402
from pypl4g.mc.streamer import MCStreamer  # noqa: E402
from pypl4g.mc.symbol import SymBinding, SymKind  # noqa: E402

_USE, _DEF, _BOTH = OperandRole.USE, OperandRole.DEF, OperandRole.DEF_USE
_OPS = {"plus": O.PLUS, "minus": O.MINUS, "times": O.TIMES, "and": O.AND, "or": O.OR,
        "xor": O.XOR, "compare": O.COMPARE}
_MNEMONICS = {"plus": "add", "minus": "sub", "times": "mul", "and": "and", "or": "or",
              "xor": "xor", "compare": "cmp"}


class Fake:
    """A target of six integer registers, three floating-point ones, a stack
    pointer and a link register, whose instructions encode as their text."""

    def __init__(self) -> None:
        info = RegisterInfo()
        self.info = info
        gpr = info.add_class("gpr", 64)
        fpr = info.add_class("fpr", 64)
        special = info.add_class("special", 64, allocatable=False)
        self.gpr, self.fpr = gpr, fpr
        for at in range(6):
            unit = info.add_unit(gpr, at, "".join(("r", str(at))))
            info.add_view(unit, "".join(("r", str(at))), 64)
            info.add_view(unit, "".join(("r", str(at), "d")), 32)
            info.add_view(unit, "".join(("r", str(at), "b")), 8)
        for at in range(3):
            unit = info.add_unit(fpr, 16 + at, "".join(("f", str(at))))
            info.add_view(unit, "".join(("f", str(at))), 64)
            info.add_view(unit, "".join(("f", str(at), "s")), 32)
        info.add_view(info.add_unit(special, 30, "sp"), "sp", 64)
        info.add_view(info.add_unit(special, 31, "lr"), "lr", 64)
        reg = info.registers
        self.sp, self.lr = reg["sp"], reg["lr"]
        self.descs: dict[str, InstDesc] = {
            "mov": InstDesc("mov", flags=InstFlags.MOVE, roles=(_DEF, _USE)),
            "add": InstDesc("add", roles=(_DEF, _USE, _USE)),
            "sub": InstDesc("sub", roles=(_DEF, _USE, _USE)),
            "mul": InstDesc("mul", roles=(_DEF, _USE, _USE), implicit_defs=(reg["r2"],),
                            implicit_uses=(reg["r0"],)),
            "and": InstDesc("and", roles=(_DEF, _USE, _USE)),
            "or": InstDesc("or", roles=(_BOTH, _USE, _USE)),
            "xor": InstDesc("xor", roles=(_DEF, _USE, _USE)),
            "cmp": InstDesc("cmp", roles=(_USE, _USE)),
            "jmp": InstDesc("jmp", flags=InstFlags.BARRIER | InstFlags.TERMINATOR,
                            roles=(_USE,)),
            "call": InstDesc("call", flags=InstFlags.CALL, roles=(_USE,)),
            "ret": InstDesc("ret", flags=InstFlags.RETURN | InstFlags.BARRIER
                            | InstFlags.TERMINATOR),
            "st": InstDesc("st", flags=InstFlags.MAY_STORE, roles=(_USE, _USE)),
            "ld": InstDesc("ld", flags=InstFlags.MAY_LOAD, roles=(_DEF, _USE)),
            "lea": InstDesc("lea", roles=(_DEF, _USE)),
        }
        for cond in Condition:
            name = "".join(("b.", cond.value))
            self.descs[name] = InstDesc(name, flags=InstFlags.TERMINATOR, roles=(_USE,))

    def _inst(self, name: str, *operands: object, span: object = None) -> MCInst:
        from pypl4g.source.location import INVALID_SPAN
        return MCInst(desc=self.descs[name], operands=tuple(operands),  # type: ignore[arg-type]
                      span=INVALID_SPAN if span is None else span)  # type: ignore[arg-type]

    # -- the selector ----------------------------------------------------------

    def select_move(self, dst, src, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("mov", MCReg(dst), src)]

    def select_op(self, op, dst, sources, span):  # noqa: ANN001, ANN201, D102
        name = _MNEMONICS[op.name]
        if dst is None:
            return [self._inst(name, *sources)]
        return [self._inst(name, MCReg(dst), *sources)]

    def select_store(self, address, value, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("st", address, value)]

    def select_call(self, target, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("call", target)]

    def select_return(self, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("ret")]

    def select_jump(self, target, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("jmp", target)]

    def select_branch(self, cond, lhs, rhs, target, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("cmp", lhs, rhs), self._inst("".join(("b.", cond.value)), target)]

    def select_address(self, dst, symbol, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("lea", MCReg(dst), symbol)]

    def select_spill(self, slot, source, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("st", MCMem(base=self.sp, disp=slot, size_bits=source.bits),
                           MCReg(source))]

    def select_reload(self, destination, slot, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("ld", MCReg(destination),
                           MCMem(base=self.sp, disp=slot, size_bits=destination.bits))]

    def select_frame(self, size, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("sub", MCReg(self.sp), MCReg(self.sp), MCImm(size, 32))]

    def select_unframe(self, size, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("add", MCReg(self.sp), MCReg(self.sp), MCImm(size, 32))]

    def link_slot_size(self) -> int:  # noqa: D102
        return 8

    def frame_walk(self, total, size, link):  # noqa: ANN001, ANN201, D102
        return (size if link else None, total)

    def select_save_link(self, offset, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("st", MCMem(base=self.sp, disp=offset, size_bits=64), MCReg(self.lr))]

    def select_restore_link(self, offset, span):  # noqa: ANN001, ANN201, D102
        return [self._inst("ld", MCReg(self.lr), MCMem(base=self.sp, disp=offset, size_bits=64))]

    # -- the encoder -----------------------------------------------------------

    def encode(self, inst: MCInst) -> tuple[bytes, list[MCFixup]]:
        """The text of the instruction, its length first, and four bytes for
        each symbol it refers to, which a fixup fills in."""
        text = inst.render().encode("utf-8")
        out = bytearray((len(text) & 0xFF,))
        out += text
        fixups: list[MCFixup] = []
        for operand in inst.operands:
            if isinstance(operand, MCSymRef):
                fixups.append(MCFixup(offset=len(out), kind=PCREL32, target=operand.expr))
                out += bytes(4)
        return bytes(out), fixups


def _units(fake: Fake, names: list[str]) -> list[object]:
    return [fake.info.units[name] for name in names]


def run_python(program: dict[str, Any]) -> str:
    """What pypl4g's layer makes of *program*, in the form mcdump prints; what
    stopped it, where something did."""
    try:
        return _run_python(program)
    except Exception as exc:  # noqa: BLE001 - the message is the answer
        return "".join(("error ", str(exc), "\n"))


def _run_python(program: dict[str, Any]) -> str:
    """What pypl4g's layer makes of *program*."""
    fake = Fake()
    streamer = MCStreamer(encode=fake.encode)
    order = program["order"]
    allocation = ({name: _units(fake, units) for name, units in order.items()}
                  if isinstance(order, dict) else _units(fake, order))
    asm = Assembler(fake, streamer, registers=fake.info, allocation_order=allocation,  # type: ignore[arg-type]
                    callee_saved=frozenset(_units(fake, program["callee_saved"])))  # type: ignore[arg-type]
    virtuals: list[object] = []

    def reg(name: str) -> object:
        if name.startswith("v"):
            return virtuals[int(name[1:])]
        return fake.info.registers[name]

    def operand(given: dict[str, Any]) -> object:
        if "reg" in given:
            return MCReg(reg(given["reg"]), given.get("bits"))
        if "imm" in given:
            return MCImm(given["imm"], given.get("bits", 32), given.get("signed", True))
        if "sym" in given:
            return asm.symref(given["sym"])
        mem = given["mem"]
        return MCMem(base=reg(mem["base"]) if mem.get("base") else None,
                     index=reg(mem["index"]) if mem.get("index") else None,
                     scale=mem.get("scale", 1), disp=mem.get("disp", 0),
                     size_bits=mem.get("size_bits"))

    symbols: dict[str, object] = {}
    for step in program["steps"]:
        kind = step[0]
        if kind == "section":
            extra = step[5] if len(step) > 5 else {}
            asm.section(step[1], executable=step[2], writable=step[3], alignment=step[4],
                        alloc=extra.get("alloc", True), sh_type=extra.get("sh_type", 1),
                        sh_link_to=extra.get("sh_link_to", ""),
                        sh_entsize=extra.get("sh_entsize", 0))
        elif kind == "virtual":
            hint = fake.info.registers[step[3]] if step[3] else None
            cls = fake.gpr if step[1] == "gpr" else fake.fpr
            virtuals.append(fake.info.new_virtual(cls, step[2], hint, step[4]))
        elif kind == "begin":
            override = step[4]
            asm.begin_function(step[1], exported=step[2], padding=step[3],
                               allocation_order=_units(fake, override) if override else None)
        elif kind == "block":
            asm.block(step[1])
        elif kind == "move":
            asm.loadreg(reg(step[1]), operand(step[2]))
        elif kind == "op":
            asm.op(_OPS[step[1]], reg(step[2]) if step[2] else None,
                   *(operand(one) for one in step[3]))
        elif kind == "store":
            asm.store(operand(step[1]), operand(step[2]))  # type: ignore[arg-type]
        elif kind == "branch":
            asm.branch(Condition(step[1]), operand(step[2]), operand(step[3]), step[4])
        elif kind == "jump":
            asm.jump(step[1])
        elif kind == "falls":
            asm.falls_through(step[1])
        elif kind == "call":
            asm.call(step[1], clobbers=[reg(one) for one in step[2]],
                     reads=[reg(one) for one in step[3]])
        elif kind == "address":
            asm.address(reg(step[1]), step[2])
        elif kind == "ret":
            asm.ret(reads=[reg(one) for one in step[1]])
        elif kind == "end":
            asm.end_function()
        elif kind == "label":
            symbols[step[1]] = asm.label(step[1], binding=SymBinding(step[2]),
                                         kind=SymKind(step[3]))
        elif kind == "end_label":
            asm.end_label(symbols[step[1]])  # type: ignore[arg-type]
        elif kind == "align":
            asm.align(step[1])
        elif kind == "bytes":
            fixups = [MCFixup(offset=one[0], kind=ABS64 if one[1] == "abs64" else PCREL32,
                              target=SymExpr(streamer.symbol(one[2])))
                      for one in step[2]]
            asm.bytes(bytes.fromhex(step[1]), fixups)
    out: list[str] = []
    sections = list(streamer.sections.values())
    symbols_all = list(streamer.symbols.values())
    try:
        image, _ = write_image(
            ImageSettings(machine=62, base_vaddr=0x400000, page_size=0x1000,
                          entry_symbol="_start", stack_size=0x100000),
            sections, symbols_all, ["t.pl4g"], apply_little_endian)
        elf = image.hex()
    except Exception as exc:  # noqa: BLE001 - the message is the answer
        elf = "".join(("error ", type(exc).__name__, ": ", str(exc)))
    out.append(dump_sections(sections, symbols_all))
    for index, assignment in enumerate(asm.assignments):
        out.append("".join((
            "assignment ", str(index), " coalesced=", str(assignment.coalesced),
            " forwarded=", str(assignment.forwarded), " spilled=[",
            ",".join("".join((str(k), ":", str(v))) for k, v in assignment.spilled.items()),
            "] units=[",
            ",".join("".join((str(k), ":", v.canonical)) for k, v in assignment.units.items()),
            "]")))
    for function in asm.functions:
        out.append("".join(("function ", function.name, " frame=", str(function.frame.size),
                            " return_at=", str(function.return_at),
                            " caller_at=", str(function.caller_at))))
    out.append("".join(("elf ", elf)))
    return "\n".join(out) + "\n"


# -- making up programs -----------------------------------------------------------

def make_program(rng: random.Random) -> dict[str, Any]:
    """A program of builder calls: a few functions with loops, calls and more
    values than there are registers, and some data."""
    gprs = ["".join(("r", str(at))) for at in range(6)]
    steps: list[list[Any]] = [["section", ".text", True, False, 16]]
    virtuals = 0
    names = ["_start", "helper"] + ["".join(("f", str(at))) for at in range(rng.randrange(0, 2))]
    for fname in names:
        override = None
        if rng.random() < 0.2:
            override = rng.sample(gprs, rng.randrange(3, 7))
        steps.append(["begin", fname, fname == "_start", rng.choice((0, 0, 8)), override])
        blocks = rng.randrange(1, 6)
        labels = ["".join((".L", fname, "_entry"))] + ["".join((fname, ".b", str(at)))
                                                        for at in range(1, blocks)]
        defined: list[str] = []
        for at in range(blocks):
            if at > 0:
                steps.append(["block", labels[at]])
            for _ in range(rng.randrange(2, 9)):
                choice = rng.random()
                if choice < 0.35 or len(defined) < 2:
                    hint = rng.choice(gprs) if rng.random() < 0.2 else None
                    bits = rng.choice((64, 64, 32))
                    steps.append(["virtual", "gpr", bits, hint, rng.random() < 0.95])
                    name = "".join(("v", str(virtuals)))
                    virtuals += 1
                    steps.append(["move", name, {"imm": rng.randrange(-5, 100), "bits": 32}])
                    defined.append(name)
                elif choice < 0.6:
                    steps.append(["virtual", "gpr", 64, None, True])
                    name = "".join(("v", str(virtuals)))
                    virtuals += 1
                    op = rng.choice(("plus", "minus", "times", "and", "or", "xor"))
                    steps.append(["op", op, name, [{"reg": rng.choice(defined)},
                                                   {"reg": rng.choice(defined)}]])
                    defined.append(name)
                elif choice < 0.7:
                    mem = {"base": rng.choice(defined), "disp": rng.randrange(-64, 64, 8),
                           "size_bits": rng.choice((64, 32, None))}
                    if rng.random() < 0.3:
                        mem["index"] = rng.choice(defined)
                        mem["scale"] = rng.choice((1, 2, 4, 8))
                    steps.append(["store", {"mem": mem},
                                  {"reg": rng.choice(defined), "bits": rng.choice((None, 32))}])
                elif choice < 0.8:
                    steps.append(["op", "compare", None, [{"reg": rng.choice(defined)},
                                                          {"imm": 3, "bits": 32}]])
                elif choice < 0.85 and blocks > 1:
                    # A branch before the end of the block, which goes on after
                    # it: what the target reads has to live up to the branch.
                    steps.append(["branch", rng.choice([c.value for c in Condition]),
                                  {"reg": rng.choice(defined)}, {"imm": 7, "bits": 32},
                                  labels[rng.randrange(1, blocks)]])
                elif choice < 0.9 and fname != "helper":
                    steps.append(["move", "r0", {"reg": rng.choice(defined)}])
                    steps.append(["call", "helper", ["r0", "r1", "r2", "r3"], ["r0"]])
                    steps.append(["virtual", "gpr", 64, None, True])
                    name = "".join(("v", str(virtuals)))
                    virtuals += 1
                    steps.append(["move", name, {"reg": "r0"}])
                    defined.append(name)
                else:
                    steps.append(["virtual", "gpr", 64, None, True])
                    name = "".join(("v", str(virtuals)))
                    virtuals += 1
                    steps.append(["address", name, rng.choice(("table", "message", "helper"))])
                    defined.append(name)
            if at + 1 < blocks:
                how = rng.random()
                target = labels[rng.randrange(1, blocks)]
                if how < 0.4:
                    steps.append(["branch", rng.choice([c.value for c in Condition]),
                                  {"reg": rng.choice(defined)}, {"imm": 7, "bits": 32}, target])
                    steps.append(["falls", labels[at + 1]])
                elif how < 0.55 and target != labels[at + 1]:
                    steps.append(["jump", target])
                else:
                    steps.append(["falls", labels[at + 1]])
        steps.append(["move", "r0", {"reg": rng.choice(defined)}])
        steps.append(["ret", ["r0"]])
        steps.append(["end"])
    steps.append(["section", ".data", False, True, 8])
    steps.append(["label", "table", "global", "object"])
    steps.append(["bytes", "00" * 16, [[0, "abs64", "_start"], [8, "abs64", "message"]]])
    steps.append(["end_label", "table"])
    steps.append(["section", ".rodata", False, False, 1])
    steps.append(["align", 4])
    steps.append(["label", "message", "local", "object"])
    steps.append(["bytes", b"hello, world".hex(), []])
    steps.append(["end_label", "message"])
    if rng.random() < 0.5:
        steps.append(["section", ".pl4g.notes", False, False, 4,
                      {"alloc": False, "sh_type": 1, "sh_link_to": ".text", "sh_entsize": 4}])
        steps.append(["bytes", "01020304", []])
    if rng.random() < 0.5:
        steps.append(["section", ".data", False, True, 8])
        steps.append(["label", "spare", rng.choice(("weak", "global", "local")),
                      rng.choice(("object", "notype"))])
        steps.append(["bytes", "ff" * 8, [[0, "abs64", "table"]]])
    order: object = gprs
    if rng.random() < 0.5:
        order = {"gpr": gprs, "fpr": ["f0", "f1", "f2"]}
    return {"steps": steps, "order": order,
            "callee_saved": rng.sample(gprs, rng.randrange(0, 3))}


def main() -> int:
    dump = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    rng = random.Random(seed)
    failures = 0
    with tempfile.TemporaryDirectory() as work:
        for index in range(count):
            program = make_program(rng)
            path = Path(work) / "".join(("p", str(index), ".json"))
            path.write_text(json.dumps(program), encoding="utf-8")
            theirs = run_python(program)
            mine = subprocess.run([dump, str(path)], capture_output=True, text=True, check=False,
                                  env={**os.environ})
            if mine.returncode != 0 or mine.stdout != theirs:
                failures += 1
                a, b = theirs.splitlines(), mine.stdout.splitlines()
                at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                keep = Path(os.environ.get("MC_AGREES_KEEP", work)) / path.name
                if keep != path:
                    keep.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                print("".join(("program ", str(index), " (", str(keep), "): line ", str(at + 1),
                               "\n  pypl4g: ", a[at][:300] if at < len(a) else "<end>",
                               "\n  C++:    ", b[at][:300] if at < len(b) else "<end>",
                               "\n", mine.stderr[-500:])))
                if failures >= 5:
                    break
    print("".join((str(count - failures) if failures < 5 else "?", " of ", str(count), " agree")))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
