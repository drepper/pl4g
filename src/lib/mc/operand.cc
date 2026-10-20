// Operands and the expressions that can stand for an address.
#include "mc/operand.hh"

#include <vector>

#include "mc/fragment.hh"


namespace pl4g::mc {

  MCSection::MCSection() = default;
  MCSection::MCSection(MCSection&&) noexcept = default;
  MCSection::~MCSection() = default;


  std::string BinExpr::render() const
  {
    return "(" + lhs->render() + " " + op + " " + rhs->render() + ")";
  }


  ExprRef const_expr(std::int64_t value)
  {
    return std::make_shared<ConstExpr>(value);
  }


  ExprRef sym_expr(MCSymbol* symbol)
  {
    return std::make_shared<SymExpr>(symbol);
  }


  ExprRef bin_expr(char op, ExprRef lhs, ExprRef rhs)
  {
    return std::make_shared<BinExpr>(op, std::move(lhs), std::move(rhs));
  }


  bool same_expr(const MCExpr* a, const MCExpr* b) noexcept
  {
    if (a == b)
      return true;
    if (a == nullptr || b == nullptr || a->kind != b->kind)
      return false;
    switch (a->kind) {
    case MCExpr::Kind::constant:
      return static_cast<const ConstExpr*>(a)->value == static_cast<const ConstExpr*>(b)->value;
    case MCExpr::Kind::symbol:
      return static_cast<const SymExpr*>(a)->symbol == static_cast<const SymExpr*>(b)->symbol;
    case MCExpr::Kind::binary: {
      auto l = static_cast<const BinExpr*>(a);
      auto r = static_cast<const BinExpr*>(b);
      return l->op == r->op && same_expr(l->lhs.get(), r->lhs.get()) && same_expr(l->rhs.get(), r->rhs.get());
    }
    }
    return false;
  }


  std::string decimal(__int128 value)
  {
    if (value == 0)
      return "0";
    bool negative = value < 0;
    auto magnitude = negative ? -static_cast<unsigned __int128>(value) : static_cast<unsigned __int128>(value);
    std::string out;
    for (; magnitude != 0; magnitude /= 10)
      out.insert(out.begin(), char('0' + int(magnitude % 10)));
    return negative ? "-" + out : out;
  }


  std::string MCReg::render() const
  {
    if (reg.is_physical())
      return reg.phys()->name;
    if (bits)
      return reg.name() + ":" + std::to_string(*bits);
    return reg.name();
  }


  std::string MCImm::render() const
  {
    return decimal(value);
  }


  bool MCImm::fits() const noexcept
  {
    __int128 one = 1;
    if (is_signed)
      return -(one << (bits - 1)) <= value && value < (one << (bits - 1));
    return 0 <= value && value < (one << bits);
  }


  std::string MCMem::render() const
  {
    std::string out;
    if (seg != nullptr)
      out += seg->name + ":";
    std::vector<std::string> inner;
    if (rip_relative)
      inner.push_back("rip");
    else if (base)
      inner.push_back(base.name());
    if (index)
      inner.push_back(index.name() + "*" + std::to_string(scale));
    if (disp_sym != nullptr)
      inner.push_back(disp_sym->render());
    if (disp != 0 || inner.empty())
      inner.push_back(std::to_string(disp));
    out += "[";
    for (std::size_t i = 0; i < inner.size(); ++i)
      out += (i > 0 ? " + " : "") + inner[i];
    return out + "]";
  }


  bool MCMem::operator==(const MCMem& other) const noexcept
  {
    return base == other.base && index == other.index && scale == other.scale && disp == other.disp
           && same_expr(disp_sym.get(), other.disp_sym.get()) && seg == other.seg
           && rip_relative == other.rip_relative && size_bits == other.size_bits && is_signed == other.is_signed;
  }


  std::string MCSymRef::render() const
  {
    if (addend == 0)
      return expr->render();
    return expr->render() + "+" + std::to_string(addend);
  }


  bool MCSymRef::operator==(const MCSymRef& other) const noexcept
  {
    return same_expr(expr.get(), other.expr.get()) && kind == other.kind && addend == other.addend;
  }


  std::string render(const MCOperand& operand)
  {
    return std::visit([](const auto& one) { return one.render(); }, operand);
  }


  Reg reg_of(const MCOperand& operand) noexcept
  {
    if (auto one = std::get_if<MCReg>(&operand))
      return one->reg;
    return {};
  }

} // namespace pl4g::mc
