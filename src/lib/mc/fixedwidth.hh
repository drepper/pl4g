// Encoding for architectures whose instructions are all the same width: an
// instruction is a template word with every operand field zero and a list of
// which bits each operand occupies.  A port of pypl4g/mc/fixedwidth.py.
#pragma once

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "mc/desc.hh"
#include "mc/fixup.hh"
#include "mc/inst.hh"


namespace pl4g::mc {

  // An instruction cannot be encoded as written.
  struct EncodingError : std::runtime_error {
    std::string detail;
    std::optional<Span> span;
    EncodingError(std::string detail, std::optional<Span> span = std::nullopt)
      : std::runtime_error(detail), detail(std::move(detail)), span(span) {}
  };

  // What goes into a run of bits.
  enum class FieldKind : std::uint8_t { register_, immediate, relocation };

  // One run of bits in the instruction word, and what fills it.
  struct Field {
    FieldKind kind = FieldKind::register_;
    // Which operand of the instruction fills it.
    int operand = 0;
    int lsb = 0;
    int width = 5;
    // The value is divided by two to this power before being stored.
    int shift = 0;
    bool is_signed = false;
    // For a relocation field, the kind of fixup to record.
    const FixupKind* reloc = nullptr;
    // Added to the address a relocated value is measured from.
    std::int64_t base_adjust = 0;
    // The width of the whole value, where this field holds part of it.
    std::optional<int> value_bits;

    // The field's bits, at bit zero.
    std::uint64_t mask() const noexcept { return width >= 64 ? ~std::uint64_t(0) : (std::uint64_t(1) << width) - 1; }
    // Whether `value` can be stored in this field.
    bool fits(__int128 value) const noexcept;
  };

  // One row of a fixed-width architecture's encoding table.
  struct FixedWidthInstDesc : InstDesc {
    // The instruction word with every operand field zero.
    std::uint64_t template_word = 0;
    std::vector<Field> fields;
  };

  // `reg` as a physical register, or refuse to encode it.
  const PhysReg* physical(Reg reg, std::optional<Span> span);
  // Encode one instruction into its word of `size` bytes and its fixups.
  std::pair<Bytes, std::vector<MCFixup>> encode_fixed_width(const MCInst& inst, int size);

  // The instruction word at `offset`; store one; put `value` into a run of bits.
  std::uint64_t read_word(const Bytes& data, std::size_t offset, int size);
  void write_word(Bytes& data, std::size_t offset, int size, std::uint64_t word);
  void insert_bits(Bytes& data, std::size_t offset, int size, std::uint64_t value, int lsb, int width);

} // namespace pl4g::mc
