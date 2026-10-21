"""Dividing by a constant without the machine's division.

A division is the slowest arithmetic every one of the three machines has: some
forty cycles on x86-64 for 64 bits, a few dozen on the others, and not
pipelined.  A multiplication is three or four and is.  Where the divisor is a
constant, the quotient is the upper half of the dividend times a constant worked
out here, shifted -- Granlund and Montgomery's method of 1994, which is what every
C compiler since has done -- and a remainder is the dividend less the quotient
times the divisor.

**What is rewritten.**  `udiv`, `urem`, `sdiv` and `srem` of any width, as the
machine's operation or as the result the language's `÷` and `%` answer, where
the divisor is a constant other than nought and, for a signed one, other than
minus one.  Nought has no answer and is the checker's to report; minus one has
one for every dividend but the smallest, and that one is a result that failed,
which only the division the backend writes knows how to say.  Every other
divisor answers every dividend, so the result is one that never failed: the
answer, beside a truth value that is `false`, which folding then reads through.

**How, by the divisor.**

- *One*: the dividend.
- *A power of two*, unsigned: a shift, and the remainder a mask.  Signed: a
  shift too, after adding `2^k - 1` to a negative dividend, which is what makes
  the shift round toward nought as the division does rather than down; and the
  answer negated for a negative divisor.
- *Anything else*, unsigned: `m = ⌈2^(64+s) ÷ d⌉` for the smallest `s` at which
  it still fits a word and is close enough for every dividend the type holds --
  `(m × d - 2^(64+s)) × largest < 2^(64+s)` -- and then the quotient is the
  upper word of the product, shifted by `s`.  A type narrower than a word is
  done in a word, so a dividend below `2^32` finds `s = 0` and no shift at all.
  Where no such `m` fits, an even divisor tries again with the dividend shifted
  down by its twos first, and an odd one takes the `m` that is one bit too wide:
  its lower 64 bits, and the bit above added back as `(x - t) ÷ 2 + t`.
- *Anything else*, signed: Hacker's Delight's magic number and the signed upper
  word, the dividend added or taken away where the number is of the other sign
  than the divisor, the arithmetic shift, and one added to a negative answer to
  round it toward nought.

All of it is done at 64 bits: a narrower dividend is widened first and the answer
narrowed after, which is what the machines do with a narrow value in any case.
The remainder is `x - q × d` with wrapping arithmetic, which cannot wrap.

Compare: GCC (`expand_divmod`, `choose_multiplier`), LLVM
(`TargetLowering::BuildUDIV`/`BuildSDIV`, the same method with the pre-shift),
and through them Clang, Rust, Zig, Swift and Julia all do exactly this; Go's
compiler does it in its generic rewrite rules; D's DMD and Odin through LLVM.
It is in the representation rather than the three selectors because what it
writes is ordinary arithmetic every back end already has, the signed upper half
included -- `⎕wide_mul` -- and because the shared operations it leaves are then
found by the pass that shares values worked out twice: `x ÷ 10` and `x % 10`
side by side multiply once.
"""

from __future__ import annotations

from typing import Final

from ...ir.function import Function
from ...ir.inst import (BinaryInst, BinOp, CastInst, CastKind, ExtractInst,
                        Instruction, WrapInst)
from ...ir.module import Module
from ...ir.rewrite import stands_for
from ...ir.types import BOOL, I64, U64, IntType, ResultType, Type
from ...ir.value import IntConst, Value

#: The four that are rewritten, and whether each is the remainder.
_REWRITTEN: Final = {BinOp.UDIV: False, BinOp.UREM: True,
                     BinOp.SDIV: False, BinOp.SREM: True}

_WORD: Final[int] = 1 << 64


def unsigned_magic(d: int, largest: int) -> tuple[int, int] | None:
    """The multiplier and the shift that divide every number up to *largest* by
    *d*: the upper word of the product, shifted.  Nothing where the multiplier
    would not fit a word."""
    s = 0
    while True:
        m = -(-(1 << (64 + s)) // d)
        if m >= _WORD:
            return None
        if (m * d - (1 << (64 + s))) * largest < 1 << (64 + s):
            return m, s
        s += 1


def signed_magic(d: int) -> tuple[int, int]:
    """Hacker's Delight's multiplier and shift for a signed 64-bit division by
    *d*, which is neither nought nor one nor minus one nor a power of two: the
    multiplier as the signed word it is read as."""
    two63 = 1 << 63
    ad = abs(d)
    t = two63 + (1 if d < 0 else 0)
    anc = t - 1 - t % ad
    p = 63
    q1, r1 = divmod(two63, anc)
    q2, r2 = divmod(two63, ad)
    while True:
        p += 1
        q1, r1 = 2 * q1, 2 * r1
        if r1 >= anc:
            q1, r1 = q1 + 1, r1 - anc
        q2, r2 = 2 * q2, 2 * r2
        if r2 >= ad:
            q2, r2 = q2 + 1, r2 - ad
        delta = ad - r2
        if not (q1 < delta or (q1 == delta and r1 == 0)):
            break
    m = (q2 + 1) % _WORD
    if d < 0:
        m = (-m) % _WORD
    if m >= two63:
        m -= _WORD
    return m, p - 64


class DivisionByConstants:
    """Rewrites a division by a constant into multiplications and shifts."""

    name = "divisions"

    def run(self, module: Module) -> bool:
        """Rewrite every division by a constant; report whether any was."""
        self.touched: list[Function] = []
        for func in module.functions.values():
            if func.is_declaration:
                continue
            changed = False
            for block in func.blocks:
                at = 0
                while at < len(block.insts):
                    inst = block.insts[at]
                    made = _rewritten(module, inst) \
                        if isinstance(inst, BinaryInst) else None
                    if made is None:
                        at += 1
                        continue
                    sequence, answer = made
                    for one in sequence:
                        one.parent = block
                    block.insts[at:at + 1] = sequence
                    stands_for(func, inst, answer)
                    at += len(sequence)
                    changed = True
            if changed:
                self.touched.append(func)
        return bool(self.touched)


class _Emitter:
    """The instructions of one rewrite, in the order they are made."""

    def __init__(self, module: Module, span: object) -> None:
        self.module = module
        self.span = span
        self.made: list[Instruction] = []

    def put(self, inst: Instruction) -> Instruction:
        """Keep *inst* and answer it."""
        self.made.append(inst)
        return inst

    def const(self, ty: IntType, number: int) -> Value:
        """*number* as a constant of *ty*."""
        return self.module.int_const(ty, number)

    def binary(self, op: BinOp, lhs: Value, rhs: Value | int) -> Value:
        """`lhs op rhs`, a number on the right being a constant of lhs's type."""
        if isinstance(rhs, int):
            ty = lhs.ty
            assert isinstance(ty, IntType)
            rhs = self.const(ty, rhs)
        return self.put(BinaryInst(op, lhs, rhs, self.span))

    def cast(self, kind: CastKind, value: Value, ty: Type) -> Value:
        """*value* converted to *ty*."""
        return self.put(CastInst(kind, value, ty, self.span))

    def upper(self, value: Value, multiplier: int) -> Value:
        """The upper word of *value* times *multiplier*, read as *value*'s type
        reads it: signed for a signed word."""
        ty = value.ty
        assert isinstance(ty, IntType)
        product = self.put(BinaryInst(BinOp.WIDE_MUL, value,
                                      self.const(ty, multiplier), self.span,
                                      self.module.types.tuple_type((ty, U64))))
        return self.put(ExtractInst(product, 0, ty, self.span))


def _rewritten(module: Module, inst: BinaryInst
               ) -> tuple[list[Instruction], Value] | None:
    """What replaces *inst*, and the value standing for it, where it divides
    by a constant this rewrites."""
    remainder = _REWRITTEN.get(inst.op)
    if remainder is None:
        return None
    lhs, rhs = inst.operands
    if not isinstance(rhs, IntConst) or isinstance(lhs, IntConst):
        return None
    ty = inst.ty.ok if isinstance(inst.ty, ResultType) else inst.ty
    if not isinstance(ty, IntType) or lhs.ty is not ty:
        return None
    signed = inst.op in (BinOp.SDIV, BinOp.SREM)
    d = rhs.value
    if d == 0 or (signed and d == -1) or ty.signed is not signed:
        return None
    out = _Emitter(module, inst.span)
    word = I64 if signed else U64
    # The dividend as a word: widened where it is narrower, its unit dropped.
    if ty.bits == 64:
        x = lhs if ty is word else out.cast(CastKind.BITCAST, lhs, word)
    else:
        x = out.cast(CastKind.SEXT if signed else CastKind.ZEXT, lhs, word)
    q = (_signed_quotient(out, x, d) if signed
         else _unsigned_quotient(out, x, d, (1 << ty.bits) - 1))
    answer = q
    if remainder:
        answer = out.binary(BinOp.WRAP_SUB, x,
                            out.binary(BinOp.WRAP_MUL, q, d))
    # And back to the type it was asked of.
    if ty.bits == 64:
        if ty is not word:
            answer = out.cast(CastKind.BITCAST, answer, ty)
    else:
        answer = out.cast(CastKind.TRUNC, answer, ty)
    if isinstance(inst.ty, ResultType):
        answer = out.put(WrapInst(answer, module.bool_const(BOOL, False),
                                  inst.ty, inst.span))
    return out.made, answer


def _unsigned_quotient(out: _Emitter, x: Value, d: int, largest: int) -> Value:
    """`x ÷ d` for a word *x* no larger than *largest*."""
    if d == 1:
        return x
    if d & (d - 1) == 0:
        return out.binary(BinOp.LSHR, x, d.bit_length() - 1)
    found = unsigned_magic(d, largest)
    if found is not None:
        m, s = found
        high = out.upper(x, m)
        return out.binary(BinOp.LSHR, high, s) if s else high
    if d % 2 == 0:
        twos = (d & -d).bit_length() - 1
        found = unsigned_magic(d >> twos, largest >> twos)
        if found is not None:
            m, s = found
            high = out.upper(out.binary(BinOp.LSHR, x, twos), m)
            return out.binary(BinOp.LSHR, high, s) if s else high
    # The multiplier one bit wider than a word: its lower 64 bits, and the bit
    # above them added back without going past the word.
    shift = (d - 1).bit_length()
    m = -(-(1 << (64 + shift)) // d) - _WORD
    t = out.upper(x, m)
    halfway = out.binary(BinOp.LSHR, out.binary(BinOp.WRAP_SUB, x, t), 1)
    return out.binary(BinOp.LSHR, out.binary(BinOp.WRAP_ADD, halfway, t),
                      shift - 1)


def _signed_quotient(out: _Emitter, x: Value, d: int) -> Value:
    """`x ÷ d`, toward nought, for a signed word *x*."""
    if d == 1:
        return x
    magnitude = abs(d)
    if magnitude & (magnitude - 1) == 0:
        k = magnitude.bit_length() - 1
        bias = out.binary(BinOp.AND, out.binary(BinOp.ASHR, x, 63),
                          magnitude - 1)
        q = out.binary(BinOp.ASHR, out.binary(BinOp.WRAP_ADD, x, bias), k)
        return out.binary(BinOp.WRAP_SUB, out.const(I64, 0), q) if d < 0 else q
    m, s = signed_magic(d)
    high = out.upper(x, m)
    if d > 0 and m < 0:
        high = out.binary(BinOp.WRAP_ADD, high, x)
    elif d < 0 and m > 0:
        high = out.binary(BinOp.WRAP_SUB, high, x)
    if s:
        high = out.binary(BinOp.ASHR, high, s)
    return out.binary(BinOp.WRAP_SUB, high, out.binary(BinOp.ASHR, high, 63))
