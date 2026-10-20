// Fixups: the places an encoding leaves to fill in once addresses are known.
// A port of pypl4g/mc/fixup.py.
#pragma once

#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

#include "mc/operand.hh"
#include "source/location.hh"


namespace pl4g::mc {

  // What the stored value is measured from.
  enum class FixupBase : std::uint8_t {
    // The address itself.
    absolute,
    // The end of the patched field, plus anything the instruction puts after
    // it: what an x86-64 displacement is relative to.
    field_end,
    // The start of the patched field: on a fixed-width architecture the
    // address of the instruction itself.
    field_start,
    // The 4 KiB page the target lies in, relative to the instruction's page.
    page_4k,
  };

  // One way of referring to an address from inside an instruction.  A target
  // defines its own as objects that live as long as the program; fixups refer
  // to them, and two are the same kind when they are one object.
  struct FixupKind {
    std::string name;
    // The number of bytes the patch reads and writes back.
    int size = 0;
    FixupBase base = FixupBase::absolute;

    // The name used in the debugging dump.
    const std::string& label() const noexcept { return name; }
    // Whether the value is relative to where it is stored.
    bool is_pcrel() const noexcept { return base != FixupBase::absolute; }
  };

  // The kinds every architecture has.
  extern const FixupKind abs32;
  extern const FixupKind abs64;
  extern const FixupKind pcrel8;
  extern const FixupKind pcrel32;
  // A displacement measured from where it is stored rather than from the end
  // of the field.
  extern const FixupKind pcrel32_at_field;


  // One place to patch, once the addresses of the symbols are known.
  struct MCFixup {
    // Offset of the patched bytes within the fragment that owns the fixup.
    std::int64_t offset = 0;
    const FixupKind* kind = nullptr;
    ExprRef target;
    // Bytes from the end of the patched field to the end of the instruction.
    std::int64_t trailing = 0;
    // Added to the address the value is measured from.
    std::int64_t base_adjust = 0;
    Span span = invalid_span;
  };


  // A fixup refers to a symbol that nothing defines.
  struct UnresolvedSymbol : std::runtime_error {
    std::string name;
    explicit UnresolvedSymbol(std::string name) : std::runtime_error(name), name(std::move(name)) {}
  };

  // The value a fixup needs does not fit in the field that holds it.
  struct FixupRangeError : std::runtime_error {
    const FixupKind* kind;
    __int128 value;
    FixupRangeError(const FixupKind* kind, __int128 value);
  };


  using Bytes = std::vector<std::uint8_t>;

  // An expression evaluated to an address; every symbol it mentions has one.
  __int128 evaluate(const MCExpr* expr);
  // The number to store for `fixup`, whose field starts at `fixup_vaddr`.
  __int128 fixup_value(const MCFixup& fixup, std::uint64_t fixup_vaddr);

  // How a target stores a computed fixup value into the bytes it was found in.
  using FixupApplier = std::function<void(Bytes& data, std::size_t offset, const MCFixup& fixup, __int128 value)>;

  // Store `value` as a little-endian field of the kind's width, refusing one
  // the field cannot hold.
  void apply_little_endian(Bytes& data, std::size_t offset, const MCFixup& fixup, __int128 value);

  // Whether `value` is representable as a signed field of `bits` bits.
  bool signed_fits(__int128 value, int bits) noexcept;

} // namespace pl4g::mc
