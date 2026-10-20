// The operations the assembler builder accepts.
//
// An operation is a value, not a member of a closed enumeration, so a target
// adds its own without touching this file: an x86-64 `syscall` is made the way
// `plus` is.  Operations are compared by identity.  A port of pypl4g/mc/ops.py.
#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>


namespace pl4g::mc {

  // One operation of the symbolic assembler.
  struct Op {
    std::string name;
    // How many source operands the operation takes.
    int arity = 0;
    // Whether the operation writes a destination register.
    bool has_result = true;
    // Whether the operation ends a block of straight-line code.
    bool is_terminator = false;
  };

  // Which whole number a floating-point number is rounded to.
  enum class Rounding : std::uint8_t { down, up, nearest, current };

  // What a conditional branch tests.
  enum class Condition : std::uint8_t { eq, ne, slt, sle, sgt, sge, ult, ule, ugt, uge };

  // The condition true exactly when `cond` is not.
  Condition inverted(Condition cond) noexcept;
  // The condition that holds when the two operands are exchanged.
  Condition swapped(Condition cond) noexcept;
  // The name pypl4g's enumeration gives it: "eq", "slt", ...
  std::string_view name_of(Condition cond) noexcept;
  std::string_view name_of(Rounding how) noexcept;


  // Add `op` to the registry of known operations, refusing a second of one
  // name, and answer the one kept.
  const Op* register_op(Op op);
  // The operation called `name`, if one is registered.
  const Op* lookup_op(std::string_view name);
  // Every registered operation, in the order they were registered.
  std::vector<const Op*> registered_ops();


  // The architecture-neutral operations, in three-address form with the
  // destination first.
  namespace ops {
    extern const Op* const move;
    extern const Op* const plus;
    extern const Op* const minus;
    extern const Op* const times;
    extern const Op* const and_;
    extern const Op* const or_;
    extern const Op* const xor_;
    extern const Op* const neg;
    extern const Op* const not_;
    extern const Op* const shift_left;
    extern const Op* const shift_right;
    // Shifting right brings in copies of the sign rather than zeroes.
    extern const Op* const shift_right_signed;
    extern const Op* const divide;
    extern const Op* const larger;
    extern const Op* const smaller;
    extern const Op* const compare;
    extern const Op* const call;
    extern const Op* const return_;
    extern const Op* const trap;
  }

} // namespace pl4g::mc
