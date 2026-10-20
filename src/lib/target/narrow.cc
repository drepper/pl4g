// How a value narrower than a register is held.
#include "target/narrow.hh"


namespace pl4g::target {

  void normalize(mc::Assembler& asm_, const ir::Type* ty, mc::Reg destination, int register_bits, Span span)
  {
    // A code point and a value of an enumeration are each held the way a value
    // of the type that holds it is.
    if (auto c = ir::as<ir::CharType>(ty))
      ty = c->holder();
    else if (auto e = ir::as<ir::EnumType>(ty))
      ty = e->holder;
    auto integer = ir::as<ir::IntType>(ty);
    if (integer == nullptr || integer->is_signed || int(integer->bits) >= register_bits)
      return;
    auto mask = (static_cast<__int128>(1) << integer->bits) - 1;
    asm_.op(mc::ops::and_, destination, { asm_.reg(destination), asm_.imm(mask, integer->bits < 32 ? 32 : 64, false) },
            span);
  }

} // namespace pl4g::target
