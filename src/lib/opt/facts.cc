// What a branch already settled, and what a value can be, used where it is
// known to hold.  A port of pypl4g's opt/passes/facts.py, which says why.
//
// Every whole number has a range, the smallest and the largest it can be,
// worked out over the SSA form from what each operation does to its operands'
// ranges -- each narrowed by the facts that hold where the operation is.  A
// fact is a comparison that went one way: on the edge into a block that edge
// is the only way into, after a check, through `∧` and through an `and` joined
// from the one way that makes it true.  Facts about two values a constant
// apart are kept as how far apart they are.  A check that holds goes, an
// operation that cannot fail is the wrapping one, and a comparison whose answer
// is known is that answer.
//
// Every answer here is the one pypl4g comes to whatever order the facts are
// kept in: the tightest bound wins, and a value unequal to an end moves past it
// as long as it can.  The numbers are `__int128`, which holds every value of
// every type and a sum or a difference of two; a product too large for it is
// beyond every type's ends, which is all that is asked of it.
#include "opt/passes.hh"

#include <algorithm>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <tuple>
#include <unordered_map>
#include <unordered_set>

#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    using Int = __int128;

    // The smallest and the largest a value can be, as one reading of its bits
    // has it.
    struct Range {
      Int lo = 0;
      Int hi = 0;
      bool operator==(const Range&) const = default;
    };

    using Fact = std::tuple<CmpPred, const Value*, const Value*>;

    // How many times a block's parameter may widen before a bound still moving
    // goes to the end of its type, and how many times a function is walked at
    // most.
    constexpr int widenings = 1;
    constexpr int rounds = 40;


    // What a comparison asks when it went the other way, and with its sides
    // swapped.
    CmpPred negated(CmpPred pred)
    {
      switch (pred) {
      case CmpPred::eq: return CmpPred::ne;
      case CmpPred::ne: return CmpPred::eq;
      case CmpPred::ult: return CmpPred::uge;
      case CmpPred::uge: return CmpPred::ult;
      case CmpPred::ule: return CmpPred::ugt;
      case CmpPred::ugt: return CmpPred::ule;
      case CmpPred::slt: return CmpPred::sge;
      case CmpPred::sge: return CmpPred::slt;
      case CmpPred::sle: return CmpPred::sgt;
      case CmpPred::sgt: return CmpPred::sle;
      }
      return pred;
    }

    CmpPred swapped(CmpPred pred)
    {
      switch (pred) {
      case CmpPred::ult: return CmpPred::ugt;
      case CmpPred::ugt: return CmpPred::ult;
      case CmpPred::ule: return CmpPred::uge;
      case CmpPred::uge: return CmpPred::ule;
      case CmpPred::slt: return CmpPred::sgt;
      case CmpPred::sgt: return CmpPred::slt;
      case CmpPred::sle: return CmpPred::sge;
      case CmpPred::sge: return CmpPred::sle;
      default: return pred;
      }
    }

    // What else holds where one holds.
    std::vector<CmpPred> implied(CmpPred pred)
    {
      switch (pred) {
      case CmpPred::ult: return { CmpPred::ule, CmpPred::ne };
      case CmpPred::ugt: return { CmpPred::uge, CmpPred::ne };
      case CmpPred::slt: return { CmpPred::sle, CmpPred::ne };
      case CmpPred::sgt: return { CmpPred::sge, CmpPred::ne };
      case CmpPred::eq: return { CmpPred::ule, CmpPred::uge, CmpPred::sle, CmpPred::sge };
      default: return {};
      }
    }

    // Whether a comparison reads its operands signed.
    bool reads_signed(CmpPred pred)
    {
      return pred == CmpPred::slt || pred == CmpPred::sle || pred == CmpPred::sgt || pred == CmpPred::sge;
    }

    // The orderings as what they ask of `a - b`: at most minus one for "less
    // than", at most nought for "at most" -- and the others, which are these
    // with their sides swapped.
    std::optional<Int> less_of(CmpPred pred)
    {
      switch (pred) {
      case CmpPred::ult: case CmpPred::slt: return -1;
      case CmpPred::ule: case CmpPred::sle: return 0;
      default: return std::nullopt;
      }
    }

    std::optional<Int> more_of(CmpPred pred)
    {
      switch (pred) {
      case CmpPred::ugt: case CmpPred::sgt: return -1;
      case CmpPred::uge: case CmpPred::sge: return 0;
      default: return std::nullopt;
      }
    }

    // The plain operation a checked one becomes where it cannot fail.
    std::optional<BinOp> plain_op(BinOp op)
    {
      switch (op) {
      case BinOp::add: case BinOp::sat_add: return BinOp::wrap_add;
      case BinOp::sub: case BinOp::sat_sub: return BinOp::wrap_sub;
      case BinOp::mul: case BinOp::sat_mul: return BinOp::wrap_mul;
      case BinOp::shl: return BinOp::wrap_shl;
      case BinOp::lshr: return BinOp::wrap_lshr;
      case BinOp::ashr: return BinOp::wrap_ashr;
      case BinOp::rotl: return BinOp::wrap_rotl;
      case BinOp::rotr: return BinOp::wrap_rotr;
      default: return std::nullopt;
      }
    }

    bool is_shift(BinOp op)
    {
      switch (op) {
      case BinOp::shl: case BinOp::lshr: case BinOp::ashr: case BinOp::rotl: case BinOp::rotr:
      case BinOp::wrap_shl: case BinOp::wrap_lshr: case BinOp::wrap_ashr: case BinOp::wrap_rotl:
      case BinOp::wrap_rotr:
        return true;
      default:
        return false;
      }
    }

    bool is_step(BinOp op)
    {
      return op == BinOp::add || op == BinOp::sub || op == BinOp::wrap_add || op == BinOp::wrap_sub;
    }

    const Instruction* as_inst(const Value* value, InstKind kind)
    {
      if (value->vkind != ValueKind::instruction)
        return nullptr;
      auto inst = static_cast<const Instruction*>(value);
      return inst->kind == kind ? inst : nullptr;
    }

    const IntConst* as_int(const Value* value)
    {
      return value->vkind == ValueKind::int_const ? static_cast<const IntConst*>(value) : nullptr;
    }

    // `value` with every cast that changes only its unit looked through.
    const Value* plain(const Value* value)
    {
      while (auto cast = as_inst(value, InstKind::CastInst)) {
        if (cast->cast != CastKind::bitcast && cast->cast != CastKind::zext && cast->cast != CastKind::sext)
          break;
        auto inner = cast->operands[0];
        auto outer_ty = as<IntType>(cast->ty), inner_ty = as<IntType>(inner->ty);
        if (outer_ty == nullptr || inner_ty == nullptr || outer_ty->bits != inner_ty->bits)
          break;
        value = inner;
      }
      return value;
    }

    // Every value `bits` bits hold, read signed or not.
    Range bounds(unsigned bits, bool is_signed)
    {
      if (is_signed)
        return { -(Int(1) << (bits - 1)), (Int(1) << (bits - 1)) - 1 };
      return { 0, (Int(1) << bits) - 1 };
    }

    Range whole_of(const IntType* ty) { return bounds(ty->bits, ty->is_signed); }

    // `found`, the values of some bits read signed where `from` says so, as they
    // read the way `to` says: the same range moved where every value in it has
    // its top bit alike, and every value there is where not.
    Range reread(Range found, unsigned bits, bool from, bool to)
    {
      if (from == to)
        return found;
      Int whole = Int(1) << bits;
      if (from) {
        if (found.lo >= 0)
          return found;
        if (found.hi < 0)
          return { found.lo + whole, found.hi + whole };
        return bounds(bits, false);
      }
      Int half = Int(1) << (bits - 1);
      if (found.hi < half)
        return found;
      if (found.lo >= half)
        return { found.lo - whole, found.hi - whole };
      return bounds(bits, true);
    }

    bool within(Range found, Range whole) { return whole.lo <= found.lo && found.hi <= whole.hi; }

    // How many bits a number takes, as Python's `int.bit_length`.
    int bit_length(Int value)
    {
      if (value < 0)
        value = -value;
      int found = 0;
      while (value != 0) {
        ++found;
        value >>= 1;
      }
      return found;
    }

    // A product, or something beyond every type's ends with its sign where the
    // product is too large to hold.
    Int times(Int left, Int right)
    {
      constexpr Int beyond = Int(1) << 126;
      if (bit_length(left) + bit_length(right) > 126)
        return (left < 0) != (right < 0) ? -beyond : beyond;
      return left * right;
    }


    // What is known where `cond` came out as `holds`.  `a ∧ b` that held is
    // both, `a ∨ b` that did not is neither, and `¬a` is `a` the other way.
    std::vector<Fact> facts_of(const Value* cond, bool holds)
    {
      if (auto binary = as_inst(cond, InstKind::BinaryInst); binary != nullptr && is<BoolType>(binary->ty)) {
        auto left = binary->operands[0], right = binary->operands[1];
        if ((binary->bin == BinOp::and_ && holds) || (binary->bin == BinOp::or_ && ! holds)) {
          auto found = facts_of(left, holds);
          auto more = facts_of(right, holds);
          found.insert(found.end(), more.begin(), more.end());
          return found;
        }
        if (binary->bin == BinOp::xor_ && right->vkind == ValueKind::bool_const)
          return facts_of(left, holds != static_cast<const BoolConst*>(right)->value);
        return {};
      }
      auto cmp = as_inst(cond, InstKind::CmpInst);
      if (cmp == nullptr)
        return {};
      auto pred = holds ? cmp->pred : negated(cmp->pred);
      auto left = plain(cmp->operands[0]), right = plain(cmp->operands[1]);
      std::vector<Fact> found;
      std::vector<CmpPred> all{ pred };
      for (auto one : implied(pred))
        all.push_back(one);
      for (auto one : all) {
        found.emplace_back(one, left, right);
        found.emplace_back(swapped(one), right, left);
      }
      return found;
    }


    // The facts that hold at one point of a function: as a set; the values each
    // is known to differ from; and, for two values a constant away from what
    // was compared, how far apart they are known to be at the least, the
    // tightest kept, by the pair and by each of the two.
    struct Known {
      struct Bound {
        Int most = 0;
        const Value* x = nullptr;
        const Value* y = nullptr;
      };
      using Key = std::tuple<const Value*, const Value*, bool>;
      using Side = std::vector<std::pair<const Value*, bool>>;

      std::set<Fact> facts;
      std::unordered_map<const Value*, std::vector<const Value*>> unequal;
      std::map<Key, Bound> bounds;
      std::unordered_map<const Value*, Side> upper;
      std::unordered_map<const Value*, Side> lower;

      // Add what `cond` coming out as `holds` says, `offset` saying what a
      // value is a constant away from.
      template<typename F>
      void learn(const Value* cond, bool holds, F&& offset)
      {
        for (const auto& fact : facts_of(cond, holds)) {
          if (! facts.insert(fact).second)
            continue;
          auto [pred, left, right] = fact;
          if (pred == CmpPred::ne)
            unequal[left].push_back(right);
          else if (auto less = less_of(pred)) {
            // The orderings the other way round are these with their sides
            // swapped, which are among the facts too.
            bool is_signed = reads_signed(pred);
            auto [x, at_x] = offset(left, is_signed);
            auto [y, at_y] = offset(right, is_signed);
            if (x != y)
              bound(x, y, is_signed, *less - at_x + at_y);
          }
        }
      }

      // Add everything `other` knows.
      void take(const Known& other)
      {
        for (const auto& [left, rights] : other.unequal)
          for (auto right : rights)
            if (! facts.contains({ CmpPred::ne, left, right }))
              unequal[left].push_back(right);
        facts.insert(other.facts.begin(), other.facts.end());
        for (const auto& [key, found] : other.bounds)
          bound(found.x, found.y, std::get<2>(key), found.most);
      }

      // Know that `x - y` is at most `most`.
      void bound(const Value* x, const Value* y, bool is_signed, Int most)
      {
        auto [at, made] = bounds.try_emplace({ x, y, is_signed }, Bound{ most, x, y });
        if (made) {
          upper[x].emplace_back(y, is_signed);
          lower[y].emplace_back(x, is_signed);
        } else if (most < at->second.most)
          at->second.most = most;
      }

      // What `x - y` is known to be at most, if anything.
      std::optional<Int> apart(const Value* x, const Value* y, bool is_signed) const
      {
        auto found = bounds.find({ x, y, is_signed });
        if (found == bounds.end())
          return std::nullopt;
        return found->second.most;
      }

      Int most(const Value* x, const Value* y, bool is_signed) const { return bounds.at({ x, y, is_signed }).most; }
    };


    // What an operation on the bits of two numbers can be, both read
    // unsigned -- or nothing, where that is every value there is.
    std::optional<Range> unsigned_op(BinOp op, Range left, Range right)
    {
      switch (op) {
      case BinOp::and_:
        return Range{ 0, std::min(left.hi, right.hi) };
      case BinOp::or_:
      case BinOp::xor_: {
        Int top = (Int(1) << bit_length(std::max(left.hi, right.hi))) - 1;
        return Range{ op == BinOp::or_ ? std::max(left.lo, right.lo) : 0, top };
      }
      case BinOp::udiv:
        if (right.lo > 0)
          return Range{ left.lo / right.hi, left.hi / right.lo };
        return std::nullopt;
      case BinOp::urem:
        if (right.lo > 0)
          return Range{ 0, std::min(left.hi, right.hi - 1) };
        return std::nullopt;
      case BinOp::umin:
        return Range{ std::min(left.lo, right.lo), std::min(left.hi, right.hi) };
      case BinOp::umax:
        return Range{ std::max(left.lo, right.lo), std::max(left.hi, right.hi) };
      default:
        return std::nullopt;
      }
    }

    // What `left pred right` answers for every value in the two ranges, where it
    // answers the same for all of them.
    std::optional<bool> compared(CmpPred pred, Range left, Range right)
    {
      switch (pred) {
      case CmpPred::ult: case CmpPred::slt:
        if (left.hi < right.lo)
          return true;
        if (left.lo >= right.hi)
          return false;
        break;
      case CmpPred::ule: case CmpPred::sle:
        if (left.hi <= right.lo)
          return true;
        if (left.lo > right.hi)
          return false;
        break;
      case CmpPred::ugt: case CmpPred::sgt:
        if (left.lo > right.hi)
          return true;
        if (left.hi <= right.lo)
          return false;
        break;
      case CmpPred::uge: case CmpPred::sge:
        if (left.lo >= right.hi)
          return true;
        if (left.hi < right.lo)
          return false;
        break;
      case CmpPred::eq: case CmpPred::ne:
        if (left.hi < right.lo || right.hi < left.lo)
          return pred == CmpPred::ne;
        if (left.lo == left.hi && left.hi == right.lo && right.lo == right.hi)
          return pred == CmpPred::eq;
        break;
      }
      return std::nullopt;
    }

    // Whether `inst` is something a range or a fact could settle.
    bool worth(const Instruction* inst)
    {
      switch (inst->kind) {
      case InstKind::AssertInst: return ! inst->observing;
      case InstKind::BinaryInst: return plain_op(inst->bin).has_value() && is<IntType>(inst->ty);
      case InstKind::CmpInst: return is<IntType>(inst->operands[0]->ty);
      default: return false;
      }
    }


    // What every value of one function can be, and what that settles.
    class Ranges {
    public:
      Ranges(Module& module, Function& func) : module(module), func(func) {}

      // Work the ranges out and change what they settle; say whether anything
      // changed.
      bool use()
      {
        bool any = false;
        for (const auto& block : func.blocks)
          for (auto inst : block->insts)
            any = any || worth(inst);
        if (! any)
          return false;
        tree = dominators(func);
        preds = predecessors(func);
        for (auto block : tree.order) {
          for (const auto& param : block->params)
            defined.insert(param.get());
          for (auto inst : block->insts)
            defined.insert(inst);
        }
        for (int round = 0; round < rounds; ++round)
          if (! walk())
            return apply();
        return false;
      }

    private:
      Module& module;
      Function& func;
      Dominators tree;
      std::unordered_map<const BasicBlock*, std::vector<BasicBlock*>> preds;
      // The range of every integer worked out so far, as its own type reads
      // it.  A value of the function not here yet has no range yet.
      std::unordered_map<const Value*, Range> ranges;
      std::unordered_set<const Value*> defined;
      std::unordered_map<const Value*, int> widened;
      std::unordered_map<const BasicBlock*, std::shared_ptr<Known>> at_end;
      // What the last walk found: the operations known to fit, the checks that
      // hold, the comparisons whose answer is known, and the blocks reached.
      std::unordered_set<const Value*> fits;
      std::vector<const Instruction*> settled;
      std::unordered_map<const Instruction*, bool> answers;
      std::unordered_set<const BasicBlock*> seen;

      auto offsetter()
      {
        return [this](const Value* value, bool is_signed) { return offset(value, is_signed); };
      }

      // Walk every block once; say whether the parameter of a block already
      // walked changed, which is what another walk is wanted for.
      bool walk()
      {
        bool changed = false;
        fits.clear();
        settled.clear();
        answers.clear();
        seen.clear();
        auto entry = tree.order.front();
        for (auto block : tree.order) {
          seen.insert(block);
          auto parent = tree.idom_of(block);
          const auto& ways_in = preds[block];
          const Instruction* last = ways_in.size() == 1 && ! ways_in.front()->insts.empty()
                                    ? ways_in.front()->insts.back() : nullptr;
          bool branched = last != nullptr && last->kind == InstKind::CondBrInst
                          && last->targets[0].block != last->targets[1].block;
          // What the block's dominator knew is what it knows, shared rather
          // than copied where the block learns nothing of its own.
          std::shared_ptr<Known> known;
          if (parent == nullptr)
            known = std::make_shared<Known>();
          else if (branched || std::ranges::any_of(block->insts, [](const Instruction* one) {
                     return one->kind == InstKind::AssertInst && ! one->observing;
                   }))
            known = std::make_shared<Known>(*at_end.at(parent));
          else
            known = at_end.at(parent);
          if (branched) {
            bool holds = last->targets[0].block == block;
            known->learn(last->operands[0], holds, offsetter());
            joined(*known, ways_in.front(), last->operands[0], holds);
          }
          if (block == entry)
            for (const auto& param : block->params)
              if (auto ty = as<IntType>(param->ty))
                ranges[param.get()] = whole_of(ty);
          for (auto inst : block->insts)
            visit(inst, *known);
          at_end[block] = known;
          changed |= hand_on(block);
        }
        return changed;
      }

      // Add to `known` what the one way into `branching` that can make its
      // parameter `cond` come out as `holds` says: `a and b` joined from `b`
      // where `a` held and a falsehood where it did not.
      void joined(Known& known, const BasicBlock* branching, const Value* cond, bool holds)
      {
        if (cond->vkind != ValueKind::block_param || static_cast<const BlockParam*>(cond)->block != branching)
          return;
        auto index = static_cast<const BlockParam*>(cond)->index;
        const BasicBlock* way = nullptr;
        const Value* arg = nullptr;
        for (auto one : preds[branching]) {
          if (one->insts.empty())
            continue;
          for (const auto& target : one->insts.back()->targets) {
            if (target.block != branching)
              continue;
            auto given = target.args[index];
            if (given->vkind == ValueKind::bool_const && static_cast<const BoolConst*>(given)->value != holds)
              continue;
            if (way != nullptr)
              return;
            way = one;
            arg = given;
          }
        }
        if (way == nullptr)
          return;
        auto there = at_end.find(way);
        if (there == at_end.end())
          return;
        known.take(*there->second);
        known.learn(arg, holds, offsetter());
      }

      // Widen each parameter of the blocks `block` goes to by what it hands
      // them; say whether one of a block already walked changed.
      bool hand_on(const BasicBlock* block)
      {
        if (block->insts.empty())
          return false;
        auto last = block->insts.back();
        const Known& known = *at_end.at(block);
        bool changed = false;
        for (std::size_t at = 0; at < last->targets.size(); ++at) {
          const auto& target = last->targets[at];
          if (std::ranges::none_of(target.block->params, [](const auto& one) { return is<IntType>(one->ty); }))
            continue;
          const Known* here = &known;
          Known edge;
          if (last->kind == InstKind::CondBrInst && last->targets[0].block != last->targets[1].block) {
            edge = known;
            edge.learn(last->operands[0], at == 0, offsetter());
            here = &edge;
          }
          for (std::size_t i = 0; i < target.block->params.size() && i < target.args.size(); ++i) {
            auto param = target.block->params[i].get();
            auto ty = as<IntType>(param->ty);
            if (ty == nullptr)
              continue;
            auto found = range_at(target.args[i], *here, ty->is_signed);
            if (found && widen(param, ty, *found))
              changed |= seen.contains(target.block);
          }
        }
        return changed;
      }

      // Make `param`'s range take `found` in; say whether it grew.
      bool widen(const BlockParam* param, const IntType* ty, Range found)
      {
        auto old = ranges.find(param);
        if (old == ranges.end()) {
          ranges[param] = found;
          return true;
        }
        Range now{ std::min(old->second.lo, found.lo), std::max(old->second.hi, found.hi) };
        if (now == old->second)
          return false;
        if (++widened[param] > widenings) {
          auto whole = whole_of(ty);
          if (now.lo < old->second.lo)
            now.lo = whole.lo;
          if (now.hi > old->second.hi)
            now.hi = whole.hi;
        }
        old->second = now;
        return true;
      }

      // Work out `inst`'s range, and what is known of it, where `known` holds.
      void visit(const Instruction* inst, Known& known)
      {
        if (inst->kind == InstKind::AssertInst) {
          auto cond = inst->operands[0];
          if (! inst->observing) {
            if (auto cmp = as_inst(cond, InstKind::CmpInst); cmp != nullptr && decide(cmp, known) == true)
              settled.push_back(inst);
            // What a check stops the program for not holding holds after it.
            known.learn(cond, true, offsetter());
          }
          return;
        }
        if (inst->kind == InstKind::CmpInst) {
          if (auto answer = decide(inst, known))
            answers[inst] = *answer;
          return;
        }
        auto ty = as<IntType>(inst->ty);
        if (ty == nullptr)
          return;
        if (auto found = range_of(inst, ty, known))
          ranges[inst] = *found;
        else
          ranges.erase(inst);
      }

      // `value`'s range as its type reads it, before anything narrows it -- or
      // nothing, where it has none yet.
      std::optional<Range> own(const Value* value) const
      {
        if (auto constant = as_int(value))
          return Range{ constant->value, constant->value };
        if (auto found = ranges.find(value); found != ranges.end())
          return found->second;
        if (defined.contains(value))
          return std::nullopt;
        auto ty = as<IntType>(value->ty);
        if (ty == nullptr)
          return std::nullopt;
        return whole_of(ty);
      }

      // `value`'s range where `known` holds, read signed or not.
      std::optional<Range> range_at(const Value* value, const Known& known, bool is_signed)
      {
        auto base = plain(value);
        auto ty = as<IntType>(base->ty);
        if (ty == nullptr)
          return std::nullopt;
        if (auto constant = as_int(base))
          return reread({ constant->value, constant->value }, ty->bits, ty->is_signed, is_signed);
        auto found = own(base);
        if (! found)
          return std::nullopt;
        return reread(narrowed(base, ty, *found, known), ty->bits, ty->is_signed, is_signed);
      }

      // `found`, the range of `base`, narrowed by every fact `known` has about
      // it or about what it is a constant away from.
      Range narrowed(const Value* base, const IntType* ty, Range found, const Known& known)
      {
        auto binary = as_inst(base, InstKind::BinaryInst);
        if (! known.unequal.contains(base) && ! known.upper.contains(base) && ! known.lower.contains(base)
            && ! (binary != nullptr && is_step(binary->bin)))
          return found;
        auto bits = ty->bits;
        bool is_signed = ty->is_signed;
        Range ends[2] = { reread(found, bits, is_signed, false), reread(found, bits, is_signed, true) };
        bool narrowed = false;
        for (bool reading : { false, true }) {
          auto [x, moved] = offset(base, reading);
          Range& end = ends[reading];
          if (auto above = known.upper.find(x); above != known.upper.end())
            for (const auto& [y, kind] : above->second) {
              auto other = kind == reading ? own(y) : std::nullopt;
              auto other_ty = as<IntType>(y->ty);
              if (! other || other_ty == nullptr)
                continue;
              auto hi = reread(*other, bits, other_ty->is_signed, reading).hi;
              end.hi = std::min(end.hi, hi + known.most(x, y, kind) + moved);
              narrowed = true;
            }
          if (auto below = known.lower.find(x); below != known.lower.end())
            for (const auto& [y, kind] : below->second) {
              auto other = kind == reading ? own(y) : std::nullopt;
              auto other_ty = as<IntType>(y->ty);
              if (! other || other_ty == nullptr)
                continue;
              auto lo = reread(*other, bits, other_ty->is_signed, reading).lo;
              end.lo = std::max(end.lo, lo - known.most(y, x, kind) + moved);
              narrowed = true;
            }
        }
        std::vector<Int> unequal;
        if (auto differ = known.unequal.find(base); differ != known.unequal.end())
          for (auto other : differ->second) {
            auto one = own(other);
            auto other_ty = as<IntType>(other->ty);
            if (one && other_ty != nullptr && one->lo == one->hi)
              unequal.push_back(reread(*one, bits, other_ty->is_signed, is_signed).lo);
          }
        if (! narrowed && unequal.empty())
          return found;
        if (ends[0].lo > ends[0].hi || ends[1].lo > ends[1].hi)
          return found;
        Range now = found;
        for (int reading = 0; reading < 2; ++reading) {
          auto one = reread(ends[reading], bits, reading != 0, is_signed);
          now.lo = std::max(now.lo, one.lo);
          now.hi = std::min(now.hi, one.hi);
        }
        if (now.lo > now.hi)
          return found;
        auto differs = [&](Int value) { return std::ranges::find(unequal, value) != unequal.end(); };
        bool moved_on = true;
        while (moved_on && now.lo <= now.hi) {
          moved_on = false;
          if (differs(now.lo)) {
            ++now.lo;
            moved_on = true;
          }
          if (differs(now.hi) && now.lo <= now.hi) {
            --now.hi;
            moved_on = true;
          }
        }
        return now.lo > now.hi ? found : now;
      }

      // What `inst`, an integer, can be where `known` holds -- or nothing, where
      // an operand has no range yet.
      std::optional<Range> range_of(const Instruction* inst, const IntType* ty, const Known& known)
      {
        auto whole = whole_of(ty);
        switch (inst->kind) {
        case InstKind::BinaryInst:
          return binary(inst, ty, known);
        case InstKind::CastInst:
          return cast(inst, ty, known);
        case InstKind::UnaryInst:
          return unary(inst, ty, known);
        case InstKind::SelectInst: {
          auto first = range_at(inst->operands[1], known, ty->is_signed);
          auto second = range_at(inst->operands[2], known, ty->is_signed);
          if (! first || ! second)
            return std::nullopt;
          return Range{ std::min(first->lo, second->lo), std::max(first->hi, second->hi) };
        }
        case InstKind::ExtractInst: {
          // The upper half of a whole product, which grows with both factors
          // as long as neither is read signed.
          auto product = as_inst(inst->operands[0], InstKind::BinaryInst);
          if (inst->index != 0 || product == nullptr || product->bin != BinOp::wide_mul)
            return whole;
          auto left = range_at(product->operands[0], known, false);
          auto right = range_at(product->operands[1], known, false);
          if (! left || ! right || ty->is_signed)
            return whole;
          using Wide = unsigned __int128;
          return Range{ Int((Wide(left->lo) * Wide(right->lo)) >> 64), Int((Wide(left->hi) * Wide(right->hi)) >> 64) };
        }
        default:
          return whole;
        }
      }

      // What an operation on two integers can be.
      std::optional<Range> binary(const Instruction* inst, const IntType* ty, const Known& known)
      {
        auto op = inst->bin;
        auto whole = whole_of(ty);
        if (is_shift(op))
          return shift(inst, ty, known);
        if ((op == BinOp::udiv || op == BinOp::urem) && ty->is_signed)
          // Asked of a value held with its sign above it, which is not the
          // value its bits read unsigned.
          return whole;
        switch (op) {
        case BinOp::and_: case BinOp::or_: case BinOp::xor_: case BinOp::udiv: case BinOp::urem:
        case BinOp::umin: case BinOp::umax: {
          // Questions about the bits, which are asked of them unsigned.
          auto left = range_at(inst->operands[0], known, false);
          auto right = range_at(inst->operands[1], known, false);
          if (! left || ! right)
            return std::nullopt;
          auto found = unsigned_op(op, *left, *right);
          if (! found)
            return whole;
          return reread(*found, ty->bits, false, ty->is_signed);
        }
        case BinOp::smin: case BinOp::smax: {
          auto left = range_at(inst->operands[0], known, true);
          auto right = range_at(inst->operands[1], known, true);
          if (! left || ! right)
            return std::nullopt;
          Range found = op == BinOp::smin ? Range{ std::min(left->lo, right->lo), std::min(left->hi, right->hi) }
                                          : Range{ std::max(left->lo, right->lo), std::max(left->hi, right->hi) };
          return reread(found, ty->bits, true, ty->is_signed);
        }
        case BinOp::add: case BinOp::sub: case BinOp::mul: case BinOp::wrap_add: case BinOp::wrap_sub:
        case BinOp::wrap_mul: case BinOp::sat_add: case BinOp::sat_sub: case BinOp::sat_mul:
          break;
        default:
          return whole;
        }
        auto left = range_at(inst->operands[0], known, ty->is_signed);
        auto right = range_at(inst->operands[1], known, ty->is_signed);
        if (! left || ! right)
          return std::nullopt;
        Int lo, hi;
        switch (op) {
        case BinOp::add: case BinOp::wrap_add: case BinOp::sat_add:
          lo = left->lo + right->lo;
          hi = left->hi + right->hi;
          break;
        case BinOp::sub: case BinOp::wrap_sub: case BinOp::sat_sub:
          lo = left->lo - right->hi;
          hi = left->hi - right->lo;
          if (! ty->is_signed && lo < 0) {
            // A difference whose right side is known to be the smaller does
            // not go below nought, whatever the ranges say.
            if (holds(CmpPred::ult, inst->operands[1], inst->operands[0], known))
              lo = 1;
            else if (holds(CmpPred::ule, inst->operands[1], inst->operands[0], known))
              lo = 0;
          }
          break;
        default: {
          Int corners[4] = { times(left->lo, right->lo), times(left->lo, right->hi), times(left->hi, right->lo),
                             times(left->hi, right->hi) };
          lo = *std::ranges::min_element(corners);
          hi = *std::ranges::max_element(corners);
          break;
        }
        }
        if (whole.lo <= lo && hi <= whole.hi) {
          fits.insert(inst);
          return Range{ lo, hi };
        }
        if (op == BinOp::sat_add || op == BinOp::sat_sub || op == BinOp::sat_mul)
          return Range{ std::min(std::max(lo, whole.lo), whole.hi), std::max(std::min(hi, whole.hi), whole.lo) };
        if (op == BinOp::add || op == BinOp::sub || op == BinOp::mul) {
          // A checked one that went past stopped the program.
          lo = std::max(lo, whole.lo);
          hi = std::min(hi, whole.hi);
          return lo <= hi ? Range{ lo, hi } : whole;
        }
        return whole;
      }

      // What a shift or a rotation can be.
      std::optional<Range> shift(const Instruction* inst, const IntType* ty, const Known& known)
      {
        auto whole = whole_of(ty);
        auto distance = range_at(inst->operands[1], known, false);
        if (! distance)
          return std::nullopt;
        auto op = inst->bin;
        Int width = ty->bits;
        // The wrapping shift takes its distance modulo the width as a mask,
        // which is the distance itself only where the width is a power of two.
        bool exact = (ty->bits & (ty->bits - 1)) == 0;
        if (plain_op(op)) {
          // A checked shift as far as the width or further stopped the program.
          if (distance->lo >= width)
            return whole;
          if (distance->hi < width && exact)
            fits.insert(inst);
          distance->hi = std::min(distance->hi, width - 1);
        } else if (distance->hi >= width || ! exact)
          *distance = Range{ 0, width - 1 };
        auto near = int(distance->lo), far = int(distance->hi);
        if (op == BinOp::shl || op == BinOp::wrap_shl) {
          auto value = range_at(inst->operands[0], known, ty->is_signed);
          if (! value)
            return std::nullopt;
          if (value->lo < 0)
            return whole;
          Range found{ value->lo << near, value->hi << far };
          return found.hi <= whole.hi ? found : whole;
        }
        // A value narrower than its register is shifted in the whole register:
        // known where the top bit is clear, or where the shift is the one the
        // type's own reading asks.
        bool arithmetic = op == BinOp::ashr || op == BinOp::wrap_ashr;
        if (arithmetic && ty->is_signed) {
          auto value = range_at(inst->operands[0], known, true);
          if (! value)
            return std::nullopt;
          return Range{ std::min(value->lo >> near, value->lo >> far), std::max(value->hi >> near, value->hi >> far) };
        }
        if (arithmetic || op == BinOp::lshr || op == BinOp::wrap_lshr) {
          auto value = range_at(inst->operands[0], known, false);
          if (! value)
            return std::nullopt;
          if ((ty->is_signed || arithmetic) && value->hi >= (Int(1) << (ty->bits - 1)))
            return whole;
          return Range{ value->lo >> far, value->hi >> near };
        }
        return whole;
      }

      // What a number read as another kind of number can be.
      std::optional<Range> cast(const Instruction* inst, const IntType* ty, const Known& known)
      {
        auto whole = whole_of(ty);
        auto inner = inst->operands[0];
        auto inner_ty = as<IntType>(inner->ty);
        if (inner_ty == nullptr)
          return whole;
        std::optional<Range> found;
        switch (inst->cast) {
        case CastKind::bitcast: case CastKind::zext: case CastKind::sext:
          if (inner_ty->bits == ty->bits)
            return range_at(inner, known, ty->is_signed);
          if (inst->cast == CastKind::bitcast)
            return whole;
          if (inst->cast == CastKind::zext)
            found = range_at(inner, known, false);
          else {
            found = range_at(inner, known, true);
            if (found && ! ty->is_signed)
              return reread(*found, ty->bits, true, false);
          }
          break;
        case CastKind::trunc:
          found = range_at(inner, known, ty->is_signed);
          if (found && ! within(*found, whole))
            return whole;
          break;
        default:
          return whole;
        }
        if (! found)
          return std::nullopt;
        return within(*found, whole) ? *found : whole;
      }

      // What an operation on one integer can be.
      std::optional<Range> unary(const Instruction* inst, const IntType* ty, const Known& known)
      {
        auto whole = whole_of(ty);
        auto inner = inst->operands[0];
        auto inner_ty = as<IntType>(inner->ty);
        if (inner_ty == nullptr)
          return whole;
        switch (inst->un) {
        case UnOp::count_ones: case UnOp::count_leading: case UnOp::count_trailing:
          return Int(inner_ty->bits) <= whole.hi ? Range{ 0, Int(inner_ty->bits) } : whole;
        case UnOp::not_:
          if (inner_ty == ty) {
            auto found = range_at(inner, known, ty->is_signed);
            if (! found)
              return std::nullopt;
            if (ty->is_signed)
              return Range{ -found->hi - 1, -found->lo - 1 };
            return Range{ whole.hi - found->hi, whole.hi - found->lo };
          }
          break;
        case UnOp::neg:
          if (inner_ty == ty && ty->is_signed) {
            auto found = range_at(inner, known, true);
            if (! found)
              return std::nullopt;
            Int lo = std::max(-found->hi, whole.lo), hi = std::min(-found->lo, whole.hi);
            return lo <= hi ? Range{ lo, hi } : whole;
          }
          break;
        default:
          break;
        }
        return whole;
      }

      // Whether `left pred right` is known to hold where `known` does.
      bool holds(CmpPred pred, const Value* left, const Value* right, const Known& known)
      {
        return answer(pred, left, right, known) == true;
      }

      // What `cmp` answers where `known` holds, where that is known.
      std::optional<bool> decide(const Instruction* cmp, const Known& known)
      {
        auto left = cmp->operands[0], right = cmp->operands[1];
        if (! is<IntType>(left->ty) || ! is<IntType>(right->ty))
          return std::nullopt;
        return answer(cmp->pred, left, right, known);
      }

      // What `left pred right` answers where `known` holds: the same value on
      // both sides, a fact, the two ranges, or a fact about what the two are a
      // constant away from.
      std::optional<bool> answer(CmpPred pred, const Value* left, const Value* right, const Known& known)
      {
        auto a = plain(left), b = plain(right);
        if (a == b)
          return pred == CmpPred::eq || pred == CmpPred::ule || pred == CmpPred::uge || pred == CmpPred::sle
                 || pred == CmpPred::sge;
        if (known.facts.contains({ pred, a, b }))
          return true;
        if (known.facts.contains({ negated(pred), a, b }))
          return false;
        bool is_signed = reads_signed(pred);
        auto first = range_at(a, known, is_signed);
        auto second = range_at(b, known, is_signed);
        if (first && second)
          if (auto found = compared(pred, *first, *second))
            return found;
        if (pred == CmpPred::eq || pred == CmpPred::ne)
          return std::nullopt;
        auto [x, at_x] = offset(a, is_signed);
        auto [y, at_y] = offset(b, is_signed);
        // `x - y` at most `below`, `y - x` at most `above`.
        std::optional<Int> below, above;
        if (x == y)
          below = above = 0;
        else {
          below = known.apart(x, y, is_signed);
          above = known.apart(y, x, is_signed);
        }
        if (auto wanted = less_of(pred)) {
          if (below && *below + at_x - at_y <= *wanted)
            return true;
          if (above && *above + at_y - at_x <= -1 - *wanted)
            return false;
        } else if (auto wanted = more_of(pred)) {
          if (above && *above + at_y - at_x <= *wanted)
            return true;
          if (below && *below + at_x - at_y <= -1 - *wanted)
            return false;
        }
        return std::nullopt;
      }

      // `value` as another value and a constant added to it, where the sum is
      // known not to wrap as `is_signed` reads it.
      std::pair<const Value*, Int> offset(const Value* value, bool is_signed) const
      {
        Int moved = 0;
        while (true) {
          auto binary = as_inst(value, InstKind::BinaryInst);
          if (binary == nullptr)
            return { value, moved };
          auto ty = as<IntType>(binary->ty);
          if (ty == nullptr || ty->is_signed != is_signed || ! is_step(binary->bin))
            return { value, moved };
          auto op = binary->bin;
          if (op != BinOp::add && op != BinOp::sub && ! fits.contains(binary))
            return { value, moved };
          bool adds = op == BinOp::add || op == BinOp::wrap_add;
          auto left = binary->operands[0], right = binary->operands[1];
          if (auto step = as_int(right)) {
            moved += adds ? step->value : -step->value;
            value = plain(left);
          } else if (auto first = as_int(left); first != nullptr && adds) {
            moved += first->value;
            value = plain(right);
          } else
            return { value, moved };
        }
      }

      // Change what the last walk settled; say whether anything changed.
      bool apply()
      {
        bool changed = false;
        for (auto& block : func.blocks)
          for (auto inst : block->insts)
            if (inst->kind == InstKind::BinaryInst && fits.contains(inst) && is<IntType>(inst->ty))
              if (auto made = plain_op(inst->bin)) {
                inst->bin = *made;
                changed = true;
              }
        std::unordered_map<const Value*, Value*> instead;
        for (auto& block : func.blocks)
          for (auto inst : block->insts)
            if (auto found = answers.find(inst); found != answers.end())
              instead[inst] = const_cast<BoolConst*>(module.bool_const(found->second));
        std::unordered_set<const Value*> dropped(settled.begin(), settled.end());
        if (! instead.empty() || ! dropped.empty()) {
          replace_uses(func, instead, dropped);
          changed = true;
        }
        return changed;
      }
    };

  } // anonymous namespace


  bool facts(Module& module)
  {
    bool changed = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration() || func->blocks.empty())
        continue;
      changed |= Ranges(module, *func).use();
    }
    return changed;
  }

} // namespace pl4g::opt
