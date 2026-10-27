// The product of two words without carries.
#include "target/carryless.hh"

#include <array>
#include <vector>


namespace pl4g::target {

  namespace {

    // How wide a word is.
    constexpr int width = 64;

    // Every bit of a word.
    constexpr __int128 all_bits = (static_cast<__int128>(1) << width) - 1;

    // Every fourth bit, from each of the four places a piece of a factor starts.
    constexpr std::array<__int128, 4> holes{ 0x1111111111111111, 0x2222222222222222,
                                             0x4444444444444444, static_cast<__int128>(0x8888888888888888u) };

    // The distances of the ladder that makes an exclusive or of every bit below.
    constexpr std::array<int, 6> ladder{ 1, 2, 4, 8, 16, 32 };

    mc::MCImm unsigned_imm(__int128 value, int bits)
    {
      return mc::MCImm{ value, bits, false };
    }

    // Whether `operand` is a number written down with every bit set.
    bool all_ones(const mc::MCOperand& operand) noexcept
    {
      auto imm = std::get_if<mc::MCImm>(&operand);
      return imm != nullptr && (imm->value & all_bits) == all_bits;
    }

    bool is_imm(const mc::MCOperand& operand) noexcept
    {
      return std::holds_alternative<mc::MCImm>(operand);
    }

    // `operand` in a register of its own, where it is not in one already.
    mc::Reg held(mc::Assembler& asm_, const mc::MCOperand& operand, Scratch& scratch, Span span)
    {
      if (auto reg = std::get_if<mc::MCReg>(&operand))
        return reg->reg;
      auto into = scratch.scratch();
      asm_.loadreg(into, operand, span);
      return into;
    }

    // `value` moved `distance` places, in a register of its own.
    mc::Reg shifted(mc::Assembler& asm_, const mc::Op* op, mc::Reg value, int distance, Scratch& scratch, Span span)
    {
      auto into = scratch.scratch();
      asm_.shift(op, into, asm_.reg(value), unsigned_imm(distance, 32), width, span);
      return into;
    }

    // The product of `factor` and a word of all ones: an exclusive or of every
    // bit at and below each place, and above it.
    void prefix(mc::Assembler& asm_, mc::Reg low, mc::Reg high, const mc::MCOperand& factor, Scratch& scratch,
                Span span)
    {
      auto up = scratch.scratch();
      auto down = scratch.scratch();
      asm_.loadreg(up, factor, span);
      asm_.loadreg(down, factor, span);
      for (int distance : ladder) {
        auto moved = shifted(asm_, mc::ops::shift_left, up, distance, scratch, span);
        asm_.op(mc::ops::xor_, up, { asm_.reg(up), asm_.reg(moved) }, span);
        moved = shifted(asm_, mc::ops::shift_right, down, distance, scratch, span);
        asm_.op(mc::ops::xor_, down, { asm_.reg(down), asm_.reg(moved) }, span);
      }
      asm_.loadreg(low, asm_.reg(up), span);
      // The upper half's bit k is the exclusive or of the bits above k, one
      // place further along than what `down` holds.
      asm_.shift(mc::ops::shift_right, high, asm_.reg(down), unsigned_imm(1, 32), width, span);
    }

    // The 64-bit product without carries of two words of thirty-two bits: each
    // cut into four pieces of every fourth bit, the sixteen products gathered by
    // where their bits are and masked down to those bits.
    mc::Reg product(mc::Assembler& asm_, mc::Reg left, mc::Reg right, Scratch& scratch, Span span)
    {
      std::vector<mc::Reg> lefts;
      std::vector<mc::Reg> rights;
      for (auto one : holes) {
        // One mask for the piece of each factor.
        auto mask = scratch.scratch();
        asm_.loadreg(mask, unsigned_imm(one, width), span);
        for (auto [word, found] : { std::pair{ left, &lefts }, std::pair{ right, &rights } }) {
          auto piece = scratch.scratch();
          asm_.op(mc::ops::and_, piece, { asm_.reg(word), asm_.reg(mask) }, span);
          found->push_back(piece);
        }
      }
      std::optional<mc::Reg> answer;
      for (std::size_t at = 0; at < holes.size(); ++at) {
        std::optional<mc::Reg> gathered;
        for (std::size_t one = 0; one < 4; ++one) {
          auto made = scratch.scratch();
          asm_.op(mc::ops::times, made, { asm_.reg(lefts[one]), asm_.reg(rights[(at + 4 - one) % 4]) }, span);
          if (! gathered) {
            gathered = made;
            continue;
          }
          asm_.op(mc::ops::xor_, *gathered, { asm_.reg(*gathered), asm_.reg(made) }, span);
        }
        auto mask = scratch.scratch();
        asm_.loadreg(mask, unsigned_imm(holes[at], width), span);
        asm_.op(mc::ops::and_, *gathered, { asm_.reg(*gathered), asm_.reg(mask) }, span);
        if (! answer) {
          answer = gathered;
          continue;
        }
        asm_.op(mc::ops::or_, *answer, { asm_.reg(*answer), asm_.reg(*gathered) }, span);
      }
      return *answer;
    }

    // The whole product of two words as three of two half-words.
    void karatsuba(mc::Assembler& asm_, mc::Reg low, mc::Reg high, mc::Reg left, mc::Reg right, Scratch& scratch,
                   Span span)
    {
      auto halves = [&](mc::Reg word) {
        auto lifted = shifted(asm_, mc::ops::shift_left, word, 32, scratch, span);
        auto lower = shifted(asm_, mc::ops::shift_right, lifted, 32, scratch, span);
        auto upper = shifted(asm_, mc::ops::shift_right, word, 32, scratch, span);
        return std::pair{ lower, upper };
      };
      auto [left_low, left_high] = halves(left);
      auto [right_low, right_high] = halves(right);
      auto lower = product(asm_, left_low, right_low, scratch, span);
      auto upper = product(asm_, left_high, right_high, scratch, span);
      auto left_both = scratch.scratch();
      auto right_both = scratch.scratch();
      asm_.op(mc::ops::xor_, left_both, { asm_.reg(left_low), asm_.reg(left_high) }, span);
      asm_.op(mc::ops::xor_, right_both, { asm_.reg(right_low), asm_.reg(right_high) }, span);
      auto middle = product(asm_, left_both, right_both, scratch, span);
      asm_.op(mc::ops::xor_, middle, { asm_.reg(middle), asm_.reg(lower) }, span);
      asm_.op(mc::ops::xor_, middle, { asm_.reg(middle), asm_.reg(upper) }, span);
      // The middle stands thirty-two places up, across the two halves.
      auto into_low = shifted(asm_, mc::ops::shift_left, middle, 32, scratch, span);
      auto into_high = shifted(asm_, mc::ops::shift_right, middle, 32, scratch, span);
      asm_.op(mc::ops::xor_, low, { asm_.reg(lower), asm_.reg(into_low) }, span);
      asm_.op(mc::ops::xor_, high, { asm_.reg(upper), asm_.reg(into_high) }, span);
    }

  } // namespace


  void lower_carryless(mc::Assembler& asm_, mc::Reg low, mc::Reg high, const mc::MCOperand& left,
                       const mc::MCOperand& right, Scratch& scratch, Span span)
  {
    if (asm_.multiplies_carryless()) {
      asm_.carryless_product(low, high, left, right, span);
      return;
    }
    if (all_ones(right) && ! is_imm(left)) {
      prefix(asm_, low, high, left, scratch, span);
      return;
    }
    if (all_ones(left) && ! is_imm(right)) {
      prefix(asm_, low, high, right, scratch, span);
      return;
    }
    auto first = held(asm_, left, scratch, span);
    auto second = held(asm_, right, scratch, span);
    karatsuba(asm_, low, high, first, second, scratch, span);
  }

} // namespace pl4g::target
