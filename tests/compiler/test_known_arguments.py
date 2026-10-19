"""What the optimizer works out across calls and through addresses.

A function every caller hands the same part of an argument has that part written
into it (`knownargs`); the difference of two addresses that are one place and
constants added on is a number, and two such addresses are compared while
compiling (`constfold`); and a branch either of those settles is taken out
together with what it reached (`settle`).  What it is all for is a view's
stride: one wherever the view is a run of elements, so that `std` asking whether
it has to copy one is answered while compiling and the copy is never there.
"""

from __future__ import annotations

from conftest import describe
from pypl4g.ir.builder import IRBuilder
from pypl4g.ir.function import FuncAttrs, Function, Linkage, SpecialKind
from pypl4g.ir.inst import CallInst, CmpInst, CmpPred
from pypl4g.ir.module import Module
from pypl4g.ir.types import U8, U64, VOID
from pypl4g.ir.verify import verify
from pypl4g.opt.pass_ import build_manager
from pypl4g.opt.passes.constfold import ConstantFolding
from pypl4g.opt.passes.knownargs import KnownArguments


def _view_taker(module: Module) -> tuple[Function, Function]:
    """`f(v)`, which calls `copy` where the stride of the view `v` is not one,
    and the declaration of `copy`."""
    view = module.types.array_type(U8, (None,))
    copy = Function("copy", module.types.func_type((), VOID),
                    FuncAttrs(impure=True), linkage=Linkage.IMPORTED)
    module.add_function(copy)
    func = Function("f", module.types.func_type((view,), U8),
                    FuncAttrs(impure=True), linkage=Linkage.INTERNAL)
    module.add_function(func)
    builder = IRBuilder(module, func)
    entry = func.add_block()
    builder.position_at(entry)
    given = entry.add_param(view, "v")
    stride = builder.extract(given, 2, U64)
    run = func.add_block("run")
    copying = func.add_block("copying")
    builder.condbr(builder.compare(CmpPred.EQ, stride, builder.int_const(U64, 1)),
                   run, copying)
    builder.position_at(copying)
    builder.call(copy, (), VOID)
    builder.br(run)
    builder.position_at(run)
    builder.ret(builder.int_const(U8, 0))
    return func, copy


def _caller(module: Module, func: Function, strides: tuple[int, ...]) -> None:
    """A startup function calling *func* once for each stride."""
    view = func.ty.params[0]
    main = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP, impure=True))
    module.add_function(main)
    module.startup = main
    builder = IRBuilder(module, main)
    builder.position_at(main.add_block())
    start = builder.cast(*_bitcast(builder.frame(module.types.array_type(U8, (8,))),
                                   module.types.ptr_type(U8, mutable=True)))
    for stride in strides:
        builder.call(func, (builder.make_tuple(
            (start, builder.int_const(U64, 4), builder.int_const(U64, stride)),
            view),), U8)
    builder.ret(builder.int_const(U8, 0))


def _calls_copy(func: Function, copy: Function) -> bool:
    return any(isinstance(inst, CallInst) and inst.callee is copy
               for block in func.blocks for inst in block.insts)


def test_a_part_every_caller_hands_alike_is_written_in() -> None:
    """Both calls hand a view whose stride is one, so the stride read in the
    body is the constant, and the copy behind the question is gone."""
    module = Module("t")
    func, copy = _view_taker(module)
    _caller(module, func, (1, 1))
    verify(module)
    assert KnownArguments().run(module)
    asked = [inst for block in func.blocks for inst in block.insts
             if isinstance(inst, CmpInst)]
    assert [getattr(one.operands[0], "value", None) for one in asked] == [1]
    build_manager(1).run(module)
    assert not _calls_copy(func, copy)


def test_a_part_the_callers_disagree_about_is_left() -> None:
    """One call hands a run and the other every other element: the stride is
    a question while running, and the copy stays."""
    module = Module("t")
    func, copy = _view_taker(module)
    _caller(module, func, (1, 2))
    assert not KnownArguments().run(module)
    build_manager(1).run(module)
    assert _calls_copy(func, copy)


def test_a_function_named_as_a_value_has_callers_nobody_sees() -> None:
    """Its code taken as a value, it may be called from anywhere, with
    anything: nothing is written in."""
    module = Module("t")
    func, _ = _view_taker(module)
    _caller(module, func, (1,))
    main = module.startup
    assert main is not None and main.entry is not None
    builder = IRBuilder(module, main)
    builder.position_at(main.entry)
    ret = main.entry.insts.pop()
    builder.code_address(func)
    main.entry.insts.append(ret)
    assert not KnownArguments().run(module)


GOES_STRAIGHT_OUT = """let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let went: u64 \N{CURRENCY SIGN}size = std.write_sync(&mut init.io.error, \N{APL FUNCTIONAL SYMBOL QUAD}bytes("text\\n")) ?? 0
    if went = 5: 0u6 else: 1u6
"""

#: The same, every other byte of a table's: a view that is not one run.
GOES_COPIED = GOES_STRAIGHT_OUT.replace(
    "\N{APL FUNCTIONAL SYMBOL QUAD}bytes(\"text\\n\")", "t\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{HORIZONTAL ELLIPSIS}2, 0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}").replace(
    "    let went", "    let k: u8 = 116u8\n"
    "    let t: u8\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2, 2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = "
    "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}k, k\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, "
    "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}10u8, 10u8\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}\n"
    "    let went").replace("went = 5", "went = 2")


def test_a_run_written_out_is_never_copied(compile_source) -> None:  # noqa: ANN001
    """The bytes of a string are one run wherever `std` is handed them, so the
    copy -- and the allocator it would take room from -- is not in the program."""
    proc, output = compile_source(GOES_STRAIGHT_OUT, "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "__pl4g_one_run" not in text
    assert "@__pl4g_alloc" not in text


def test_a_view_that_is_not_a_run_still_is(compile_source) -> None:  # noqa: ANN001
    """A column is copied into a run for the kernel, which needs the copy."""
    proc, output = compile_source(GOES_COPIED, "--emit=ir", "-O1")
    assert proc.returncode == 0, describe(proc)
    assert "__pl4g_one_run" in output.read_text(encoding="utf-8")


def test_addresses_one_place_apart_are_worked_out() -> None:
    """The second element's address less the first's is the stride, and the
    first element's address taken twice is one address."""
    module = Module("t")
    func = Function("main", module.types.func_type((), U8),
                    FuncAttrs(special=SpecialKind.STARTUP))
    module.add_function(func)
    module.startup = func
    builder = IRBuilder(module, func)
    builder.position_at(func.add_block())
    place = builder.frame(module.types.array_type(U8, (8,)))
    pointer = module.types.ptr_type(U8, mutable=True)
    first = builder.cast(*_bitcast(place, pointer))
    second = builder.binary(*_add(first, builder.int_const(U64, 1)))
    apart = builder.binary(*_sub(builder.cast(*_bitcast(second, U64)),
                                 builder.cast(*_bitcast(first, U64))))
    same = builder.compare(CmpPred.EQ, builder.cast(*_bitcast(first, U64)),
                           builder.cast(*_bitcast(place, U64)))
    narrowed = builder.cast(*_trunc(apart, U8))
    widened = builder.cast(*_zext(same, U8))
    builder.ret(builder.binary(*_add(narrowed, widened)))
    verify(module)
    assert ConstantFolding().run(module)
    assert getattr(narrowed.operands[0], "value", None) == 1
    assert getattr(widened.operands[0], "value", None) is True


def _bitcast(value, ty):  # noqa: ANN001, ANN202
    from pypl4g.ir.inst import CastKind
    return CastKind.BITCAST, value, ty


def _trunc(value, ty):  # noqa: ANN001, ANN202
    from pypl4g.ir.inst import CastKind
    return CastKind.TRUNC, value, ty


def _zext(value, ty):  # noqa: ANN001, ANN202
    from pypl4g.ir.inst import CastKind
    return CastKind.ZEXT, value, ty


def _add(lhs, rhs):  # noqa: ANN001, ANN202
    from pypl4g.ir.inst import BinOp
    return BinOp.ADD, lhs, rhs


def _sub(lhs, rhs):  # noqa: ANN001, ANN202
    from pypl4g.ir.inst import BinOp
    return BinOp.WRAP_SUB, lhs, rhs
