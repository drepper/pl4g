// Counting the bits of a value.
#include "target/bitcount.hh"


namespace pl4g::target {

  namespace {

    // The masks the counting sequence folds with, and what sums the bytes into
    // the top one.
    constexpr __int128 pairs = 0x5555555555555555;
    constexpr __int128 nibbles = 0x3333333333333333;
    constexpr __int128 bytes = 0x0F0F0F0F0F0F0F0F;
    constexpr __int128 sum = 0x0101010101010101;

    mc::MCImm unsigned_imm(__int128 value, int bits)
    {
      return mc::MCImm{ value, bits, false };
    }

    // How wide what holds a value of `bits` bits is, as `IntType::held` says.
    int holder_of(int bits) noexcept
    {
      return bits <= 8 ? 8 : bits <= 16 ? 16 : bits <= 32 ? 32 : count_width;
    }

    // Clear everything above `bits` of `held`.
    void mask_to(mc::Assembler& asm_, mc::Reg held, int bits, Scratch& scratch, Span span)
    {
      auto mask = scratch.scratch();
      asm_.loadreg(mask, unsigned_imm((static_cast<__int128>(1) << bits) - 1, count_width), span);
      asm_.op(mc::ops::and_, held, { asm_.reg(held), asm_.reg(mask) }, span);
    }

    // How many bits of `held` are set.
    void ones(mc::Assembler& asm_, mc::Reg held, mc::Reg destination, Scratch& scratch, Span span)
    {
      if (asm_.counts_ones()) {
        asm_.count_ones(destination, asm_.reg(held), span);
        return;
      }
      auto mask = scratch.scratch();
      auto other = scratch.scratch();
      // Pairs: each pair of bits replaced by how many of the two are set.
      asm_.loadreg(mask, unsigned_imm(pairs, count_width), span);
      asm_.shift(mc::ops::shift_right, other, asm_.reg(held), unsigned_imm(1, 32), count_width, span);
      asm_.op(mc::ops::and_, other, { asm_.reg(other), asm_.reg(mask) }, span);
      asm_.op(mc::ops::minus, held, { asm_.reg(held), asm_.reg(other) }, span);
      // Nibbles: the two pairs of each added together.
      asm_.loadreg(mask, unsigned_imm(nibbles, count_width), span);
      asm_.shift(mc::ops::shift_right, other, asm_.reg(held), unsigned_imm(2, 32), count_width, span);
      asm_.op(mc::ops::and_, other, { asm_.reg(other), asm_.reg(mask) }, span);
      asm_.op(mc::ops::and_, held, { asm_.reg(held), asm_.reg(mask) }, span);
      asm_.op(mc::ops::plus, held, { asm_.reg(held), asm_.reg(other) }, span);
      // Bytes: the two nibbles of each added together, masked after.
      asm_.shift(mc::ops::shift_right, other, asm_.reg(held), unsigned_imm(4, 32), count_width, span);
      asm_.op(mc::ops::plus, held, { asm_.reg(held), asm_.reg(other) }, span);
      asm_.loadreg(mask, unsigned_imm(bytes, count_width), span);
      asm_.op(mc::ops::and_, held, { asm_.reg(held), asm_.reg(mask) }, span);
      // And the bytes summed into the top one by a multiplication.
      asm_.loadreg(mask, unsigned_imm(sum, count_width), span);
      asm_.op(mc::ops::times, held, { asm_.reg(held), asm_.reg(mask) }, span);
      asm_.shift(mc::ops::shift_right, destination, asm_.reg(held), unsigned_imm(count_width - 8, 32), count_width,
                 span);
    }

    // How many zeroes stand above the highest set bit of `held`, counted over
    // `bits` and not over the register.
    void leading(mc::Assembler& asm_, mc::Reg held, int bits, mc::Reg destination, Scratch& scratch, Span span)
    {
      if (asm_.counts_leading()) {
        asm_.count_leading(destination, asm_.reg(held), span);
        if (bits < count_width)
          asm_.op(mc::ops::minus, destination, { asm_.reg(destination), unsigned_imm(count_width - bits, 32) }, span);
        return;
      }
      // Every bit below the highest set one set as well, after which the zeroes
      // above it are the width less the number of bits set.
      auto other = scratch.scratch();
      for (int distance : { 1, 2, 4, 8, 16, 32 }) {
        asm_.shift(mc::ops::shift_right, other, asm_.reg(held), unsigned_imm(distance, 32), count_width, span);
        asm_.op(mc::ops::or_, held, { asm_.reg(held), asm_.reg(other) }, span);
      }
      ones(asm_, held, destination, scratch, span);
      // The width less what was counted, in a register of its own.
      auto width = scratch.scratch();
      asm_.loadreg(width, unsigned_imm(bits, 32), span);
      asm_.op(mc::ops::minus, width, { asm_.reg(width), asm_.reg(destination) }, span);
      asm_.loadreg(destination, asm_.reg(width), span);
    }

    // How many zeroes stand below the lowest set bit of `value`, counted over
    // `bits`.  A value narrower than the register has the bit just above its
    // own set first, which stops the count at the width of the type and keeps
    // whatever stood above the value in the register out of it.
    void trailing(mc::Assembler& asm_, mc::Reg held, const mc::MCOperand& value, int bits, mc::Reg destination,
                  Scratch& scratch, Span span)
    {
      if (bits < count_width) {
        asm_.widen(held, value, holder_of(bits), false, span);
        auto stop = scratch.scratch();
        asm_.loadreg(stop, unsigned_imm(static_cast<__int128>(1) << bits, count_width), span);
        asm_.op(mc::ops::or_, held, { asm_.reg(held), asm_.reg(stop) }, span);
      } else
        asm_.loadreg(held, value, span);
      if (asm_.counts_trailing()) {
        asm_.count_trailing(destination, asm_.reg(held), span);
        return;
      }
      // The zeroes below the lowest set bit, made ones and nothing else: the
      // value less one, with what the value itself has cleared from it.
      auto below = scratch.scratch();
      auto one = scratch.scratch();
      asm_.loadreg(one, unsigned_imm(1, count_width), span);
      asm_.op(mc::ops::minus, below, { asm_.reg(held), asm_.reg(one) }, span);
      asm_.op(mc::ops::not_, held, { asm_.reg(held) }, span);
      asm_.op(mc::ops::and_, held, { asm_.reg(held), asm_.reg(below) }, span);
      ones(asm_, held, destination, scratch, span);
    }

  } // anonymous namespace


  void lower_count(mc::Assembler& asm_, ir::UnOp op, const mc::MCOperand& value, int bits, bool is_signed,
                   mc::Reg destination, Scratch& scratch, Span span)
  {
    auto held = scratch.scratch();
    if (op == ir::UnOp::count_trailing) {
      auto answer = scratch.scratch();
      trailing(asm_, held, value, bits, answer, scratch, span);
      asm_.loadreg(destination, mc::MCReg{ answer, destination ? destination.bits() : count_width }, span);
      return;
    }
    // The type's own bits and nothing above them.
    if (bits < count_width) {
      asm_.widen(held, value, holder_of(bits), false, span);
      // A signed value's sign stands above its own bits, up to the holder's.
      if (is_signed)
        mask_to(asm_, held, bits, scratch, span);
    } else
      asm_.loadreg(held, value, span);
    auto answer = scratch.scratch();
    if (op == ir::UnOp::count_ones)
      ones(asm_, held, answer, scratch, span);
    else
      leading(asm_, held, bits, answer, scratch, span);
    asm_.loadreg(destination, mc::MCReg{ answer, destination ? destination.bits() : count_width }, span);
  }

} // namespace pl4g::target
