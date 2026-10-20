// Fixups.
#include "mc/fixup.hh"


namespace pl4g::mc {

  const FixupKind abs32{ "abs32", 4 };
  const FixupKind abs64{ "abs64", 8 };
  const FixupKind pcrel8{ "pcrel8", 1, FixupBase::field_end };
  const FixupKind pcrel32{ "pcrel32", 4, FixupBase::field_end };
  const FixupKind pcrel32_at_field{ "pcrel32_at_field", 4, FixupBase::field_start };


  FixupRangeError::FixupRangeError(const FixupKind* kind, __int128 value)
    : std::runtime_error("a value of " + decimal(value) + " does not fit the " + kind->name + " field"),
      kind(kind),
      value(value)
  {
  }


  __int128 evaluate(const MCExpr* expr)
  {
    switch (expr->kind) {
    case MCExpr::Kind::constant:
      return static_cast<const ConstExpr*>(expr)->value;
    case MCExpr::Kind::symbol: {
      auto symbol = static_cast<const SymExpr*>(expr)->symbol;
      if (! symbol->vaddr) [[unlikely]]
        throw UnresolvedSymbol(symbol->name);
      return __int128(*symbol->vaddr);
    }
    case MCExpr::Kind::binary: {
      auto binary = static_cast<const BinExpr*>(expr);
      auto lhs = evaluate(binary->lhs.get());
      auto rhs = evaluate(binary->rhs.get());
      return binary->op == '+' ? lhs + rhs : lhs - rhs;
    }
    }
    throw UnresolvedSymbol("<expression>");
  }


  __int128 fixup_value(const MCFixup& fixup, std::uint64_t fixup_vaddr)
  {
    __int128 target = evaluate(fixup.target.get());
    __int128 at = __int128(fixup_vaddr);
    switch (fixup.kind->base) {
    case FixupBase::absolute:
      return target;
    case FixupBase::field_end:
      return target - (at + fixup.kind->size + fixup.trailing + fixup.base_adjust);
    case FixupBase::field_start:
      return target - (at + fixup.base_adjust);
    case FixupBase::page_4k: {
      __int128 base = at + fixup.base_adjust;
      return (target & ~__int128(0xFFF)) - (base & ~__int128(0xFFF));
    }
    }
    return target;
  }


  void apply_little_endian(Bytes& data, std::size_t offset, const MCFixup& fixup, __int128 value)
  {
    int size = fixup.kind->size;
    int bits = size * 8;
    __int128 one = 1;
    // The field is refused below its signed range and above its unsigned
    // one, as pypl4g refuses it.
    if (! (-(one << (bits - 1)) <= value && value < (one << bits))) [[unlikely]]
      throw FixupRangeError(fixup.kind, value);
    auto stored = static_cast<unsigned __int128>(value);
    for (int i = 0; i < size; ++i)
      data[offset + std::size_t(i)] = std::uint8_t(stored >> (8 * i));
  }


  bool signed_fits(__int128 value, int bits) noexcept
  {
    __int128 one = 1;
    return -(one << (bits - 1)) <= value && value < (one << (bits - 1));
  }

} // namespace pl4g::mc
