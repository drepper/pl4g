// The AArch64 relocations, and how they are stored.
#include "target/aarch64/fixups.hh"

#include "mc/fixedwidth.hh"
#include "target/aarch64/desc.hh"


namespace pl4g::target::aarch64 {

  const mc::FixupKind branch26{ "aarch64_branch26", 4, mc::FixupBase::field_start };
  const mc::FixupKind branch19{ "aarch64_branch19", 4, mc::FixupBase::field_start };
  const mc::FixupKind adr_page21{ "aarch64_adr_page21", 4, mc::FixupBase::page_4k };
  const mc::FixupKind add_lo12{ "aarch64_add_lo12", 4, mc::FixupBase::absolute, true };
  const mc::FixupKind ldst8_lo12{ "aarch64_ldst8_lo12", 4, mc::FixupBase::absolute, true };
  const mc::FixupKind ldst16_lo12{ "aarch64_ldst16_lo12", 4, mc::FixupBase::absolute, true };
  const mc::FixupKind ldst32_lo12{ "aarch64_ldst32_lo12", 4, mc::FixupBase::absolute, true };
  const mc::FixupKind ldst64_lo12{ "aarch64_ldst64_lo12", 4, mc::FixupBase::absolute, true };
  const mc::FixupKind ldst128_lo12{ "aarch64_ldst128_lo12", 4, mc::FixupBase::absolute, true };


  namespace {

    // Put `value` into a run of bits of the instruction word at `offset`.
    void insert(mc::Bytes& data, std::size_t offset, __int128 value, int lsb, int width)
    {
      mc::insert_bits(data, offset, instruction_size, std::uint64_t(value), lsb, width);
    }

    // A branch offset of the given shape, in units of four bytes.
    void branch(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value, int width, int lsb)
    {
      if ((value & 3) != 0) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      __int128 scaled = value >> 2;
      if (! mc::signed_fits(scaled, width)) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      insert(data, offset, scaled, lsb, width);
    }

    // The page difference, which the instruction splits into two pieces.
    void adr_page(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value)
    {
      __int128 pages = value >> 12;
      if (! mc::signed_fits(pages, 21)) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      insert(data, offset, pages & 3, 29, 2);
      insert(data, offset, pages >> 2, 5, 19);
    }

    // The offset within the page, in units of what is touched.
    void lo12_scaled(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value, int touched)
    {
      __int128 within = value & 0xFFF;
      if (within % touched != 0) [[unlikely]]
        throw mc::FixupRangeError(fixup.kind, value);
      insert(data, offset, within / touched, 10, 12);
    }

  } // anonymous namespace


  void apply_fixup(mc::Bytes& data, std::size_t offset, const mc::MCFixup& fixup, __int128 value)
  {
    const mc::FixupKind* kind = fixup.kind;
    // The two that reach a whole address are stored as the bytes they are:
    // they name no instruction field.
    if (kind == &mc::abs32 || kind == &mc::abs64)
      mc::apply_little_endian(data, offset, fixup, value);
    else if (kind == &branch26)
      branch(data, offset, fixup, value, 26, 0);
    else if (kind == &branch19)
      branch(data, offset, fixup, value, 19, 5);
    else if (kind == &adr_page21)
      adr_page(data, offset, fixup, value);
    else if (kind == &add_lo12)
      insert(data, offset, value & 0xFFF, 10, 12);
    else if (kind == &ldst8_lo12)
      lo12_scaled(data, offset, fixup, value, 1);
    else if (kind == &ldst16_lo12)
      lo12_scaled(data, offset, fixup, value, 2);
    else if (kind == &ldst32_lo12)
      lo12_scaled(data, offset, fixup, value, 4);
    else if (kind == &ldst64_lo12)
      lo12_scaled(data, offset, fixup, value, 8);
    else if (kind == &ldst128_lo12)
      lo12_scaled(data, offset, fixup, value, 16);
    else [[unlikely]]
      throw mc::FixupRangeError(kind, value);
  }


  const FixupKinds& by_name()
  {
    static const FixupKinds made = [] {
      FixupKinds kinds;
      for (const mc::FixupKind* one : { &mc::abs32, &mc::abs64, &mc::pcrel32, &branch26, &branch19, &adr_page21,
                                        &add_lo12, &ldst8_lo12, &ldst16_lo12, &ldst32_lo12, &ldst64_lo12,
                                        &ldst128_lo12 })
        kinds.emplace(one->name, one);
      return kinds;
    }();
    return made;
  }

} // namespace pl4g::target::aarch64
