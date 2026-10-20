// What a branch already settled, used where it is known to hold.  A port of
// pypl4g's opt/passes/facts.py.
//
// A block a conditional branch is the only way into knows which way it went,
// and so does every block it dominates.  An index's check asking that again is
// dropped, and a saturating step of one that cannot reach an end of its type
// becomes a plain one.  Casts that change only a unit are looked through.
#include "opt/passes.hh"

#include <set>
#include <tuple>
#include <unordered_map>
#include <unordered_set>

#include "opt/flow.hh"


namespace pl4g::opt {

  using namespace ir;

  namespace {

    using Fact = std::tuple<CmpPred, const Value*, const Value*>;
    using Facts = std::set<Fact>;

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

    const Instruction* as_inst(const Value* value, InstKind kind)
    {
      if (value->vkind != ValueKind::instruction)
        return nullptr;
      auto inst = static_cast<const Instruction*>(value);
      return inst->kind == kind ? inst : nullptr;
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

    // What is known where `cond` came out as `holds`.
    std::vector<Fact> facts_of(const Value* cond, bool holds)
    {
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

    // What the one branch into `block`, if there is one, says holds.
    std::vector<Fact> on_the_way_in(const BasicBlock* block, const std::vector<BasicBlock*>& preds)
    {
      if (preds.size() != 1)
        return {};
      auto last = preds.front()->insts.empty() ? nullptr : preds.front()->insts.back();
      if (last == nullptr || last->kind != InstKind::CondBrInst)
        return {};
      auto yes = last->targets[0].block, no = last->targets[1].block;
      if (yes == no)
        return {};
      return facts_of(last->operands[0], block == yes);
    }

    bool holds_here(const Value* cond, const Facts& facts)
    {
      auto cmp = as_inst(cond, InstKind::CmpInst);
      if (cmp == nullptr)
        return false;
      return facts.contains({ cmp->pred, plain(cmp->operands[0]), plain(cmp->operands[1]) });
    }

    // Make a saturating step of one a plain one where it cannot reach an end
    // of its type.
    bool cannot_saturate(Instruction* inst, const Facts& facts)
    {
      if (inst->bin != BinOp::sat_add && inst->bin != BinOp::sat_sub)
        return false;
      auto ty = as<IntType>(inst->ty);
      auto step = inst->operands[1];
      if (ty == nullptr || step->vkind != ValueKind::int_const || static_cast<const IntConst*>(step)->value != 1)
        return false;
      auto moved = plain(inst->operands[0]);
      CmpPred asked;
      BinOp made;
      if (inst->bin == BinOp::sat_add) {
        // Below something, so below the largest the type holds.
        asked = ty->is_signed ? CmpPred::slt : CmpPred::ult;
        made = BinOp::wrap_add;
      } else {
        asked = ty->is_signed ? CmpPred::sgt : CmpPred::ugt;
        made = BinOp::wrap_sub;
      }
      bool known = false;
      for (const auto& [pred, left, _] : facts)
        if (pred == asked && left == moved) {
          known = true;
          break;
        }
      if (! known)
        return false;
      inst->bin = made;
      return true;
    }

    bool use(Function& func)
    {
      bool branches = false, settles = false;
      for (const auto& block : func.blocks) {
        if (! block->insts.empty() && block->insts.back()->kind == InstKind::CondBrInst)
          branches = true;
        for (auto inst : block->insts)
          if (inst->kind == InstKind::AssertInst
              || (inst->kind == InstKind::BinaryInst && (inst->bin == BinOp::sat_add || inst->bin == BinOp::sat_sub)))
            settles = true;
      }
      if (! branches || ! settles)
        return false;
      auto tree = dominators(func);
      auto preds = predecessors(func);
      std::unordered_map<const BasicBlock*, Facts> known;
      std::unordered_set<const Instruction*> settled;
      bool changed = false;
      for (auto block : tree.order) {
        auto parent = tree.idom_of(block);
        Facts facts = parent != nullptr ? known[parent] : Facts{};
        for (const auto& one : on_the_way_in(block, preds[block]))
          facts.insert(one);
        known[block] = facts;
        for (auto inst : block->insts)
          if (inst->kind == InstKind::AssertInst && ! inst->observing && holds_here(inst->operands[0], facts))
            settled.insert(inst);
          else if (inst->kind == InstKind::BinaryInst)
            changed |= cannot_saturate(inst, facts);
      }
      if (! settled.empty())
        for (auto& block : func.blocks)
          std::erase_if(block->insts, [&](const Instruction* one) { return settled.contains(one); });
      return changed || ! settled.empty();
    }

  } // anonymous namespace


  bool facts(Module& module)
  {
    bool changed = false;
    for (auto& [_, func] : module.functions) {
      if (func->is_declaration() || func->blocks.empty())
        continue;
      changed |= use(*func);
    }
    return changed;
  }

} // namespace pl4g::opt
