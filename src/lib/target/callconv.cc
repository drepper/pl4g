// Calling conventions.
#include "target/callconv.hh"
#include "target/emit.hh"

#include "ir/builder.hh"

#include <algorithm>


namespace pl4g::target {

  namespace {

    // The place each part of each of `types` occupies, registers counted per
    // kind and the stack, where `stack` allows it, taking what they do not.
    Places places(const CallConvDesc& cconv, std::span<const mc::PhysReg* const> integers,
                  std::span<const mc::PhysReg* const> floats, std::span<const ir::Type* const> types, bool stack)
    {
      Places found;
      std::size_t used[2] = { 0, 0 };
      std::int64_t slots = 0;
      for (auto ty : types) {
        std::vector<Place> one;
        for (auto part : ir::leaves_of(ty)) {
          bool floating = ir::is<ir::FloatType>(part);
          auto bank = floating ? floats : integers;
          if (used[floating] < bank.size())
            one.emplace_back(bank[used[floating]++]);
          else if (floating && cconv.floats_in_integers && used[0] < integers.size())
            one.emplace_back(integers[used[0]++]);
          else if (! stack) [[unlikely]]
            throw TooManyArguments();
          else
            one.emplace_back(OnStack{ stack_slot * slots++ });
        }
        found.push_back(std::move(one));
      }
      return found;
    }

  } // anonymous namespace


  OrderedMap<std::string, std::vector<const mc::RegUnit*>> CallConvDesc::orders(const std::string& integers,
                                                                                const std::string& floats) const
  {
    OrderedMap<std::string, std::vector<const mc::RegUnit*>> found;
    found[integers] = allocation_order;
    if (! float_allocation_order.empty())
      found[floats] = float_allocation_order;
    return found;
  }


  std::vector<const mc::PhysReg*> destroyed_by(const ir::Function* callee, const CallConvDesc& cconv,
                                               mc::RegisterInfo& registers, const Destroyed* known)
  {
    const std::set<const mc::RegUnit*>* units = nullptr;
    if (known != nullptr && callee != nullptr)
      if (auto it = known->find(ir::symbol_name(*callee)); it != known->end())
        units = &it->second;
    // Everything the convention allows, which is what a call through a value
    // has to assume.
    if (units == nullptr)
      units = &cconv.caller_saved;
    std::vector<const mc::PhysReg*> found;
    for (auto unit : *units)
      found.push_back(registers.widest(unit));
    return found;
  }


  Places argument_places(const CallConvDesc& cconv, std::span<const ir::Type* const> types, bool chained,
                         bool answer_first)
  {
    if (answer_first && cconv.answer_reg != nullptr && ! types.empty()) {
      auto found = places(cconv, cconv.int_arg_regs, cconv.float_arg_regs, types.subspan(1), true);
      found.insert(found.begin(), { Place(cconv.answer_reg) });
      return found;
    }
    if (! chained || types.empty())
      return places(cconv, cconv.int_arg_regs, cconv.float_arg_regs, types, true);
    if (cconv.chain_reg == nullptr) [[unlikely]]
      throw InternalError("a chained call in a convention with no chain");
    auto found = places(cconv, cconv.int_arg_regs, cconv.float_arg_regs, types.subspan(1), true);
    found.insert(found.begin(), { Place(cconv.chain_reg) });
    return found;
  }


  std::int64_t stack_bytes(const Places& places)
  {
    std::int64_t end = 0;
    for (const auto& parts : places)
      for (const auto& place : parts)
        if (auto slot = std::get_if<OnStack>(&place))
          end = std::max(end, slot->offset + stack_slot);
    return (end + 15) & ~std::int64_t(15);
  }


  std::vector<const mc::PhysReg*> result_places(const CallConvDesc& cconv, const ir::Type* ty)
  {
    const ir::Type* one[] = { ty };
    std::vector<const mc::PhysReg*> found;
    for (const auto& place : places(cconv, cconv.int_ret_regs, cconv.float_ret_regs, one, false)[0])
      found.push_back(std::get<const mc::PhysReg*>(place));
    return found;
  }


  CallsPlaced calls_placed(const ir::Function& func, const Conventions& conventions)
  {
    CallsPlaced found;
    for (const auto& block : func.blocks)
      for (const ir::Instruction* inst : block->insts) {
        if (inst->kind != ir::InstKind::CallInst)
          continue;
        bool through = inst->callee == nullptr;
        const auto& theirs = conventions(through ? ir::default_cconv : std::string_view(inst->callee->cconv));
        std::vector<const ir::Type*> types;
        for (auto one : inst->arguments())
          types.push_back(one->ty);
        found.emplace(inst, std::pair{ &theirs, argument_places(theirs, types, through || inst->callee->attrs.chained,
                                                                ! through && inst->callee->attrs.answer_first) });
      }
    return found;
  }

} // namespace pl4g::target
