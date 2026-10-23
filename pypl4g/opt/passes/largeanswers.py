"""Answering with more than the registers hold.

A function says how it hands back an answer of more than one value, and the one
style that exists answers two of them in registers and everything larger through
storage the caller provides.  This is where the second half of that is carried
out: the function grows one parameter, which is where to put the answer; its
`ret` becomes writes through that place; and every call to it makes a place,
hands it over, and reads the answer back out of it.

It is done here rather than in the three instruction selectors because there is
nothing about it that differs between targets -- a place, some writes and some
reads -- and doing it once is what makes the three of them agree by construction.
It is done here rather than while the language is being checked because the
signature it produces is not the signature the program wrote: the pointer is not
an argument any program can pass, and no rule about arguments should have to know
that one of them is not one.

**A record laid out for C** (`@[abi]`) is answered by these rules in the
language's own convention, always through the caller's room whatever its size,
and so is anything holding one -- a result, a tuple: what is answered is bytes C
may be handed, and the room is where they are.  A function of the *system's*
convention answering one answers it as C does (`ir/cabi.py`), which does differ
between targets and is done here all the same, for the same reason: the
instruction selectors then see words in registers, or a place handed over
first, and nothing they do not see already.  The words are the record's members
put together with shifts, as a C compiler puts them together, rather than
written to memory and read back.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Sequence

from ...ir import cabi
from ...ir.reports import ReportKind, ReportLog
from ...ir.function import BasicBlock, Function, ReturnStyle
from ...ir.inst import (BinOp, BinaryInst, CallInst, CastInst, CastKind,
                        ErrorInst, ExtractInst, FailedInst, FrameInst,
                        Instruction, LoadInst,
                        MemStartInst, RetInst, SelectInst, StoreInst,
                        TupleInst, UnwrapInst, WrapInst)
from ...ir.layout import (DataLayout, error_offset_of,
                          part_offsets_of, tag_offset_of)
from ...ir.module import Module, Output
from ...ir.rewrite import stands_for
from ...ir.layout import size_of
from ...ir.types import (BOOL, F64, MEM, FloatType, ProductType, PtrType,
                         ResultType, TupleType, Type, U8,
                         U16, U32, U64,
                         VOID, held_in_memory, leaves_of, parts_of)
from ...ir.value import Value

#: The one layout this compiler has.  Where it grows a second, the pass is
#: handed the target's rather than knowing one.
LAYOUT = DataLayout(pointer_size=8)


def holds_abi(ty: Type) -> bool:
    """Whether a value of *ty* is a record laid out for C, or has one inside it
    by value: a result answering one, a tuple or a record holding one."""
    if isinstance(ty, ProductType):
        return ty.abi or any(holds_abi(one) for _, one in ty.fields)
    if isinstance(ty, TupleType):
        return any(holds_abi(one) for one in ty.members)
    if isinstance(ty, ResultType):
        return holds_abi(ty.ok) or (ty.err is not None and holds_abi(ty.err))
    return False


def answers_as_c(module: Module, func: Function) -> bool:
    """Whether *func* answers a record laid out for C in the system's
    convention: the answer C's rules are about, which is answered by them.

    Not in a program the interpreter runs.  Nothing there reads a register: a
    function it runs answers what it answers as it answers every value, and one
    the host defines is called through libffi, which is told the record's
    members, applies C's rules itself and writes the answer into the room the
    caller made.
    """
    ret = func.ty.ret
    return isinstance(ret, ProductType) and ret.abi \
        and cabi.follows_system(module.triple, func.cconv) \
        and module.output is not Output.INTERPRETED


def through_storage(module: Module, func: Function) -> bool:
    """Whether this function's answer travels through the caller's storage.

    A part that is itself several values -- a result among a tuple's members --
    is left alone, and the instruction selector says what it says about such an
    answer today.  Nothing anywhere counts registers per part recursively, so
    routing one through storage here would only move where it went wrong.

    A value held in memory -- a sum -- goes through storage whatever its size:
    what a value of one *is*, is where its bytes are, and the bytes of one a
    function made are in that function's own room.  So the caller provides the
    room and the answer is written into it, which is the one way such a value
    can outlive the call that made it.

    So does one holding a record laid out for C, in the language's own
    convention whatever its size, and a result or a tuple holding one in any:
    what is answered is bytes something else may be handed, and they are
    wanted where they will be read.  A record answered whole in the system's
    convention is C's to say (`answers_as_c`) -- but in the interpreter, where
    one the host answers goes through the room too: the interpreter has libffi
    call the host's function, which writes the `struct` C answered into it.
    """
    ret = func.ty.ret
    if ret is VOID:
        return False
    if isinstance(ret, ProductType) and ret.abi \
            and cabi.follows_system(module.triple, func.cconv):
        if module.output is not Output.INTERPRETED:
            return False
        if func.attrs.host is not None:
            return True
    elif holds_abi(ret):
        return True
    if func.answer_style.in_registers(ret):
        return False
    if held_in_memory(ret):
        return True
    return all(len(parts_of(leaf)) == 1 for leaf in leaves_of(ret))


class LargeAnswers:
    """Rewrites the functions whose answer does not fit in registers."""

    name = "largeanswers"

    def run(self, module: Module) -> bool:
        """Rewrite those functions and every call to them.

        An answer that travels without its allocator first (`thinanswers`): what
        is left of it is what this pass asks how many words it is of.
        """
        from .thinanswers import ThinAnswers  # noqa: PLC0415 -- one question
        thinned = ThinAnswers().run(module)
        stored = self._through_storage(module)
        return self._as_c(module) or stored or thinned

    def _through_storage(self, module: Module) -> bool:
        """Rewrite the functions answering through storage, and the calls."""
        reports = module.reports
        # By identity, since a module may name one function more than once and
        # widening it twice would give it two places to put the answer.
        every = {id(func): func for func in module.functions.values()}
        wanted = {at: func for at, func in every.items()
                  if through_storage(module, func)}
        if not wanted and not _calls_through_values(every.values()):
            return False
        answers = {at: func.ty.ret for at, func in wanted.items()}
        for func in wanted.values():
            self._widen(module, func)
        for func in every.values():
            for block in func.blocks:
                self._rewrite_calls(module, block, wanted, answers)
        for func in wanted.values():
            if func.blocks:
                self._store_the_answer(module, func, answers[id(func)],
                                       func.blocks[0].params[-1], None)
        for func in wanted.values():
            reports.record(
                ReportKind.ANSWER_IN_STORAGE, func.name,
                "".join(("'", answers[id(func)].written(), "' is more values ",
                         "than the style answers in registers, so the caller ",
                         "provides the place")
                        if not holds_abi(answers[id(func)])
                        else ("'", answers[id(func)].written(), "' holds a record ",
                              "laid out for C, which the language's own ",
                              "convention answers in room the caller provides")),
                func.name_span if func.name_span.is_valid else func.span)
        return True

    def _widen(self, module: Module, func: Function) -> None:
        """Give the function the parameter that says where to put the answer.

        It goes last rather than first.  A hidden first argument is what the
        system ABIs do, and they do it because theirs has to be in one known
        register whatever else is passed; this convention is the compiler's own,
        so putting it last leaves every argument the program wrote in the place
        it already had.
        """
        place = module.types.ptr_type(func.ty.ret, mutable=True)
        func.ty = module.types.func_type((*func.ty.params, place), VOID)
        # It writes memory that outlives the call, which is not the same thing
        # as being impure: what it writes is the place it was handed and
        # nothing else.  Saying that is what lets a caller reading none of that
        # place drop the call, which calling it impure would have forbidden --
        # and a pure function whose answer nobody wants is exactly what a caller
        # is entitled to drop, whatever shape the answer turned out to have.
        func.attrs = replace(func.attrs, answer_in_storage=True)
        if func.blocks:
            func.blocks[0].add_param(place, "answer")

    def _store_the_answer(self, module: Module, func: Function,
                          answer: Type, place: Value,
                          returned: Value | None) -> None:
        """Turn each `ret v` into writes through *place* and a `ret` of
        *returned*: nothing, or -- where the system's convention says so -- the
        place itself."""
        for block in func.blocks:
            last = block.terminator
            if not isinstance(last, RetInst) or not last.operands:
                continue
            made: list[Instruction] = []
            # The token first: finding it may put the start of the chain at the
            # top of the entry block, which moves everything below it along.
            token = _token_before(func, block, len(block.insts) - 1)
            at = block.insts.index(last)
            value = last.operands[0]
            if held_in_memory(answer):
                # Its bytes, copied into the caller's room: there is nothing to
                # take apart, the value being where the bytes are.
                token = _copied(module, place, value, size_of(answer, LAYOUT),
                                token, made, last.span)
                made.append(RetInst(returned, last.span))
                _splice(block, at, made)
                continue
            for index, (part, offset) in enumerate(
                    zip(parts_of(answer), _offsets(answer))):
                taken = _part(value, index, part, answer, last.span)
                made.append(taken)
                where = _at(module, place, part, offset, made, last.span)
                if held_in_memory(part):
                    # A part held in memory -- the sum a result answers -- is
                    # where its bytes are, and it is the bytes that go.  An error
                    # has no answer to be read, and where it is stands nothing:
                    # the place is copied onto itself instead, which reads
                    # nothing that is not there.
                    bytes_ = module.types.ptr_type(U8, mutable=True)
                    held = CastInst(CastKind.BITCAST, taken, bytes_, last.span)
                    failed = FailedInst(value, BOOL, last.span)
                    itself = CastInst(CastKind.BITCAST, where, bytes_, last.span)
                    source = SelectInst(failed, itself, held, last.span)
                    made.extend((held, failed, itself, source))
                    token = _copied(module, where, source, size_of(part, LAYOUT),
                                    token, made, last.span)
                    continue
                token = StoreInst(token, where, taken, last.span)
                made.append(token)
            made.append(RetInst(returned, last.span))
            _splice(block, at, made)

    def _rewrite_calls(self, module: Module, block: BasicBlock,
                       wanted: dict[int, Function],
                       answers: dict[int, Type]) -> None:
        """Make a place for each such call, hand it over, and read it back.

        A call through a value answering something holding a record laid out
        for C is one of them as well: whatever the value points at follows the
        language's own convention, which answers that through the caller's room.
        """
        index = 0
        while index < len(block.insts):
            inst = block.insts[index]
            callee = getattr(inst, "callee", None)
            # A call already rewritten answers with nothing, which is what says
            # it has been: the place it writes into is among its arguments and
            # giving it a second one would be giving it two.
            through = isinstance(inst, CallInst) and inst.target is not None \
                and holds_abi(inst.ty)
            if not isinstance(inst, CallInst) or inst.ty is VOID \
                    or (id(callee) not in wanted and not through):
                index += 1
                continue
            answer = inst.ty if through else answers[id(callee)]
            span = inst.span
            # The token first: finding it may put the start of the chain at the
            # top of the entry block, which moves this call along with it.
            token = _token_before(block.parent, block, index)
            index = block.insts.index(inst)
            made: list[Instruction] = []
            slot = FrameInst(answer, module.types.ptr_type(answer, mutable=True),
                             span)
            made.append(slot)
            if through:
                assert inst.target is not None
                made.append(CallInst(inst.target, (*inst.arguments, slot), VOID,
                                     span))
            else:
                made.append(CallInst(callee, (*inst.operands, slot), VOID, span))
            index = self._read_back(module, block, index, inst, answer, slot,
                                    token, made)

    def _read_back(self, module: Module, block: BasicBlock, index: int,
                   inst: Instruction, answer: Type, slot: Value, token: Value,
                   made: list[Instruction]) -> int:
        """Read the answer *inst* gave out of *slot*, put *made* where *inst*
        stood with the reads after it, and answer where the walk goes on."""
        span = inst.span
        if held_in_memory(answer):
            # Nothing to read back: the room the caller made *is* the answer,
            # since a value of such a type is where its bytes are.
            rebuilt: Instruction = CastInst(CastKind.BITCAST, slot, answer, span)
            made.append(rebuilt)
            _splice(block, index, made)
            stands_for(block.parent, inst, rebuilt)
            return index + len(made)
        taken: list[Value] = []
        for part, offset in zip(parts_of(answer), _offsets(answer)):
            where = _at(module, slot, part, offset, made, span)
            if held_in_memory(part):
                # Where its bytes are in the room the caller made is the part,
                # as the whole is where the whole is held in memory.
                read: Instruction = CastInst(CastKind.BITCAST, where, part, span)
            else:
                read = LoadInst(part, (token, where), span)
            made.append(read)
            taken.append(read)
        rebuilt = _whole(taken, answer, span)
        made.append(rebuilt)
        _splice(block, index, made)
        stands_for(block.parent, inst, rebuilt)
        return index + len(made)

    # -- answered as C answers ------------------------------------------------

    def _as_c(self, module: Module) -> bool:
        """Rewrite the functions answering a record the way C does, and the
        calls to them: in registers, the record's members put together into
        the words the system's convention answers, or through room the caller
        makes, handed over before every other argument."""
        every = {id(func): func for func in module.functions.values()}
        wanted = {at: func for at, func in every.items()
                  if answers_as_c(module, func)}
        if not wanted:
            return False
        shapes: dict[int, tuple[ProductType, tuple[cabi.Piece, ...] | None]] = {}
        for at, func in wanted.items():
            record = func.ty.ret
            assert isinstance(record, ProductType)
            shapes[at] = (record, cabi.answered_by_c(module.triple, record))
        for at, func in wanted.items():
            record, pieces = shapes[at]
            if pieces is None:
                self._widen_first(module, func)
            else:
                func.ty = module.types.func_type(func.ty.params,
                                                 _words_type(module, pieces))
        for func in every.values():
            for block in func.blocks:
                self._rewrite_c_calls(module, block, wanted, shapes)
        for at, func in wanted.items():
            record, pieces = shapes[at]
            if not func.blocks:
                continue
            if pieces is None:
                place = func.blocks[0].params[0]
                self._store_the_answer(
                    module, func, record, place,
                    place if cabi.answers_its_place(module.triple) else None)
            else:
                self._answer_words(module, func, record, pieces)
        for at, func in wanted.items():
            record, pieces = shapes[at]
            module.reports.record(
                ReportKind.ANSWER_IN_STORAGE if pieces is None else ReportKind.ANSWER_AS_C,
                func.name,
                "".join(("'", record.written(), "' is laid out for C, which answers it ",
                         "in room the caller provides, its address handed over first")
                        if pieces is None
                        else ("'", record.written(), "' is laid out for C, which answers ",
                              "it in registers as ",
                              ", ".join(one.ty.written() for one in pieces)
                              or "nothing")),
                func.name_span if func.name_span.is_valid else func.span)
        return True

    def _widen_first(self, module: Module, func: Function) -> None:
        """Give the function the place its answer is written to, first.

        First because that is where C puts it: in the first argument register,
        or -- on AArch64 -- in one of its own, which the attribute says to the
        instruction selector (`answer_first`).  And on x86-64 the function
        answers with the place, which C's callers may count on.
        """
        record = func.ty.ret
        place = module.types.ptr_type(record, mutable=True)
        func.ty = module.types.func_type(
            (place, *func.ty.params),
            place if cabi.answers_its_place(module.triple) else VOID)
        func.attrs = replace(func.attrs, answer_in_storage=True, answer_first=True)
        if func.blocks:
            entry = func.blocks[0]
            entry.add_param(place, "answer")
            entry.params.insert(0, entry.params.pop())
            for at, param in enumerate(entry.params):
                param.index = at

    def _rewrite_c_calls(self, module: Module, block: BasicBlock,
                         wanted: dict[int, Function],
                         shapes: dict[int, tuple[ProductType,
                                                 tuple[cabi.Piece, ...] | None]]) -> None:
        """Rewrite each call of a function answering as C does."""
        index = 0
        while index < len(block.insts):
            inst = block.insts[index]
            callee = getattr(inst, "callee", None)
            # A call already rewritten answers with something other than the
            # record, which is what says it has been.
            if not isinstance(inst, CallInst) or id(callee) not in wanted \
                    or not isinstance(inst.ty, ProductType):
                index += 1
                continue
            record, pieces = shapes[id(callee)]
            span = inst.span
            token = _token_before(block.parent, block, index)
            index = block.insts.index(inst)
            made: list[Instruction] = []
            assert isinstance(callee, Function)
            if pieces is None:
                slot = FrameInst(record, module.types.ptr_type(record, mutable=True),
                                 span)
                made.append(slot)
                made.append(CallInst(callee, (slot, *inst.operands),
                                     callee.ty.ret, span))
                index = self._read_back(module, block, index, inst, record, slot,
                                        token, made)
                continue
            call = CallInst(callee, inst.operands, callee.ty.ret, span)
            made.append(call)
            words: list[Value] = []
            if len(pieces) == 1:
                words.append(call)
            else:
                for at, piece in enumerate(pieces):
                    one = ExtractInst(call, at, piece.ty, span)
                    made.append(one)
                    words.append(one)
            rebuilt = _unpacked(module, record, pieces, words, token, made, span)
            _splice(block, index, made)
            stands_for(block.parent, inst, rebuilt)
            index += len(made)

    def _answer_words(self, module: Module, func: Function, record: ProductType,
                      pieces: tuple[cabi.Piece, ...]) -> None:
        """Turn each `ret v` into a `ret` of the words C answers *v* in."""
        for block in func.blocks:
            last = block.terminator
            if not isinstance(last, RetInst) or not last.operands:
                continue
            made: list[Instruction] = []
            token = _token_before(func, block, len(block.insts) - 1)
            at = block.insts.index(last)
            words = _packed(module, record, pieces, last.operands[0], token, made,
                            last.span)
            if not words:
                answer: Value | None = None
            elif len(words) == 1:
                answer = words[0]
            else:
                whole = TupleInst(words, func.ty.ret, last.span)
                made.append(whole)
                answer = whole
            made.append(RetInst(answer, last.span))
            _splice(block, at, made)


def _calls_through_values(funcs: Iterable[Function]) -> bool:
    """Whether any of *funcs* calls through a value something answering a
    record laid out for C, which is answered through the caller's room."""
    return any(isinstance(inst, CallInst) and inst.target is not None
               and holds_abi(inst.ty)
               for func in funcs for block in func.blocks for inst in block.insts)


def _words_type(module: Module, pieces: Sequence[cabi.Piece]) -> Type:
    """What a function answering in *pieces* answers: nothing, the one word,
    or a tuple of them, which the convention answers a register a part."""
    if not pieces:
        return VOID
    if len(pieces) == 1:
        return pieces[0].ty
    return module.types.tuple_type(tuple(one.ty for one in pieces))


def _by_memory(record: ProductType) -> bool:
    """Whether the words of *record* are put together in memory rather than
    with shifts.

    Where the record is bytes in a place -- it holds an array or a sum -- the
    bytes are what there is to read; and where a part is something no register
    is read as a number -- a set, a table -- there is nothing to shift.  Neither
    is anything C has, so the slower way costs nothing a program would see.
    """
    return held_in_memory(record) \
        or not all(cabi.plain(one) for one in parts_of(record))


def _room(module: Module, record: ProductType) -> Type:
    """Room for *record* that whole words may be read from and written to: as
    many words as cover it, the last read whole where the record ends inside
    it."""
    words = (size_of(record, cabi.SYSTEM_LAYOUT) + 7) // 8
    return module.types.tuple_type(tuple(U64 for _ in range(max(words, 1))))


def _packed(module: Module, record: ProductType, pieces: Sequence[cabi.Piece],
            value: Value, token: Value, made: list[Instruction],
            span) -> list[Value]:  # noqa: ANN001
    """The words *value* is answered in, one for each of *pieces*."""
    if _by_memory(record):
        room = _room(module, record)
        slot = FrameInst(room, module.types.ptr_type(room, mutable=True), span)
        made.append(slot)
        if held_in_memory(record):
            token = _copied(module, slot, value, size_of(record, cabi.SYSTEM_LAYOUT),
                            token, made, span)
        else:
            offsets = part_offsets_of(record, cabi.SYSTEM_LAYOUT)
            for index, (part, offset) in enumerate(zip(parts_of(record), offsets)):
                taken = ExtractInst(value, index, part, span)
                made.append(taken)
                token = StoreInst(token, _at(module, slot, part, offset, made, span),
                                  taken, span)
                made.append(token)
        words: list[Value] = []
        for piece in pieces:
            read = LoadInst(piece.ty, (token, _at(module, slot, piece.ty, piece.offset,
                                                  made, span)), span)
            made.append(read)
            words.append(read)
        return words
    leaves = list(zip(parts_of(record), part_offsets_of(record, cabi.SYSTEM_LAYOUT)))
    words = []
    for piece in pieces:
        inside = [(index, part, offset)
                  for index, (part, offset) in enumerate(leaves)
                  if piece.offset <= offset < piece.offset + piece.size]
        if len(inside) == 1 and inside[0][1] is piece.ty:
            # The member is the word, as it is in most records: one to a
            # register.
            taken = ExtractInst(value, inside[0][0], piece.ty, span)
            made.append(taken)
            words.append(taken)
            continue
        whole: Value | None = None
        for index, part, offset in inside:
            taken = ExtractInst(value, index, part, span)
            made.append(taken)
            bits = _wide_bits(module, taken, made, span)
            if offset > piece.offset:
                moved = BinaryInst(BinOp.SHL, bits,
                                   module.int_const(U64, 8 * (offset - piece.offset)),
                                   span)
                made.append(moved)
                bits = moved
            if whole is not None:
                joined = BinaryInst(BinOp.OR, whole, bits, span)
                made.append(joined)
                bits = joined
            whole = bits
        if whole is None:
            whole = module.int_const(U64, 0)
        if isinstance(piece.ty, FloatType):
            # The two `float`s of one of x86-64's eightbytes, in one register
            # of the other kind.
            word = CastInst(CastKind.FBITS, whole, F64, span)
            made.append(word)
            whole = word
        words.append(whole)
    return words


def _wide_bits(module: Module, value: Value, made: list[Instruction],
               span) -> Value:  # noqa: ANN001
    """The bits of *value*, a member C holds, as a `u64` with nought above."""
    ty = value.ty
    if isinstance(ty, FloatType):
        bits: Value = CastInst(CastKind.FBITS, value, U32 if ty.bits == 32 else U64,
                               span)
        made.append(bits)  # type: ignore[arg-type]
        value = bits
    elif isinstance(ty, PtrType):
        bits = CastInst(CastKind.BITCAST, value, U64, span)
        made.append(bits)  # type: ignore[arg-type]
        return bits
    if value.ty is U64:
        return value
    wide = CastInst(CastKind.ZEXT, value, U64, span)
    made.append(wide)
    return wide


def _unpacked(module: Module, record: ProductType, pieces: Sequence[cabi.Piece],
              words: Sequence[Value], token: Value, made: list[Instruction],
              span) -> Instruction:  # noqa: ANN001
    """The record *words* are, taken apart into its members."""
    if _by_memory(record):
        room = _room(module, record)
        slot = FrameInst(room, module.types.ptr_type(room, mutable=True), span)
        made.append(slot)
        for piece, word in zip(pieces, words):
            token = StoreInst(token, _at(module, slot, piece.ty, piece.offset, made, span),
                              word, span)
            made.append(token)
        if held_in_memory(record):
            rebuilt: Instruction = CastInst(CastKind.BITCAST, slot, record, span)
            made.append(rebuilt)
            return rebuilt
        taken: list[Value] = []
        offsets = part_offsets_of(record, cabi.SYSTEM_LAYOUT)
        for part, offset in zip(parts_of(record), offsets):
            read = LoadInst(part, (token, _at(module, slot, part, offset, made, span)),
                            span)
            made.append(read)
            taken.append(read)
        rebuilt = TupleInst(taken, record, span)
        made.append(rebuilt)
        return rebuilt
    taken = []
    for part, offset in zip(parts_of(record),
                            part_offsets_of(record, cabi.SYSTEM_LAYOUT)):
        at = next(index for index, piece in enumerate(pieces)
                  if piece.offset <= offset < piece.offset + piece.size)
        piece, word = pieces[at], words[at]
        if piece.ty is part:
            taken.append(word)
            continue
        if isinstance(piece.ty, FloatType):
            bits = CastInst(CastKind.FBITS, word, U64, span)
            made.append(bits)
            word = bits
        if offset > piece.offset:
            moved = BinaryInst(BinOp.LSHR, word,
                               module.int_const(U64, 8 * (offset - piece.offset)), span)
            made.append(moved)
            word = moved
        taken.append(_narrowed(word, part, made, span))
    rebuilt = TupleInst(taken, record, span)
    made.append(rebuilt)
    return rebuilt


def _narrowed(word: Value, part: Type, made: list[Instruction],
              span) -> Value:  # noqa: ANN001
    """The member of type *part* in the low bits of the `u64` *word*."""
    if isinstance(part, FloatType):
        if part.bits == 32:
            low = CastInst(CastKind.TRUNC, word, U32, span)
            made.append(low)
            word = low
        value = CastInst(CastKind.FBITS, word, part, span)
    elif isinstance(part, PtrType):
        value = CastInst(CastKind.BITCAST, word, part, span)
    elif part is U64:
        return word
    else:
        value = CastInst(CastKind.TRUNC, word, part, span)
    made.append(value)
    return value


def _offsets(answer: Type) -> tuple[int, ...]:
    """Where each part of the answer goes in the place that holds it."""
    if isinstance(answer, ResultType):
        if answer.err is None:
            # Nothing carried with the error: the answer and the truth value.
            return (0, tag_offset_of(answer, LAYOUT))
        return (0, tag_offset_of(answer, LAYOUT),
                error_offset_of(answer, LAYOUT))
    if not isinstance(answer, TupleType | ProductType):
        # A string, a list, an array of no stated length: one word a part.
        return tuple(8 * at for at in range(len(parts_of(answer))))
    return part_offsets_of(answer, LAYOUT)


def _part(value: Value, index: int, part: Type, answer: Type,
          span) -> Instruction:  # noqa: ANN001
    """One part of an answer, taken out of it.

    A tuple's parts are its members and a result's are the answer, whether
    there is one, and what the error carries -- three different instructions
    rather than one indexed by number, because they are of three types and the
    representation says so.
    """
    if not isinstance(answer, ResultType):
        return ExtractInst(value, index, part, span)
    return (UnwrapInst(value, part, span) if index == 0
            else FailedInst(value, part, span) if index == 1
            else ErrorInst(value, part, span))


def _whole(taken: Sequence[Value], answer: Type,
           span) -> Instruction:  # noqa: ANN001
    """The parts put back together into the one value they are."""
    if not isinstance(answer, ResultType):
        return TupleInst(list(taken), answer, span)
    return WrapInst(taken[0], taken[1], answer, span,
                    taken[2] if len(taken) > 2 else None)


#: What the bytes of a value held in memory are copied in, largest first.  A
#: copy of a size known while compiling is written out rather than looped over:
#: the sizes here are a handful of words, and a loop would be a block to splice
#: into a function this pass is walking.
_CHUNKS: Sequence[tuple[Type, int]] = ((U64, 8), (U32, 4), (U16, 2), (U8, 1))


def _copied(module: Module, into: Value, from_: Value, size: int, token: Value,
            made: list[Instruction], span) -> Value:  # noqa: ANN001
    """Copy *size* bytes into *into*, and answer the token that follows."""
    at = 0
    while at < size:
        part, width = next(one for one in _CHUNKS if one[1] <= size - at)
        source = _at(module, from_, part, at, made, span)
        read = LoadInst(part, (token, source), span)
        made.append(read)
        token = StoreInst(token, _at(module, into, part, at, made, span), read,
                          span)
        made.append(token)
        at += width
    return token


def _at(module: Module, place: Value, part: Type, offset: int,
        made: list[Instruction], span) -> Value:  # noqa: ANN001
    """The address one part of the answer lives at, as a pointer to that part."""
    base = CastInst(CastKind.BITCAST, place,
                    module.types.ptr_type(part, mutable=True), span)
    made.append(base)
    if offset == 0:
        return base
    moved = BinaryInst(BinOp.ADD, base, module.int_const(U64, offset), span)
    made.append(moved)
    return moved


def _token_before(func: Function | None, block: BasicBlock,
                  at: int) -> Value:
    """The memory token in force just before instruction *at* of *block*.

    What is written here has to be ordered after everything the function wrote
    before it, so the chain is joined where it stands rather than started again:
    the nearest memory-valued instruction above, or the parameter the block took
    it in, or -- in a function that has not touched memory at all -- the start of
    a chain, put where every chain starts.
    """
    for earlier in reversed(block.insts[:at]):
        if earlier.ty is MEM:
            return earlier
    for param in block.params:
        if param.ty is MEM:
            return param
    assert func is not None
    entry = func.blocks[0]
    if entry.insts and isinstance(entry.insts[0], MemStartInst):
        return entry.insts[0]
    start = MemStartInst()
    start.parent = entry
    entry.insts.insert(0, start)
    return start


def _splice(block: BasicBlock, at: int, made: list[Instruction]) -> None:
    """Put *made* where the instruction at *at* stood."""
    for one in made:
        one.parent = block
    block.insts[at:at + 1] = made
