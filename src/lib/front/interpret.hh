// Running a macro while the compiler runs.
//
// A port of pypl4g's front/interpret.py.  A macro written as a function is an
// ordinary function that runs at expansion, and what runs is the IR the
// ordinary checker lowered its body to -- not the syntax tree -- so that what
// an expression means is said once, by the checker.
//
// This machine is not the one in interp/: that one runs a program as the back
// end would, faults and all; this one runs the small subset a macro needs with
// the rules pypl4g's has -- an answer that does not fit stops the macro, an
// instruction outside the subset is refused and says which it was.
//
// What the machine holds:
//
// - **Values** (`MValue`): a whole number for everything a register holds -- a
//   number, a truth value (0 or 1), a code point, a piece of the program, an
//   address -- and a run of values for what is several at once: a record, a
//   tuple, a result, an alternative.  Nothing is Python's `None`.  Numbers are
//   128 bits wide, which every integer type of the language (sixty-four bits at
//   most) and every product of two of them fit.
// - **Memory**: one byte vector that grows; an address is an offset into it.
// - **A piece of the program**: a handle, a number the expander gives meaning.
#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <variant>
#include <vector>

#include "ir/ir.hh"
#include "ir/layout.hh"


namespace pl4g::macro {

  // How many instructions one expansion may run before it is stopped.
  inline constexpr std::int64_t steps_allowed = 1'000'000;

  // How deep calls may nest.  pypl4g has no limit of its own and stops with
  // Python's recursion error, a few hundred calls deep; this one stops the
  // macro, as running too long does, rather than the compiler's stack.
  inline constexpr std::int64_t calls_allowed = 10'000;


  // Raised where a macro's body reaches something the machine will not run.
  struct Refused : std::runtime_error {
    std::string detail;
    const ir::Instruction* inst = nullptr;

    explicit Refused(std::string detail, const ir::Instruction* inst = nullptr)
      : std::runtime_error(detail), detail(std::move(detail)), inst(inst) {}
  };

  // Raised where a check in a macro's body did not hold, or it ran too long.
  struct Stopped : std::runtime_error {
    std::string detail;

    explicit Stopped(std::string detail) : std::runtime_error(detail), detail(std::move(detail)) {}
  };


  // One value the machine holds: nothing, a number, or several values.
  struct MValue {
    using Parts = std::shared_ptr<const std::vector<MValue>>;

    std::variant<std::monostate, __int128, Parts> v;

    MValue() = default;
    MValue(__int128 number) : v(number) {}
    MValue(bool truth) : v(__int128(truth ? 1 : 0)) {}
    explicit MValue(std::vector<MValue> parts) : v(std::make_shared<const std::vector<MValue>>(std::move(parts))) {}

    bool is_none() const noexcept { return std::holds_alternative<std::monostate>(v); }
    const __int128* number() const noexcept { return std::get_if<__int128>(&v); }
    const std::vector<MValue>* parts() const noexcept
    {
      auto p = std::get_if<Parts>(&v);
      return p == nullptr ? nullptr : p->get();
    }
    // Python's truth: nothing and nought are false, and so is no parts.
    bool truthy() const noexcept;
    // Python's `==`.
    bool operator==(const MValue& other) const;
  };


  // What a call to a function with no body does, given its arguments.  A
  // missing argument arrives as nothing.
  using Builtin = std::function<MValue(std::span<const MValue>)>;
  using Builtins = std::unordered_map<std::string, Builtin>;


  // One run of the compiler's own macros.  Memory and the step count last as
  // long as the machine does.
  class Machine {
  public:
    explicit Machine(Builtins builtins = {}, std::int64_t steps = steps_allowed)
      : builtins(std::move(builtins)), limit(steps) {}

    // Say what the functions with no body do: apart from the constructor, so
    // that one of them may be the machine's own allocator.
    void knows(Builtins found) { builtins = std::move(found); }

    // Run `func` on `args` and answer what it returned.
    MValue call(const ir::Function* func, std::span<const MValue> args);

    // Text as a value of `str`: where the bytes are, how many, no allocator.
    MValue text(std::string_view held);
    // And back: what a value of `str` says.
    std::string read_text(const MValue& held) const;
    // Room out of the machine's memory, for a macro that allocates.
    __int128 allocate(__int128 size);

  private:
    struct Frame;

    // One block: where it goes next, or that it returned.
    bool block(const ir::BasicBlock*& at, Frame& frame, std::vector<MValue>& incoming, MValue& answer);
    void goes(const ir::BlockTarget& target, Frame& frame, const ir::BasicBlock*& at,
              std::vector<MValue>& incoming);
    MValue one(const ir::Instruction* inst, Frame& frame, bool& computes);
    MValue binary(const ir::Instruction* inst, Frame& frame);
    MValue unary(const ir::Instruction* inst, Frame& frame);
    MValue cast(const ir::Instruction* inst, Frame& frame);
    std::uint64_t offset(const ir::Instruction* inst) const;

    __int128 at(const ir::Value* var);
    void put(__int128 at, const ir::Value* held);
    __int128 room(const ir::Type* held);
    MValue read(__int128 where, const ir::Type* ty) const;
    void write(__int128 where, const ir::Type* ty, const MValue& held);

    MValue operand(const ir::Value* value, const Frame& frame) const;
    __int128 number(const ir::Value* value, const Frame& frame) const;

    Builtins builtins;
    std::vector<std::uint8_t> memory = std::vector<std::uint8_t>(16);
    // Where each variable the program wrote has been put, by its name.
    std::unordered_map<std::string, __int128> placed;
    std::int64_t steps = 0;
    std::int64_t limit;
    std::int64_t depth = 0;
  };


  // How a value of `ty` is held in the machine, for a message about one.
  std::string what_it_holds(const ir::Type* ty);

} // namespace pl4g::macro
