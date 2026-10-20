// Constant folding.  A port of pypl4g's opt/passes/constfold.py.
//
// An operation on constants is replaced by the constant it computes, checked
// against the range of its type rather than wrapped silently; a comparison of
// constants folds to a truth value; and a check whose condition folded to a
// truth goes with it.  A check that folded to a falsehood stays: the program
// stops when it runs, which is what it should do.
#include "opt/passes.hh"

#include <optional>
#include <unordered_map>
#include <unordered_set>

#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    // The number a constant operand stands for, where it is a constant.
    std::optional<__int128> as_number(const Value* value)
    {
      switch (value->vkind) {
      case ValueKind::int_const: return static_cast<const IntConst*>(value)->value;
      case ValueKind::bool_const: return static_cast<const BoolConst*>(value)->value ? 1 : 0;
      case ValueKind::char_const: return static_cast<const CharConst*>(value)->value;
      default: return std::nullopt;
      }
    }

    const IntConst* int_of(const Value* value)
    {
      return value->vkind == ValueKind::int_const ? static_cast<const IntConst*>(value) : nullptr;
    }

    Value* mut(const Value* value) { return const_cast<Value*>(value); }

    // `value` as the low bits of it, read the way its type reads them.
    __int128 wrapped(__int128 value, const IntType* ty)
    {
      auto bits = static_cast<unsigned __int128>(value);
      if (ty->bits < 128)
        bits &= (static_cast<unsigned __int128>(1) << ty->bits) - 1;
      auto found = static_cast<__int128>(bits);
      if (ty->is_signed && found > ty->high())
        found -= static_cast<__int128>(static_cast<unsigned __int128>(1) << ty->bits);
      return found;
    }

    // *left* divided by *right*, truncated toward zero, and what is left of
    // it with the sign of *left*: what C++ does already.
    bool is_division(BinOp op)
    {
      return op == BinOp::sdiv || op == BinOp::udiv || op == BinOp::srem || op == BinOp::urem;
    }

    bool is_saturating(BinOp op) { return op == BinOp::sat_add || op == BinOp::sat_sub || op == BinOp::sat_mul; }
    bool is_wrapping(BinOp op) { return op == BinOp::wrap_add || op == BinOp::wrap_sub || op == BinOp::wrap_mul; }

    // What `op` answers of two numbers: exactly, or where the answer does not
    // fit 128 bits, the low bits of it and which side of nought it is on.
    struct Answer {
      __int128 value = 0;
      bool exact = true;
      bool negative = false;
    };

    // The operations that fold, which leaves out the ones that move bits.
    bool folds(BinOp op)
    {
      switch (op) {
      case BinOp::add: case BinOp::sub: case BinOp::mul: case BinOp::sat_add: case BinOp::sat_sub:
      case BinOp::sat_mul: case BinOp::udiv: case BinOp::urem: case BinOp::sdiv: case BinOp::srem:
      case BinOp::and_: case BinOp::or_: case BinOp::xor_: case BinOp::wrap_add: case BinOp::wrap_sub:
      case BinOp::wrap_mul:
        return true;
      default:
        return false;
      }
    }

    Answer folded(BinOp op, __int128 a, __int128 b)
    {
      switch (op) {
      case BinOp::add: case BinOp::sat_add: case BinOp::wrap_add: return { a + b };
      case BinOp::sub: case BinOp::sat_sub: case BinOp::wrap_sub: return { a - b };
      case BinOp::mul: case BinOp::sat_mul: case BinOp::wrap_mul: {
        __int128 out = 0;
        if (! __builtin_mul_overflow(a, b, &out))
          return { out };
        auto low = static_cast<__int128>(static_cast<unsigned __int128>(a) * static_cast<unsigned __int128>(b));
        return { low, false, (a < 0) != (b < 0) };
      }
      // The values reaching here are in the range of their type, so what C++
      // answers is what the machine would: truncated toward nought.
      case BinOp::udiv: case BinOp::sdiv: return { a / b };
      case BinOp::urem: case BinOp::srem: return { a % b };
      case BinOp::and_: return { a & b };
      case BinOp::or_: return { a | b };
      case BinOp::xor_: return { a ^ b };
      default: return {};
      }
    }

    // What leaves a number as it is on the right of each operation, and on the
    // left for the ones whose sides may change places.
    std::optional<__int128> neutral_right(BinOp op)
    {
      switch (op) {
      case BinOp::add: case BinOp::sub: case BinOp::wrap_add: case BinOp::wrap_sub: case BinOp::sat_add:
      case BinOp::sat_sub: case BinOp::or_: case BinOp::xor_:
        return 0;
      case BinOp::mul: case BinOp::wrap_mul: case BinOp::sat_mul:
        return 1;
      default:
        return std::nullopt;
      }
    }

    std::optional<__int128> neutral_left(BinOp op)
    {
      switch (op) {
      case BinOp::add: case BinOp::wrap_add: case BinOp::sat_add: case BinOp::or_: case BinOp::xor_:
        return 0;
      case BinOp::mul: case BinOp::wrap_mul: case BinOp::sat_mul:
        return 1;
      default:
        return std::nullopt;
      }
    }

    // The operand `inst` leaves as it is, where the other one is neutral.
    Value* unchanged(const Instruction* inst)
    {
      if (inst->ty == bool_type())
        return nullptr;
      auto lhs = inst->operands[0], rhs = inst->operands[1];
      auto right = int_of(rhs), left = int_of(lhs);
      if (right != nullptr && neutral_right(inst->bin) == right->value && lhs->ty == inst->ty)
        return lhs;
      if (left != nullptr && neutral_left(inst->bin) == left->value && rhs->ty == inst->ty)
        return rhs;
      if (auto ty = as<IntType>(inst->ty); inst->bin == BinOp::and_ && ty != nullptr) {
        __int128 every = ty->is_signed ? -1 : static_cast<__int128>((static_cast<unsigned __int128>(1) << ty->bits) - 1);
        if (right != nullptr && right->value == every && lhs->ty == inst->ty)
          return lhs;
        if (left != nullptr && left->value == every && rhs->ty == inst->ty)
          return rhs;
      }
      return nullptr;
    }

    bool is_bitcast(const Value* value)
    {
      return value->vkind == ValueKind::instruction && static_cast<const Instruction*>(value)->kind == InstKind::CastInst
             && static_cast<const Instruction*>(value)->cast == CastKind::bitcast;
    }

    // Where `value` points, as a place and a number of bytes past it, where
    // that can be read off bitcasts and constants added on.
    std::optional<std::pair<const Value*, __int128>> place_and_offset(const Value* value)
    {
      const Value* seen = value;
      __int128 offset = 0;
      while (true) {
        if (is_bitcast(seen) && is<PtrType>(static_cast<const Instruction*>(seen)->operands[0]->ty)) {
          seen = static_cast<const Instruction*>(seen)->operands[0];
          continue;
        }
        if (seen->vkind == ValueKind::instruction) {
          auto inst = static_cast<const Instruction*>(seen);
          if (inst->kind == InstKind::BinaryInst && (inst->bin == BinOp::add || inst->bin == BinOp::wrap_add)
              && is<PtrType>(inst->ty) && int_of(inst->operands[1]) != nullptr) {
            offset += int_of(inst->operands[1])->value;
            seen = inst->operands[0];
            continue;
          }
        }
        break;
      }
      if (! is<PtrType>(seen->ty))
        return std::nullopt;
      return std::pair{ seen, offset };
    }

    // Whether two pointers are one: the same value, or the same part taken
    // out of the same value twice.
    bool same_place(const Value* one, const Value* other)
    {
      if (one == other)
        return true;
      auto a = one->vkind == ValueKind::instruction ? static_cast<const Instruction*>(one) : nullptr;
      auto b = other->vkind == ValueKind::instruction ? static_cast<const Instruction*>(other) : nullptr;
      return a != nullptr && b != nullptr && a->kind == InstKind::ExtractInst && b->kind == InstKind::ExtractInst
             && a->index == b->index && a->operands[0] == b->operands[0];
    }

    // How far apart two addresses taken as words are.
    std::optional<__int128> apart(const Instruction* inst)
    {
      if (inst->bin != BinOp::sub && inst->bin != BinOp::wrap_sub)
        return std::nullopt;
      auto lhs = inst->operands[0], rhs = inst->operands[1];
      auto cast_inst = [](const Value* v) {
        return v->vkind == ValueKind::instruction && static_cast<const Instruction*>(v)->kind == InstKind::CastInst;
      };
      if (! cast_inst(lhs) || ! cast_inst(rhs))
        return std::nullopt;
      auto one = place_and_offset(lhs), other = place_and_offset(rhs);
      if (! one || ! other || ! same_place(one->first, other->first))
        return std::nullopt;
      return one->second - other->second;
    }

    bool compares(CmpPred pred, __int128 a, __int128 b)
    {
      switch (pred) {
      case CmpPred::eq: return a == b;
      case CmpPred::ne: return a != b;
      case CmpPred::slt: case CmpPred::ult: return a < b;
      case CmpPred::sle: case CmpPred::ule: return a <= b;
      case CmpPred::sgt: case CmpPred::ugt: return a > b;
      case CmpPred::sge: case CmpPred::uge: return a >= b;
      }
      return false;
    }

    bool reflexive(CmpPred pred)
    {
      return pred == CmpPred::eq || pred == CmpPred::sle || pred == CmpPred::sge || pred == CmpPred::ule
             || pred == CmpPred::uge;
    }


    class Folding {
    public:
      explicit Folding(Module& module) : module(module) {}

      // Fold once, and say whether anything folded.
      bool sweep(Function& func)
      {
        std::unordered_map<const Value*, Value*> replacements;
        std::unordered_set<const Value*> settled;
        for (auto& block : func.blocks)
          for (auto inst : block->insts) {
            if (inst->kind == InstKind::AssertInst) {
              auto condition = inst->operands[0];
              if (condition->vkind == ValueKind::bool_const && static_cast<const BoolConst*>(condition)->value)
                settled.insert(inst);
              continue;
            }
            if (auto found = fold(inst))
              replacements[inst] = found;
          }
        if (replacements.empty() && settled.empty())
          return false;
        replace_uses(func, replacements, settled);
        return true;
      }

    private:
      // A constant read as another type, where the operand is one.
      Value* reread(const Instruction* inst)
      {
        auto number = as_number(inst->operands[0]);
        if (! number)
          return nullptr;
        if (auto ty = as<IntType>(inst->ty))
          return mut(module.int_const(ty, *number));
        if (inst->ty == char_type())
          return mut(module.char_const(std::uint32_t(*number)));
        return nullptr;
      }

      Value* truth(bool value) { return mut(module.bool_const(value)); }

      Value* fold(const Instruction* inst)
      {
        switch (inst->kind) {
        case InstKind::CmpInst:
          return fold_comparison(inst);
        case InstKind::SelectInst: {
          // A choice made by a truth value that is known is the one it makes,
          // and one between a value and itself is that value.
          auto chooser = inst->operands[0], first = inst->operands[1], second = inst->operands[2];
          if (chooser->vkind == ValueKind::bool_const)
            return static_cast<const BoolConst*>(chooser)->value ? first : second;
          return first == second ? first : nullptr;
        }
        case InstKind::CastInst:
          if (inst->cast != CastKind::bitcast)
            return nullptr;
          // Read as what it already is, which is reading it; or the same bits
          // of a constant read as something else, which is a constant.
          if (inst->operands[0]->ty == inst->ty)
            return inst->operands[0];
          return reread(inst);
        case InstKind::ExtractInst: {
          // One part of a value put together from its parts is that part.
          auto whole = inst->operands[0];
          if (whole->vkind == ValueKind::instruction) {
            auto made = static_cast<const Instruction*>(whole);
            if (made->kind == InstKind::TupleInst && inst->index < std::int64_t(made->operands.size())) {
              auto part = made->operands[std::size_t(inst->index)];
              if (same(part->ty, inst->ty))
                return part;
            }
          }
          return nullptr;
        }
        case InstKind::BinaryInst:
          return fold_binary(inst);
        default:
          return nullptr;
        }
      }

      Value* fold_binary(const Instruction* inst)
      {
        if (! folds(inst->bin))
          return nullptr;
        auto lhs = inst->operands[0], rhs = inst->operands[1];
        if (auto kept = unchanged(inst))
          return kept;
        if (auto distance = apart(inst); distance && is<IntType>(inst->ty))
          return mut(module.int_const(inst->ty, wrapped(*distance, as<IntType>(inst->ty))));
        if (is_division(inst->bin) && int_of(rhs) != nullptr && int_of(rhs)->value == 0)
          // Nothing to fold and nothing to report: the program stops when it
          // runs, and saying so is the checker's business.
          return nullptr;
        if (inst->ty == bool_type()) {
          // The logical operators are these same instructions asked of values
          // that are one or nought, and what they answer is a truth value.
          auto a = as_number(lhs), b = as_number(rhs);
          if (! a || ! b || (is_division(inst->bin) && *b == 0))
            return nullptr;
          return truth(folded(inst->bin, *a, *b).value != 0);
        }
        auto left = int_of(lhs), right = int_of(rhs);
        auto ty = as<IntType>(inst->ty);
        if (left == nullptr || right == nullptr || ty == nullptr)
          return nullptr;
        auto answer = folded(inst->bin, left->value, right->value);
        __int128 value = answer.value;
        if (is_saturating(inst->bin)) {
          // These do not go past the ends of their type.
          if (! answer.exact)
            value = answer.negative ? ty->low() : ty->high();
          else
            value = std::max(ty->low(), std::min(ty->high(), value));
        } else if (is_wrapping(inst->bin))
          value = wrapped(value, ty);
        else if (! answer.exact || ! ty->holds(value))
          return nullptr;
        return mut(module.int_const(ty, value));
      }

      Value* fold_comparison(const Instruction* inst)
      {
        auto lhs = inst->operands[0], rhs = inst->operands[1];
        if (lhs == rhs && (is<IntType>(lhs->ty) || is<BoolType>(lhs->ty) || is<CharType>(lhs->ty) || is<PtrType>(lhs->ty)))
          // A value compared with itself; not for floating point, where a
          // value that is not a number is not equal to itself.
          return truth(reflexive(inst->pred));
        auto a = as_number(lhs), b = as_number(rhs);
        if (! a || ! b) {
          // Two places at known distances from one.
          auto one = place_and_offset(lhs), other = place_and_offset(rhs);
          if (! one || ! other || ! same_place(one->first, other->first)
              || (inst->pred != CmpPred::eq && inst->pred != CmpPred::ne))
            return nullptr;
          return truth(compares(inst->pred, one->second, other->second));
        }
        return truth(compares(inst->pred, *a, *b));
      }

      Module& module;
    };

  } // anonymous namespace


  bool constant_folding(Module& module)
  {
    Folding folding(module);
    bool changed = false;
    for (auto& [_, func] : module.functions)
      while (folding.sweep(*func))
        changed = true;
    return changed;
  }

} // namespace pl4g::opt
