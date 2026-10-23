// Calling conventions: a value attached to a function rather than a property of
// the target, so two functions of one compilation may follow different ones.
// A port of pypl4g/target/callconv.py.
#pragma once

#include <functional>
#include <optional>
#include <set>
#include <span>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <variant>
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
    // Whether a floating-point argument that finds no register of its own kind
    // left takes an ordinary one before it takes the stack, as RISC-V's
    // convention has it.
    bool floats_in_integers = false;
    // Where a function answering through room its caller made is handed where
    // that room is, where the convention keeps a register of its own for it
    // rather than taking the first argument register: AArch64's `x8`.
    const mc::PhysReg* answer_reg = nullptr;

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

  // More parts in an answer than a convention answers in registers: a defect
  // of the compiler, `largeanswers` having sent every answer too large for the
  // registers through storage before code is generated.
  struct TooManyArguments : std::runtime_error {
    TooManyArguments() : std::runtime_error("too many arguments") {}
  };

  // How many bytes one part takes on the stack.
  inline constexpr std::int64_t stack_slot = 8;

  // A part passed on the stack: its slot begins `offset` bytes above the stack
  // pointer at the call.
  struct OnStack {
    std::int64_t offset = 0;
    bool operator==(const OnStack&) const = default;
  };

  // Where one part of an argument travels.
  using Place = std::variant<const mc::PhysReg*, OnStack>;
  using Places = std::vector<std::vector<Place>>;

  // Where each argument is passed, in order, one place per part: a register of
  // its kind while there is one, an eight-byte slot on the stack after that.
  // `chained` passes the first in the chain register, and `answer_first` --
  // where the first is where the answer goes -- in the convention's
  // `answer_reg` where it has one.
  Places argument_places(const CallConvDesc& cconv, std::span<const ir::Type* const> types, bool chained = false,
                         bool answer_first = false);

  // How much room the parts `places` puts on the stack take, rounded to sixteen.
  std::int64_t stack_bytes(const Places& places);

  // Which registers a value of `ty` is answered with, one per part.
  std::vector<const mc::PhysReg*> result_places(const CallConvDesc& cconv, const ir::Type* ty);

  // Every call of a function, with the convention it is placed by and where
  // each part of each of its arguments goes.
  using CallsPlaced = std::unordered_map<const ir::Instruction*, std::pair<const CallConvDesc*, Places>>;

  // What `calls_placed` asks a convention of: how a target looks one up.
  using Conventions = std::function<const CallConvDesc&(std::string_view)>;

  // Every call of `func`, placed: the callee's convention, and for a call
  // through a value the language's own with the environment in its chain
  // register.  Asked before any code is generated, the stack's share being
  // room at the bottom of the caller's frame taken before anything else is.
  CallsPlaced calls_placed(const ir::Function& func, const Conventions& conventions);

} // namespace pl4g::target
