// Turning the branches of the representation into machine branches.
#include "target/branches.hh"
#include "target/emit.hh"

#include <algorithm>

#include "ir/builder.hh"
#include "mc/regalloc.hh"


namespace pl4g::target {

  namespace {

    // Whether putting something in `reg` would change what `source` names.
    bool reads(const mc::MCOperand& source, mc::Reg reg)
    {
      for (const auto& [named, _] : mc::registers_of(source))
        if (mc::interferes(named, reg))
          return true;
      return false;
    }

    // Whether a move puts a register back where it already is.
    bool holds_it_already(const Move& move)
    {
      auto reg = std::get_if<mc::MCReg>(&move.source);
      return reg != nullptr && mc::interferes(reg->reg, move.into);
    }

    // The label of the block a branch names.
    std::string target_label(const ir::Function& func, std::span<const std::string> labels,
                             const ir::BasicBlock* block)
    {
      for (std::size_t index = 0; index < func.blocks.size(); ++index)
        if (func.blocks[index].get() == block)
          return labels[index];
      throw InternalError("a branch names a block that is not in this function");
    }

    // Every register a branch writes and what it writes there, built whole
    // before anything is emitted.
    std::vector<Move> moves_of(const ir::BlockTarget& target, Operands& operands, Span span)
    {
      std::vector<Move> moves;
      for (auto [param, argument] : ir::carried_values(target)) {
        auto pieces = ir::leaves_of(param->ty);
        if (! ir::made_of_parts(param->ty)) {
          auto into = operands.destination(param);
          moves.push_back({ into, operands.value(argument, span) });
          continue;
        }
        for (std::size_t index = 0; index < pieces.size(); ++index) {
          auto into = operands.part_of(param, index, span);
          moves.push_back({ into, mc::MCReg{ operands.part_of(argument, index, span) } });
        }
      }
      return moves;
    }

    // Put a branch's arguments where the block it goes to will look for them.
    void pass_arguments(mc::Assembler& asm_, const ir::BlockTarget& target, Operands& operands, Span span)
    {
      if (target.args.empty())
        return;
      auto moves = moves_of(target, operands, span);
      for (const auto& move : sequenced(moves, [&](mc::Reg like) { return asm_.temporary(like); }))
        asm_.loadreg(move.into, move.source, span);
    }

    // Emit a two-way branch, turning it round where that saves the jump.
    void lower_conditional(mc::Assembler& asm_, const ir::Function& func, std::span<const std::string> labels,
                           const std::optional<std::string>& following, const ir::Instruction* terminator,
                           Operands& operands, const mc::MCImm& zero)
    {
      auto span = terminator->span;
      auto when_true = target_label(func, labels, terminator->targets[0].block);
      auto when_false = target_label(func, labels, terminator->targets[1].block);
      auto [condition, lhs, rhs] = condition_of(func, terminator, operands, zero, span);
      if (when_true == following) {
        asm_.branch(mc::inverted(condition), lhs, rhs, when_false, span);
        asm_.falls_through(when_true);
        return;
      }
      asm_.branch(condition, lhs, rhs, when_true, span);
      if (when_false == following)
        asm_.falls_through(when_false);
      else
        asm_.jump(when_false, span);
    }

    // Emit a many-way branch as a chain of comparisons, one per case.
    void lower_switch(mc::Assembler& asm_, const ir::Function& func, std::span<const std::string> labels,
                      const std::optional<std::string>& following, const ir::Instruction* terminator,
                      Operands& operands, const mc::MCImm& zero)
    {
      auto span = terminator->span;
      auto value = operands.in_register(terminator->operands[0], span);
      for (std::size_t at = 0; at < terminator->cases.size(); ++at)
        asm_.branch(mc::Condition::eq, value, mc::MCImm{ terminator->cases[at], zero.bits, true },
                    target_label(func, labels, terminator->targets[at].block), span);
      auto fallback = target_label(func, labels, terminator->targets.back().block);
      if (fallback == following)
        asm_.falls_through(fallback);
      else
        asm_.jump(fallback, span);
    }

  } // anonymous namespace


  mc::Condition condition_for(ir::CmpPred pred) noexcept
  {
    switch (pred) {
    case ir::CmpPred::eq: return mc::Condition::eq;
    case ir::CmpPred::ne: return mc::Condition::ne;
    case ir::CmpPred::slt: return mc::Condition::slt;
    case ir::CmpPred::sle: return mc::Condition::sle;
    case ir::CmpPred::sgt: return mc::Condition::sgt;
    case ir::CmpPred::sge: return mc::Condition::sge;
    case ir::CmpPred::ult: return mc::Condition::ult;
    case ir::CmpPred::ule: return mc::Condition::ule;
    case ir::CmpPred::ugt: return mc::Condition::ugt;
    case ir::CmpPred::uge: return mc::Condition::uge;
    }
    return mc::Condition::eq;
  }


  std::string block_label(std::string_view symbol, std::size_t index, const ir::BasicBlock& block)
  {
    if (index == 0)
      return ".L" + std::string(symbol) + "_entry";
    return ".L" + std::string(symbol) + "_" + block.label;
  }


  std::vector<std::string> labels_of(std::string_view symbol, const ir::Function& func)
  {
    std::vector<std::string> found;
    for (std::size_t index = 0; index < func.blocks.size(); ++index)
      found.push_back(block_label(symbol, index, *func.blocks[index]));
    return found;
  }


  bool condition_used_once(const ir::Function& func, const ir::Value* value)
  {
    std::size_t users = 0;
    for (const auto& block : func.blocks)
      for (auto inst : block->insts)
        users += std::size_t(std::ranges::count(inst->operands, value));
    return users == 1;
  }


  bool folded_into_branch(const ir::Function& func, const ir::Value* value)
  {
    if (value->vkind != ir::ValueKind::instruction)
      return false;
    auto inst = static_cast<const ir::Instruction*>(value);
    // A comparison of floating-point values is never absorbed.
    if (inst->kind == ir::InstKind::CmpInst && ir::is<ir::FloatType>(inst->operands[0]->ty))
      return false;
    if (! condition_used_once(func, value))
      return false;
    for (const auto& block : func.blocks)
      for (auto user : block->insts)
        if (std::ranges::contains(user->operands, value))
          // A check is a branch too.
          return (user->kind == ir::InstKind::CondBrInst || user->kind == ir::InstKind::AssertInst)
                 && user->operands[0] == value;
    return false;
  }


  void lower_comparison(mc::Assembler& asm_, const ir::Instruction* inst, Operands& operands, mc::Reg destination)
  {
    auto lhs = operands.in_register(inst->operands[0], inst->span);
    auto rhs = operands.value(inst->operands[1], inst->span);
    asm_.setcond(condition_for(inst->pred), destination, lhs, rhs, inst->span);
  }


  bool lower_branch(mc::Assembler& asm_, const ir::Function& func, std::span<const std::string> labels,
                    std::size_t index, const ir::Instruction* terminator, Operands& operands, const mc::MCImm& zero)
  {
    auto span = terminator->span;
    std::optional<std::string> following;
    if (index + 1 < labels.size())
      following = labels[index + 1];
    switch (terminator->kind) {
    case ir::InstKind::BrInst: {
      auto target = target_label(func, labels, terminator->targets[0].block);
      pass_arguments(asm_, terminator->targets[0], operands, span);
      if (target == following)
        asm_.falls_through(target);
      else
        asm_.jump(target, span);
      return true;
    }
    case ir::InstKind::SwitchInst:
      for (const auto& edge : terminator->targets)
        if (! ir::carried_values(edge).empty()) [[unlikely]]
          throw UnsupportedBranch("a switch that passes arguments", span);
      lower_switch(asm_, func, labels, following, terminator, operands, zero);
      return true;
    case ir::InstKind::CondBrInst:
      for (const auto& edge : terminator->targets)
        if (! ir::carried_values(edge).empty()) [[unlikely]]
          throw UnsupportedBranch("a conditional branch that passes arguments", span);
      lower_conditional(asm_, func, labels, following, terminator, operands, zero);
      return true;
    default:
      return false;
    }
  }


  std::vector<Move> sequenced(std::span<const Move> moves, const std::function<mc::Reg(mc::Reg)>& spare)
  {
    std::vector<Move> pending;
    for (const auto& move : moves)
      if (! holds_it_already(move))
        pending.push_back(move);
    std::vector<Move> ordered;
    while (! pending.empty()) {
      std::vector<bool> ready(pending.size());
      bool any = false;
      for (std::size_t i = 0; i < pending.size(); ++i) {
        bool blocked = false;
        for (std::size_t j = 0; j < pending.size() && ! blocked; ++j)
          blocked = j != i && reads(pending[j].source, pending[i].into);
        ready[i] = ! blocked;
        any = any || ! blocked;
      }
      if (any) {
        std::vector<Move> left;
        for (std::size_t i = 0; i < pending.size(); ++i)
          (ready[i] ? ordered : left).push_back(pending[i]);
        pending = std::move(left);
        continue;
      }
      // Every move left reads a register another writes: break one link by
      // holding a copy of what it reads.
      auto stuck = pending[0].into;
      auto held = spare(stuck);
      ordered.push_back({ held, mc::MCReg{ stuck } });
      for (std::size_t i = 1; i < pending.size(); ++i)
        if (reads(pending[i].source, stuck))
          pending[i].source = mc::MCReg{ held };
    }
    return ordered;
  }


  std::tuple<mc::Condition, mc::MCOperand, mc::MCOperand> condition_of(const ir::Function& func,
                                                                       const ir::Instruction* terminator,
                                                                       Operands& operands, const mc::MCImm& zero,
                                                                       Span span)
  {
    auto value = terminator->operands[0];
    if (value->vkind == ir::ValueKind::instruction && static_cast<ir::Instruction*>(value)->kind == ir::InstKind::CmpInst
        && folded_into_branch(func, value)) {
      auto compare = static_cast<ir::Instruction*>(value);
      auto lhs = operands.in_register(compare->operands[0], span);
      auto rhs = operands.value(compare->operands[1], span);
      return { condition_for(compare->pred), lhs, rhs };
    }
    return { mc::Condition::ne, operands.in_register(value, span), zero };
  }

} // namespace pl4g::target
