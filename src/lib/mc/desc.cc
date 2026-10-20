// Instruction descriptors and the table that holds them.
#include "mc/desc.hh"


namespace pl4g::mc {

  bool OperandSpec::matches(const MCOperand& operand) const noexcept
  {
    if (auto reg = std::get_if<MCReg>(&operand)) {
      if (! has(kinds, OperandKind::reg))
        return false;
      if (fixed != nullptr)
        return reg->reg.is_physical() && reg->reg.phys() == fixed;
      if (rclass != nullptr && reg->reg.cls() != rclass)
        return false;
      return ! bits || reg->width() == *bits;
    }
    if (auto mem = std::get_if<MCMem>(&operand)) {
      if (! has(kinds, OperandKind::mem))
        return false;
      // Where the access states its width and the slot states one too, they
      // must agree.
      auto wanted = mem_bits ? mem_bits : bits;
      if (wanted && mem->size_bits)
        return *mem->size_bits == *wanted;
      return true;
    }
    if (auto imm = std::get_if<MCImm>(&operand)) {
      if (! has(kinds, OperandKind::imm))
        return false;
      if (bits && imm->bits > *bits)
        return false;
      if (imm_max && imm->value > *imm_max)
        return false;
      if (imm_min && imm->value < *imm_min)
        return false;
      return true;
    }
    if (std::holds_alternative<MCSymRef>(operand))
      return has(kinds, OperandKind::rel) || has(kinds, OperandKind::sym);
    return false;
  }


  OperandRole InstDesc::role_of(std::size_t index) const noexcept
  {
    if (roles)
      return (*roles)[index];
    return index == 0 ? OperandRole::def : OperandRole::use;
  }


  bool InstDesc::matches(std::span<const MCOperand> given) const noexcept
  {
    if (given.size() != operands.size())
      return false;
    for (std::size_t i = 0; i < given.size(); ++i)
      if (! operands[i].matches(given[i]))
        return false;
    return true;
  }


  namespace {

    std::string described(std::string_view mnemonic, std::span<const MCOperand> operands)
    {
      std::string text = "no encoding of '" + std::string(mnemonic) + "' accepts ";
      for (std::size_t i = 0; i < operands.size(); ++i)
        text += (i > 0 ? ", " : "") + render(operands[i]);
      return text;
    }

  } // anonymous namespace


  SelectionError::SelectionError(std::string mnemonic, std::span<const MCOperand> operands)
    : std::runtime_error(described(mnemonic, operands)), mnemonic(std::move(mnemonic))
  {
  }


  InstrTable::InstrTable(std::span<const InstDesc* const> rows)
  {
    for (auto row : rows)
      add(row);
  }


  void InstrTable::add(const InstDesc* row)
  {
    by_mnemonic[row->mnemonic].push_back(row);
  }


  std::vector<std::string> InstrTable::mnemonics() const
  {
    return by_mnemonic.keys();
  }


  std::vector<const InstDesc*> InstrTable::rows() const
  {
    std::vector<const InstDesc*> all;
    for (const auto& [_, rows] : by_mnemonic)
      all.insert(all.end(), rows.begin(), rows.end());
    return all;
  }


  std::vector<const InstDesc*> InstrTable::rows(std::string_view mnemonic) const
  {
    auto found = by_mnemonic.get(std::string(mnemonic));
    return found == nullptr ? std::vector<const InstDesc*>{} : *found;
  }


  const InstDesc* InstrTable::select(std::string_view mnemonic, std::span<const MCOperand> operands) const
  {
    auto found = by_mnemonic.get(std::string(mnemonic));
    const InstDesc* best = nullptr;
    if (found != nullptr)
      // The shortest that accepts them; of equal ones the first written, which
      // is what a strict comparison over them in order picks.
      for (auto row : *found)
        if (row->matches(operands) && (best == nullptr || row->est_size < best->est_size))
          best = row;
    if (best == nullptr) [[unlikely]]
      throw SelectionError(std::string(mnemonic), operands);
    return best;
  }

} // namespace pl4g::mc
