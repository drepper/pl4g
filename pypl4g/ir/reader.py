"""Reading the textual form of the IR back into memory.

This exists so that the printer can be tested against a fixed point: printing a
module, reading it and printing it again must give the same text.  It is a
testing facility, not a serialization format -- it does not preserve source
spans, and a persistent form would be a packed binary one instead.
"""

from __future__ import annotations

from typing import Sequence

from .function import (DEFAULT_CCONV, BasicBlock, FuncAttrs, Function,
                       InlineHint, Linkage,
                       SpecialKind)
from .inst import (AddressInst, SyscallInst, AnyLaneInst, BinaryInst, BinOp, BlockTarget,
                   BrInst, CastInst, CastKind, SplatInst,
                   CmpInst, CmpPred, CondBrInst, LoadInst, MemStartInst,
                   Ordering, RetInst,
                   StoreInst, UnaryInst, UnOp, UnreachableInst)
from .module import GlobalVar, Module
from .printer import IR_VERSION
from .types import (BOOL, BUILTIN_TYPES, MEM, PtrType, Type, TypeContext, U64,
                    VecType, VOID)
from .value import Value


class IRSyntaxError(Exception):
    """The text is not a well-formed textual IR."""

    def __init__(self, line_number: int, detail: str) -> None:
        super().__init__("".join(("line ", str(line_number), ": ", detail)))
        self.line_number = line_number
        self.detail = detail


_BINOPS = {op.value: op for op in BinOp}
_UNOPS = {op.value: op for op in UnOp}
_PREDS = {p.value: p for p in CmpPred}
_CASTS = {k.value: k for k in CastKind}


def _ordered(text: str) -> tuple[str, Ordering]:
    """What is left of a load's or a store's suffix, and the ordering it named.

    The ordering is written before the type -- `load.acquire.u32` -- so that the
    suffix a reader already knows how to take apart still ends with the type.
    """
    for ordering in (Ordering.ACQUIRE, Ordering.RELEASE):
        head = "".join((ordering.value, "."))
        if text.startswith(head):
            return text[len(head):], ordering
    return text, Ordering.PLAIN


def _parse_type(text: str, types: TypeContext, line_number: int) -> Type:
    """Parse a type name."""
    text = text.strip()
    if text == "mem":
        return MEM
    if text.startswith("ptr<") and text.endswith(">"):
        inner = text[4:-1]
        mutable = inner.startswith("mut ")
        return types.ptr_type(_parse_type(inner.removeprefix("mut "), types, line_number),
                              mutable=mutable)
    lanes_at = text.rfind("\N{MULTIPLICATION SIGN}")
    if lanes_at > 0:
        count = text[lanes_at + 1:]
        if not count.isdigit():
            raise IRSyntaxError(line_number, "".join((
                "'", text, "' says '", count, "' lanes")))
        return types.vec_type(_parse_type(text[:lanes_at], types, line_number),
                              int(count))
    found = BUILTIN_TYPES.get(text)
    if found is None:
        raise IRSyntaxError(line_number, "".join(("unknown type '", text, "'")))
    return found


def _split_opcode(body: str) -> tuple[str, str]:
    """Separate an instruction's opcode from its operands.

    The opcode carries a type, and a type may have a space inside it --
    ``ptr<mut u8>`` is one -- so where the opcode ends is the first space
    outside any brackets and not simply the first space.
    """
    depth = 0
    for index, ch in enumerate(body):
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth -= 1
        elif ch == " " and depth == 0:
            return body[:index], body[index + 1:]
    return body, ""


def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split on *sep*, ignoring separators inside parentheses or angle brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in text:
        if ch in "(<[":
            depth += 1
        elif ch in ")>]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


class _FunctionReader:
    """Reads one function from the lines that make it up."""

    def __init__(self, module: Module, header: str, lines: Sequence[tuple[int, str]]) -> None:
        self._module = module
        self._header = header
        self._lines = lines
        self._values: dict[int, Value] = {}
        self._blocks: dict[str, BasicBlock] = {}

    def read(self) -> Function:
        """Parse the function and return it."""
        func = self._parse_header()
        self._collect_blocks(func)
        self._parse_body()
        return func

    def _parse_header(self) -> Function:
        """Parse the ``fn @name(...) -> ...`` line."""
        text = self._header.removeprefix("fn @").removesuffix("{").strip()
        open_paren = text.index("(")
        close_paren = text.index(")")
        name = text[:open_paren].strip()
        param_text = text[open_paren + 1:close_paren].strip()
        params = tuple(_parse_type(p, self._module.types, 0)
                       for p in _split_top(param_text)) if param_text else ()
        rest = text[close_paren + 1:].strip().removeprefix("\N{RIGHTWARDS ARROW}").strip()
        words = rest.split()
        ret = _parse_type(words[0], self._module.types, 0)
        attrs = FuncAttrs()
        linkage = Linkage.INTERNAL
        cconv = DEFAULT_CCONV
        special: SpecialKind | None = None
        priority: int | None = None
        abi: str | None = None
        impure = False
        for word in words[1:]:
            if word == "impure":
                impure = True
            elif word in (l.value for l in Linkage):
                linkage = Linkage(word)
            elif word.startswith("cconv("):
                cconv = word[len("cconv("):-1]
            elif word.startswith("special("):
                special = SpecialKind(word[len("special("):-1])
            elif word.startswith("priority("):
                priority = int(word[len("priority("):-1])
            elif word.startswith("abi("):
                abi = word[len("abi("):-1]
        attrs = FuncAttrs(special=special, priority=priority, inline=InlineHint.DEFAULT,
                          abi=abi, impure=impure)
        func = Function(name=name, ty=self._module.types.func_type(params, ret),
                        attrs=attrs, linkage=linkage, cconv=cconv)
        return func

    def _collect_blocks(self, func: Function) -> None:
        """First pass: create every block and register its parameters.

        The numbers of the parameters are written in the text, so a branch may
        refer to a block defined further down without any fixing up.
        """
        for number, line in self._lines:
            if not line.endswith(":") or line.startswith(" "):
                continue
            head = line[:-1]
            if "(" in head:
                label = head[:head.index("(")]
                param_text = head[head.index("(") + 1:head.rindex(")")]
            else:
                label = head
                param_text = ""
            block = func.add_block(label)
            self._blocks[label] = block
            if not param_text:
                continue
            for item in _split_top(param_text):
                name, _, type_text = item.partition(":")
                param = block.add_param(_parse_type(type_text, self._module.types, number))
                self._values[int(name.strip().removeprefix("%"))] = param

    def _parse_body(self) -> None:
        """Second pass: parse the instructions of every block."""
        current: BasicBlock | None = None
        for number, line in self._lines:
            if line.endswith(":") and not line.startswith(" "):
                label = line[:-1].split("(")[0]
                current = self._blocks[label]
                continue
            body = line.strip()
            if not body:
                continue
            if current is None:
                raise IRSyntaxError(number, "instruction outside a block")
            self._parse_inst(current, body, number)

    def _parse_inst(self, block: BasicBlock, body: str, number: int) -> None:
        """Parse one instruction line and append it to *block*."""
        comment = body.find(";")
        hint: str | None = None
        if comment >= 0:
            trailer = body[comment + 1:].strip()
            body = body[:comment].strip()
            if trailer.startswith("name="):
                hint = trailer[len("name="):]
        result: int | None = None
        if body.startswith("%"):
            name, _, body = body.partition("=")
            result = int(name.strip().removeprefix("%"))
            body = body.strip()
        opcode, rest = _split_opcode(body)
        inst = self._build(block, opcode, rest.strip(), number)
        if hint is not None:
            inst.name_hint = hint
        if result is not None:
            self._values[result] = inst

    def _value(self, text: str, ty: Type, number: int) -> Value:
        """Resolve one operand against its expected type."""
        text = text.strip()
        if text.startswith("%"):
            found = self._values.get(int(text[1:]))
            if found is None:
                raise IRSyntaxError(number, "".join(("undefined value '", text, "'")))
            return found
        if text.startswith("@"):
            found_global = self._module.globals.get(text[1:])
            if found_global is None:
                raise IRSyntaxError(number, "".join(("undefined global '", text, "'")))
            return found_global
        if text == "true" or text == "false":
            return self._module.bool_const(BOOL, text == "true")
        from .types import IntType
        if isinstance(ty, IntType):
            return self._module.int_const(ty, int(text, 0))
        raise IRSyntaxError(number, "".join(("cannot read '", text, "' as ", ty.render())))

    def _target(self, text: str, number: int) -> BlockTarget:
        """Parse a branch destination and its arguments."""
        text = text.strip()
        if "(" not in text:
            return BlockTarget(self._blocks[text])
        label = text[:text.index("(")]
        block = self._blocks[label]
        arg_text = text[text.index("(") + 1:text.rindex(")")]
        args = [self._value(a, block.params[i].ty, number)
                for i, a in enumerate(_split_top(arg_text))] if arg_text.strip() else []
        return BlockTarget(block, args)

    def _build(self, block: BasicBlock, opcode: str, rest: str, number: int):  # noqa: ANN202
        """Build the instruction named by *opcode* and append it to *block*."""
        if opcode == "unreachable":
            return block.append(UnreachableInst())
        if opcode == "mem.start":
            return block.append(MemStartInst())
        if opcode.startswith("store."):
            written, ordering = _ordered(opcode[len("store."):])
            ty = _parse_type(written, self._module.types, number)
            parts = _split_top(rest)
            token = self._value(parts[0], MEM, number)
            address = self._value(parts[1], self._module.types.ptr_type(ty), number)
            stored = self._value(parts[2], ty, number)
            return block.append(StoreInst(token, address, stored,
                                          ordering=ordering))
        if opcode.startswith("load."):
            read, ordering = _ordered(opcode[len("load."):])
            ty = _parse_type(read, self._module.types, number)
            parts = _split_top(rest)
            token = self._value(parts[0], MEM, number)
            address = self._value(parts[1], self._module.types.ptr_type(ty), number)
            return block.append(LoadInst(ty, (token, address),
                                         ordering=ordering))
        if opcode == "ret.void":
            return block.append(RetInst())
        if opcode.startswith("ret."):
            ty = _parse_type(opcode[4:], self._module.types, number)
            return block.append(RetInst(self._value(rest, ty, number)))
        if opcode == "br":
            return block.append(BrInst(self._target(rest, number)))
        if opcode == "condbr":
            parts = _split_top(rest)
            cond = self._value(parts[0], BOOL, number)
            return block.append(CondBrInst(cond, self._target(parts[1], number),
                                           self._target(parts[2], number)))
        head, _, type_text = opcode.rpartition(".")
        ty = _parse_type(type_text, self._module.types, number)
        parts = _split_top(rest)
        if head == "address":
            return block.append(AddressInst(self._value(parts[0], ty, number)))
        if head == "syscall":
            # Everything in one is a machine word, so each operand is read at
            # the width the answer is: the checker widened them before this.
            given = [self._value(one, ty, number) for one in parts]
            return block.append(SyscallInst(given[0], given[1:], ty))
        if head in _BINOPS:
            lhs = self._value(parts[0], ty, number)
            # What moves an address is a number of bytes, not another address,
            # so the right operand of this one is read as what it says it is.
            rhs = self._value(parts[1], U64 if isinstance(ty, PtrType) else ty,
                              number)
            return block.append(BinaryInst(_BINOPS[head], lhs, rhs))
        if head in _UNOPS:
            return block.append(UnaryInst(_UNOPS[head], self._value(parts[0], ty, number)))
        if head in _CASTS:
            return block.append(CastInst(_CASTS[head], self._value(parts[0], ty, number), ty))
        if head == "splat":
            if not isinstance(ty, VecType):
                raise IRSyntaxError(number, "".join((
                    "spreading a value over '", ty.render(), "'")))
            return block.append(SplatInst(self._value(parts[0], ty.element, number), ty))
        if head == "anylane":
            return block.append(AnyLaneInst(self._value(parts[0], None, number), ty))
        if head.startswith("icmp."):
            pred = _PREDS.get(head[len("icmp."):])
            if pred is None:
                raise IRSyntaxError(number, "".join(("unknown predicate in '", opcode, "'")))
            lhs = self._value(parts[0], ty, number)
            rhs = self._value(parts[1], ty, number)
            # A comparison of vectors answers a lane apiece; of anything else,
            # one truth value.
            answer = (self._module.types.vec_type(BOOL, ty.lanes)
                      if isinstance(ty, VecType) else BOOL)
            return block.append(CmpInst(pred, lhs, rhs, answer))
        raise IRSyntaxError(number, "".join(("unknown instruction '", opcode, "'")))


def read_module(text: str) -> Module:
    """Parse the textual form of a module."""
    lines = [(i + 1, line.rstrip()) for i, line in enumerate(text.splitlines())]
    if not lines or not lines[0][1].startswith("; pl4g-ir "):
        raise IRSyntaxError(1, "missing '; pl4g-ir' version line")
    version = int(lines[0][1][len("; pl4g-ir "):])
    if version != IR_VERSION:
        raise IRSyntaxError(1, "".join(("textual IR version ", str(version),
                                        " cannot be read by this compiler")))
    module: Module | None = None
    index = 1
    while index < len(lines):
        number, line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            index += 1
            continue
        if stripped.startswith("module "):
            name = stripped.split('"')[1]
            triple = stripped.split('"')[3]
            module = Module(name=name, triple=triple)
            index += 1
            continue
        if stripped.startswith("let "):
            if module is None:
                raise IRSyntaxError(number, "a variable before the module line")
            _read_global(module, stripped, number)
            index += 1
            continue
        if stripped.startswith("fn @"):
            if module is None:
                raise IRSyntaxError(number, "function before the module line")
            body: list[tuple[int, str]] = []
            header = stripped
            index += 1
            if header.endswith("{"):
                while index < len(lines) and lines[index][1].strip() != "}":
                    body.append((lines[index][0], lines[index][1]))
                    index += 1
                index += 1
            module.add_function(_FunctionReader(module, header, body).read())
            continue
        raise IRSyntaxError(number, "".join(("unexpected line '", stripped, "'")))
    if module is None:
        raise IRSyntaxError(1, "no module line")
    _rebuild_caches(module)
    return module


def _read_global(module: Module, line: str, number: int) -> None:
    """Parse one ``let @name: type linkage = value`` line."""
    head, _, initializer = line.removeprefix("let @").partition("=")
    name, _, rest = head.strip().partition(":")
    words = rest.split()
    mutable = words[0] == "mut"
    if mutable:
        words = words[1:]
    ty = _parse_type(words[0], module.types, number)
    linkage = Linkage(words[1]) if len(words) > 1 else Linkage.INTERNAL
    from .types import IntType

    text = initializer.strip()
    value = module.int_const(ty, int(text, 0)) if isinstance(ty, IntType) else None
    module.add_global(GlobalVar(name.strip(), ty,
                                module.types.ptr_type(ty, mutable=mutable), value,
                                linkage))


def _rebuild_caches(module: Module) -> None:
    """Refill the module's special-function caches from the function attributes."""
    for func in module.functions.values():
        match func.attrs.special:
            case SpecialKind.STARTUP:
                module.startup = func
            case SpecialKind.CONSTRUCTOR:
                module.ctors.append(func)
            case SpecialKind.DESTRUCTOR:
                module.dtors.append(func)
            case SpecialKind.TEST_ALWAYS | SpecialKind.TEST_BUILD | SpecialKind.TEST_SUITE:
                module.tests.append(func)
            case _:
                pass
