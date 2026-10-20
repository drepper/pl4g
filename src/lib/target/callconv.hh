// Calling conventions: a value attached to a function rather than a property of
// the target, so two functions of one compilation may follow different ones.
// A port of pypl4g/target/callconv.py.
#pragma once

#include <optional>
#include <set>
#include <span>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "ir/ir.hh"
#include "mc/reg.hh"
#include "util/ordered.hh"


namespace pl4g::target {

  // One calling convention.
  struct CallConvDesc {
    std::string name;
    std::vector<const mc::PhysReg*> int_arg_regs;
    std::vector<const mc::PhysReg*> int_ret_regs;
    std::set<const mc::RegUnit*> callee_saved;
    std::set<const mc::RegUnit*> caller_saved;
    // The same for values that are not integers.
    std::vector<const mc::PhysReg*> float_arg_regs;
    std::vector<const mc::PhysReg*> float_ret_regs;
    std::vector<const mc::RegUnit*> float_allocation_order;
    // The units the allocator may give out, in the order it prefers them.
    std::vector<const mc::RegUnit*> allocation_order;
    std::int64_t stack_align = 16;
    // Bytes below the stack pointer a leaf function may use without moving it.
    std::int64_t red_zone = 0;
    bool variadic = false;
    // Where a call through a value passes a lambda's environment, and where a
    // function called that way finds it; nothing for a convention that has none.
    const mc::PhysReg* chain_reg = nullptr;

    // The allocation orders by the name of the class each belongs to: one
    // entry where the target says nothing about floating point.
    OrderedMap<std::string, std::vector<const mc::RegUnit*>> orders(const std::string& integers,
                                                                    const std::string& floats) const;
  };

  // What each called function turned out to destroy, by its symbol.
  using Destroyed = std::unordered_map<std::string, std::set<const mc::RegUnit*>>;

  // Which registers a call to `callee` destroys: what it turned out to destroy
  // where `known` says, everything its convention allows where not.
  std::vector<const mc::PhysReg*> destroyed_by(const ir::Function* callee, const CallConvDesc& cconv,
                                               mc::RegisterInfo& registers, const Destroyed* known);

  // More arguments than a convention passes in registers.
  struct TooManyArguments : std::runtime_error {
    TooManyArguments() : std::runtime_error("too many arguments") {}
  };

  // Which registers each argument is passed in, in order, one per part.
  // `chained` passes the first in the chain register.
  std::vector<std::vector<const mc::PhysReg*>> argument_places(const CallConvDesc& cconv,
                                                               std::span<const ir::Type* const> types,
                                                               bool chained = false);

  // Which registers a value of `ty` is answered with, one per part.
  std::vector<const mc::PhysReg*> result_places(const CallConvDesc& cconv, const ir::Type* ty);

} // namespace pl4g::target
