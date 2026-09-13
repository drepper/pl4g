"""The textual form of the IR.

The form is canonical and deterministic: values are numbered by position, so the
same module always prints the same text and a golden file never depends on an
object address or on the order of a dictionary.  It is the form ``--emit=ir``
writes and the form ``reader`` reads back.
"""

from typing import Sequence

from .function import BasicBlock, Function
from .inst import (BinaryInst, BlockTarget, BrInst, CastInst, CmpInst, CondBrInst,
                   Instruction, LoadInst, MemStartInst, RetInst, StoreInst,
                   SwitchInst, Terminator, UnaryInst, UnreachableInst)
from .module import Module
from .types import VOID
from .value import BoolConst, FloatConst, IntConst, UndefConst, Value

#: Bumped whenever the textual form changes, so that a stale golden file is
#: rejected loudly instead of being misread.
IR_VERSION = 1


class Numbering:
    """Assigns ``%N`` numbers to the values of one function, by position."""

    def __init__(self, func: Function) -> None:
        self._numbers: dict[int, int] = {}
        counter = 0
        for block in func.blocks:
            for param in block.params:
                self._numbers[id(param)] = counter
                counter += 1
            for inst in block.insts:
                if inst.has_result:
                    self._numbers[id(inst)] = counter
                    counter += 1

    def name_of(self, value: Value) -> str:
        """The textual name of *value*."""
        number = self._numbers.get(id(value))
        if number is None:
            return "%?"
        return "".join(("%", str(number)))


def render_operand(value: Value, numbers: Numbering) -> str:
    """Render one operand: a literal for a constant, ``@name`` for a global."""
    from .module import GlobalVar

    if isinstance(value, GlobalVar):
        return "".join(("@", value.name))
    if isinstance(value, IntConst):
        return str(value.value)
    if isinstance(value, BoolConst):
        return "true" if value.value else "false"
    if isinstance(value, FloatConst):
        # Written the way Python writes a float, which reads back as the same
        # number: a dump that rounded would be a dump that could not be trusted.
        return repr(value.value)
    if isinstance(value, UndefConst):
        return "undef"
    return numbers.name_of(value)


def _render_target(target: BlockTarget, numbers: Numbering) -> str:
    """Render a branch destination and the arguments it supplies."""
    block = target.block
    label = block.label if isinstance(block, BasicBlock) else "?"
    if not target.args:
        return label
    args = ", ".join(render_operand(a, numbers) for a in target.args)
    return "".join((label, "(", args, ")"))


def _render_inst(inst: Instruction, numbers: Numbering) -> str:
    """Render one instruction, without its indentation or trailing comment."""
    operands = ", ".join(render_operand(o, numbers) for o in inst.operands)
    match inst:
        case RetInst():
            if not inst.operands:
                return "ret.void"
            return "".join(("ret.", inst.operands[0].ty.render(), " ", operands))
        case BrInst():
            return "".join(("br ", _render_target(inst.target, numbers)))
        case CondBrInst():
            return "".join(("condbr ", operands, ", ",
                            _render_target(inst.true_target, numbers), ", ",
                            _render_target(inst.false_target, numbers)))
        case UnreachableInst():
            return "unreachable"
        case SwitchInst():
            cases = ", ".join("".join((str(v), " \N{RIGHTWARDS ARROW} ",
                                       _render_target(t, numbers)))
                              for v, t in inst.cases)
            return "".join(("switch ", operands, ", default ",
                            _render_target(inst.default, numbers), " [", cases, "]"))
        case CmpInst():
            suffix = inst.operands[0].ty.render() if inst.operands else "void"
            return "".join((inst.opcode, ".", suffix, " ", operands))
        case MemStartInst():
            return inst.opcode
        case LoadInst():
            return "".join((inst.opcode, ".", inst.ty.render(), " ", operands))
        case StoreInst():
            stored = inst.operands[2].ty.render() if len(inst.operands) > 2 else "void"
            return "".join((inst.opcode, ".", stored, " ", operands))
        case BinaryInst() | UnaryInst() | CastInst():
            return "".join((inst.opcode, ".", inst.ty.render(), " ", operands))
        case _:
            suffix = "" if inst.ty is VOID else "".join((".", inst.ty.render()))
            return "".join((inst.opcode, suffix, " ", operands)).rstrip()


def _render_block_header(block: BasicBlock, numbers: Numbering) -> str:
    """Render the label line of a block, with its parameters."""
    if not block.params:
        return "".join((block.label, ":"))
    params = ", ".join("".join((numbers.name_of(p), ": ", p.ty.render()))
                       for p in block.params)
    return "".join((block.label, "(", params, "):"))


def render_function(func: Function, out: list[str]) -> None:
    """Append the textual form of *func* to *out*."""
    numbers = Numbering(func)
    head: list[str] = ["fn @", func.name, "("]
    head.append(", ".join(p.render() for p in func.ty.params))
    head.append(") \N{RIGHTWARDS ARROW} ")
    head.append(func.ty.ret.render())
    head.append("".join((" ", func.linkage.value)))
    head.append("".join((" cconv(", func.cconv, ")")))
    if func.attrs.special is not None:
        head.append("".join((" special(", func.attrs.special.value, ")")))
    if func.attrs.priority is not None:
        head.append("".join((" priority(", str(func.attrs.priority), ")")))
    if func.attrs.abi is not None:
        head.append("".join((" abi(", func.attrs.abi, ")")))
    if func.is_declaration:
        out.append("".join(head))
        return
    head.append(" {")
    out.append("".join(head))
    for block in func.blocks:
        out.append(_render_block_header(block, numbers))
        for inst in block.insts:
            line = ["  "]
            if inst.has_result:
                line.append("".join((numbers.name_of(inst), " = ")))
            line.append(_render_inst(inst, numbers))
            if inst.name_hint is not None:
                line.append("".join(("    ; name=", inst.name_hint)))
            out.append("".join(line))
    out.append("}")


def render_global(var: object, out: list[str]) -> None:
    """Append the textual form of one global variable to *out*.

    An initializer is rendered the same way a constant operand is, so that a
    truth value reads as `true` rather than as "not a number, so undefined" --
    which is what it said before there was anywhere for a `bool` to be read.
    """
    from .module import GlobalVar
    from .value import (BoolConst as _BoolConst, FloatConst as _FloatConst,
                        IntConst as _IntConst)

    assert isinstance(var, GlobalVar)
    initializer = var.initializer
    if isinstance(initializer, _IntConst):
        text = str(initializer.value)
    elif isinstance(initializer, _BoolConst):
        text = "true" if initializer.value else "false"
    elif isinstance(initializer, _FloatConst):
        text = repr(initializer.value)
    else:
        text = "undef"
    out.append("".join(("let @", var.name, ": ", "mut " if var.mutable else "",
                        var.value_type.render(), " ", var.linkage.value, " = ", text)))


def render_module(module: Module) -> str:
    """Return the textual form of *module*."""
    out: list[str] = ["".join(("; pl4g-ir ", str(IR_VERSION)))]
    out.append("".join(("module \"", module.name, "\" triple \"", module.triple, "\"")))
    if module.globals:
        out.append("")
        for var in module.globals.values():
            render_global(var, out)
    for func in module.functions.values():
        out.append("")
        render_function(func, out)
    out.append("")
    return "\n".join(out)


def terminators_of(blocks: Sequence[BasicBlock]) -> list[Terminator | None]:
    """The terminator of each block, for the verifier's convenience."""
    return [b.terminator for b in blocks]
