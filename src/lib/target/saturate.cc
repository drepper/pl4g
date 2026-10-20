// The saturating operations, and the others written once for all targets.
#include "target/saturate.hh"
#include "target/emit.hh"


namespace pl4g::target {

  namespace {

    using ir::BinOp;
    using mc::Condition;

    mc::MCImm imm(__int128 value, int bits, bool is_signed)
    {
      return mc::MCImm{ value, bits, is_signed };
    }

    mc::MCImm zero()
    {
      return imm(0, 32, false);
    }

    // The unsaturating operation each saturating one corresponds to.
    BinOp ordinary(BinOp op)
    {
      switch (op) {
      case BinOp::sat_add: return BinOp::add;
      case BinOp::sat_sub: return BinOp::sub;
      case BinOp::sat_mul: return BinOp::mul;
      default: return op;
      }
    }

    // The two whose overflow a flag answers for at every width.
    bool flagged(BinOp op)
    {
      return op == BinOp::add || op == BinOp::sub;
    }

    // Compute the operation at the type's own width and ask the flags.
    void by_flags(mc::Assembler& asm_, BinOp op, const ir::IntType* ty, const mc::MCOperand& left,
                  const mc::MCOperand& right, mc::Reg destination, Fault& fault, Span span)
    {
      asm_.op_at(plain_op(op), destination, left, right, int(ty->bits), span);
      auto carry_on = asm_.reserve_label("in.range");
      asm_.branch_if_in_range(plain_op(op), ty->is_signed, carry_on, span);
      fault.out_of_range(asm_, span);
      asm_.block(carry_on);
    }

    // What is done where `lhs` and `rhs` stand in `cond`: the bound put in the
    // answer's place, or the program stopped.
    void answer(mc::Assembler& asm_, Condition cond, mc::Reg destination, const mc::MCOperand& lhs,
                const mc::MCOperand& rhs, const mc::MCOperand& bound, Fault* fault, Span span)
    {
      if (fault == nullptr) {
        asm_.clamp(cond, destination, lhs, rhs, bound, span);
        return;
      }
      auto carry_on = asm_.reserve_label("in.range");
      asm_.branch(mc::inverted(cond), lhs, rhs, carry_on, span);
      fault->out_of_range(asm_, span);
      asm_.block(carry_on);
    }

    // Compute the operation as it stands and bring it back to the ends of the
    // type.
    void exact(mc::Assembler& asm_, BinOp op, const ir::IntType* ty, const mc::MCOperand& left,
               const mc::MCOperand& right, mc::Reg destination, int bits, Fault* fault, Span span)
    {
      asm_.op(plain_op(op), destination, { left, right }, span);
      mc::MCOperand value = asm_.reg(destination);
      auto high = imm(ty->high(), bits, ty->is_signed);
      if (ty->is_signed) {
        answer(asm_, Condition::sgt, destination, value, high, high, fault, span);
        auto low = imm(ty->low(), bits, true);
        answer(asm_, Condition::slt, destination, value, low, low, fault, span);
        return;
      }
      if (ordinary(op) == BinOp::sub) {
        // The one unsigned answer that can come out below nought.
        answer(asm_, Condition::slt, destination, value, zero(), zero(), fault, span);
        return;
      }
      answer(asm_, Condition::ugt, destination, value, high, high, fault, span);
    }

    // Fill whole registers with the two operands and then compute exactly.
    void widened(mc::Assembler& asm_, BinOp op, const ir::IntType* ty, const mc::MCOperand& left,
                 const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, Fault* fault, Span span)
    {
      auto first = scratch.scratch();
      auto second = scratch.scratch();
      asm_.widen(first, left, int(ty->bits), ty->is_signed, span);
      asm_.widen(second, right, int(ty->bits), ty->is_signed, span);
      auto wide = scratch.scratch();
      exact(asm_, op, ty, asm_.reg(first), asm_.reg(second), wide, 64, fault, span);
      asm_.loadreg(destination, mc::MCReg{ wide, ty->bits >= 32 ? int(ty->bits) : 32 }, span);
    }

    // A product of the widest type, seen through the upper half of itself.
    void wide_product(mc::Assembler& asm_, const ir::IntType* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, Fault* fault, Span span)
    {
      auto high = scratch.scratch();
      asm_.wide_product(destination, high, left, right, ty->is_signed, span);
      if (! ty->is_signed) {
        auto bound = scratch.scratch();
        asm_.loadreg(bound, imm(ty->high(), 64, false), span);
        answer(asm_, Condition::ne, destination, asm_.reg(high), zero(), asm_.reg(bound), fault, span);
        return;
      }
      auto expected = scratch.scratch();
      asm_.shift(mc::ops::shift_right_signed, expected, asm_.reg(destination), imm(63, 32, false), 64, span);
      auto bound = scratch.scratch();
      asm_.loadreg(bound, imm(ty->high(), 64, true), span);
      if (fault == nullptr)
        // A product whose upper half is negative went past the bottom.
        asm_.clamp(Condition::slt, bound, asm_.reg(high), zero(), imm(ty->low(), 64, true), span);
      answer(asm_, Condition::ne, destination, asm_.reg(high), asm_.reg(expected), asm_.reg(bound), fault, span);
    }

    // The signed half of letting it wrap and asking what happened.
    void signed_wrapping(mc::Assembler& asm_, BinOp op, const ir::IntType* ty, const mc::MCReg& left,
                         const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, Fault* fault, Span span)
    {
      mc::MCOperand result = asm_.reg(destination);
      auto first = asm_.reg(scratch.scratch());
      auto second = asm_.reg(scratch.scratch());
      asm_.op(mc::ops::xor_, first.reg, { left, result }, span);
      if (ordinary(op) == BinOp::add)
        asm_.op(mc::ops::xor_, second.reg, { right, result }, span);
      else
        asm_.op(mc::ops::xor_, second.reg, { left, right }, span);
      asm_.op(mc::ops::and_, first.reg, { first, second }, span);
      auto bound = scratch.scratch();
      asm_.loadreg(bound, imm(ty->high(), 64, true), span);
      if (fault == nullptr)
        asm_.clamp(Condition::slt, bound, left, zero(), imm(ty->low(), 64, true), span);
      answer(asm_, Condition::slt, destination, first, zero(), asm_.reg(bound), fault, span);
    }

    // Let the operation wrap and ask the wrapped answer what happened.
    void wrapping(mc::Assembler& asm_, BinOp op, const ir::IntType* ty, const mc::MCOperand& left,
                  const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, Fault* fault, Span span)
    {
      if (ordinary(op) == BinOp::mul) {
        wide_product(asm_, ty, left, right, destination, scratch, fault, span);
        return;
      }
      // The left operand is read again after the operation.
      mc::MCReg held;
      if (auto reg = std::get_if<mc::MCReg>(&left))
        held = *reg;
      else {
        held = asm_.reg(scratch.scratch());
        asm_.loadreg(held.reg, left, span);
      }
      asm_.op(plain_op(op), destination, { held, right }, span);
      mc::MCOperand result = asm_.reg(destination);
      if (! ty->is_signed) {
        if (ordinary(op) == BinOp::add) {
          auto high = imm(ty->high(), 64, false);
          answer(asm_, Condition::ult, destination, result, held, high, fault, span);
        } else
          answer(asm_, Condition::ult, destination, held, right, zero(), fault, span);
        return;
      }
      signed_wrapping(asm_, op, ty, held, right, destination, scratch, fault, span);
    }

    // The body of the saturating and the trapping forms.
    void lower(mc::Assembler& asm_, BinOp op, const ir::Type* ty, const mc::MCOperand& left,
               const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits, Fault* fault,
               Span span)
    {
      auto integer = ir::as<ir::IntType>(ty);
      if (integer == nullptr) [[unlikely]]
        throw Unsupported("arithmetic on something that is not an integer");
      if (fault != nullptr && flagged(op) && asm_.flagged_widths().contains(int(integer->bits))) {
        by_flags(asm_, op, integer, left, right, destination, *fault, span);
        return;
      }
      if (int(integer->bits) < register_bits) {
        exact(asm_, op, integer, left, right, destination, register_bits, fault, span);
        return;
      }
      if (integer->bits < 64) {
        widened(asm_, op, integer, left, right, destination, scratch, fault, span);
        return;
      }
      wrapping(asm_, op, integer, left, right, destination, scratch, fault, span);
    }

    // `operand` where reading it twice is possible.
    mc::MCReg in_a_register(mc::Assembler& asm_, const mc::MCOperand& operand, Scratch& scratch, Span span)
    {
      if (auto reg = std::get_if<mc::MCReg>(&operand))
        return *reg;
      auto carried = asm_.reg(scratch.scratch());
      asm_.loadreg(carried.reg, operand, span);
      return carried;
    }

    // `operand` in a register named at the width the answer is.
    mc::MCReg at_the_width(mc::Assembler& asm_, const mc::MCOperand& operand, Scratch& scratch, int bits, Span span)
    {
      if (auto reg = std::get_if<mc::MCReg>(&operand))
        return *reg;
      auto carried = scratch.scratch();
      asm_.loadreg(carried, operand, span);
      return mc::MCReg{ carried, bits };
    }

    // `operand` naming the whole of the register it is in.
    mc::MCReg whole(const mc::MCReg& operand)
    {
      return mc::MCReg{ operand.reg, 64 };
    }

    // Put the bits above the type back to what the type says they are.
    void back_into_the_type(mc::Assembler& asm_, const ir::IntType* ty, mc::Reg destination, Span span)
    {
      if (ty->bits >= 64)
        return;
      if (! ty->is_signed) {
        asm_.op(mc::ops::and_, destination,
                { asm_.reg(destination),
                  imm((static_cast<__int128>(1) << ty->bits) - 1, ty->bits < 32 ? 32 : 64, false) },
                span);
        return;
      }
      if (ty->whole()) {
        asm_.widen(destination, asm_.reg(destination), int(ty->bits), true, span);
        return;
      }
      auto over = imm(64 - ty->bits, 32, false);
      asm_.shift(mc::ops::shift_left, destination, mc::MCReg{ destination, 64 }, over, 64, span);
      asm_.shift(mc::ops::shift_right_signed, destination, mc::MCReg{ destination, 64 }, over, 64, span);
    }

    // Turn the bits of `value` round by `distance`, with two shifts and an or.
    void rotate(mc::Assembler& asm_, BinOp op, const ir::IntType* ty, const mc::MCReg& value,
                const mc::MCReg& distance, mc::Reg destination, Scratch& scratch, Span span)
    {
      auto left = asm_.reg(scratch.scratch());
      auto right = asm_.reg(scratch.scratch());
      auto other = asm_.reg(scratch.scratch());
      asm_.op(mc::ops::minus, other.reg, { imm(ty->bits, 32, false), whole(distance) }, span);
      asm_.op(mc::ops::and_, other.reg, { other, imm(ty->bits - 1, 32, false) }, span);
      auto forwards = op == BinOp::rotl ? whole(distance) : other;
      auto backwards = op == BinOp::rotl ? other : whole(distance);
      asm_.shift(mc::ops::shift_left, left.reg, whole(value), forwards, 64, span);
      asm_.shift(mc::ops::shift_right, right.reg, whole(value), backwards, 64, span);
      asm_.op(mc::ops::or_, destination, { left, right }, span);
      back_into_the_type(asm_, ty, destination, span);
    }

    // The checked operation each wrapping shift is the unchecked form of.
    std::optional<BinOp> unchecked(BinOp op)
    {
      switch (op) {
      case BinOp::wrap_shl: return BinOp::shl;
      case BinOp::wrap_ashr: return BinOp::ashr;
      case BinOp::wrap_lshr: return BinOp::lshr;
      case BinOp::wrap_rotl: return BinOp::rotl;
      case BinOp::wrap_rotr: return BinOp::rotr;
      default: return std::nullopt;
      }
    }

    // The two questions a signed division asks of its operands, put together:
    // the dividend the most negative number and the divisor minus one.
    mc::MCReg smallest_by_minus_one(mc::Assembler& asm_, const ir::IntType* ty, const mc::MCReg& held,
                                    const mc::MCReg& divisor, int bits, Scratch& scratch, Span span)
    {
      auto smallest = asm_.reg(scratch.scratch());
      asm_.setcond(Condition::eq, smallest.reg, held, imm(ty->low(), bits, true), span);
      auto negative_one = asm_.reg(scratch.scratch());
      asm_.setcond(Condition::eq, negative_one.reg, divisor, imm(-1, 32, true), span);
      asm_.op(mc::ops::and_, smallest.reg, { smallest, negative_one }, span);
      return smallest;
    }

  } // anonymous namespace


  bool is_saturating(ir::BinOp op) noexcept
  {
    return op == BinOp::sat_add || op == BinOp::sat_sub || op == BinOp::sat_mul;
  }

  bool is_trapping(ir::BinOp op) noexcept
  {
    return op == BinOp::add || op == BinOp::sub || op == BinOp::mul;
  }

  bool is_wrapping(ir::BinOp op) noexcept
  {
    return op == BinOp::wrap_add || op == BinOp::wrap_sub || op == BinOp::wrap_mul;
  }

  bool is_shift(ir::BinOp op) noexcept
  {
    switch (op) {
    case BinOp::shl: case BinOp::ashr: case BinOp::lshr: case BinOp::rotl: case BinOp::rotr:
    case BinOp::wrap_shl: case BinOp::wrap_ashr: case BinOp::wrap_lshr: case BinOp::wrap_rotl:
    case BinOp::wrap_rotr:
      return true;
    default:
      return false;
    }
  }

  bool is_division(ir::BinOp op) noexcept
  {
    return op == BinOp::sdiv || op == BinOp::udiv || op == BinOp::srem || op == BinOp::urem;
  }

  std::optional<mc::Condition> extremum_condition(ir::BinOp op) noexcept
  {
    switch (op) {
    case BinOp::smax: return Condition::slt;
    case BinOp::umax: return Condition::ult;
    case BinOp::smin: return Condition::sgt;
    case BinOp::umin: return Condition::ugt;
    default: return std::nullopt;
    }
  }

  std::string_view operation_name(ir::BinOp op) noexcept
  {
    switch (op) {
    case BinOp::add: return "addition";
    case BinOp::sub: return "subtraction";
    case BinOp::mul: return "multiplication";
    case BinOp::shl: case BinOp::ashr: case BinOp::lshr:
    case BinOp::wrap_shl: case BinOp::wrap_ashr: case BinOp::wrap_lshr:
      return "shift";
    case BinOp::rotl: case BinOp::rotr: case BinOp::wrap_rotl: case BinOp::wrap_rotr:
      return "rotation";
    case BinOp::sdiv: case BinOp::udiv: case BinOp::fdiv:
      return "division";
    case BinOp::srem: case BinOp::urem:
      return "remainder";
    default:
      return {};
    }
  }

  const mc::Op* plain_op(ir::BinOp op)
  {
    switch (op) {
    case BinOp::sat_add: case BinOp::add: case BinOp::wrap_add:
      return mc::ops::plus;
    case BinOp::sat_sub: case BinOp::sub: case BinOp::wrap_sub:
      return mc::ops::minus;
    case BinOp::sat_mul: case BinOp::mul: case BinOp::wrap_mul:
      return mc::ops::times;
    default:
      throw InternalError("no plain operation for this one");
    }
  }


  void lower_saturating(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                        const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits,
                        Span span)
  {
    lower(asm_, op, ty, left, right, destination, scratch, register_bits, nullptr, span);
  }


  void lower_trapping(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits,
                      Fault& fault, Span span)
  {
    lower(asm_, op, ty, left, right, destination, scratch, register_bits, &fault, span);
  }


  void lower_division(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, Scratch& scratch, int register_bits,
                      Fault& fault, Fault& divide_by_zero, Span span)
  {
    auto integer = ir::as<ir::IntType>(ty);
    if (integer == nullptr) [[unlikely]]
      throw Unsupported("a division of something that is not an integer");
    int bits = register_bits < 64 ? std::max(32, int(integer->bits)) : 64;
    auto held = in_a_register(asm_, left, scratch, span);
    auto divisor = in_a_register(asm_, right, scratch, span);
    answer(asm_, Condition::eq, destination, divisor, zero(), zero(), &divide_by_zero, span);
    if (integer->is_signed) {
      auto smallest = smallest_by_minus_one(asm_, integer, held, divisor, bits, scratch, span);
      answer(asm_, Condition::ne, destination, smallest, zero(), zero(), &fault, span);
    }
    asm_.divide(destination, held, divisor, integer->is_signed, op == BinOp::srem || op == BinOp::urem, bits, span);
  }


  void lower_division_result(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                             const mc::MCOperand& right, mc::Reg destination, mc::Reg failed, Scratch& scratch,
                             int register_bits, Span span)
  {
    auto integer = ir::as<ir::IntType>(ty);
    if (integer == nullptr) [[unlikely]]
      throw Unsupported("a division of something that is not an integer");
    int bits = register_bits < 64 ? std::max(32, int(integer->bits)) : 64;
    auto held = in_a_register(asm_, left, scratch, span);
    auto divisor = in_a_register(asm_, right, scratch, span);
    asm_.setcond(Condition::eq, failed, divisor, zero(), span);
    if (integer->is_signed) {
      auto smallest = smallest_by_minus_one(asm_, integer, held, divisor, bits, scratch, span);
      asm_.op(mc::ops::or_, failed, { asm_.reg(failed), mc::MCReg{ smallest.reg, failed.bits() } }, span);
    }
    asm_.loadreg(destination, imm(0, std::max(32, int(integer->bits)), false), span);
    auto answered = asm_.reserve_label("no.answer");
    asm_.branch(Condition::ne, asm_.reg(failed), zero(), answered, span);
    asm_.divide(destination, held, divisor, integer->is_signed, op == BinOp::srem || op == BinOp::urem, bits, span);
    asm_.block(answered);
  }


  void lower_wrapping(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& left,
                      const mc::MCOperand& right, mc::Reg destination, int register_bits, Span span)
  {
    asm_.op(plain_op(op), destination, { left, right }, span);
    if (auto integer = ir::as<ir::IntType>(ty); integer != nullptr && int(integer->bits) < register_bits)
      back_into_the_type(asm_, integer, destination, span);
  }


  void lower_extremum(mc::Assembler& asm_, ir::BinOp op, const mc::MCOperand& left, const mc::MCOperand& right,
                      mc::Reg destination, Scratch& scratch, Span span)
  {
    auto held = at_the_width(asm_, left, scratch, destination.bits(), span);
    auto other = at_the_width(asm_, right, scratch, destination.bits(), span);
    asm_.loadreg(destination, held, span);
    asm_.clamp(*extremum_condition(op), destination, held, other, other, span);
  }


  void lower_shift(mc::Assembler& asm_, ir::BinOp op, const ir::Type* ty, const mc::MCOperand& value,
                   const mc::MCOperand& amount, mc::Reg destination, Scratch& scratch, int register_bits,
                   Fault& too_far, Span span)
  {
    auto integer = ir::as<ir::IntType>(ty);
    if (integer == nullptr) [[unlikely]]
      throw Unsupported("a shift of something that is not an integer");
    auto held = in_a_register(asm_, value, scratch, span);
    auto distance = in_a_register(asm_, amount, scratch, span);
    if (auto plain = unchecked(op)) {
      // Inside a wrap the distance is taken modulo the width of the type.
      asm_.op(mc::ops::and_, distance.reg, { distance, imm(integer->bits - 1, 32, false) }, span);
      op = *plain;
    } else
      answer(asm_, Condition::uge, destination, distance, imm(integer->bits, 32, false), zero(), &too_far, span);
    auto result = scratch.scratch();
    if (op == BinOp::rotl || op == BinOp::rotr)
      rotate(asm_, op, integer, held, distance, result, scratch, span);
    else {
      auto moving = op == BinOp::shl ? mc::ops::shift_left
                    : op == BinOp::lshr ? mc::ops::shift_right
                                        : mc::ops::shift_right_signed;
      asm_.shift(moving, result, whole(held), whole(distance), 64, span);
      back_into_the_type(asm_, integer, result, span);
    }
    asm_.loadreg(destination, mc::MCReg{ result, register_bits }, span);
  }

} // namespace pl4g::target
