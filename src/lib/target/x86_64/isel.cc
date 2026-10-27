// Instruction selection for x86-64: the selector.  The lowering of a whole
// function is in lower.cc.
#include "target/x86_64/isel.hh"

#include <format>
#include <map>

#include "target/runs.hh"
#include "target/x86_64/ops.hh"
#include "target/x86_64/regs.hh"


namespace pl4g::target::x86_64 {

  namespace {

    using mc::Condition;
    using mc::Insts;
    using mc::MCImm;
    using mc::MCMem;
    using mc::MCOperand;
    using mc::MCReg;
    using mc::MCSymRef;
    using mc::Reg;

    std::optional<Span> valid(Span span)
    {
      return span.is_valid() ? std::optional<Span>(span) : std::nullopt;
    }

    // The mnemonic that implements each architecture-neutral binary
    // operation, and the operations that write their one source in place.
    std::string_view binary_of(const mc::Op* op)
    {
      if (op == mc::ops::plus)
        return "add";
      if (op == mc::ops::minus)
        return "sub";
      if (op == mc::ops::times)
        return "imul";
      if (op == mc::ops::xor_)
        return "xor";
      if (op == mc::ops::and_)
        return "and";
      if (op == mc::ops::or_)
        return "or";
      return {};
    }

    std::string_view unary_of(const mc::Op* op) { return op == mc::ops::not_ ? "not" : ""; }

    // Operations that are one instruction with no operands.
    std::string_view nullary_of(const mc::Op* op)
    {
      if (op == syscall_op())
        return "syscall";
      if (op == cpuid_op())
        return "cpuid";
      if (op == xgetbv_op())
        return "xgetbv";
      if (op == mc::ops::trap)
        return "ud2";
      return {};
    }

    // The jump, the set and the conditional move that read each condition:
    // the condition is part of the opcode here.
    std::string_view conditional(Condition cond)
    {
      switch (cond) {
      case Condition::eq: return "je";
      case Condition::ne: return "jne";
      case Condition::slt: return "jl";
      case Condition::sle: return "jle";
      case Condition::sgt: return "jg";
      case Condition::sge: return "jge";
      case Condition::ult: return "jb";
      case Condition::ule: return "jbe";
      case Condition::ugt: return "ja";
      case Condition::uge: return "jae";
      }
      return {};
    }

    std::string_view set_of(Condition cond)
    {
      switch (cond) {
      case Condition::eq: return "sete";
      case Condition::ne: return "setne";
      case Condition::slt: return "setl";
      case Condition::sle: return "setle";
      case Condition::sgt: return "setg";
      case Condition::sge: return "setge";
      case Condition::ult: return "setb";
      case Condition::ule: return "setbe";
      case Condition::ugt: return "seta";
      case Condition::uge: return "setae";
      }
      return {};
    }

    std::string_view cmov_of(Condition cond)
    {
      switch (cond) {
      case Condition::eq: return "cmove";
      case Condition::ne: return "cmovne";
      case Condition::slt: return "cmovl";
      case Condition::sle: return "cmovle";
      case Condition::sgt: return "cmovg";
      case Condition::sge: return "cmovge";
      case Condition::ult: return "cmovb";
      case Condition::ule: return "cmovbe";
      case Condition::ugt: return "cmova";
      case Condition::uge: return "cmovae";
      }
      return {};
    }

    // Which move carries a floating-point value of a given width.
    std::string_view float_move_of(int bits)
    {
      if (bits == 32)
        return "movss";
      if (bits == 64)
        return "movsd";
      return {};
    }

    // What moves a run of a given number of bits; each reads exactly that
    // many and clears the rest of the register.
    std::string_view run_move_of(int bits)
    {
      if (bits == 32)
        return "movd";
      if (bits == 64)
        return "movq";
      if (bits == 128)
        return "movdqu";
      return {};
    }

    // The bitwise three over a run, one instruction whatever the lanes are;
    // and the arithmetic, one per lane width.
    std::string_view run_operation_of(const mc::Op* op, int bits)
    {
      if (op == mc::ops::and_)
        return "pand";
      if (op == mc::ops::or_)
        return "por";
      if (op == mc::ops::xor_)
        return "pxor";
      static const std::map<std::pair<const mc::Op*, int>, std::string_view> arithmetic{
        { { mc::ops::plus, 8 }, "paddb" },   { { mc::ops::plus, 16 }, "paddw" },
        { { mc::ops::plus, 32 }, "paddd" },  { { mc::ops::plus, 64 }, "paddq" },
        { { mc::ops::minus, 8 }, "psubb" },  { { mc::ops::minus, 16 }, "psubw" },
        { { mc::ops::minus, 32 }, "psubd" }, { { mc::ops::minus, 64 }, "psubq" },
        // The low half of the product, all a wrapping multiplication wants;
        // there is no byte-wide form and none for the widest lane.
        { { mc::ops::times, 16 }, "pmullw" }, { { mc::ops::times, 32 }, "pmulld" },
      };
      auto it = arithmetic.find({ op, bits });
      return it == arithmetic.end() ? std::string_view() : it->second;
    }

    // How one value is spread over a whole register, by its width: each step
    // doubles how wide the repeated piece is.
    std::vector<std::string_view> spread_of(int bits)
    {
      switch (bits) {
      case 8: return { "punpcklbw", "punpcklwd", "pshufd" };
      case 16: return { "punpcklwd", "pshufd" };
      case 32: return { "pshufd" };
      case 64: return { "punpcklqdq" };
      default: return {};
      }
    }

    // What puts one value in every lane in a single instruction, by its width.
    std::string_view broadcast_of(int bits)
    {
      switch (bits) {
      case 8: return "pbroadcastb";
      case 16: return "pbroadcastw";
      case 32: return "pbroadcastd";
      case 64: return "pbroadcastq";
      default: return {};
      }
    }

    // The saturating arithmetic over a run, by whether it adds, whether the
    // lanes are signed, and how wide one is.
    std::string_view run_saturating_of(bool adding, bool is_signed, int bits)
    {
      if (bits != 8 && bits != 16)
        return {};
      if (adding)
        return is_signed ? (bits == 8 ? "paddsb" : "paddsw") : (bits == 8 ? "paddusb" : "paddusw");
      return is_signed ? (bits == 8 ? "psubsb" : "psubsw") : (bits == 8 ? "psubusb" : "psubusw");
    }

    // What each shift is called here, in the two-operand and the newer
    // three-operand form.
    std::string_view shift_of(const mc::Op* op)
    {
      if (op == mc::ops::shift_left)
        return "shl";
      if (op == mc::ops::shift_right)
        return "shr";
      if (op == mc::ops::shift_right_signed)
        return "sar";
      return {};
    }

    std::string_view wide_shift_of(const mc::Op* op)
    {
      if (op == mc::ops::shift_left)
        return "shlx";
      if (op == mc::ops::shift_right)
        return "shrx";
      if (op == mc::ops::shift_right_signed)
        return "sarx";
      return {};
    }

    // What each operation is called for each width of floating-point value.
    // The `and` is no operation of the language: what the magnitude is built
    // from, the mask being a constant in the image.
    std::string_view float_binary_of(const mc::Op* op, int bits)
    {
      static const std::map<std::pair<const mc::Op*, int>, std::string_view> table{
        { { mc::ops::plus, 32 }, "addss" },    { { mc::ops::plus, 64 }, "addsd" },
        { { mc::ops::minus, 32 }, "subss" },   { { mc::ops::minus, 64 }, "subsd" },
        { { mc::ops::times, 32 }, "mulss" },   { { mc::ops::times, 64 }, "mulsd" },
        { { mc::ops::divide, 32 }, "divss" },  { { mc::ops::divide, 64 }, "divsd" },
        { { mc::ops::larger, 32 }, "maxss" },  { { mc::ops::larger, 64 }, "maxsd" },
        { { mc::ops::smaller, 32 }, "minss" }, { { mc::ops::smaller, 64 }, "minsd" },
        { { mc::ops::and_, 32 }, "andps" },    { { mc::ops::and_, 64 }, "andpd" },
      };
      auto it = table.find({ op, bits });
      return it == table.end() ? std::string_view() : it->second;
    }

    std::string_view float_compare_of(int bits) { return bits == 32 ? "ucomiss" : "ucomisd"; }

    // How wide a floating-point register is declared to be.
    constexpr int float_register_bits = 128;

    bool is_float(Reg reg) { return reg.cls() == registers().vec; }

    bool same_register(Reg dst, const MCOperand& operand)
    {
      auto reg = std::get_if<MCReg>(&operand);
      return reg != nullptr && reg->reg == dst;
    }

    // A number written at no fewer bits than a word, anything else as it is.
    MCOperand at_least_a_word(const MCOperand& operand)
    {
      if (auto imm = std::get_if<MCImm>(&operand); imm != nullptr && imm->bits < 32)
        return MCImm{ imm->value, 32, imm->is_signed };
      return operand;
    }

    // Whether `imm` says the same thing declared at `bits`.
    bool fits(const MCImm& imm, int bits)
    {
      if (imm.is_signed)
        return -(__int128(1) << (bits - 1)) <= imm.value && imm.value < (__int128(1) << (bits - 1));
      return 0 <= imm.value && imm.value < (__int128(1) << bits);
    }

    // The same operand named at `bits`, where naming it is what it is.
    MCOperand named_at(const MCOperand& operand, int bits)
    {
      if (auto reg = std::get_if<MCReg>(&operand))
        return MCReg{ reg->reg, bits };
      if (auto imm = std::get_if<MCImm>(&operand); imm != nullptr && imm->bits > bits && fits(*imm, bits))
        return MCImm{ imm->value, bits, imm->is_signed };
      return operand;
    }

    // `src`, declared wide enough that moving it into `into` bits says what it
    // means: an immediate narrower than the register is sign-extended.
    MCOperand carried(const MCOperand& src, int into)
    {
      auto imm = std::get_if<MCImm>(&src);
      if (imm == nullptr || imm->bits >= into)
        return src;
      if (-(__int128(1) << (imm->bits - 1)) <= imm->value && imm->value < (__int128(1) << (imm->bits - 1)))
        return src;
      return MCImm{ imm->value, into, imm->is_signed };
    }

    // How wide a register holding one of these operands has to be.
    int operand_width(std::span<const MCOperand> operands)
    {
      for (const auto& operand : operands)
        if (auto reg = std::get_if<MCReg>(&operand))
          return reg->width();
      return 32;
    }

    // How wide the register a run is held in is, given a move's two ends.
    int held_bits(const std::variant<Reg, MCMem>& dst, const MCOperand& src)
    {
      if (auto reg = std::get_if<Reg>(&dst))
        return std::max(128, reg->bits());
      if (auto from = std::get_if<MCReg>(&src))
        return std::max(128, from->width());
      return 128;
    }

    Insts joined(std::initializer_list<const Insts*> parts)
    {
      Insts all;
      for (auto part : parts)
        all.insert(all.end(), part->begin(), part->end());
      return all;
    }

    void extend(Insts& into, const Insts& more) { into.insert(into.end(), more.begin(), more.end()); }

    mc::VirtReg const* new_gpr(int bits) { return registers().info.new_virtual(registers().gpr, bits); }

    MCMem slot_at(std::int64_t slot, int size_bits = 64)
    {
      MCMem made;
      made.base = registers().rsp;
      made.disp = slot;
      made.size_bits = size_bits;
      return made;
    }

  } // anonymous namespace


  mc::InstRef X86Selector::inst(std::string_view mnemonic, std::vector<MCOperand> operands, Span span) const
  {
    const mc::InstDesc* desc;
    try {
      desc = table.select(mnemonic, operands);
    } catch (const mc::SelectionError& error) {
      throw UnsupportedOperation(error.what(), valid(span));
    }
    return mc::make_inst(desc, std::move(operands), span);
  }


  Insts X86Selector::select_move(Reg dst, const MCOperand& src, Span span)
  {
    if (is_float(dst))
      return select_float_move(dst, src, span);
    if (auto reg = std::get_if<MCReg>(&src); reg != nullptr && is_float(reg->reg)) [[unlikely]]
      throw UnsupportedOperation("moving a floating-point value into an ordinary register", span);
    if (auto mem = std::get_if<MCMem>(&src))
      return select_load(dst, *mem, span);
    if (same_register(dst, src))
      return {};
    // Clearing a register by exclusive-or is shorter, but it writes the
    // flags; the peephole pass substitutes it where the flags are dead.
    return { inst("mov", { MCReg{ dst }, carried(src, dst.bits()) }, span) };
  }


  Insts X86Selector::select_float_move(Reg dst, const MCOperand& src, Span span)
  {
    if (auto mem = std::get_if<MCMem>(&src)) {
      int width = mem->size_bits.value_or(64);
      auto mnemonic = float_move_of(width);
      if (mnemonic.empty()) [[unlikely]]
        throw UnsupportedOperation(std::format("reading {} bits of floating point", width), span);
      if (mem->disp_sym != nullptr || ! mem->base.is_none())
        return { inst(mnemonic, { MCReg{ dst, 128 }, *mem }, span) };
      throw UnsupportedOperation("a floating-point read of nowhere", span);
    }
    if (auto reg = std::get_if<MCReg>(&src)) {
      if (same_register(dst, src))
        return {};
      return { inst("movsd", { MCReg{ dst, 128 }, MCReg{ reg->reg, 128 } }, span) };
    }
    if (auto imm = std::get_if<MCImm>(&src)) {
      // A number written out, which is the nought standing for the answer of
      // an error that nothing reads.  Nothing puts one straight into a
      // floating-point register, so it goes into an ordinary one and its bits
      // are moved across.
      auto carried = new_gpr(64);
      Insts made = select_move(carried, MCImm{ imm->value, 64, false }, span);
      extend(made, select_move_bits(dst, MCReg{ carried, 64 }, 64, span));
      return made;
    }
    throw UnsupportedOperation("putting that kind of operand in a floating-point register", span);
  }


  Insts X86Selector::select_load(Reg dst, const MCMem& src, Span span)
  {
    int width = src.size_bits.value_or(32);
    std::string_view mnemonic = width <= 16 ? (src.is_signed ? "movsx" : "movzx") : "mov";
    return { inst(mnemonic, { MCReg{ dst }, src }, span) };
  }


  std::pair<Insts, std::vector<MCOperand>> X86Selector::accepting(std::string_view mnemonic,
                                                                  std::vector<MCOperand> operands, Span span)
  {
    try {
      table.select(mnemonic, operands);
      return { {}, std::move(operands) };
    } catch (const mc::SelectionError&) {
    }
    Insts before;
    std::vector<MCOperand> rewritten;
    for (const auto& operand : operands) {
      if (! std::holds_alternative<MCImm>(operand)) {
        rewritten.push_back(operand);
        continue;
      }
      // The register is a whole word even where the instruction names only a
      // part of it.
      int width = operand_width(operands);
      auto held = new_gpr(std::max(32, width));
      extend(before, select_move(held, at_least_a_word(operand), span));
      rewritten.push_back(MCReg{ held, width });
    }
    return { std::move(before), std::move(rewritten) };
  }


  Insts X86Selector::select_op(const mc::Op* op, Reg dst, std::span<const MCOperand> sources, Span span)
  {
    if (auto nullary = nullary_of(op); ! nullary.empty())
      return { inst(nullary, {}, span) };
    if (op == mc::ops::move) {
      if (dst.is_none()) [[unlikely]]
        throw UnsupportedOperation("a move needs a destination", span);
      return select_move(dst, sources[0], span);
    }
    if (auto mnemonic = binary_of(op); ! mnemonic.empty()) {
      if (dst.is_none() || sources.size() != 2) [[unlikely]]
        throw UnsupportedOperation(std::format("'{}' needs a destination and two sources", op->name), span);
      auto [before, ready] = accepting_pair(mnemonic, dst, sources, span);
      auto two = two_address(mnemonic, dst, ready[0], ready[1], span);
      return joined({ &before, &two });
    }
    if (auto mnemonic = unary_of(op); ! mnemonic.empty()) {
      if (dst.is_none() || sources.size() != 1) [[unlikely]]
        throw UnsupportedOperation(std::format("'{}' needs a destination and one source", op->name), span);
      auto moved = select_move(dst, sources[0], span);
      moved.push_back(inst(mnemonic, { MCReg{ dst } }, span));
      return moved;
    }
    throw UnsupportedOperation(std::format("no x86-64 selection rule for '{}'", op->name), span);
  }


  std::pair<Insts, std::vector<MCOperand>> X86Selector::accepting_pair(std::string_view mnemonic, Reg dst,
                                                                       std::span<const MCOperand> sources,
                                                                       Span span)
  {
    // The destination is also the first source, so the first source has to
    // be in a register; only the second may be an immediate.
    Insts before;
    MCOperand left = sources[0];
    if (std::holds_alternative<MCImm>(left)) {
      std::vector<MCOperand> all{ MCReg{ dst } };
      all.insert(all.end(), sources.begin(), sources.end());
      auto held = new_gpr(operand_width(all));
      extend(before, select_move(held, left, span));
      left = MCReg{ held };
    }
    auto [after, ready] = accepting(mnemonic, { MCReg{ dst }, sources[1] }, span);
    extend(before, after);
    return { std::move(before), { left, ready[1] } };
  }


  Insts X86Selector::two_address(std::string_view mnemonic, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                                 Span span)
  {
    if (same_register(dst, lhs))
      return { inst(mnemonic, { MCReg{ dst }, rhs }, span) };
    auto moved = select_move(dst, lhs, span);
    moved.push_back(inst(mnemonic, { MCReg{ dst }, rhs }, span));
    return moved;
  }


  Insts X86Selector::select_store(const MCMem& address, const MCOperand& value, Span span)
  {
    if (auto reg = std::get_if<MCReg>(&value); reg != nullptr && is_float(reg->reg)) {
      int width = address.size_bits.value_or(64);
      auto mnemonic = float_move_of(width);
      if (mnemonic.empty()) [[unlikely]]
        throw UnsupportedOperation(std::format("writing {} bits of floating point", width), span);
      return { inst(mnemonic, { address, MCReg{ reg->reg, 128 } }, span) };
    }
    // A constant too wide for the four sign-extended bytes a store carries
    // goes through a register, which a move of eight bytes can fill.
    if (auto imm = std::get_if<MCImm>(&value); imm != nullptr && ! fits_a_store(address, *imm)) {
      auto held = new_gpr(64);
      auto moved = select_move(held, value, span);
      moved.push_back(inst("mov", { address, MCReg{ held, address.size_bits } }, span));
      return moved;
    }
    return { inst("mov", { address, value }, span) };
  }


  Insts X86Selector::select_acquire(Reg dst, const MCMem& address, Span span)
  {
    // This architecture's reads already acquire and its writes already
    // release.
    return select_move(dst, address, span);
  }


  Insts X86Selector::select_release(const MCMem& address, const MCOperand& value, Span span)
  {
    return select_store(address, value, span);
  }


  bool X86Selector::fits_a_store(const MCMem& address, const MCImm& value) const
  {
    try {
      std::vector<MCOperand> operands{ address, value };
      table.select("mov", operands);
    } catch (const mc::SelectionError&) {
      return false;
    }
    return true;
  }


  Insts X86Selector::select_call(const MCOperand& target, Span span) { return { inst("call", { target }, span) }; }

  Insts X86Selector::select_return(Span span) { return { inst("ret", {}, span) }; }

  Insts X86Selector::select_jump(const MCOperand& target, Span span)
  {
    // A symbol or a register: the one mnemonic, and the row by the operand.
    return { inst("jmp", { target }, span) };
  }


  Insts X86Selector::select_branch(Condition cond, const MCOperand& lhs, const MCOperand& rhs,
                                   const MCSymRef& target, Span span)
  {
    MCOperand left = lhs, right = rhs;
    if (std::holds_alternative<MCImm>(left) && ! std::holds_alternative<MCImm>(right)) {
      // The comparison takes its immediate second.
      std::swap(left, right);
      cond = mc::swapped(cond);
    }
    auto made = select_compare(left, right, cond, span);
    made.push_back(inst(conditional(cond), { target }, span));
    return made;
  }


  Insts X86Selector::select_op_at(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                                  Span span)
  {
    auto mnemonic = binary_of(op);
    if (mnemonic.empty()) [[unlikely]]
      throw UnsupportedOperation(std::format("'{}' at a stated width", op->name), span);
    MCReg narrow{ dst, bits };
    // The first source goes into the destination at a width the move has a
    // form for; the arithmetic then names the narrow view of it.
    Insts moved;
    if (! same_register(dst, left))
      moved = select_move(dst, at_least_a_word(left), span);
    auto [before, ready] = accepting(mnemonic, { narrow, named_at(right, bits) }, span);
    extend(moved, before);
    moved.push_back(inst(mnemonic, ready, span));
    return moved;
  }


  Insts X86Selector::select_branch_if_in_range(const mc::Op*, bool is_signed, const MCSymRef& target, Span span)
  {
    // A signed operation goes past when the overflow flag says so, an
    // unsigned one when it carried or borrowed.
    return { inst(is_signed ? "jno" : "jae", { target }, span) };
  }


  Insts X86Selector::select_run_move(const std::variant<Reg, MCMem>& dst, const MCOperand& src, int bits, Span span)
  {
    int wide = held_bits(dst, src);
    std::string_view mnemonic = bits >= 128 ? "movdqu" : run_move_of(bits);
    if (mnemonic.empty()) [[unlikely]]
      throw UnsupportedOperation(std::format("moving {} bits of a run of elements", bits), span);
    MCOperand target = std::holds_alternative<MCMem>(dst) ? MCOperand(std::get<MCMem>(dst))
                                                          : MCOperand(MCReg{ std::get<Reg>(dst), wide });
    if (auto reg = std::get_if<MCReg>(&src); reg != nullptr && ! std::holds_alternative<MCMem>(dst))
      // Between two of these registers the whole of one goes: what is beyond
      // the run is clear in the source and has to stay clear.
      return { inst("movdqu", { target, MCReg{ reg->reg, wide } }, span) };
    return { inst(mnemonic, { target, src }, span) };
  }


  Insts X86Selector::select_run_op(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right, int bits,
                                   Span span)
  {
    auto mnemonic = run_operation_of(op, bits);
    if (mnemonic.empty()) [[unlikely]]
      throw UnsupportedOperation(std::format("'{}' over a whole run of {}-bit elements", op->name, bits), span);
    return run_two_or_three(mnemonic, dst, left, right, span);
  }


  Insts X86Selector::run_two_or_three(std::string_view mnemonic, Reg dst, const MCOperand& left,
                                      const MCOperand& right, Span span)
  {
    // The wider registers came with a form naming the destination apart from
    // both sources; the narrower ones have only the two-operand form.
    int wide = dst.bits();
    MCReg target{ dst, wide };
    if (wide > 128)
      return { inst(mnemonic, { target, named_at(left, wide), named_at(right, wide) }, span) };
    Insts moved;
    if (! same_register(dst, left))
      moved = select_run_move(dst, left, wide, span);
    moved.push_back(inst(mnemonic, { target, named_at(right, wide) }, span));
    return moved;
  }


  Insts X86Selector::select_run_splat(Reg dst, const MCOperand& src, int bits, Span span)
  {
    auto steps = spread_of(bits);
    if (steps.empty()) [[unlikely]]
      throw UnsupportedOperation(std::format("one value of {} bits in every lane", bits), span);
    Insts before;
    MCOperand from = src;
    if (std::holds_alternative<MCImm>(src)) {
      // There is no way to put a number straight into one of these.
      auto held = new_gpr(std::max(32, bits));
      extend(before, select_move(held, at_least_a_word(src), span));
      from = MCReg{ held, std::max(32, bits) };
    } else if (auto reg = std::get_if<MCReg>(&src))
      from = MCReg{ reg->reg, std::max(32, bits) };
    MCReg narrow{ dst, 128 };
    before.push_back(inst(bits == 64 ? "movq" : "movd", { narrow, from }, span));
    if (dst.bits() > 128) {
      // The wider registers came with one instruction doing the whole of it.
      before.push_back(inst(broadcast_of(bits), { MCReg{ dst, dst.bits() }, narrow }, span));
      return before;
    }
    for (auto step : steps) {
      std::vector<MCOperand> operands{ narrow, narrow };
      if (step == "pshufd")
        operands.push_back(MCImm{ 0, 8, false });
      before.push_back(inst(step, std::move(operands), span));
    }
    return before;
  }


  Insts X86Selector::select_run_saturating(Reg dst, const MCOperand& left, const MCOperand& right, bool adding,
                                           bool is_signed, int bits, Span span)
  {
    auto mnemonic = run_saturating_of(adding, is_signed, bits);
    if (mnemonic.empty()) [[unlikely]]
      throw UnsupportedOperation(std::format("saturating arithmetic over a run of {}-bit elements", bits), span);
    return run_two_or_three(mnemonic, dst, left, right, span);
  }


  Insts X86Selector::select_run_any_lane(Reg dst, const MCOperand& src, int stride, int lanes, Span span)
  {
    auto reg = std::get_if<MCReg>(&src);
    if (reg == nullptr) [[unlikely]]
      throw UnsupportedOperation("gathering the top bits of something not in a register", span);
    int wide = reg->width();
    MCReg target{ dst, 32 };
    auto wanted = top_bytes(std::uint64_t(stride), std::uint64_t(lanes), wide);
    return { inst("pmovmskb", { target, MCReg{ reg->reg, wide } }, span),
             inst("and", { target, MCImm{ __int128(wanted), 32, false } }, span) };
  }


  Insts X86Selector::select_run_ones(Reg dst, Span span)
  {
    // Every lane compared with itself is equal, and equal is every bit set.
    return run_two_or_three("pcmpeqd", dst, MCReg{ dst }, MCReg{ dst }, span);
  }


  Insts X86Selector::select_run_compare(Condition cond, Reg dst, const MCOperand& left, const MCOperand& right,
                                        int bits, Span span)
  {
    // Equality and the signed "greater" are the comparisons there are; the
    // rest are those the other way round or turned round, and the unsigned
    // orders are asked through the larger or the smaller of the two -- `a` is
    // at least `b` exactly where the larger of them is `a`.
    if (bits != 8) [[unlikely]]
      throw UnsupportedOperation(std::format("comparing a run of {}-bit elements", bits), span);
    Insts made;
    bool turned = false;
    if (cond == Condition::eq || cond == Condition::ne) {
      extend(made, run_two_or_three("pcmpeqb", dst, left, right, span));
      turned = cond == Condition::ne;
    } else if (cond == Condition::sgt || cond == Condition::slt || cond == Condition::sle || cond == Condition::sge) {
      bool exchanged = cond == Condition::slt || cond == Condition::sge;
      turned = cond == Condition::sle || cond == Condition::sge;
      extend(made, run_two_or_three("pcmpgtb", dst, exchanged ? right : left, exchanged ? left : right, span));
    } else {
      bool larger = cond == Condition::uge || cond == Condition::ult;
      turned = cond == Condition::ult || cond == Condition::ugt;
      auto either = registers().info.new_virtual(registers().vec, dst.bits());
      extend(made, run_two_or_three(larger ? "pmaxub" : "pminub", either, left, right, span));
      extend(made, run_two_or_three("pcmpeqb", dst, MCReg{ either }, left, span));
    }
    if (turned) {
      auto ones = registers().info.new_virtual(registers().vec, dst.bits());
      extend(made, select_run_ones(ones, span));
      extend(made, run_two_or_three("pxor", dst, MCReg{ dst }, MCReg{ ones }, span));
    }
    return made;
  }


  Insts X86Selector::select_run_mask(Reg dst, const MCOperand& src, int lanes, Span span)
  {
    // The top bit of every byte is the whole of it: a truth value in a
    // register is every bit of its lane set or clear.  The bits of bytes
    // beyond a run shorter than the register are cleared.
    auto reg = std::get_if<MCReg>(&src);
    if (reg == nullptr) [[unlikely]]
      throw UnsupportedOperation("gathering the lanes of something not in a register", span);
    int wide = reg->width();
    MCReg target{ dst, 32 };
    Insts made{ inst("pmovmskb", { target, MCReg{ reg->reg, wide } }, span) };
    if (lanes < wide / 8)
      made.push_back(inst("and", { target, MCImm{ (__int128(1) << lanes) - 1, 32, false } }, span));
    return made;
  }


  Insts X86Selector::select_run_truths(Reg dst, const MCOperand& src, Span span)
  {
    // Every byte taken from nought: one becomes every bit set and back.  The
    // register is cleared first by an exclusive or with itself.
    auto made = run_two_or_three("pxor", dst, MCReg{ dst }, MCReg{ dst }, span);
    extend(made, run_two_or_three("psubb", dst, MCReg{ dst }, src, span));
    return made;
  }


  Insts X86Selector::select_set(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, Span span)
  {
    // Three instructions: the set writes a byte, which is widened after;
    // clearing the register first would write the flags just set.
    MCOperand left = lhs, right = rhs;
    if (std::holds_alternative<MCImm>(left) && ! std::holds_alternative<MCImm>(right)) {
      std::swap(left, right);
      cond = mc::swapped(cond);
    }
    MCReg low{ dst, 8 };
    auto made = select_compare(left, right, cond, span);
    made.push_back(inst(set_of(cond), { low }, span));
    made.push_back(inst("movzx", { MCReg{ dst, 32 }, low }, span));
    return made;
  }


  Insts X86Selector::select_address(Reg dst, const MCSymRef& symbol, Span span)
  {
    // Computed from the program counter, which keeps the image free of
    // anything that has to be patched when it is loaded.
    MCMem place;
    place.disp_sym = symbol.expr;
    place.rip_relative = true;
    return { inst("lea", { MCReg{ dst, 64 }, place }, span) };
  }


  Insts X86Selector::select_widen(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    if (bits >= 64)
      return select_move(dst, src, span);
    if (std::holds_alternative<MCImm>(src))
      return select_move(dst, src, span);
    MCOperand narrow;
    if (auto reg = std::get_if<MCReg>(&src))
      narrow = MCReg{ reg->reg, bits };
    else {
      MCMem place = std::get<MCMem>(src);
      place.size_bits = bits;
      narrow = place;
    }
    if (! is_signed) {
      // Writing a four-byte register clears the four above it.
      if (bits == 32)
        return { inst("mov", { MCReg{ dst, 32 }, narrow }, span) };
      return { inst("movzx", { MCReg{ dst, 32 }, narrow }, span) };
    }
    return { inst(bits == 32 ? "movsxd" : "movsx", { MCReg{ dst, 64 }, narrow }, span) };
  }


  Insts X86Selector::select_clamp(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                                  const MCOperand& bound, Span span)
  {
    MCOperand left = lhs, right = rhs;
    if (std::holds_alternative<MCImm>(left) && ! std::holds_alternative<MCImm>(right)) {
      std::swap(left, right);
      cond = mc::swapped(cond);
    }
    Insts before;
    MCReg limit;
    if (auto reg = std::get_if<MCReg>(&bound))
      limit = *reg;
    else {
      auto held = new_gpr(64);
      extend(before, select_move(held, bound, span));
      limit = MCReg{ held };
    }
    extend(before, select_compare(left, right, cond, span));
    before.push_back(inst(cmov_of(cond), { MCReg{ dst, 64 }, MCReg{ limit.reg, 64 } }, span));
    return before;
  }


  Insts X86Selector::select_select(Reg dst, const MCOperand& chooser, const MCOperand& if_true,
                                   const MCOperand& if_false, bool floating, int bits, Span span)
  {
    // The second goes in first and the conditional move puts the first over
    // it; a choice of two floating-point values is made of their bits in
    // ordinary registers.
    Insts before;
    auto in_a_register = [&](const MCOperand& operand) -> MCReg {
      if (auto reg = std::get_if<MCReg>(&operand))
        return *reg;
      auto held = new_gpr(64);
      extend(before, select_move(held, operand, span));
      return MCReg{ held };
    };
    auto asked = in_a_register(chooser);
    auto test = select_compare(asked, MCImm{ 0, asked.reg.bits() }, Condition::ne, span);
    if (! floating) {
      auto first = in_a_register(if_true);
      auto moved = select_move(dst, if_false, span);
      extend(before, moved);
      extend(before, test);
      before.push_back(inst(cmov_of(Condition::ne), { MCReg{ dst, 64 }, MCReg{ first.reg, 64 } }, span));
      return before;
    }
    std::string_view mnemonic = bits == 64 ? "movq" : "movd";
    int width = bits == 64 ? 64 : 32;
    auto first = new_gpr(width);
    auto second = new_gpr(width);
    before.push_back(inst(mnemonic, { MCReg{ first, width }, if_true }, span));
    before.push_back(inst(mnemonic, { MCReg{ second, width }, if_false }, span));
    extend(before, test);
    before.push_back(inst(cmov_of(Condition::ne), { MCReg{ second, 64 }, MCReg{ first, 64 } }, span));
    before.push_back(inst(mnemonic, { MCReg{ dst }, MCReg{ second, width } }, span));
    return before;
  }


  Insts X86Selector::select_compare(MCOperand lhs, MCOperand rhs, Condition cond, Span span)
  {
    // Testing a register against itself sets the flags a comparison with
    // nought does and is a byte shorter.  A constant too wide for the four
    // bytes a comparison carries goes into a register first.
    auto imm = std::get_if<MCImm>(&rhs);
    if (imm != nullptr && imm->value == 0 && std::holds_alternative<MCReg>(lhs)
        && (cond == Condition::eq || cond == Condition::ne))
      return { inst("test", { lhs, lhs }, span) };
    if (imm != nullptr) {
      try {
        std::vector<MCOperand> operands{ lhs, rhs };
        table.select("cmp", operands);
      } catch (const mc::SelectionError&) {
        auto reg = std::get_if<MCReg>(&lhs);
        int width = reg != nullptr ? reg->reg.bits() : 64;
        auto held = new_gpr(width);
        auto moved = select_move(held, rhs, span);
        moved.push_back(inst("cmp", { lhs, MCReg{ held } }, span));
        return moved;
      }
    }
    return { inst("cmp", { lhs, rhs }, span) };
  }


  Insts X86Selector::select_float_abs(Reg, const MCOperand&, int, Span span)
  {
    // There is no instruction that clears one bit of a vector register; the
    // magnitude is an `and` with a mask in the image, reached elsewhere.
    throw UnsupportedOperation("a magnitude, which here is an and with a mask in the image", span);
  }


  Insts X86Selector::select_float_extend(Reg dst, const MCOperand& src, int from_bits, int to_bits, Span span)
  {
    if (from_bits != 32 || to_bits != 64) [[unlikely]]
      throw UnsupportedOperation(
        std::format("widening {} bits of floating point to {}", from_bits, to_bits), span);
    return { inst("cvtss2sd", { MCReg{ dst, 128 }, src }, span) };
  }


  Insts X86Selector::select_branch_if_finite(Reg value, int bits, const MCSymRef& target, Span span)
  {
    // The difference compared against itself is unordered only where it is
    // not a number, which is what the parity flag then says.
    auto held = registers().info.new_virtual(registers().vec, float_register_bits);
    auto made = select_float_move(held, MCReg{ value }, span);
    made.push_back(inst(float_binary_of(mc::ops::minus, bits), { MCReg{ held, 128 }, MCReg{ value, 128 } }, span));
    made.push_back(inst(float_compare_of(bits), { MCReg{ held, 128 }, MCReg{ held, 128 } }, span));
    made.push_back(inst("jnp", { target }, span));
    return made;
  }


  Insts X86Selector::select_float_round(mc::Rounding how, Reg dst, const MCOperand& src, int bits, Span span)
  {
    if (! rounds) [[unlikely]]
      // SSE4.1, which the second level promises and the first does not.
      throw UnsupportedOperation("rounding a floating-point number at the oldest x86-64 level", span);
    // Bits zero and one name a direction and bit two says to ask MXCSR.
    int immediate = 0;
    switch (how) {
    case mc::Rounding::nearest: immediate = 0x00; break;
    case mc::Rounding::down: immediate = 0x01; break;
    case mc::Rounding::up: immediate = 0x02; break;
    case mc::Rounding::current: immediate = 0x04; break;
    }
    return { inst(bits == 32 ? "roundss" : "roundsd", { MCReg{ dst, 128 }, src, MCImm{ immediate, 8, false } },
                  span) };
  }


  Insts X86Selector::select_float_op(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right,
                                     int bits, Span span)
  {
    auto mnemonic = float_binary_of(op, bits);
    if (mnemonic.empty()) [[unlikely]]
      throw UnsupportedOperation(std::format("'{}' on a floating-point value", op->name), span);
    auto made = select_move(dst, left, span);
    made.push_back(inst(mnemonic, { MCReg{ dst, 128 }, right }, span));
    return made;
  }


  Insts X86Selector::select_float_compare(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                                          int bits, Span span)
  {
    // The comparison sets the flags of an unsigned one, and the parity flag
    // where the two are not ordered; equality asks about parity as well.
    MCReg low{ dst, 8 };
    MCReg whole{ dst, 32 };
    if (cond == Condition::eq || cond == Condition::ne) {
      auto other = new_gpr(32);
      std::string_view unordered = cond == Condition::ne ? "setp" : "setnp";
      auto joining = cond == Condition::ne ? mc::ops::or_ : mc::ops::and_;
      Insts made{ inst(float_compare_of(bits), { lhs, rhs }, span),
                  inst(cond == Condition::eq ? "sete" : "setne", { low }, span),
                  inst("movzx", { whole, low }, span),
                  inst(unordered, { MCReg{ other, 8 } }, span),
                  inst("movzx", { MCReg{ other, 32 }, MCReg{ other, 8 } }, span) };
      std::vector<MCOperand> sources{ whole, MCReg{ other } };
      extend(made, select_op(joining, dst, sources, span));
      return made;
    }
    bool exchanged = cond == Condition::slt || cond == Condition::ult || cond == Condition::sle
                     || cond == Condition::ule;
    const MCOperand& first = exchanged ? rhs : lhs;
    const MCOperand& second = exchanged ? lhs : rhs;
    bool above = cond == Condition::slt || cond == Condition::ult || cond == Condition::sgt
                 || cond == Condition::ugt;
    return { inst(float_compare_of(bits), { first, second }, span), inst(above ? "seta" : "setae", { low }, span),
             inst("movzx", { whole, low }, span) };
  }


  Insts X86Selector::select_shift(const mc::Op* op, Reg dst, const MCOperand& value, const MCOperand& amount,
                                  int bits, Span span)
  {
    Insts held;
    if (counting_leading) {
      // The third level's form, taking the count in any register.
      auto [counted, before] = in_register(amount, 64, span);
      extend(held, before);
      auto [wide, more] = in_register(value, 64, span);
      extend(held, more);
      held.push_back(inst(wide_shift_of(op), { MCReg{ dst, 64 }, wide, counted }, span));
      return held;
    }
    // The count goes in the low byte of one fixed register, which the shift
    // says it uses.
    auto& r = registers();
    extend(held, select_move(r.info.view(r.rcx->unit, bits), amount, span));
    held.push_back(inst("mov", { MCReg{ dst, bits }, value }, span));
    held.push_back(inst(shift_of(op), { MCReg{ dst, bits }, MCReg{ r.info.view(r.rcx->unit, 8), 8 } }, span));
    return held;
  }


  Insts X86Selector::select_count_ones(Reg dst, const MCOperand& src, Span span)
  {
    return { inst("popcnt", { MCReg{ dst, 64 }, src }, span) };
  }


  Insts X86Selector::select_count_leading(Reg dst, const MCOperand& src, Span span)
  {
    return { inst("lzcnt", { MCReg{ dst, 64 }, src }, span) };
  }


  Insts X86Selector::select_count_trailing(Reg dst, const MCOperand& src, Span span)
  {
    // BMI1 comes at the level that adds the count from the top.  Below it,
    // `bsf` leaves its destination undefined for nought and says so in the
    // zero flag, and a conditional move puts the width there.
    if (counting_leading)
      return { inst("tzcnt", { MCReg{ dst, 64 }, src }, span) };
    auto width = new_gpr(64);
    auto made = select_move(width, MCImm{ 64, 32, false }, span);
    made.push_back(inst("bsf", { MCReg{ dst, 64 }, src }, span));
    made.push_back(inst("cmove", { MCReg{ dst, 64 }, MCReg{ width } }, span));
    return made;
  }


  Insts X86Selector::select_int_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    return { inst(bits == 64 ? "cvtsi2sd" : "cvtsi2ss", { MCReg{ dst, 128 }, src }, span) };
  }


  Insts X86Selector::select_truncate_to_int(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    if (! is_signed) [[unlikely]]
      throw UnsupportedOperation("an unsigned conversion of a floating-point number", span);
    MCOperand operand = src;
    if (auto reg = std::get_if<MCReg>(&src))
      operand = MCReg{ reg->reg, 128 };
    return { inst(bits == 64 ? "cvttsd2si" : "cvttss2si", { MCReg{ dst, 64 }, operand }, span) };
  }


  Insts X86Selector::select_move_bits(Reg dst, const MCOperand& src, int bits, Span span)
  {
    // `movq` for the wider format and `movd` for the narrower, either way.
    auto reg = std::get_if<MCReg>(&src);
    if (reg == nullptr) [[unlikely]]
      throw UnsupportedOperation("moving the bits of what is not in a register", span);
    std::string_view mnemonic = bits == 64 ? "movq" : "movd";
    if (is_float(dst))
      return { inst(mnemonic, { MCReg{ dst, 128 }, MCReg{ reg->reg, bits } }, span) };
    return { inst(mnemonic, { MCReg{ dst, bits }, MCReg{ reg->reg, 128 } }, span) };
  }


  Insts X86Selector::select_divide(Reg dst, const MCOperand& left, const MCOperand& right, bool is_signed,
                                   bool remainder, int bits, Span span)
  {
    // The dividend goes in one fixed register and is stretched across a
    // second; the quotient comes back in the first, the remainder in the
    // second.
    auto& r = registers();
    Insts held;
    auto accumulator = r.info.view(r.rax->unit, bits);
    auto [dividend, before] = in_register(left, bits, span);
    extend(held, before);
    extend(held, select_move(accumulator, dividend, span));
    if (is_signed)
      held.push_back(inst(bits == 64 ? "cqo" : "cdq", {}, span));
    else
      // The upper half is nought, cleared with a move to leave the flags.
      extend(held, select_move(r.info.view(r.rdx->unit, bits), MCImm{ 0, 32, false }, span));
    auto [divisor, more] = in_register(right, bits, span);
    extend(held, more);
    held.push_back(inst(is_signed ? "idiv" : "div", { divisor }, span));
    auto answer = r.info.view((remainder ? r.rdx : r.rax)->unit, bits);
    extend(held, select_move(dst, MCReg{ answer }, span));
    return held;
  }


  Insts X86Selector::select_wide_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right,
                                         bool is_signed, Span span)
  {
    // The one-operand multiply is the only instruction answering with the
    // upper half: one factor in a fixed register, both halves in a fixed pair.
    auto& r = registers();
    Insts held;
    auto accumulator = r.info.view(r.rax->unit, 64);
    auto [first, before] = in_register(left, 64, span);
    extend(held, before);
    extend(held, select_move(accumulator, first, span));
    auto [second, more] = in_register(right, 64, span);
    extend(held, more);
    held.push_back(inst(is_signed ? "imul" : "mul", { second }, span));
    // The upper half first: the low one is where the factor came from.
    extend(held, select_move(high, MCReg{ r.info.view(r.rdx->unit, 64) }, span));
    extend(held, select_move(low, MCReg{ accumulator }, span));
    return held;
  }


  Insts X86Selector::select_carryless_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right,
                                              Span span)
  {
    auto& r = registers();
    Insts held;
    std::vector<Reg> factors;
    for (const auto* factor : { &left, &right }) {
      auto [word, before] = in_register(*factor, 64, span);
      extend(held, before);
      auto into = r.info.new_virtual(r.vec, float_register_bits);
      held.push_back(inst("movq", { MCReg{ into, 128 }, word }, span));
      factors.push_back(into);
    }
    auto product = factors[0];
    auto other = factors[1];
    held.push_back(inst("pclmulqdq", { MCReg{ product, 128 }, MCReg{ other, 128 }, MCImm{ 0, 8, false } }, span));
    held.push_back(inst("movq", { MCReg{ low, 64 }, MCReg{ product, 128 } }, span));
    // The upper quadword copied into both halves, so that the move that reads
    // the lower one reads it.
    auto upper = r.info.new_virtual(r.vec, float_register_bits);
    held.push_back(inst("pshufd", { MCReg{ upper, 128 }, MCReg{ product, 128 }, MCImm{ 0xEE, 8, false } }, span));
    held.push_back(inst("movq", { MCReg{ high, 64 }, MCReg{ upper, 128 } }, span));
    return held;
  }


  std::pair<MCReg, Insts> X86Selector::in_register(const MCOperand& operand, int bits, Span span)
  {
    if (auto reg = std::get_if<MCReg>(&operand))
      return { MCReg{ reg->reg, bits }, {} };
    auto held = new_gpr(bits);
    return { MCReg{ held, bits }, select_move(held, operand, span) };
  }


  std::int64_t X86Selector::frame_size(std::int64_t total, bool calls)
  {
    // The stack is aligned to sixteen bytes where a call is made, so it is
    // eight past that where the called function begins, the return address
    // having been pushed: a frame of eight past a multiple of sixteen brings it
    // back.  A function that calls nothing has no call to align for.
    if (! calls)
      return total;
    return total + ((8 - total) % 16 + 16) % 16;
  }


  std::pair<std::optional<std::int64_t>, std::int64_t> X86Selector::frame_walk(std::int64_t total, std::int64_t,
                                                                               std::int64_t)
  {
    // The call pushed the return address, above the whole frame.
    return { total, total + 8 };
  }


  Insts X86Selector::select_save_link(std::int64_t, Span) { return {}; }

  Insts X86Selector::select_restore_link(std::int64_t, Span) { return {}; }


  Insts X86Selector::select_incoming(Reg dst, std::int64_t offset, int bits, bool is_signed, Span span)
  {
    // A load from the stack pointer and the offset, which any offset fits.
    MCMem place;
    place.base = registers().rsp;
    place.disp = offset;
    place.size_bits = bits;
    place.is_signed = is_signed;
    return select_move(dst, place, span);
  }


  Insts X86Selector::select_spill(std::int64_t slot, Reg source, Span span)
  {
    // The whole of a vector register goes: the same register holds a run.
    if (is_float(source)) {
      int wide = std::max(128, source.bits());
      return { inst("movdqu", { slot_at(slot, wide), MCReg{ source, wide } }, span) };
    }
    return { inst("mov", { slot_at(slot), MCReg{ source, 64 } }, span) };
  }


  Insts X86Selector::select_reload(Reg destination, std::int64_t slot, Span span)
  {
    if (is_float(destination)) {
      int wide = std::max(128, destination.bits());
      return { inst("movdqu", { MCReg{ destination, wide }, slot_at(slot, wide) }, span) };
    }
    return { inst("mov", { MCReg{ destination, 64 }, slot_at(slot) }, span) };
  }


  Insts X86Selector::select_frame(std::int64_t size, Span span)
  {
    return { inst("sub", { MCReg{ registers().rsp }, MCImm{ size, 32 } }, span) };
  }


  Insts X86Selector::select_unframe(std::int64_t size, Span span)
  {
    return { inst("add", { MCReg{ registers().rsp }, MCImm{ size, 32 } }, span) };
  }

} // namespace pl4g::target::x86_64
