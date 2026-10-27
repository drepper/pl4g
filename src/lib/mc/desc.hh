// Instruction descriptors and the table that holds them.  A port of
// pypl4g/mc/desc.py.
//
// A descriptor says what an instruction *is*: its name, the operands it
// accepts, the registers it touches besides those operands, and roughly how
// large it encodes.  How it is encoded differs entirely between architectures,
// so each target derives from `InstDesc` with the fields its own encoder
// reads, and finds them again with `dynamic_cast` (Python's `isinstance`).
#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>

#include "mc/operand.hh"
#include "util/ordered.hh"


namespace pl4g::mc {

  // The shapes an operand slot will accept, as bits.
  enum class OperandKind : std::uint8_t { reg = 1, mem = 2, imm = 4, rel = 8, sym = 16 };

  constexpr OperandKind operator|(OperandKind a, OperandKind b) noexcept
  {
    return OperandKind(std::uint8_t(a) | std::uint8_t(b));
  }
  constexpr bool has(OperandKind set, OperandKind one) noexcept { return (std::uint8_t(set) & std::uint8_t(one)) != 0; }

  // Whether an instruction reads an operand, writes it, or both.
  enum class OperandRole : std::uint8_t { use, def, def_use };


  // What one operand slot of an instruction accepts.
  struct OperandSpec {
    OperandKind kinds{};
    const RegClass* rclass = nullptr;
    std::optional<int> bits{};
    // A slot that is always one particular register.
    const PhysReg* fixed = nullptr;
    // The width of the place, where a slot takes a register or a place in
    // memory and the two are named at different widths.
    std::optional<int> mem_bits{};
    // The largest and the smallest value an immediate slot accepts.
    std::optional<__int128> imm_max{};
    std::optional<__int128> imm_min{};

    // Whether `operand` may be used in this slot.
    bool matches(const MCOperand& operand) const noexcept;
  };


  // Properties of an instruction that passes need to know, as bits.
  enum class InstFlags : std::uint16_t {
    none = 0,
    terminator = 1 << 0,
    call = 1 << 1,
    barrier = 1 << 2,
    may_load = 1 << 3,
    may_store = 1 << 4,
    // A write of the narrow view clears the rest of the register.
    zext32 = 1 << 5,
    lockable = 1 << 6,
    // Copies one operand to another and does nothing else.
    move = 1 << 7,
    // Returns from the function.
    return_ = 1 << 8,
  };

  constexpr InstFlags operator|(InstFlags a, InstFlags b) noexcept
  {
    return InstFlags(std::uint16_t(a) | std::uint16_t(b));
  }
  constexpr bool has(InstFlags set, InstFlags one) noexcept { return (std::uint16_t(set) & std::uint16_t(one)) != 0; }


  // What an instruction is, without saying how it is encoded.
  struct InstDesc {
    std::string mnemonic;
    std::vector<OperandSpec> operands;
    std::vector<const PhysReg*> implicit_defs;
    std::vector<const PhysReg*> implicit_uses;
    InstFlags flags = InstFlags::none;
    // The encoded size in bytes, used to choose between rows that overlap.
    int est_size = 0;
    // What the instruction does with each operand; nothing means the first is
    // written and the rest read.
    std::optional<std::vector<OperandRole>> roles;

    virtual ~InstDesc() = default;

    // What this instruction does with the operand in position `index`.
    OperandRole role_of(std::size_t index) const noexcept;
    // Whether this row accepts the given operands.
    bool matches(std::span<const MCOperand> operands) const noexcept;
  };


  // No row of the table accepts the given operands.
  struct SelectionError : std::runtime_error {
    std::string mnemonic;
    explicit SelectionError(std::string mnemonic, std::span<const MCOperand> operands);
  };


  // The encoding table of one target, indexed by mnemonic.  It refers to the
  // rows; whoever made them keeps them.
  class InstrTable {
  public:
    InstrTable() = default;
    explicit InstrTable(std::span<const InstDesc* const> rows);

    void add(const InstDesc* row);
    // Every mnemonic the table knows, in the order first seen.
    std::vector<std::string> mnemonics() const;
    // Every row, or every row of one mnemonic.
    std::vector<const InstDesc*> rows() const;
    std::vector<const InstDesc*> rows(std::string_view mnemonic) const;
    // The shortest row that accepts `operands`, the first written of equal ones.
    const InstDesc* select(std::string_view mnemonic, std::span<const MCOperand> operands) const;

  private:
    OrderedMap<std::string, std::vector<const InstDesc*>> by_mnemonic;
  };

} // namespace pl4g::mc
