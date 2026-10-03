"""A generator's body split at its stops: a function resumed where it stopped.

The checker lowers a generator's body as a function of three pointers -- where
its frame is, where to write what it hands out, and where its arguments are --
with a `yield` instruction at each stop: the value written, and the block the
body goes on in.  This pass makes that a function that can be called again and
again, each call running from where the last stopped to the next stop:

- **a frame**: the first word is which stop it is at -- nought before the first
  call, and all ones once it has finished -- and after it, room for every value
  that crosses from one block to another and for every `frame` the body takes.
  The first is what a stop must keep: a value worked out before it and read
  after it.  Keeping every value read outside the block that made it is more
  than that, and is always enough: no value crosses a stop without crossing a
  block, a stop being the end of one.
- **a new first block**, which reads the stop number and branches -- nought to
  the body's own beginning, each stop to the block after it, anything else to
  the end -- with every value read in another block than its own written to its
  room where it is made and read from it at the head of each block that wants
  it.
- **each stop** becomes the stop's number written and one answered -- two
  where it hands the walk on with `yield ⇉`, whose delegation the body left
  in the frame's second word; each return becomes "finished" written and
  nought answered.

The memory chain is not carried across a stop: what a block after one reads,
it reads with the chain's beginning.  Nothing below this pass orders memory by
the chain -- the back ends keep the order the instructions are in -- and the
reads it adds are of the frame's own room.

How much room the frame takes is known only here, so it is written into the
`CoroutineFrame` type the walks' room was taken with, before anything is laid
out.  The pass runs at every level, since a function with a `yield` in it is
not one any back end can compile.
"""

from __future__ import annotations

from ...ir.function import BasicBlock, Function
from ...ir.builder import IRBuilder
from ...ir.inst import (BinOp, CastKind, FrameInst, MemStartInst, RetInst,
                        SizeOfInst, Terminator, YieldInst)
from ...ir.layout import DataLayout, align_of, measured, size_of
from ...ir.module import Module
from ...ir.reports import ReportKind
from ...ir.types import (MEM, U64, U8, CoroutineFrame, Type, held_in_memory,
                         parts_of)
from ...ir.value import BlockParam, Value

LAYOUT = DataLayout(pointer_size=8)

#: What the first word holds once the body has finished.
FINISHED = (1 << 64) - 1


def _round(value: int, to: int) -> int:
    return (value + to - 1) // to * to


class Coroutines:
    """Splits every generator body at its stops."""

    name = "coroutines"

    def run(self, module: Module) -> bool:
        """Split what has stops in it; report whether anything was."""
        changed = False
        for func in list(module.functions.values()):
            if func.is_declaration or not func.blocks:
                continue
            if any(isinstance(inst, YieldInst) for block in func.blocks
                   for inst in block.insts):
                self._split(module, func)
                changed = True
        # Every frame is measured now, so room asked for one -- a walk whose
        # description is in an arena -- is a number.
        for func in module.functions.values():
            numbers = {id(inst): module.int_const(U64, size_of(inst.held, LAYOUT))
                       for block in func.blocks for inst in block.insts
                       if isinstance(inst, SizeOfInst)}
            if not numbers:
                continue
            self._rename(func, numbers)
            for block in func.blocks:
                block.insts = [one for one in block.insts if id(one) not in numbers]
            changed = True
        return changed

    def _split(self, module: Module, func: Function) -> None:
        """Make *func* resumable."""
        old_entry = func.blocks[0]
        frame_param, out_param, args_param = old_entry.params
        entry = BasicBlock(label=func._unique("resume"), parent=func)  # noqa: SLF001
        frame_ptr, out_ptr, args_ptr = (entry.add_param(one.ty, one.name_hint)
                                        for one in (frame_param, out_param,
                                                    args_param))
        old_entry.params = []
        func.blocks.insert(0, entry)
        builder = IRBuilder(module, func)
        builder.position_at(entry)
        token = builder._append(MemStartInst())  # noqa: SLF001
        builder.set_memory(token)
        renamed: dict[int, Value] = {id(frame_param): frame_ptr,
                                     id(out_param): out_ptr,
                                     id(args_param): args_ptr}
        raw = builder.cast(CastKind.BITCAST, frame_ptr,
                           module.types.ptr_type(U8, mutable=True))
        # The first word is where the body stopped; the second, where it leaves
        # the walk's driver a delegation when it hands the walk on.
        offset = 16

        def place(at: int, ty: Type) -> Value:
            """Where in the frame *at* bytes in is, as a pointer to *ty*."""
            here = builder.binary(BinOp.ADD, raw, builder.int_const(U64, at))
            return builder.cast(CastKind.BITCAST, here,
                                module.types.ptr_type(ty, mutable=True))

        state = place(0, U64)
        # The body's own room, kept in the frame where it lasts across a stop.
        for block in func.blocks[1:]:
            for inst in list(block.insts):
                if isinstance(inst, FrameInst):
                    held = inst.held
                    offset = _round(offset, max(align_of(held, LAYOUT), 8))
                    found = builder.cast(CastKind.BITCAST,
                                         builder.binary(BinOp.ADD, raw,
                                                        builder.int_const(U64, offset)),
                                         inst.ty)
                    offset += size_of(held, LAYOUT)
                    renamed[id(inst)] = found
                    block.insts.remove(inst)
                elif isinstance(inst, MemStartInst):
                    renamed[id(inst)] = token
                    block.insts.remove(inst)
        self._rename(func, renamed)
        # Every value read outside the block that made it gets a room.
        home: dict[int, BasicBlock] = {}
        values: dict[int, Value] = {}
        for block in func.blocks[1:]:
            for param in block.params:
                if param.ty is MEM:
                    continue
                home[id(param)] = block
                values[id(param)] = param
            for inst in block.insts:
                if inst.has_result and inst.ty is not MEM:
                    home[id(inst)] = block
                    values[id(inst)] = inst
        wanted: dict[int, list[BasicBlock]] = {}
        for block in func.blocks[1:]:
            for inst in block.insts:
                used = list(inst.operands)
                for target in (inst.successors() if isinstance(inst, Terminator)
                               else ()):
                    used.extend(target.args)
                for one in used:
                    if id(one) in home and home[id(one)] is not block:
                        readers = wanted.setdefault(id(one), [])
                        if block not in readers:
                            readers.append(block)
        rooms: dict[int, tuple[int, list[Type]]] = {}
        for key in wanted:
            ty = values[key].ty
            words = self._words(module, ty)
            at = offset = _round(offset, 8)
            for word in words:
                offset = _round(offset, max(align_of(word, LAYOUT), 1))
                offset += size_of(word, LAYOUT)
            rooms[key] = (at, words)
        # Where each value is made, it is written to its room.
        for key, (at, words) in rooms.items():
            value = values[key]
            block = home[key]
            scratch = BasicBlock(label="scratch", parent=func)
            inner = IRBuilder(module, func)
            inner.position_at(scratch)
            inner.set_memory(token)
            self._put(module, inner, raw, at, words, value)
            if isinstance(value, BlockParam):
                block.insts[0:0] = scratch.insts
            else:
                index = block.insts.index(value) + 1
                block.insts[index:index] = scratch.insts
            for one in scratch.insts:
                one.parent = block
        # Where it is read in another block, it is read back at that block's head.
        for key, readers in wanted.items():
            at, words = rooms[key]
            value = values[key]
            for block in readers:
                scratch = BasicBlock(label="scratch", parent=func)
                inner = IRBuilder(module, func)
                inner.position_at(scratch)
                inner.set_memory(token)
                back = self._took(module, inner, raw, at, words, value.ty)
                block.insts[0:0] = scratch.insts
                for one in scratch.insts:
                    one.parent = block
                self._rename_in(block, {id(value): back},
                                skip=set(id(one) for one in scratch.insts))
        # The memory chain starts again after every stop.
        for block in func.blocks[1:]:
            made = {id(inst) for inst in block.insts}

            def foreign(one: Value) -> bool:
                return one.ty is MEM and id(one) not in made \
                    and not (isinstance(one, BlockParam) and one.block is block)

            for inst in block.insts:
                for index, one in enumerate(inst.operands):
                    if foreign(one):
                        inst.operands[index] = token
                for target in (inst.successors() if isinstance(inst, Terminator)
                               else ()):
                    for index, one in enumerate(target.args):
                        if foreign(one):
                            target.args[index] = token
        # The stops, and the end.
        stops: list[BasicBlock] = []
        for block in func.blocks[1:]:
            last = block.insts[-1] if block.insts else None
            if isinstance(last, YieldInst):
                stops.append(last.target.block)
                number = len(stops)
                block.insts.pop()
                tail = IRBuilder(module, func)
                tail.position_at(block)
                tail.set_memory(token)
                tail.store(state, tail.int_const(U64, number))
                tail.ret(tail.int_const(U8, 2 if last.delegates else 1))
            elif isinstance(last, RetInst):
                block.insts.pop()
                tail = IRBuilder(module, func)
                tail.position_at(block)
                tail.set_memory(token)
                tail.store(state, tail.int_const(U64, FINISHED))
                tail.ret(tail.int_const(U8, 0))
        done = func.add_block("finished")
        ending = IRBuilder(module, func)
        ending.position_at(done)
        ending.ret(ending.int_const(U8, 0))
        at = builder.load(state)
        builder.switch(at, [(0, old_entry),
                            *((number + 1, block) for number, block in enumerate(stops))],
                       done)
        size = _round(offset, 16)
        frame = func.attrs.extra.get("coroutine")
        if isinstance(frame, CoroutineFrame):
            measured(frame, size)
        module.reports.record(
            ReportKind.ITERATOR, func.name, "".join((
                "a generator split at its ", str(len(stops)), " stop",
                "" if len(stops) == 1 else "s", ": ", str(len(rooms)),
                " value", "" if len(rooms) == 1 else "s",
                " kept across them, in a frame of ", str(size), " bytes")),
            func.span)

    def _words(self, module: Module, ty: Type) -> list[Type]:
        """The words a value of *ty* is kept as: its parts, or where what it
        stands for is held in memory, where."""
        if held_in_memory(ty):
            return [module.types.ptr_type(U8, mutable=True)]
        return list(parts_of(ty))

    def _put(self, module: Module, builder: IRBuilder, raw: Value, at: int,
             words: list[Type], value: Value) -> None:
        """Write *value* into its room, word by word."""
        if held_in_memory(value.ty):
            pieces = [builder.cast(CastKind.BITCAST, value, words[0])]
        elif len(words) == 1:
            pieces = [value]
        else:
            pieces = [builder.extract(value, index, word)
                      for index, word in enumerate(words)]
        offset = at
        for piece, word in zip(pieces, words):
            offset = _round(offset, max(align_of(word, LAYOUT), 1))
            here = builder.cast(CastKind.BITCAST,
                                builder.binary(BinOp.ADD, raw,
                                               builder.int_const(U64, offset)),
                                module.types.ptr_type(word, mutable=True))
            builder.store(here, piece)
            offset += size_of(word, LAYOUT)

    def _took(self, module: Module, builder: IRBuilder, raw: Value, at: int,
              words: list[Type], ty: Type) -> Value:
        """A value read back out of its room."""
        pieces: list[Value] = []
        offset = at
        for word in words:
            offset = _round(offset, max(align_of(word, LAYOUT), 1))
            here = builder.cast(CastKind.BITCAST,
                                builder.binary(BinOp.ADD, raw,
                                               builder.int_const(U64, offset)),
                                module.types.ptr_type(word, mutable=True))
            pieces.append(builder.load(here))
            offset += size_of(word, LAYOUT)
        if held_in_memory(ty):
            return builder.cast(CastKind.BITCAST, pieces[0], ty)
        if len(pieces) == 1:
            return pieces[0]
        return builder.make_tuple(tuple(pieces), ty)

    def _rename(self, func: Function, renamed: dict[int, Value]) -> None:
        """Point every use of a value in *renamed* at what replaces it."""
        for block in func.blocks:
            self._rename_in(block, renamed)

    def _rename_in(self, block: BasicBlock, renamed: dict[int, Value],
                   skip: set[int] | None = None) -> None:
        """The same, in one block, leaving the instructions in *skip* alone."""
        for inst in block.insts:
            if skip is not None and id(inst) in skip:
                continue
            for index, one in enumerate(inst.operands):
                if id(one) in renamed:
                    inst.operands[index] = renamed[id(one)]
            for target in (inst.successors() if isinstance(inst, Terminator) else ()):
                for index, one in enumerate(target.args):
                    if id(one) in renamed:
                        target.args[index] = renamed[id(one)]

