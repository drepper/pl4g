// The RISC-V relocations, and how they are stored.
#include "target/riscv64/fixups.hh"

#include "mc/fixedwidth.hh"


namespace pl4g::target::riscv64 {

  const mc::FixupKind jal_fixup{ "riscv_jal", instruction_size, mc::FixupBase::field_start };
  const mc::FixupKind branch_fixup{ "riscv_branch", instruction_size, mc::FixupBase::field_start };
  const mc::FixupKind pcrel_hi20{ "riscv_pcrel_hi20", instruction_size, mc::FixupBase::field_start };
  const mc::FixupKind pcrel_lo12_i{ "riscv_pcrel_lo12_i", instruction_size, mc::FixupBase::field_start };
  const mc::FixupKind pcrel_lo12_s{ "riscv_pcrel_lo12_s", instruction_size, mc::FixupBase::field_start };
  const mc::FixupKind call_fixup{ "riscv_call", 2 * instruction_size, mc::FixupBase::field_start };


  namespace {

    std::uint64_t low_bits(__int128 value, int width)
    {
      return std::uint64_t(value) & ((std::uint64_t(1) << width) - 1);
    }

    // A jump offset, which the instruction keeps in four pieces.
    void apply_jal(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value)
    {
      if ((value & 1) != 0 || ! mc::signed_fits(value, 21)) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      std::uint64_t bits = (low_bits(value >> 20, 1) << 31) | (low_bits(value >> 1, 10) << 21)
                           | (low_bits(value >> 11, 1) << 20) | (low_bits(value >> 12, 8) << 12);
      auto word = mc::read_word(data, offset, instruction_size);
      mc::write_word(data, offset, instruction_size, (word & 0x00000FFF) | bits);
    }

    // A conditional branch offset, kept in four pieces of its own.
    void apply_branch(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value)
    {
      if ((value & 1) != 0 || ! mc::signed_fits(value, 13)) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      mc::insert_bits(data, offset, instruction_size, low_bits(value >> 12, 1), 31, 1);
      mc::insert_bits(data, offset, instruction_size, low_bits(value >> 5, 6), 25, 6);
      mc::insert_bits(data, offset, instruction_size, low_bits(value >> 1, 4), 8, 4);
      mc::insert_bits(data, offset, instruction_size, low_bits(value >> 11, 1), 7, 1);
    }

    // The upper bits of an address, rounded so that the signed lower half
    // added after them comes to the right sum either way.
    void apply_pcrel_hi20(mc::Bytes& data, std::size_t offset, __int128 value)
    {
      mc::insert_bits(data, offset, instruction_size, std::uint64_t((value + 0x800) >> 12), 12, 20);
    }

    // The lower twelve bits of an address.
    void apply_pcrel_lo12(mc::Bytes& data, std::size_t offset, __int128 value)
    {
      mc::insert_bits(data, offset, instruction_size, low_bits(value, 12), 20, 12);
    }

    // The same into a store: the top seven above the source register, the
    // bottom five where a destination would be.
    void apply_pcrel_lo12_s(mc::Bytes& data, std::size_t offset, __int128 value)
    {
      mc::insert_bits(data, offset, instruction_size, low_bits(value >> 5, 7), 25, 7);
      mc::insert_bits(data, offset, instruction_size, low_bits(value, 5), 7, 5);
    }

    // A call's offset, into the `auipc` and the `jalr` after it.
    void apply_call(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value)
    {
      if (! mc::signed_fits(value, 32)) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      apply_pcrel_hi20(data, offset, value);
      apply_pcrel_lo12(data, offset + instruction_size, value);
    }

  } // anonymous namespace


  void apply_fixup(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value)
  {
    const mc::FixupKind* kind = fixup.kind;
    // The two that reach a whole address name no instruction field: what holds
    // them is as wide as they are and is overwritten outright.
    if (kind == &mc::abs32 || kind == &mc::abs64)
      mc::apply_little_endian(data, offset, fixup, value);
    else if (kind == &jal_fixup)
      apply_jal(data, offset, fixup, value);
    else if (kind == &branch_fixup)
      apply_branch(data, offset, fixup, value);
    else if (kind == &pcrel_hi20)
      apply_pcrel_hi20(data, offset, value);
    else if (kind == &pcrel_lo12_i)
      apply_pcrel_lo12(data, offset, value);
    else if (kind == &pcrel_lo12_s)
      apply_pcrel_lo12_s(data, offset, value);
    else if (kind == &call_fixup)
      apply_call(data, offset, fixup, value);
    else [[unlikely]]
      throw mc::FixupRangeError(kind, value);
  }


  const FixupKinds& by_name()
  {
    static const FixupKinds made = [] {
      FixupKinds kinds;
      for (const mc::FixupKind* one : { &mc::abs32, &mc::abs64, &mc::pcrel32, &jal_fixup, &branch_fixup, &pcrel_hi20,
                                        &pcrel_lo12_i, &pcrel_lo12_s, &call_fixup })
        kinds[one->name] = one;
      return kinds;
    }();
    return made;
  }

} // namespace pl4g::target::riscv64
