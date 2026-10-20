// What a read or a write may say about what another observer sees.
#include "target/ordering.hh"


namespace pl4g::target {

  std::optional<std::string> cannot_order(const ir::Instruction* inst)
  {
    if (inst == nullptr || (inst->kind != ir::InstKind::LoadInst && inst->kind != ir::InstKind::StoreInst))
      return std::nullopt;
    if (inst->ordering == ir::Ordering::plain)
      return std::nullopt;
    const ir::Type* ty = inst->kind == ir::InstKind::LoadInst ? inst->ty : inst->operands[2]->ty;
    if (ir::made_of_parts(ty))
      return "an ordered access to '" + ty->written() + "', which is several values and not one";
    if (ir::is<ir::FloatType>(ty) || ir::is<ir::VecType>(ty))
      return "an ordered access to '" + ty->written() + "'";
    return std::nullopt;
  }

} // namespace pl4g::target
