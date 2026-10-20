// Operands and the expressions that can stand for an address.  A port of
// pypl4g/mc/operand.py.
//
// Operands are values and compare as values, as Python's frozen dataclasses
// do -- a register by identity, a symbol by identity, the rest field by field.
#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <variant>

#include "mc/reg.hh"
#include "mc/symbol.hh"


namespace pl4g::mc {

  // How the value of a symbol reference is to be computed.
  enum class RelocKind : std::uint8_t { abs, pcrel, got, plt };


  // Base of the expressions that may stand where an address is wanted.
  struct MCExpr {
    enum class Kind : std::uint8_t { constant, symbol, binary };
    Kind kind;

    explicit MCExpr(Kind kind) noexcept : kind(kind) {}
    virtual ~MCExpr() = default;
    // A readable form, for the debugging dump.
    virtual std::string render() const = 0;
  };

  using ExprRef = std::shared_ptr<const MCExpr>;

  // A constant.
  struct ConstExpr final : MCExpr {
    std::int64_t value;
    explicit ConstExpr(std::int64_t value) noexcept : MCExpr(Kind::constant), value(value) {}
    std::string render() const override { return std::to_string(value); }
  };

  // The address of a symbol.
  struct SymExpr final : MCExpr {
    MCSymbol* symbol;
    explicit SymExpr(MCSymbol* symbol) noexcept : MCExpr(Kind::symbol), symbol(symbol) {}
    std::string render() const override { return symbol->name; }
  };

  // The sum or difference of two expressions; `op` is "+" or "-".
  struct BinExpr final : MCExpr {
    char op;
    ExprRef lhs;
    ExprRef rhs;
    BinExpr(char op, ExprRef lhs, ExprRef rhs) noexcept
      : MCExpr(Kind::binary), op(op), lhs(std::move(lhs)), rhs(std::move(rhs)) {}
    std::string render() const override;
  };

  ExprRef const_expr(std::int64_t value);
  ExprRef sym_expr(MCSymbol* symbol);
  ExprRef bin_expr(char op, ExprRef lhs, ExprRef rhs);
  // Whether two expressions are equal as Python's dataclasses compare them.
  bool same_expr(const MCExpr* a, const MCExpr* b) noexcept;


  // A register operand: the register, and the width it names where that is
  // not the register's own.
  struct MCReg {
    Reg reg{};
    std::optional<int> bits = std::nullopt;

    int width() const noexcept { return bits ? *bits : reg.bits(); }
    std::string render() const;
    bool operator==(const MCReg&) const = default;
  };

  // An immediate operand of a stated width and signedness.
  struct MCImm {
    __int128 value = 0;
    int bits = 32;
    bool is_signed = true;

    std::string render() const;
    // Whether the value is representable in the stated width.
    bool fits() const noexcept;
    bool operator==(const MCImm&) const = default;
  };

  // A memory operand.
  struct MCMem {
    Reg base;
    Reg index;
    int scale = 1;
    std::int64_t disp = 0;
    ExprRef disp_sym;
    const PhysReg* seg = nullptr;
    // Relative to the end of the instruction rather than to a base register.
    bool rip_relative = false;
    // The width of the access, where the instruction does not imply it.
    std::optional<int> size_bits;
    // Whether a narrower value arrives widened by its sign.
    bool is_signed = false;

    std::string render() const;
    bool operator==(const MCMem& other) const noexcept;
  };

  // A reference to a symbol, as a branch target or an address.
  struct MCSymRef {
    ExprRef expr;
    RelocKind kind = RelocKind::pcrel;
    std::int64_t addend = 0;

    std::string render() const;
    bool operator==(const MCSymRef& other) const noexcept;
  };

  using MCOperand = std::variant<MCReg, MCImm, MCMem, MCSymRef>;

  // A readable form of any operand.
  std::string render(const MCOperand& operand);
  // The register an operand names, if it names one.
  Reg reg_of(const MCOperand& operand) noexcept;

  // Python's `str(int)` of a 128-bit value.
  std::string decimal(__int128 value);

} // namespace pl4g::mc
