// Dividing by a constant without the machine's division.  A port of pypl4g's
// opt/passes/divisions.py.
//
// `udiv`, `urem`, `sdiv` and `srem` by a constant other than nought -- and,
// signed, other than minus one -- become the upper word of a product and
// shifts, Granlund and Montgomery's method: a shift for a power of two, the
// multiplier for anything else, the dividend shifted down by the divisor's
// twos first or the multiplier one bit wider where none fits, and for a signed
// one Hacker's Delight's multiplier with the corrections for the sign.  All of
// it at 64 bits, a narrower dividend widened first and the answer narrowed
// after; a remainder is the dividend less the quotient times the divisor.
#include "opt/passes.hh"

#include <bit>
#include <optional>
#include <utility>

#include "ir/builder.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    using u128 = unsigned __int128;

    constexpr u128 word = u128(1) << 64;

    // The multiplier and the shift that divide every number up to `largest`
    // by `d`; nothing where the multiplier would not fit a word.
    std::optional<std::pair<u128, int>> unsigned_magic(u128 d, u128 largest)
    {
      for (int s = 0; s < 64; ++s) {
        auto power = u128(1) << (64 + s);
        auto m = (power + d - 1) / d;
        if (m >= word)
          return std::nullopt;
        if ((m * d - power) * largest < power)
          return std::pair{ m, s };
      }
      return std::nullopt;
    }

    // Hacker's Delight's multiplier and shift for a signed 64-bit division by
    // `d`, neither nought nor one nor minus one nor a power of two.
    std::pair<__int128, int> signed_magic(__int128 d)
    {
      const u128 two63 = u128(1) << 63;
      u128 ad = d < 0 ? u128(-d) : u128(d);
      u128 t = two63 + (d < 0 ? 1 : 0);
      u128 anc = t - 1 - t % ad;
      int p = 63;
      u128 q1 = two63 / anc, r1 = two63 % anc;
      u128 q2 = two63 / ad, r2 = two63 % ad;
      while (true) {
        ++p;
        q1 *= 2;
        r1 *= 2;
        if (r1 >= anc) {
          ++q1;
          r1 -= anc;
        }
        q2 *= 2;
        r2 *= 2;
        if (r2 >= ad) {
          ++q2;
          r2 -= ad;
        }
        auto delta = ad - r2;
        if (! (q1 < delta || (q1 == delta && r1 == 0)))
          break;
      }
      auto m = (q2 + 1) % word;
      if (d < 0)
        m = (word - m) % word;
      __int128 signed_m = m >= two63 ? __int128(m) - __int128(word) : __int128(m);
      return { signed_m, p - 64 };
    }

    // The instructions of one rewrite, in the order they are made.
    class Emitter {
    public:
      Emitter(Module& module, Function& func, Span span) : module(module), func(func), span(span) {}

      std::vector<Instruction*> made;

      Instruction* put(Instruction* inst)
      {
        inst->span = span;
        made.push_back(inst);
        return inst;
      }

      Value* constant(const Type* ty, __int128 number) { return const_cast<IntConst*>(module.int_const(ty, number)); }

      Value* binary(BinOp op, Value* lhs, Value* rhs, const Type* ty = nullptr)
      {
        auto inst = func.make(InstKind::BinaryInst, ty == nullptr ? lhs->ty : ty);
        inst->bin = op;
        inst->operands = { lhs, rhs };
        return put(inst);
      }

      // `lhs op rhs`, the number a constant of lhs's type.
      Value* binary_n(BinOp op, Value* lhs, __int128 rhs) { return binary(op, lhs, constant(lhs->ty, rhs)); }

      Value* cast(CastKind kind, Value* value, const Type* ty)
      {
        auto inst = func.make(InstKind::CastInst, ty);
        inst->cast = kind;
        inst->operands = { value };
        return put(inst);
      }

      // The upper word of `value` times `multiplier`, read as `value`'s type
      // reads it: signed for a signed word.
      Value* upper(Value* value, __int128 multiplier)
      {
        auto product = binary(BinOp::wide_mul, value, constant(value->ty, multiplier),
                              tuple_type({ value->ty, int_type(64, false) }));
        auto inst = func.make(InstKind::ExtractInst, value->ty);
        inst->index = 0;
        inst->operands = { product };
        return put(inst);
      }

      Value* wrap(Value* value, const Type* result)
      {
        auto inst = func.make(InstKind::WrapInst, result);
        inst->operands = { value, const_cast<BoolConst*>(module.bool_const(false)) };
        return put(inst);
      }

    private:
      Module& module;
      Function& func;
      Span span;
    };

    int bit_length(u128 value)
    {
      int count = 0;
      for (; value != 0; value >>= 1)
        ++count;
      return count;
    }

    // `x ÷ d` for a word `x` no larger than `largest`.
    Value* unsigned_quotient(Emitter& out, Value* x, u128 d, u128 largest)
    {
      if (d == 1)
        return x;
      if ((d & (d - 1)) == 0)
        return out.binary_n(BinOp::lshr, x, bit_length(d) - 1);
      if (auto found = unsigned_magic(d, largest)) {
        auto [m, s] = *found;
        auto high = out.upper(x, __int128(m));
        return s != 0 ? out.binary_n(BinOp::lshr, high, s) : high;
      }
      if (d % 2 == 0) {
        int twos = bit_length(d & (~d + 1)) - 1;
        if (auto found = unsigned_magic(d >> twos, largest >> twos)) {
          auto [m, s] = *found;
          auto high = out.upper(out.binary_n(BinOp::lshr, x, twos), __int128(m));
          return s != 0 ? out.binary_n(BinOp::lshr, high, s) : high;
        }
      }
      // The multiplier one bit wider than a word: its lower 64 bits, and the
      // bit above them added back without going past the word.
      int shift = bit_length(d - 1);
      auto m = word * ((u128(1) << shift) - d) / d + 1;
      auto t = out.upper(x, __int128(m));
      auto halfway = out.binary_n(BinOp::lshr, out.binary(BinOp::wrap_sub, x, t), 1);
      return out.binary_n(BinOp::lshr, out.binary(BinOp::wrap_add, halfway, t), shift - 1);
    }

    // `x ÷ d`, toward nought, for a signed word `x`.
    Value* signed_quotient(Emitter& out, Value* x, __int128 d)
    {
      if (d == 1)
        return x;
      u128 magnitude = d < 0 ? u128(-d) : u128(d);
      if ((magnitude & (magnitude - 1)) == 0) {
        int k = bit_length(magnitude) - 1;
        auto bias = out.binary_n(BinOp::and_, out.binary_n(BinOp::ashr, x, 63), __int128(magnitude - 1));
        auto q = out.binary_n(BinOp::ashr, out.binary(BinOp::wrap_add, x, bias), k);
        return d < 0 ? out.binary(BinOp::wrap_sub, out.constant(int_type(64, true), 0), q) : q;
      }
      auto [m, s] = signed_magic(d);
      auto high = out.upper(x, m);
      if (d > 0 && m < 0)
        high = out.binary(BinOp::wrap_add, high, x);
      else if (d < 0 && m > 0)
        high = out.binary(BinOp::wrap_sub, high, x);
      if (s != 0)
        high = out.binary_n(BinOp::ashr, high, s);
      return out.binary(BinOp::wrap_sub, high, out.binary_n(BinOp::ashr, high, 63));
    }

    // What replaces `inst`, and the value standing for it, where it divides by
    // a constant this rewrites.
    std::optional<std::pair<std::vector<Instruction*>, Value*>> rewritten(Module& module, Function& func,
                                                                         Instruction* inst)
    {
      bool remainder;
      bool is_signed;
      switch (inst->bin) {
      case BinOp::udiv: remainder = false; is_signed = false; break;
      case BinOp::urem: remainder = true; is_signed = false; break;
      case BinOp::sdiv: remainder = false; is_signed = true; break;
      case BinOp::srem: remainder = true; is_signed = true; break;
      default: return std::nullopt;
      }
      auto lhs = inst->operands[0], rhs = inst->operands[1];
      if (rhs->vkind != ValueKind::int_const || lhs->vkind == ValueKind::int_const)
        return std::nullopt;
      auto result = as<ResultType>(inst->ty);
      auto ty = as<IntType>(result != nullptr ? result->ok : inst->ty);
      if (ty == nullptr || lhs->ty != ty)
        return std::nullopt;
      auto d = static_cast<const IntConst*>(rhs)->value;
      if (d == 0 || (is_signed && d == -1) || ty->is_signed != is_signed)
        return std::nullopt;
      Emitter out(module, func, inst->span);
      auto word_ty = int_type(64, is_signed);
      // The dividend as a word: widened where it is narrower, its unit dropped.
      Value* x = lhs;
      if (ty->bits == 64) {
        if (ty != word_ty)
          x = out.cast(CastKind::bitcast, lhs, word_ty);
      } else {
        x = out.cast(is_signed ? CastKind::sext : CastKind::zext, lhs, word_ty);
      }
      auto q = is_signed ? signed_quotient(out, x, d)
                         : unsigned_quotient(out, x, u128(d), (u128(1) << ty->bits) - 1);
      auto answer = q;
      if (remainder)
        answer = out.binary(BinOp::wrap_sub, x, out.binary_n(BinOp::wrap_mul, q, d));
      // And back to the type it was asked of.
      if (ty->bits == 64) {
        if (ty != word_ty)
          answer = out.cast(CastKind::bitcast, answer, ty);
      } else {
        answer = out.cast(CastKind::trunc, answer, ty);
      }
      if (result != nullptr)
        answer = out.wrap(answer, inst->ty);
      return std::pair{ std::move(out.made), answer };
    }

  } // anonymous namespace


  bool divisions(Module& module)
  {
    bool touched = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration())
        continue;
      for (auto& block : func->blocks)
        for (std::size_t at = 0; at < block->insts.size();) {
          auto inst = block->insts[at];
          auto made = inst->kind == InstKind::BinaryInst ? rewritten(module, *func, inst) : std::nullopt;
          if (! made) {
            ++at;
            continue;
          }
          auto& [sequence, answer] = *made;
          for (auto one : sequence)
            one->parent = block.get();
          block->insts.erase(block->insts.begin() + std::ptrdiff_t(at));
          block->insts.insert(block->insts.begin() + std::ptrdiff_t(at), sequence.begin(), sequence.end());
          stands_for(*func, inst, answer);
          at += sequence.size();
          touched = true;
        }
    }
    return touched;
  }

} // namespace pl4g::opt
