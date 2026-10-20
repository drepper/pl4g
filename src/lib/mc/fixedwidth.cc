// Encoding for architectures whose instructions are all the same width.
#include "mc/fixedwidth.hh"


namespace pl4g::mc {

  bool Field::fits(__int128 value) const noexcept
  {
    __int128 one = 1;
    if (value_bits) {
      if (is_signed)
        return -(one << (*value_bits - 1)) <= value && value < (one << (*value_bits - 1));
      return 0 <= value && value < (one << *value_bits);
    }
    if ((value & ((one << shift) - 1)) != 0)
      return false;
    auto scaled = value >> shift;
    if (is_signed)
      return -(one << (width - 1)) <= scaled && scaled < (one << (width - 1));
    return 0 <= scaled && scaled <= __int128(mask());
  }


  const PhysReg* physical(Reg reg, std::optional<Span> span)
  {
    if (reg.is_virtual()) [[unlikely]]
      throw EncodingError("virtual register %v" + std::to_string(reg.virt()->ident)
                            + " reached the encoder; it has not been assigned",
                          span);
    return reg.phys();
  }


  namespace {

    std::uint64_t register_field(const MCOperand& operand, const Field& field, std::optional<Span> span)
    {
      auto reg = std::get_if<MCReg>(&operand);
      if (reg == nullptr) [[unlikely]]
        throw EncodingError("a register operand was expected", span);
      return std::uint64_t(physical(reg->reg, span)->enc()) & field.mask();
    }

    std::uint64_t immediate_field(const MCOperand& operand, const Field& field, std::optional<Span> span)
    {
      auto imm = std::get_if<MCImm>(&operand);
      if (imm == nullptr) [[unlikely]]
        throw EncodingError("an immediate operand was expected", span);
      if (! field.fits(imm->value)) [[unlikely]]
        throw EncodingError("an immediate of " + decimal(imm->value) + " does not fit the " + std::to_string(field.width)
                              + "-bit field of this instruction",
                            span);
      return std::uint64_t(imm->value >> field.shift) & field.mask();
    }

    MCFixup relocation(const MCOperand& operand, const Field& field, std::optional<Span> span)
    {
      auto ref = std::get_if<MCSymRef>(&operand);
      if (ref == nullptr) [[unlikely]]
        throw EncodingError("a symbol reference was expected", span);
      if (field.reloc == nullptr) [[unlikely]]
        throw EncodingError("a relocation field names no relocation kind", span);
      return MCFixup{ 0, field.reloc, ref->expr, 0, field.base_adjust, span ? *span : invalid_span };
    }

  } // anonymous namespace


  std::pair<Bytes, std::vector<MCFixup>> encode_fixed_width(const MCInst& inst, int size)
  {
    auto desc = dynamic_cast<const FixedWidthInstDesc*>(inst.desc);
    if (desc == nullptr) [[unlikely]]
      throw EncodingError("'" + inst.desc->mnemonic + "' is not described for this architecture");
    std::optional<Span> span;
    if (inst.span.is_valid())
      span = inst.span;
    std::uint64_t word = desc->template_word;
    std::vector<MCFixup> fixups;
    for (const auto& field : desc->fields) {
      if (std::size_t(field.operand) >= inst.operands.size()) [[unlikely]]
        throw EncodingError("an encoding names operand " + std::to_string(field.operand) + " but only "
                              + std::to_string(inst.operands.size()) + " were given",
                            span);
      const auto& operand = inst.operands[std::size_t(field.operand)];
      switch (field.kind) {
      case FieldKind::register_:
        word |= register_field(operand, field, span) << field.lsb;
        break;
      case FieldKind::immediate:
        word |= immediate_field(operand, field, span) << field.lsb;
        break;
      case FieldKind::relocation:
        fixups.push_back(relocation(operand, field, span));
        break;
      }
    }
    Bytes out(std::size_t(size), 0);
    for (int i = 0; i < size; ++i)
      out[std::size_t(i)] = std::uint8_t(word >> (8 * i));
    return { std::move(out), std::move(fixups) };
  }


  std::uint64_t read_word(const Bytes& data, std::size_t offset, int size)
  {
    std::uint64_t word = 0;
    for (int i = 0; i < size; ++i)
      word |= std::uint64_t(data[offset + std::size_t(i)]) << (8 * i);
    return word;
  }


  void write_word(Bytes& data, std::size_t offset, int size, std::uint64_t word)
  {
    for (int i = 0; i < size; ++i)
      data[offset + std::size_t(i)] = std::uint8_t(word >> (8 * i));
  }


  void insert_bits(Bytes& data, std::size_t offset, int size, std::uint64_t value, int lsb, int width)
  {
    auto word = read_word(data, offset, size);
    std::uint64_t mask = width >= 64 ? ~std::uint64_t(0) : (std::uint64_t(1) << width) - 1;
    write_word(data, offset, size, (word & ~(mask << lsb)) | ((value & mask) << lsb));
  }

} // namespace pl4g::mc
