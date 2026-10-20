// Calling conventions.
#include "target/callconv.hh"
#include "target/emit.hh"

#include "ir/builder.hh"


namespace pl4g::target {

  namespace {

    // The registers each of `types` occupies, counted per kind.
    std::vector<std::vector<const mc::PhysReg*>> places(std::span<const mc::PhysReg* const> integers,
                                                        std::span<const mc::PhysReg* const> floats,
                                                        std::span<const ir::Type* const> types)
    {
      std::vector<std::vector<const mc::PhysReg*>> found;
      std::size_t used[2] = { 0, 0 };
      for (auto ty : types) {
        std::vector<const mc::PhysReg*> one;
        for (auto part : ir::leaves_of(ty)) {
          bool floating = ir::is<ir::FloatType>(part);
          auto bank = floating ? floats : integers;
          if (used[floating] >= bank.size()) [[unlikely]]
            throw TooManyArguments();
          one.push_back(bank[used[floating]++]);
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


  std::vector<std::vector<const mc::PhysReg*>> argument_places(const CallConvDesc& cconv,
                                                               std::span<const ir::Type* const> types, bool chained)
  {
    if (! chained || types.empty())
      return places(cconv.int_arg_regs, cconv.float_arg_regs, types);
    if (cconv.chain_reg == nullptr) [[unlikely]]
      throw InternalError("a chained call in a convention with no chain");
    auto found = places(cconv.int_arg_regs, cconv.float_arg_regs, types.subspan(1));
    found.insert(found.begin(), { cconv.chain_reg });
    return found;
  }


  std::vector<const mc::PhysReg*> result_places(const CallConvDesc& cconv, const ir::Type* ty)
  {
    const ir::Type* one[] = { ty };
    return places(cconv.int_ret_regs, cconv.float_ret_regs, one)[0];
  }

} // namespace pl4g::target
