"""Write the IR pypl4g makes of a program as JSON, which the C++ side reads.

Run as ``irjson.py [--text=OUT.ir] [--before=OUT.json] [PYPL4G-OPTION...] SOURCE.pl4g -o OUT.json``:
the command line is pypl4g's own, run with ``--emit ir``, and what would have
been the IR's text is this instead -- and with ``--text``, the text as well, in
a file of its own, for comparing with what the C++ side prints of the JSON; and
with ``--before``, the module as the checker left it, before any pass ran, for
the C++ passes to be held to what pypl4g's made of it.  The text leaves out what it does not need to show a reader --
which function a call calls, what a constant holds, how large a generator's
frame came to -- and an interpreter needs all of it.

Nothing in pypl4g is changed: its `main` runs as it always does, with the one
function that renders the module put aside for this one.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypl4g.ir import inst as I  # noqa: E402
from pypl4g.ir import layout as L  # noqa: E402
from pypl4g.ir import types as T  # noqa: E402
from pypl4g.ir import value as V  # noqa: E402
from pypl4g.ir.function import BasicBlock, Function  # noqa: E402
from pypl4g.ir.module import GlobalVar, Module  # noqa: E402


class Writer:
    """Turns one module into JSON, types and functions referred to by name or
    number so that a cycle -- a record reaching itself -- is written once."""

    def __init__(self, module: Module) -> None:
        self.module = module
        self.types: list[dict[str, Any]] = []
        self.type_ids: dict[int, int] = {}
        self.keep: list[object] = []
        #: The constants made of their own, numbered as they are met.
        self.fresh: dict[int, int] = {}
        #: What each function is filed under, which is what a call names it by:
        #: a name may be overloaded, a key is the module's own and is not.  A
        #: function a call reaches that the module's table does not hold -- two
        #: overloads of an operator filed under one key, the second replacing the
        #: first -- is filed under a key of its own, as the back end reaches it
        #: through the call.
        self.functions: list[tuple[str, Function]] = list(module.functions.items())
        self.keys: dict[int, str] = {id(f): key for key, f in self.functions}
        at = 0
        while at < len(self.functions):
            for block in self.functions[at][1].blocks:
                for one in block.insts:
                    for found in (getattr(one, "callee", None), *one.operands):
                        if isinstance(found, Function) and id(found) not in self.keys:
                            key = "".join((found.name, "#", str(len(self.functions))))
                            self.keys[id(found)] = key
                            self.functions.append((key, found))
            at += 1

    # -- types -----------------------------------------------------------------

    def unit(self, unit: T.Unit) -> Any:
        return {"powers": [[n, e] for n, e in unit.powers],
                "scale": [unit.scale.numerator, unit.scale.denominator]}

    def type(self, ty: T.Type | None) -> int | None:
        """The number of *ty*, written down the first time it is met."""
        if ty is None:
            return None
        found = self.type_ids.get(id(ty))
        if found is not None:
            return found
        number = len(self.types)
        self.type_ids[id(ty)] = number
        self.keep.append(ty)
        entry: dict[str, Any] = {}
        self.types.append(entry)
        entry.update(self._describe(ty))
        self.types[number]["layout"] = self._layout(ty)
        made = self._made(ty)
        if made is not None:
            self.types[number]["made"] = made
        return number

    def _made(self, ty: T.Type) -> str | None:
        """Where an object of a structural type came from: the module's
        context, which holds one per structure; the cache of pointers
        `parts_of` answers with; or neither.  Equal objects from two of these
        are two objects, and an `is` tells them apart."""
        c = self.module.types
        key: object
        match ty:
            case T.PtrType():
                if T._ELEMENT_POINTERS.get(ty.pointee) is ty:
                    return "element"
                table, key = c._pointers, (ty.pointee, ty.mutable, ty.lasting)
            case T.ResultType():
                table, key = c._results, (ty.ok, ty.err)
            case T.TupleType():
                table, key = c._tuples, ty.members
            case T.ArrayType():
                table, key = c._arrays, (ty.element, ty.shape)
            case T.VecType():
                table, key = c._vectors, (ty.element, ty.lanes)
            case T.ListType():
                table, key = c._lists, ty.element
            case T.CursorType():
                table, key = c._cursors, ty.element
            case T.SetType():
                table, key = c._sets, (ty.element, ty.mutable)
            case T.DictType():
                table, key = c._dicts, (ty.key, ty.value, ty.mutable)
            case T.FuncType():
                table, key = c._functions, (ty.params, ty.ret, ty.listable, ty.hands_out, ty.lasting, ty.impure,
                                            ty.can_ignore, ty.borrows, ty.cconv)
            case T.IterType():
                table, key = c._iterators, ty.element
            case T.RunningType():
                table, key = c._running, (ty.element, ty.given, ty.lasting)
            case _:
                return None
        return "context" if table.get(key) is ty else "stray"

    def _layout(self, ty: T.Type) -> Any:
        """How *ty* is laid out on a target of eight-byte pointers, for the
        C++ side to check its own layout against; nothing where there is none."""
        layout = L.DataLayout(pointer_size=8)
        try:
            found: dict[str, Any] = {"size": L.size_of(ty, layout), "align": L.align_of(ty, layout),
                                     "parts": list(L.part_offsets_of(ty, layout)),
                                     "leaves": list(L.leaf_offsets_of(ty, layout))}
            if isinstance(ty, T.ProductType):
                found["offsets"] = list(L.offsets_of(ty, layout))
            elif isinstance(ty, T.TupleType):
                found["offsets"] = list(L.member_offsets_of(ty, layout))
            return found
        except (L.NoLayoutError, AssertionError, KeyError, TypeError, ValueError):
            return None

    def _describe(self, ty: T.Type) -> dict[str, Any]:
        match ty:
            case T.VoidType():
                return {"k": "void"}
            case T.BoolType():
                return {"k": "bool"}
            case T.CharType():
                return {"k": "char"}
            case T.SyntaxType():
                return {"k": "syntax"}
            case T.MemType():
                return {"k": "mem"}
            case T.ErrorType():
                return {"k": "error"}
            case T.StrType():
                return {"k": "str"}
            case T.IntType():
                return {"k": "int", "bits": ty.bits, "signed": ty.signed, "unit": self.unit(ty.unit)}
            case T.FloatType():
                return {"k": "float", "bits": ty.bits, "unit": self.unit(ty.unit)}
            case T.PtrType():
                return {"k": "ptr", "to": self.type(ty.pointee), "mut": ty.mutable, "lasting": ty.lasting}
            case T.CoroutineFrame():
                return {"k": "frame", "name": ty.name, "size": L._FRAME_SIZES.get(id(ty))}
            case T.RunningType():
                return {"k": "running", "element": self.type(ty.element), "given": self.type(ty.given),
                        "lasting": ty.lasting}
            case T.IterType():
                return {"k": "iter", "element": self.type(ty.element)}
            case T.FuncType():
                return {"k": "func", "params": [self.type(p) for p in ty.params], "ret": self.type(ty.ret),
                        "listable": ty.listable, "hands_out": ty.hands_out, "lasting": ty.lasting,
                        "impure": ty.impure, "can_ignore": ty.can_ignore, "cconv": ty.cconv,
                        "borrows": [[leaf, {"params": list(b.params),
                                            "leaves": [None if one is None else list(one) for one in b.leaves],
                                            "outer": [list(o) for o in b.outer]}]
                                    for leaf, b in ty.borrows]}
            case T.OpaqueType():
                return {"k": "opaque", "name": ty.name}
            case T.ProductType():
                # The fields after the entry exists, so a field naming the record
                # finds its number.
                entry: dict[str, Any] = {"k": "product", "name": ty.name, "origin": ty.origin, "abi": ty.abi,
                                         "unique": ty.unique, "device": ty.device,
                                         "mutable_fields": sorted(ty.mutable_fields), "closed_by": ty.closed_by}
                self.types[self.type_ids[id(ty)]] = entry
                entry["fields"] = [[n, self.type(t)] for n, t in ty.fields]
                entry["args"] = [self.type(a) for a in ty.args]
                return entry
            case T.ResultType():
                return {"k": "result", "ok": self.type(ty.ok), "err": self.type(ty.err)}
            case T.TupleType():
                return {"k": "tuple", "members": [self.type(m) for m in ty.members]}
            case T.ArrayType():
                return {"k": "array", "element": self.type(ty.element), "shape": list(ty.shape)}
            case T.VecType():
                return {"k": "vec", "element": self.type(ty.element), "lanes": ty.lanes}
            case T.ListType():
                return {"k": "list", "element": self.type(ty.element)}
            case T.CursorType():
                return {"k": "cursor", "element": self.type(ty.element)}
            case T.SetType():
                return {"k": "set", "element": self.type(ty.element), "mut": ty.mutable}
            case T.DictType():
                return {"k": "dict", "key": self.type(ty.key), "value": self.type(ty.value), "mut": ty.mutable}
            case T.EnumType():
                return {"k": "enum", "members": list(ty.members), "values": list(ty.values),
                        "holder": self.type(ty.holder), "flag": ty.flag, "name": ty.name, "origin": ty.origin}
            case T.SumType():
                entry = {"k": "sum", "name": ty.name, "origin": ty.origin}
                self.types[self.type_ids[id(ty)]] = entry
                entry["variants"] = [[n, self.type(t)] for n, t in ty.variants]
                entry["args"] = [self.type(a) for a in ty.args]
                return entry
        raise TypeError(repr(ty))

    # -- values ----------------------------------------------------------------

    def const(self, value: object) -> Any:
        """A constant, written out whole -- and, where it is one pypl4g made of its
        own rather than the module's interned one, numbered, so that the reader
        tells it from an equal one by identity as pypl4g does."""
        found = self.whole_const(value)
        if found is not None and not self.interned(value):
            number = self.fresh.get(id(value))
            if number is None:
                number = self.fresh[id(value)] = len(self.fresh)
                self.keep.append(value)
            found["fresh"] = number
        return found

    def interned(self, value: object) -> bool:
        """Whether *value* is the constant the module answers for what it is."""
        module = self.module
        match value:
            case V.IntConst():
                ty = value.ty
                return module._int_consts.get((ty.bits, ty.signed, ty.unit, value.value)) is value
            case V.CharConst():
                return module._char_consts.get(value.value) is value
            case V.FloatConst():
                key = (value.ty.bits, value.ty.unit, struct.pack("<d", value.value))
                return module._float_consts.get(key) is value
            case V.BoolConst():
                return module._bool_consts.get(value.value) is value
            case V.EnumConst():
                return module._enum_consts.get((id(value.ty), value.index)) is value
            case V.UndefConst():
                return False
        return True

    def whole_const(self, value: object) -> Any:
        """A constant, written out whole."""
        match value:
            case None:
                return None
            case V.IntConst():
                return {"c": "int", "t": self.type(value.ty), "v": str(value.value)}
            case V.CharConst():
                return {"c": "char", "t": self.type(value.ty), "v": value.value}
            case V.FloatConst():
                return {"c": "float", "t": self.type(value.ty),
                        "bits": format(struct.unpack("<Q", struct.pack("<d", value.value))[0], "x")}
            case V.BoolConst():
                return {"c": "bool", "t": self.type(value.ty), "v": value.value}
            case V.EnumConst():
                return {"c": "enum", "t": self.type(value.ty), "index": value.index}
            case V.ArrayConst():
                return {"c": "array", "t": self.type(value.ty), "elements": [self.const(e) for e in value.elements]}
            case V.RecordConst():
                return {"c": "record", "t": self.type(value.ty), "fields": [self.const(f) for f in value.fields]}
            case V.ResultConst():
                return {"c": "result", "t": self.type(value.ty), "answer": self.const(value.answer),
                        "failed": value.failed}
            case V.SumConst():
                return {"c": "sum", "t": self.type(value.ty), "index": value.index, "part": self.const(value.part)}
            case V.AddressConst():
                target = value.target
                if isinstance(target, GlobalVar):
                    named: Any = {"g": target.name}
                elif isinstance(target, Function):
                    named = {"f": self.keys.get(id(target), target.name)}
                elif target is None:
                    named = None
                else:
                    raise TypeError(repr(target))
                # Whether it is one of the module's addresses of the ring, whose
                # offset the back end puts in by identity once the layout is known.
                ring = any(value is one for one in self.module.ring_addresses)
                return {"c": "address", "t": self.type(value.ty), "target": named, "offset": value.offset,
                        "ring": ring}
            case V.PartsConst():
                return {"c": "parts", "t": self.type(value.ty), "parts": [self.const(p) for p in value.parts]}
            case V.UndefConst():
                return {"c": "undef", "t": self.type(value.ty)}
        raise TypeError(repr(value))

    def operand(self, value: object, numbers: dict[int, int]) -> Any:
        """A value an instruction uses: a number within the function, a global,
        a function, or a constant."""
        if isinstance(value, GlobalVar):
            return {"g": value.name}
        if isinstance(value, Function):
            return {"f": self.keys.get(id(value), value.name)}
        if isinstance(value, V.Const):
            return self.const(value)
        found = numbers.get(id(value))
        if found is None:
            raise ValueError("".join(("a value with no number: ", repr(value))))
        return {"v": found}

    def function_name(self, callee: object) -> Any:
        if isinstance(callee, Function):
            return self.keys.get(id(callee), callee.name)
        return None if callee is None else str(callee)

    def target(self, target: I.BlockTarget, labels: dict[int, int], numbers: dict[int, int]) -> Any:
        return {"block": labels[id(target.block)], "args": [self.operand(a, numbers) for a in target.args]}

    def instruction(self, inst: I.Instruction, labels: dict[int, int], numbers: dict[int, int]) -> dict[str, Any]:
        out: dict[str, Any] = {"op": type(inst).__name__, "t": self.type(inst.ty),
                               "operands": [self.operand(o, numbers) for o in inst.operands]}
        if inst.has_result:
            out["id"] = numbers[id(inst)]
        if inst.name_hint is not None:
            out["name"] = inst.name_hint
        if inst.span.is_valid:
            out["span"] = [inst.span.start, inst.span.end]
        match inst:
            case I.BinaryInst() | I.UnaryInst():
                out["kind"] = inst.op.value
            case I.CmpInst():
                out["kind"] = inst.pred.value
            case I.CastInst():
                out["kind"] = inst.kind.value
            case I.ExtractInst():
                out["index"] = inst.index
            case I.FieldInst():
                out["index"] = inst.field
            case I.SumMakeInst() | I.SumGetInst():
                out["index"] = inst.variant
            case I.CallInst():
                out["callee"] = self.function_name(inst.callee)
                out["cconv"] = inst.cconv
                if inst.answer_first:
                    out["answer_first"] = True
            case I.CodeInst():
                out["callee"] = self.function_name(inst.callee)
            case I.FrameInst() | I.SizeOfInst():
                out["held"] = self.type(inst.held)
            case I.AssertInst():
                out["what"] = inst.what
                out["status"] = inst.status
                out["observing"] = inst.observing
            case I.LoadInst() | I.StoreInst():
                out["ordering"] = inst.ordering.value
            case I.BrInst():
                out["targets"] = [self.target(inst.target, labels, numbers)]
            case I.CondBrInst():
                out["targets"] = [self.target(inst.true_target, labels, numbers),
                                  self.target(inst.false_target, labels, numbers)]
            case I.SwitchInst():
                out["targets"] = [*(self.target(t, labels, numbers) for _, t in inst.cases),
                                  self.target(inst.default, labels, numbers)]
                out["cases"] = [str(v) for v, _ in inst.cases]
            case I.YieldInst():
                out["targets"] = [self.target(inst.target, labels, numbers)]
                out["delegates"] = inst.delegates
        return out

    def function(self, func: Function) -> dict[str, Any]:
        numbers: dict[int, int] = {}
        counter = 0
        labels: dict[int, int] = {id(b): at for at, b in enumerate(func.blocks)}
        for block in func.blocks:
            for param in block.params:
                numbers[id(param)] = counter
                counter += 1
            for one in block.insts:
                if one.has_result:
                    numbers[id(one)] = counter
                    counter += 1
        attrs = func.attrs
        out: dict[str, Any] = {
            "name": func.name, "t": self.type(func.ty), "linkage": func.linkage.value, "cconv": func.cconv,
            "attrs": {"special": None if attrs.special is None else attrs.special.value,
                      "priority": attrs.priority, "inline": attrs.inline.value, "abi": attrs.abi,
                      "external": attrs.external, "host": attrs.host, "builtin": attrs.builtin, "can_ignore": attrs.can_ignore,
                      "impure": attrs.impure, "io": attrs.io, "opaque": attrs.opaque,
                      "answer_in_storage": attrs.answer_in_storage,
                      "answer_first": attrs.answer_first, "chained": attrs.chained,
                      "listable": attrs.listable},
            "answering": None if func.answering is None else func.answering.value,
            "source_path": func.source_path, "module": func.module, "exported": func.exported,
            "param_names": list(func.param_names),
            "blocks": [self.block(b, labels, numbers) for b in func.blocks],
        }
        if func.span.is_valid:
            out["span"] = [func.span.start, func.span.end]
        if func.name_span.is_valid:
            out["name_span"] = [func.name_span.start, func.name_span.end]
        out["answer_from"] = None if func.answer_from is None else list(func.answer_from)
        frame = func.attrs.extra.get("coroutine")
        out["coroutine"] = self.type(frame) if isinstance(frame, T.CoroutineFrame) else None
        return out

    def block(self, block: BasicBlock, labels: dict[int, int], numbers: dict[int, int]) -> dict[str, Any]:
        return {"label": block.label,
                "params": [{"id": numbers[id(p)], "t": self.type(p.ty),
                            **({"name": p.name_hint} if p.name_hint is not None else {})}
                           for p in block.params],
                "insts": [self.instruction(i, labels, numbers) for i in block.insts]}

    def global_var(self, var: GlobalVar) -> dict[str, Any]:
        return {"name": var.name, "t": self.type(var.value_type), "pt": self.type(var.ty),
                "linkage": var.linkage.value, "init": self.const(var.initializer) if var.initializer is not None
                else None, "module": var.module, "exported": var.exported, "system_layout": var.system_layout,
                "relro": var.relro}

    def run(self) -> dict[str, Any]:
        module = self.module

        def name(func: Function | None) -> str | None:
            return None if func is None else self.keys.get(id(func), func.name)

        out: dict[str, Any] = {
            "format": "pl4g-ir-json 1", "name": module.name, "triple": module.triple,
            "stack_size": module.stack_size, "guard_size": module.guard_size, "output": module.output.value,
            "globals": [self.global_var(v) for v in module.globals.values()],
            "global_keys": list(module.globals),
            "texts": [self.global_var(v) for v in module.texts.values()],
            "functions": [self.function(f) for _, f in self.functions],
            "function_keys": [key for key, _ in self.functions],
            "listed": len(module.functions),
            "startup": name(module.startup), "build": name(module.build),
            "environ": None if module.environ is None else module.environ.name,
            "auxv": None if module.auxv is None else module.auxv.name,
            "ring_addresses": [self.const(c) for c in module.ring_addresses],
            "ctors": [name(f) for f in module.ctors], "dtors": [name(f) for f in module.dtors],
            "tests": [name(f) for f in module.tests], "test_plan": [name(f) for f in module.test_plan],
            "source_paths": list(module.source_paths),
        }
        out["types"] = self.types
        return out


#: Every file pypl4g read, in the order it read them: what its spans are
#: offsets into, so that the C++ side can read them back as places.
FILES: list[tuple[str, str]] = []


def render(module: Module) -> str:
    """The module as JSON, in place of the IR's text."""
    written = Writer(module).run()
    written["files"] = [{"path": path, "text": text} for path, text in FILES]
    return json.dumps(written, ensure_ascii=False, separators=(",", ":")) + "\n"


def main() -> int:
    import pypl4g.driver.main as driver
    from pypl4g.source import manager
    adding = manager.SourceManager.add

    def kept(self: manager.SourceManager, path: Path, text: str) -> manager.SourceFile:
        FILES.append((str(path), text))
        return adding(self, path, text)

    manager.SourceManager.add = kept  # type: ignore[method-assign]
    args = sys.argv[1:]
    text: Path | None = None
    before: Path | None = None
    while args and args[0].startswith(("--text=", "--before=")):
        one = args.pop(0)
        if one.startswith("--text="):
            text = Path(one[len("--text="):])
        else:
            before = Path(one[len("--before="):])
    original = driver.render_module
    if before is not None:
        from pypl4g.opt import pass_
        running = pass_.PassManager.run

        def first_written(self: pass_.PassManager, module: Module) -> None:
            before.write_text(render(module), encoding="utf-8")
            running(self, module)

        pass_.PassManager.run = first_written  # type: ignore[method-assign]

    def both(module: Module) -> str:
        if text is not None:
            text.write_text(original(module), encoding="utf-8")
        return render(module)

    driver.render_module = both  # type: ignore[assignment]
    return driver.main(["--emit", "ir", *args])


if __name__ == "__main__":
    sys.exit(main())
