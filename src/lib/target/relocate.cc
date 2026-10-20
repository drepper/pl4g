// Applying a position-independent image's table of relative relocations.
#include "target/relocate.hh"

#include "target/emit.hh"


namespace pl4g::target {

  namespace {

    // A small constant, as the other helpers here write one.
    mc::MCImm number(std::int64_t value)
    {
      return mc::MCImm{ value, 32, false };
    }

  } // anonymous namespace


  void emit_relocate(mc::Assembler& asm_, std::span<const mc::Reg> scratch)
  {
    // An even entry is the address of the next word to fix; an odd one is a
    // bitmap of the 63 words after the last address, its lowest bit the mark.
    auto mem = [](mc::Reg reg) { return memory(reg, 0, 64); };
    asm_.begin_function(std::string(relocate_symbol), false);
    // Values of the allocator's, of the width of `like`: what the selector
    // takes for a constant of its own is then one the allocator knows about.
    auto like = scratch[0];
    auto base = asm_.temporary(like), at = asm_.temporary(like), end = asm_.temporary(like),
         where = asm_.temporary(like), entry = asm_.temporary(like), word = asm_.temporary(like),
         place = asm_.temporary(like), mask = asm_.temporary(like);
    // Where the image is: its first byte's address, which in the image says
    // nought.
    asm_.address(base, std::string(image_start_symbol));
    asm_.address(at, std::string(relr_start_symbol));
    asm_.address(end, std::string(relr_end_symbol));
    asm_.loadreg(where, number(0));
    auto next = asm_.reserve_label("relr.next");
    auto read = asm_.reserve_label("relr.read");
    auto address = asm_.reserve_label("relr.address");
    auto bitmap = asm_.reserve_label("relr.bitmap");
    auto bit = asm_.reserve_label("relr.bit");
    auto set = asm_.reserve_label("relr.set");
    auto skip = asm_.reserve_label("relr.skip");
    auto spent = asm_.reserve_label("relr.spent");
    auto done = asm_.reserve_label("relr.done");
    // Every branch ends its block, as a lowered function's do: the allocator
    // follows a value along the edges blocks end in.
    auto branch_or_on = [&](mc::Condition cond, mc::Reg value, const mc::MCOperand& other, const std::string& to,
                            const std::string& on) {
      asm_.branch(cond, asm_.reg(value), other, to);
      asm_.falls_through(on);
      asm_.block(on);
    };
    asm_.falls_through(next);
    asm_.block(next);
    branch_or_on(mc::Condition::eq, at, asm_.reg(end), done, read);
    asm_.loadreg(entry, mem(at));
    asm_.op(mc::ops::plus, at, { asm_.reg(at), number(8) });
    asm_.op(mc::ops::and_, word, { asm_.reg(entry), number(1) });
    branch_or_on(mc::Condition::ne, word, number(0), bitmap, address);
    // An address: fix the word there, and the bitmaps after go on from the
    // one after it.
    asm_.op(mc::ops::plus, where, { asm_.reg(base), asm_.reg(entry) });
    asm_.loadreg(word, mem(where));
    asm_.op(mc::ops::plus, word, { asm_.reg(word), asm_.reg(base) });
    asm_.store(mem(where), asm_.reg(word));
    asm_.op(mc::ops::plus, where, { asm_.reg(where), number(8) });
    asm_.jump(next);
    // A bitmap: each bit set past the mark is a word to fix, in order.  A
    // mask walks the bits rather than the bitmap being shifted: this runs
    // before the entry point asks whether the processor can run what was
    // built for it, so nothing in it may be what only a later level has, and
    // an addition and a conjunction are what every level has.
    asm_.block(bitmap);
    asm_.loadreg(place, asm_.reg(where));
    asm_.loadreg(mask, number(2));
    asm_.falls_through(bit);
    asm_.block(bit);
    asm_.op(mc::ops::and_, word, { asm_.reg(entry), asm_.reg(mask) });
    branch_or_on(mc::Condition::eq, word, number(0), skip, set);
    asm_.loadreg(word, mem(place));
    asm_.op(mc::ops::plus, word, { asm_.reg(word), asm_.reg(base) });
    asm_.store(mem(place), asm_.reg(word));
    asm_.falls_through(skip);
    asm_.block(skip);
    asm_.op(mc::ops::plus, place, { asm_.reg(place), number(8) });
    // Past the top bit the mask doubles to nought.
    asm_.op(mc::ops::plus, mask, { asm_.reg(mask), asm_.reg(mask) });
    branch_or_on(mc::Condition::ne, mask, number(0), bit, spent);
    // Whatever the bitmap said, the next one starts 63 words further on.
    asm_.op(mc::ops::plus, where, { asm_.reg(where), number(63 * 8) });
    asm_.jump(next);
    asm_.block(done);
    asm_.ret();
    asm_.end_function();
  }

} // namespace pl4g::target
