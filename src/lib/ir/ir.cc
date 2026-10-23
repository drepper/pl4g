#include "ir/ir.hh"

#include <algorithm>
#include <bit>


namespace pl4g::ir {

  std::string Instruction::opcode() const
  {
    switch (kind) {
    case InstKind::BinaryInst: return std::string(describe(bin));
    case InstKind::UnaryInst: return std::string(describe(un));
    case InstKind::CmpInst: return "icmp." + std::string(enum_name(pred));
    case InstKind::SelectInst: return "select";
    case InstKind::SplatInst: return "splat";
    case InstKind::AnyLaneInst: return "anylane";
    case InstKind::WrapInst: return "wrap";
    case InstKind::TupleInst: return "tuple";
    case InstKind::ExtractInst: return "extract." + std::to_string(index);
    case InstKind::UnwrapInst: return "unwrap";
    case InstKind::ErrorInst: return "error";
    case InstKind::FailedInst: return "failed";
    case InstKind::CastInst: return std::string(enum_name(cast));
    case InstKind::CallInst: return cconv == "pl4g" ? "call" : "call." + cconv;
    case InstKind::SyscallInst: return "syscall";
    case InstKind::AddressInst: return "address";
    case InstKind::CodeInst: return "code";
    case InstKind::FrameInst: return "frame";
    case InstKind::SizeOfInst: return "sizeof";
    case InstKind::AssertInst: return observing ? "observe" : "assert";
    case InstKind::MemStartInst: return "mem.start";
    case InstKind::LoadInst: return ordering == Ordering::plain ? "load" : "load." + std::string(enum_name(ordering));
    case InstKind::StoreInst:
      return ordering == Ordering::plain ? "store" : "store." + std::string(enum_name(ordering));
    case InstKind::FieldInst: return "field";
    case InstKind::SumMakeInst: return "sum.make";
    case InstKind::SumTagInst: return "sum.tag";
    case InstKind::SumGetInst: return "sum.get";
    case InstKind::RetInst: return "ret";
    case InstKind::BrInst: return "br";
    case InstKind::CondBrInst: return "condbr";
    case InstKind::SwitchInst: return "switch";
    case InstKind::YieldInst: return delegates ? "yield.on" : "yield";
    case InstKind::UnreachableInst: return "unreachable";
    }
    return "?";
  }


  BlockParam* BasicBlock::add_param(const Type* ty)
  {
    params.push_back(std::make_unique<BlockParam>(ty, this, params.size()));
    return params.back().get();
  }


  std::string Function::unique_label(std::optional<std::string> label) const
  {
    if (! label)
      return "block" + std::to_string(blocks.size());
    auto taken = [&](const std::string& l) {
      return std::ranges::any_of(blocks, [&](const auto& b) { return b->label == l; });
    };
    if (! taken(*label))
      return *label;
    unsigned count = 1;
    while (taken(*label + std::to_string(count)))
      ++count;
    return *label + std::to_string(count);
  }


  BasicBlock* Function::add_block(std::optional<std::string> label)
  {
    blocks.push_back(detached_block(unique_label(std::move(label))));
    return blocks.back().get();
  }


  std::unique_ptr<BasicBlock> Function::detached_block(std::string label)
  {
    auto block = std::make_unique<BasicBlock>();
    block->label = std::move(label);
    block->parent = this;
    return block;
  }


  Instruction* Function::make(InstKind kind, const Type* ty)
  {
    pool.push_back(std::make_unique<Instruction>(kind, ty));
    return pool.back().get();
  }


  Function* Module::add_function(std::unique_ptr<Function> func, std::optional<std::string> key)
  {
    auto name = key ? *key : func->name;
    // A key already taken keeps its place and takes the new function, as a
    // Python dict does; what it held stays alive for whatever calls it.
    for (auto& [k, f] : functions)
      if (k == name) {
        unlisted.push_back(std::move(f));
        f = std::move(func);
        return f.get();
      }
    functions.emplace_back(std::move(name), std::move(func));
    return functions.back().second.get();
  }


  GlobalVar* Module::add_global(std::unique_ptr<GlobalVar> var, std::optional<std::string> key)
  {
    auto name = key ? *key : var->name;
    for (auto& [k, v] : globals)
      if (k == name) {
        retired_globals.push_back(std::move(v));
        v = std::move(var);
        return v.get();
      }
    globals.emplace_back(std::move(name), std::move(var));
    return globals.back().second.get();
  }


  Function* Module::function(std::string_view key) const
  {
    for (const auto& [k, f] : functions)
      if (k == key)
        return f.get();
    return nullptr;
  }


  GlobalVar* Module::global(std::string_view key) const
  {
    for (const auto& [k, g] : globals)
      if (k == key)
        return g.get();
    return nullptr;
  }


  const IntConst* Module::int_const(const Type* ty, __int128 value)
  {
    auto& found = ints[{ ty, value }];
    if (found == nullptr)
      found = constant<IntConst>(ty, value);
    return found;
  }


  const BoolConst* Module::bool_const(bool value)
  {
    auto& found = bools[value];
    if (found == nullptr)
      found = constant<BoolConst>(bool_type(), value);
    return found;
  }


  const FloatConst* Module::float_const(const Type* ty, double value)
  {
    auto& found = floats[{ ty, std::bit_cast<std::uint64_t>(value) }];
    if (found == nullptr)
      found = constant<FloatConst>(ty, value);
    return found;
  }


  const CharConst* Module::char_const(std::uint32_t value)
  {
    auto& found = chars[value];
    if (found == nullptr)
      found = constant<CharConst>(char_type(), value);
    return found;
  }


  const UndefConst* Module::undef(const Type* ty)
  {
    auto& found = undefs[ty];
    if (found == nullptr)
      found = constant<UndefConst>(ty);
    return found;
  }

} // namespace pl4g::ir
