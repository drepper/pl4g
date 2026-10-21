// Instruction selection for RISC-V.
#include "target/riscv64/isel.hh"

#include <algorithm>
#include <format>
#include <unordered_map>

#include "ir/builder.hh"
#include "ir/layout.hh"
#include "mc/desc.hh"
#include "target/bitcount.hh"
#include "target/branches.hh"
#include "target/globals.hh"
#include "target/narrow.hh"
#include "target/ordering.hh"
#include "target/riscv64/abi.hh"
#include "target/riscv64/opcodes.hh"
#include "target/riscv64/regs.hh"
#include "target/riscv64/startup.hh"
#include "target/saturate.hh"
#include "target/scratch.hh"
#include "target/statuses.hh"
#include "util/reflect.hh"


namespace pl4g::target::riscv64 {

  namespace {

    using mc::Condition;
    using mc::Insts;
    using mc::MCImm;
    using mc::MCMem;
    using mc::MCOperand;
    using mc::MCReg;
    using mc::MCSymRef;
    using mc::Reg;
    using mc::Rounding;

    // What a branch compares against where its condition is a value.
    const MCImm zero_immediate{ 0, 12, true };

    // The two fences the orderings come to: `fence r, rw` after an acquiring
    // read, `fence rw, w` before a releasing write.
    constexpr __int128 fence_r_rw = 0x023;
    constexpr __int128 fence_rw_w = 0x031;

    // How wide a floating-point register is declared to be.
    constexpr int float_register_bits = 64;

    // What memory looks like here.
    const ir::DataLayout& plain_layout()
    {
      static const ir::DataLayout made;
      return made;
    }

    bool is_float(Reg reg) { return reg.cls() == registers().fpr; }

    MCImm imm(__int128 value, int bits, bool is_signed = true) { return MCImm{ value, bits, is_signed }; }

    MCReg mcreg(Reg reg) { return MCReg{ reg }; }

    // The branch for each condition the architecture has; the other four are
    // these with the operands the other way round.
    std::optional<std::string_view> conditional(Condition cond)
    {
      switch (cond) {
      case Condition::eq: return "beq";
      case Condition::ne: return "bne";
      case Condition::slt: return "blt";
      case Condition::sge: return "bge";
      case Condition::ult: return "bltu";
      case Condition::uge: return "bgeu";
      default: return std::nullopt;
      }
    }

    // What the architecture's three-bit rounding field says.
    int rounds(Rounding how)
    {
      switch (how) {
      case Rounding::nearest: return 0b000;
      case Rounding::down: return 0b010;
      case Rounding::up: return 0b011;
      case Rounding::current: return 0b111;
      }
      return 0b111;
    }

    // The low twelve bits of `value`, read as signed.
    __int128 signed_twelve(__int128 value)
    {
      __int128 low = value & 0xFFF;
      return low >= 0x800 ? low - 0x1000 : low;
    }

    // How many low bits of `value` are nought.
    int trailing_zeroes(__int128 value)
    {
      int count = 0;
      while ((value & 1) == 0) {
        value >>= 1;
        ++count;
      }
      return count;
    }

  } // anonymous namespace


  // -- the selector -----------------------------------------------------------

  mc::InstRef RVSelector::inst(std::string_view mnemonic, std::vector<MCOperand> operands, Span span) const
  {
    const mc::InstDesc* desc = nullptr;
    try {
      desc = table().select(mnemonic, operands);
    } catch (const mc::SelectionError& error) {
      throw UnsupportedOperation(error.what(), span.is_valid() ? std::optional(span) : std::nullopt);
    }
    return mc::make_inst(desc, std::move(operands), span);
  }


  Insts RVSelector::select_move(Reg dst, const MCOperand& src, Span span)
  {
    if (is_float(dst))
      return float_move(dst, src, span);
    if (auto reg = std::get_if<MCReg>(&src); reg != nullptr && is_float(reg->reg)) [[unlikely]]
      throw UnsupportedOperation("moving a floating-point value into an ordinary register", span);
    if (auto mem = std::get_if<MCMem>(&src))
      return load(dst, *mem, span);
    if (auto reg = std::get_if<MCReg>(&src)) {
      if (reg->reg == dst)
        return {};
      return { inst("mv", { mcreg(dst), *reg }, span) };
    }
    if (auto value = std::get_if<MCImm>(&src))
      return materialize(dst, value->value, span);
    throw UnsupportedOperation("moving from this kind of operand", span);
  }


  Insts RVSelector::float_move(Reg dst, const MCOperand& src, Span span)
  {
    if (auto mem = std::get_if<MCMem>(&src)) {
      int width = mem->size_bits.value_or(64);
      std::string_view mnemonic;
      if (width == 32)
        mnemonic = "flw";
      else if (width == 64)
        mnemonic = "fld";
      else [[unlikely]]
        throw UnsupportedOperation(std::format("reading {} bits of floating point", width), span);
      auto offset = imm(mem->disp, 12);
      if (mem->disp_sym == nullptr) {
        if (mem->base.is_none()) [[unlikely]]
          throw UnsupportedOperation("a floating-point read of nowhere", span);
        return { inst(mnemonic, { mcreg(dst), mcreg(mem->base), offset }, span) };
      }
      MCSymRef symbol{ mem->disp_sym };
      MCReg held = mcreg(registers().info.new_virtual(registers().gpr, 64, nullptr, false));
      return { inst("auipc.hi20", { held, symbol }, span), inst("addi.lo12", { held, held, symbol }, span),
               inst(mnemonic, { mcreg(dst), held, offset }, span) };
    }
    if (auto reg = std::get_if<MCReg>(&src)) {
      if (reg->reg == dst)
        return {};
      return { inst("fsgnj.d", { mcreg(dst), *reg, *reg }, span) };
    }
    throw UnsupportedOperation("putting that kind of operand in a floating-point register", span);
  }


  Insts RVSelector::materialize(Reg dst, __int128 value, Span span)
  {
    // Twelve bits is one instruction; thirty-two an upper-immediate load and a
    // signed add; anything wider the upper part built the same way, shifted
    // into place as far as its own trailing zeroes allow, and the last twelve
    // bits added -- the sequence LLVM generates.
    MCReg target = mcreg(dst);
    Insts built;
    if (value >= (__int128(1) << 63))
      value -= __int128(1) << 64;
    auto build = [&](auto& self, __int128 remaining) -> void {
      if (imm12_min <= remaining && remaining <= imm12_max) {
        built.push_back(inst("li", { target, imm(remaining, 12) }, span));
        return;
      }
      __int128 low = signed_twelve(remaining);
      __int128 upper = (remaining - low) >> 12;
      if (-(__int128(1) << 31) <= remaining && remaining < (__int128(1) << 31)) {
        built.push_back(inst("lui", { target, imm(upper & 0xFFFFF, 20, false) }, span));
        if (low != 0)
          built.push_back(inst("addiw", { target, target, imm(low, 12) }, span));
        return;
      }
      int spare = trailing_zeroes(upper);
      self(self, upper >> spare);
      built.push_back(inst("slli", { target, target, imm(12 + spare, 6, false) }, span));
      if (low != 0)
        built.push_back(inst("addi", { target, target, imm(low, 12) }, span));
    };
    build(build, value);
    return built;
  }


  Insts RVSelector::load(Reg dst, const MCMem& src, Span span)
  {
    // The register holding the address between the two halves of its
    // building is one of its own the allocator will never spill.
    int width = src.size_bits.value_or(64);
    std::string_view mnemonic;
    switch (width) {
    case 8: mnemonic = src.is_signed ? "lb" : "lbu"; break;
    case 16: mnemonic = src.is_signed ? "lh" : "lhu"; break;
    case 32: mnemonic = src.is_signed ? "lw" : "lwu"; break;
    case 64: mnemonic = "ld"; break;
    default: throw UnsupportedOperation(std::format("reading {} bits from memory", width), span);
    }
    if (src.disp_sym == nullptr) {
      Reg base = ! src.base.is_none() ? src.base : dst;
      return { inst(mnemonic, { mcreg(dst), mcreg(base), imm(src.disp, 12) }, span) };
    }
    MCSymRef symbol{ src.disp_sym };
    MCReg held = mcreg(registers().info.new_virtual(registers().gpr, 64, nullptr, false));
    return { inst("auipc.hi20", { held, symbol }, span), inst("addi.lo12", { held, held, symbol }, span),
             inst(mnemonic, { mcreg(dst), held, imm(src.disp, 12) }, span) };
  }


  std::pair<Insts, std::vector<MCOperand>> RVSelector::accepting(std::string_view mnemonic,
                                                                 std::vector<MCOperand> operands, Span span)
  {
    try {
      table().select(mnemonic, operands);
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
      auto carried = registers().info.new_virtual(registers().gpr, 64);
      auto moved = select_move(carried, operand, span);
      before.insert(before.end(), moved.begin(), moved.end());
      rewritten.push_back(mcreg(carried));
    }
    return { std::move(before), std::move(rewritten) };
  }


  Insts RVSelector::select_op(const mc::Op* op, Reg dst, std::span<const MCOperand> sources, Span span)
  {
    if (op == environment_call())
      return { inst("ecall", {}, span) };
    if (op == mc::ops::trap)
      return { inst("unimp", {}, span) };
    if (op == mc::ops::move) {
      if (dst.is_none()) [[unlikely]]
        throw UnsupportedOperation("a move needs a destination", span);
      return select_move(dst, sources[0], span);
    }
    std::string_view mnemonic;
    if (op == mc::ops::plus)
      mnemonic = "add";
    else if (op == mc::ops::minus)
      mnemonic = "sub";
    else if (op == mc::ops::times)
      mnemonic = "mul";
    else if (op == mc::ops::xor_)
      mnemonic = "xor";
    else if (op == mc::ops::and_)
      mnemonic = "and";
    else if (op == mc::ops::or_)
      mnemonic = "or";
    if (! mnemonic.empty()) {
      if (dst.is_none() || sources.size() != 2) [[unlikely]]
        throw UnsupportedOperation(std::format("'{}' needs a destination and two sources", op->name), span);
      auto [before, ready] = accepting(mnemonic, { mcreg(dst), sources[0], sources[1] }, span);
      before.push_back(inst(mnemonic, std::move(ready), span));
      return before;
    }
    if (op == mc::ops::not_) {
      if (dst.is_none() || sources.size() != 1) [[unlikely]]
        throw UnsupportedOperation(std::format("'{}' needs a destination and one source", op->name), span);
      auto [before, ready] = accepting("not", { mcreg(dst), sources[0] }, span);
      before.push_back(inst("not", std::move(ready), span));
      return before;
    }
    throw UnsupportedOperation(std::format("no RISC-V selection rule for '{}'", op->name), span);
  }


  Insts RVSelector::select_acquire(Reg dst, const MCMem& address, Span span)
  {
    auto out = select_move(dst, address, span);
    out.push_back(inst("fence", { imm(fence_r_rw, 12) }, span));
    return out;
  }


  Insts RVSelector::select_release(const MCMem& address, const MCOperand& value, Span span)
  {
    Insts out{ inst("fence", { imm(fence_rw_w, 12) }, span) };
    auto stored = select_store(address, value, span);
    out.insert(out.end(), stored.begin(), stored.end());
    return out;
  }


  Insts RVSelector::select_store(const MCMem& address, const MCOperand& value, Span span)
  {
    int width = address.size_bits.value_or(64);
    if (auto reg = std::get_if<MCReg>(&value); reg != nullptr && is_float(reg->reg))
      return float_store(address, *reg, width, span);
    std::string_view mnemonic;
    switch (width) {
    case 8: mnemonic = "sb"; break;
    case 16: mnemonic = "sh"; break;
    case 32: mnemonic = "sw"; break;
    case 64: mnemonic = "sd"; break;
    default: throw UnsupportedOperation(std::format("writing {} bits to memory", width), span);
    }
    Insts held;
    MCReg source;
    if (auto reg = std::get_if<MCReg>(&value))
      source = *reg;
    else {
      auto carried = registers().info.new_virtual(registers().gpr, 64);
      held = select_move(carried, value, span);
      source = mcreg(carried);
    }
    MCReg place = mcreg(registers().info.new_virtual(registers().gpr, 64, nullptr, false));
    if (address.disp_sym == nullptr) {
      MCReg base = ! address.base.is_none() ? mcreg(address.base) : place;
      held.push_back(inst(mnemonic, { source, base, imm(address.disp, 12) }, span));
      return held;
    }
    MCSymRef symbol{ address.disp_sym };
    held.push_back(inst("auipc.hi20", { place, symbol }, span));
    held.push_back(inst("addi.lo12", { place, place, symbol }, span));
    held.push_back(inst(mnemonic, { source, place, imm(address.disp, 12) }, span));
    return held;
  }


  Insts RVSelector::float_store(const MCMem& address, const MCReg& value, int width, Span span)
  {
    std::string_view mnemonic;
    if (width == 32)
      mnemonic = "fsw";
    else if (width == 64)
      mnemonic = "fsd";
    else [[unlikely]]
      throw UnsupportedOperation(std::format("writing {} bits of floating point", width), span);
    auto offset = imm(address.disp, 12);
    if (address.disp_sym == nullptr) {
      if (address.base.is_none()) [[unlikely]]
        throw UnsupportedOperation("a floating-point write to nowhere", span);
      return { inst(mnemonic, { value, mcreg(address.base), offset }, span) };
    }
    MCReg place = mcreg(registers().info.new_virtual(registers().gpr, 64, nullptr, false));
    MCSymRef symbol{ address.disp_sym };
    return { inst("auipc.hi20", { place, symbol }, span), inst("addi.lo12", { place, place, symbol }, span),
             inst(mnemonic, { value, place, offset }, span) };
  }


  Insts RVSelector::select_call(const MCOperand& target, Span span)
  {
    // A call through a register is how a function held in a value is called.
    if (! std::holds_alternative<MCSymRef>(target))
      return { inst("jalr", { target }, span) };
    return { inst("jal", { target }, span) };
  }


  Insts RVSelector::select_return(Span span)
  {
    return { inst("ret", {}, span) };
  }


  Insts RVSelector::select_jump(const MCOperand& target, Span span)
  {
    if (! std::holds_alternative<MCSymRef>(target))
      // Through a register: wherever it holds.
      return { inst("jr", { target }, span) };
    return { inst("j", { target }, span) };
  }


  Insts RVSelector::select_branch(Condition cond, const MCOperand& lhs, const MCOperand& rhs, const MCSymRef& target,
                                  Span span)
  {
    // A branch compares two registers itself; half the orderings are the
    // other half with the operands exchanged, and the zero register is what a
    // comparison against zero names.
    MCOperand first = lhs;
    MCOperand second = rhs;
    if (! conditional(cond)) {
      std::swap(first, second);
      cond = mc::swapped(cond);
    }
    Insts held;
    auto [left, before_left] = as_register(first, span);
    auto [right, before_right] = as_register(second, span);
    held.insert(held.end(), before_left.begin(), before_left.end());
    held.insert(held.end(), before_right.begin(), before_right.end());
    held.push_back(inst(*conditional(cond), { left, right, target }, span));
    return held;
  }


  Insts RVSelector::select_set(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs, Span span)
  {
    // The comparison is the answer: "set if less than", signed and unsigned,
    // with the operands exchanged, the answer inverted, or both; equality a
    // subtraction and a question about the difference.
    Insts held;
    auto [left, before_left] = as_register(lhs, span);
    auto [right, before_right] = as_register(rhs, span);
    held.insert(held.end(), before_left.begin(), before_left.end());
    held.insert(held.end(), before_right.begin(), before_right.end());
    MCReg answer = mcreg(dst);
    if (cond == Condition::eq || cond == Condition::ne) {
      held.push_back(inst("sub", { answer, left, right }, span));
      if (cond == Condition::eq)
        held.push_back(inst("sltiu", { answer, answer, imm(1, 12) }, span));
      else
        held.push_back(inst("sltu", { answer, mcreg(registers().zero), answer }, span));
      return held;
    }
    if (cond == Condition::sgt || cond == Condition::sle || cond == Condition::ugt || cond == Condition::ule)
      std::swap(left, right);
    bool is_unsigned = cond == Condition::ult || cond == Condition::ule || cond == Condition::ugt
                       || cond == Condition::uge;
    held.push_back(inst(is_unsigned ? "sltu" : "slt", { answer, left, right }, span));
    if (cond == Condition::sle || cond == Condition::sge || cond == Condition::ule || cond == Condition::uge)
      held.push_back(inst("xori", { answer, answer, imm(1, 12) }, span));
    return held;
  }


  Insts RVSelector::select_address(Reg dst, const MCSymRef& symbol, Span span)
  {
    MCReg held = mcreg(registers().info.new_virtual(registers().gpr, 64, nullptr, false));
    return { inst("auipc.hi20", { held, symbol }, span), inst("addi.lo12", { held, held, symbol }, span),
             inst("mv", { mcreg(dst), held }, span) };
  }


  Insts RVSelector::select_widen(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    // One register width: an unsigned value is already the whole of one, and
    // a signed one has its sign spread by two shifts.
    if (bits >= 64 || std::holds_alternative<MCImm>(src))
      return select_move(dst, src, span);
    if (auto mem = std::get_if<MCMem>(&src)) {
      // The load itself widens, as the cast says.
      MCMem narrow = *mem;
      narrow.size_bits = bits;
      narrow.is_signed = is_signed;
      return select_move(dst, narrow, span);
    }
    if (! is_signed)
      return select_move(dst, src, span);
    auto spare = imm(64 - bits, 6, false);
    return { inst("slli", { mcreg(dst), src, spare }, span), inst("srai", { mcreg(dst), mcreg(dst), spare }, span) };
  }


  Insts RVSelector::select_clamp(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                                 const MCOperand& bound, Span span)
  {
    // Nothing moves a register only sometimes: the answer becomes a mask and
    // the difference between the two values is let through it.
    Insts held;
    auto flag = registers().info.new_virtual(registers().gpr, 64);
    auto set = select_set(cond, flag, lhs, rhs, span);
    held.insert(held.end(), set.begin(), set.end());
    MCReg mask = mcreg(flag);
    held.push_back(inst("sub", { mask, mcreg(registers().zero), mask }, span));
    auto [carried, before] = as_register(bound, span);
    held.insert(held.end(), before.begin(), before.end());
    MCReg difference = mcreg(registers().info.new_virtual(registers().gpr, 64));
    held.push_back(inst("xor", { difference, mcreg(dst), carried }, span));
    held.push_back(inst("and", { difference, difference, mask }, span));
    held.push_back(inst("xor", { mcreg(dst), mcreg(dst), difference }, span));
    return held;
  }


  Insts RVSelector::select_select(Reg dst, const MCOperand& chooser, const MCOperand& if_true,
                                  const MCOperand& if_false, bool floating, int bits, Span span)
  {
    auto& info = registers().info;
    const auto* gpr = registers().gpr;
    Insts held;
    auto [flag, before] = as_register(chooser, span);
    held.insert(held.end(), before.begin(), before.end());
    MCReg mask = mcreg(info.new_virtual(gpr, 64));
    held.push_back(inst("sub", { mask, mcreg(registers().zero), flag }, span));
    std::string_view out = bits == 64 ? "fmv.x.d" : "fmv.x.w";
    std::string_view back = bits == 64 ? "fmv.d.x" : "fmv.w.x";
    MCReg first;
    MCReg second;
    if (floating) {
      first = mcreg(info.new_virtual(gpr, 64));
      second = mcreg(info.new_virtual(gpr, 64));
      held.push_back(inst(out, { first, if_true }, span));
      held.push_back(inst(out, { second, if_false }, span));
    } else {
      auto [one, before_one] = as_register(if_true, span);
      held.insert(held.end(), before_one.begin(), before_one.end());
      auto [other, before_other] = as_register(if_false, span);
      held.insert(held.end(), before_other.begin(), before_other.end());
      first = one;
      second = other;
    }
    MCReg difference = mcreg(info.new_virtual(gpr, 64));
    held.push_back(inst("xor", { difference, first, second }, span));
    held.push_back(inst("and", { difference, difference, mask }, span));
    if (floating) {
      MCReg chosen = mcreg(info.new_virtual(gpr, 64));
      held.push_back(inst("xor", { chosen, second, difference }, span));
      held.push_back(inst(back, { mcreg(dst), chosen }, span));
    } else
      held.push_back(inst("xor", { mcreg(dst), second, difference }, span));
    return held;
  }


  std::pair<MCReg, Insts> RVSelector::as_register(const MCOperand& operand, Span span)
  {
    if (auto reg = std::get_if<MCReg>(&operand))
      return { *reg, {} };
    if (auto value = std::get_if<MCImm>(&operand); value != nullptr && value->value == 0)
      // Zero is a register here.
      return { mcreg(registers().zero), {} };
    auto carried = registers().info.new_virtual(registers().gpr, 64);
    auto moved = select_move(carried, operand, span);
    return { mcreg(carried), std::move(moved) };
  }


  Insts RVSelector::select_float_abs(Reg dst, const MCOperand& src, int bits, Span span)
  {
    // The sign of the answer is the two signs exclusive-ored.
    return { inst(bits == 32 ? "fsgnjx.s" : "fsgnjx.d", { mcreg(dst), src, src }, span) };
  }


  Insts RVSelector::select_float_extend(Reg dst, const MCOperand& src, int from_bits, int to_bits, Span span)
  {
    if (from_bits != 32 || to_bits != 64) [[unlikely]]
      throw UnsupportedOperation(
        std::format("widening {} bits of floating point to {}", from_bits, to_bits), span);
    return { inst("fcvt.d.s", { mcreg(dst), src }, span) };
  }


  Insts RVSelector::select_branch_if_finite(Reg value, int bits, const MCSymRef& target, Span span)
  {
    // The difference against itself compared with itself: one where it is a
    // number, nought where it is not.
    std::string_view suffix = bits == 32 ? ".s" : ".d";
    MCReg held = mcreg(registers().info.new_virtual(registers().fpr, float_register_bits));
    MCReg answer = mcreg(registers().info.new_virtual(registers().gpr, 64));
    return { inst(std::format("fsub{}", suffix), { held, mcreg(value), mcreg(value) }, span),
             inst(std::format("feq{}", suffix), { answer, held, held }, span),
             inst("bne", { answer, mcreg(registers().zero), target }, span) };
  }


  Insts RVSelector::select_float_round(Rounding how, Reg dst, const MCOperand& src, int bits, Span span)
  {
    // One instruction where the program is built for something with Zfa.
    if (! isa.has("zfa")) [[unlikely]]
      throw UnsupportedOperation("rounding a floating-point number in one instruction", span);
    return { inst(bits == 32 ? "fround.s" : "fround.d", { mcreg(dst), src, imm(rounds(how), 3, false) }, span) };
  }


  Insts RVSelector::select_float_to_int(Rounding how, Reg dst, const MCOperand& src, int bits, Span span)
  {
    return { inst(bits == 32 ? "fcvt.w.s" : "fcvt.l.d", { mcreg(dst), src, imm(rounds(how), 3, false) }, span) };
  }


  Insts RVSelector::select_int_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    // A signed doubleword, for both formats: a word handed over is in one
    // already, extended by its sign.
    return { inst(bits == 32 ? "fcvt.s.l" : "fcvt.d.l", { mcreg(dst), src }, span) };
  }


  Insts RVSelector::select_unsigned_to_float(Reg dst, const MCOperand& src, int bits, Span span)
  {
    return { inst(bits == 32 ? "fcvt.s.lu" : "fcvt.d.lu", { mcreg(dst), src }, span) };
  }


  Insts RVSelector::select_truncate_to_int(Reg dst, const MCOperand& src, int bits, bool is_signed, Span span)
  {
    // The rounding field that says toward zero, whatever `frm` holds.
    constexpr int toward_zero = 0b001;
    std::string_view mnemonic = bits == 32 ? (is_signed ? "fcvt.l.s" : "fcvt.lu.s")
                                           : (is_signed ? "fcvt.l.d" : "fcvt.lu.d");
    return { inst(mnemonic, { mcreg(dst), src, imm(toward_zero, 3, false) }, span) };
  }


  Insts RVSelector::select_move_bits(Reg dst, const MCOperand& src, int bits, Span span)
  {
    auto reg = std::get_if<MCReg>(&src);
    if (reg == nullptr) [[unlikely]]
      throw UnsupportedOperation("moving the bits of what is not in a register", span);
    bool into_float = dst.cls() == registers().fpr;
    std::string_view mnemonic = bits == 32 ? (into_float ? "fmv.w.x" : "fmv.x.w")
                                           : (into_float ? "fmv.d.x" : "fmv.x.d");
    return { inst(mnemonic, { mcreg(dst), mcreg(reg->reg) }, span) };
  }


  Insts RVSelector::select_count_ones(Reg dst, const MCOperand& src, Span span)
  {
    return { inst("cpop", { mcreg(dst), src }, span) };
  }


  Insts RVSelector::select_count_leading(Reg dst, const MCOperand& src, Span span)
  {
    return { inst("clz", { mcreg(dst), src }, span) };
  }


  Insts RVSelector::select_count_trailing(Reg dst, const MCOperand& src, Span span)
  {
    return { inst("ctz", { mcreg(dst), src }, span) };
  }


  Insts RVSelector::select_float_copysign(Reg dst, const MCOperand& magnitude, const MCOperand& sign, int bits,
                                          Span span)
  {
    return { inst(bits == 32 ? "fsgnj.s" : "fsgnj.d", { mcreg(dst), magnitude, sign }, span) };
  }


  Insts RVSelector::select_float_op(const mc::Op* op, Reg dst, const MCOperand& left, const MCOperand& right,
                                    int bits, Span span)
  {
    std::string_view base;
    if (op == mc::ops::plus)
      base = "fadd";
    else if (op == mc::ops::minus)
      base = "fsub";
    else if (op == mc::ops::times)
      base = "fmul";
    else if (op == mc::ops::divide)
      base = "fdiv";
    else if (op == mc::ops::larger)
      base = "fmax";
    else if (op == mc::ops::smaller)
      base = "fmin";
    if (base.empty() || (bits != 32 && bits != 64)) [[unlikely]]
      throw UnsupportedOperation(std::format("'{}' on a floating-point value", op->name), span);
    return { inst(std::format("{}{}", base, bits == 32 ? ".s" : ".d"), { mcreg(dst), left, right }, span) };
  }


  Insts RVSelector::select_float_compare(Condition cond, Reg dst, const MCOperand& lhs, const MCOperand& rhs,
                                         int bits, Span span)
  {
    // Three comparisons writing one or nought; the other three exchange the
    // operands, and inequality is equality turned round.
    std::string_view suffix = bits == 32 ? ".s" : ".d";
    bool exchanged = cond == Condition::sgt || cond == Condition::ugt || cond == Condition::sge
                     || cond == Condition::uge;
    const MCOperand& first = exchanged ? rhs : lhs;
    const MCOperand& second = exchanged ? lhs : rhs;
    std::string_view asking;
    switch (cond) {
    case Condition::eq:
    case Condition::ne: asking = "feq"; break;
    case Condition::slt:
    case Condition::ult:
    case Condition::sgt:
    case Condition::ugt: asking = "flt"; break;
    default: asking = "fle"; break;
    }
    Insts built{ inst(std::format("{}{}", asking, suffix), { mcreg(dst), first, second }, span) };
    if (cond == Condition::ne)
      built.push_back(inst("xori", { mcreg(dst), mcreg(dst), imm(1, 12) }, span));
    return built;
  }


  Insts RVSelector::select_shift(const mc::Op* op, Reg dst, const MCOperand& value, const MCOperand& amount, int,
                                 Span span)
  {
    std::string_view mnemonic;
    if (op == mc::ops::shift_left)
      mnemonic = "sll";
    else if (op == mc::ops::shift_right)
      mnemonic = "srl";
    else if (op == mc::ops::shift_right_signed)
      mnemonic = "sra";
    else [[unlikely]]
      throw UnsupportedOperation(std::format("no RISC-V selection rule for '{}'", op->name), span);
    Insts held;
    auto [shifted, before_value] = as_register(value, span);
    held.insert(held.end(), before_value.begin(), before_value.end());
    auto [counted, before_amount] = as_register(amount, span);
    held.insert(held.end(), before_amount.begin(), before_amount.end());
    held.push_back(inst(mnemonic, { mcreg(dst), shifted, counted }, span));
    return held;
  }


  Insts RVSelector::select_wide_product(Reg low, Reg high, const MCOperand& left, const MCOperand& right,
                                        bool is_signed, Span span)
  {
    // The upper half first, as the architecture asks of a pair to be fused.
    Insts held;
    auto [first, before_first] = as_register(left, span);
    held.insert(held.end(), before_first.begin(), before_first.end());
    auto [second, before_second] = as_register(right, span);
    held.insert(held.end(), before_second.begin(), before_second.end());
    held.push_back(inst(is_signed ? "mulh" : "mulhu", { mcreg(high), first, second }, span));
    held.push_back(inst("mul", { mcreg(low), first, second }, span));
    return held;
  }


  Insts RVSelector::select_divide(Reg dst, const MCOperand& left, const MCOperand& right, bool is_signed,
                                  bool remainder, int, Span span)
  {
    Insts held;
    auto [dividend, before_left] = as_register(left, span);
    held.insert(held.end(), before_left.begin(), before_left.end());
    auto [divisor, before_right] = as_register(right, span);
    held.insert(held.end(), before_right.begin(), before_right.end());
    auto mnemonic = std::format("{}{}", remainder ? "rem" : "div", is_signed ? "" : "u");
    held.push_back(inst(mnemonic, { mcreg(dst), dividend, divisor }, span));
    return held;
  }


  std::int64_t RVSelector::frame_size(std::int64_t total, bool)
  {
    // A multiple of sixteen, which is what the stack pointer is aligned to
    // everywhere and not only where a call is made.
    return (total + 15) & ~std::int64_t(15);
  }


  std::pair<std::optional<std::int64_t>, std::int64_t> RVSelector::frame_walk(std::int64_t total, std::int64_t size,
                                                                              std::int64_t link)
  {
    // The return address lies wherever the function put it -- and nowhere
    // where it calls nothing.
    return { link != 0 ? std::optional(size) : std::nullopt, total };
  }


  Insts RVSelector::select_save_link(std::int64_t offset, Span span)
  {
    return { inst("sd", { mcreg(registers().ra), mcreg(registers().sp), imm(offset, 12) }, span) };
  }


  Insts RVSelector::select_restore_link(std::int64_t offset, Span span)
  {
    return { inst("ld", { mcreg(registers().ra), mcreg(registers().sp), imm(offset, 12) }, span) };
  }


  Insts RVSelector::select_spill(std::int64_t slot, Reg source, Span span)
  {
    return { inst(is_float(source) ? "fsd" : "sd", { mcreg(source), mcreg(registers().sp), imm(slot, 12) }, span) };
  }


  Insts RVSelector::select_reload(Reg destination, std::int64_t slot, Span span)
  {
    return { inst(is_float(destination) ? "fld" : "ld", { mcreg(destination), mcreg(registers().sp), imm(slot, 12) },
                  span) };
  }


  Insts RVSelector::select_frame(std::int64_t size, Span span)
  {
    return { inst("addi", { mcreg(registers().sp), mcreg(registers().sp), imm(-size, 12) }, span) };
  }


  Insts RVSelector::select_unframe(std::int64_t size, Span span)
  {
    return { inst("addi", { mcreg(registers().sp), mcreg(registers().sp), imm(size, 12) }, span) };
  }


  // -- lowering a function ----------------------------------------------------

  namespace {

    using ir::InstKind;

    bool is_floating(const ir::Type* ty)
    {
      if (auto result = ir::as<ir::ResultType>(ty))
        ty = result->ok;
      return ir::is<ir::FloatType>(ty);
    }

    int bits_of(const ir::Type* ty)
    {
      return int(static_cast<const ir::FloatType*>(ty)->bits);
    }

    // How many bits a value of `ty` occupies in memory.
    int width_of(const ir::Type* ty)
    {
      if (auto integer = ir::as<ir::IntType>(ty))
        return int(integer->held());
      if (auto floating = ir::as<ir::FloatType>(ty))
        return int(floating->bits);
      if (auto character = ir::as<ir::CharType>(ty))
        return int(character->holder()->bits);
      if (auto enumeration = ir::as<ir::EnumType>(ty))
        return int(enumeration->holder->bits);
      if (ir::is<ir::ProductType>(ty)) {
        auto inner = ir::parts_of(ty);
        if (inner.size() == 1 && inner[0] != ty && ! ir::made_of_parts(inner[0]))
          return width_of(inner[0]);
      }
      return ir::is<ir::BoolType>(ty) ? 8 : 64;
    }

    // Whether a narrow value of `ty` is widened by its sign when it is read.
    bool is_signed(const ir::Type* ty)
    {
      if (auto character = ir::as<ir::CharType>(ty))
        return character->holder()->is_signed;
      if (auto enumeration = ir::as<ir::EnumType>(ty))
        return enumeration->holder->is_signed;
      auto integer = ir::as<ir::IntType>(ty);
      return integer != nullptr && integer->is_signed;
    }

    // The number a constant stands for and its type.
    std::optional<std::pair<__int128, const ir::Type*>> number_of(const ir::Value* value)
    {
      switch (value->vkind) {
      case ir::ValueKind::int_const: return std::pair{ static_cast<const ir::IntConst*>(value)->value, value->ty };
      case ir::ValueKind::char_const:
        return std::pair{ __int128(static_cast<const ir::CharConst*>(value)->value), value->ty };
      case ir::ValueKind::bool_const:
        return std::pair{ __int128(static_cast<const ir::BoolConst*>(value)->value ? 1 : 0), value->ty };
      case ir::ValueKind::enum_const:
        return std::pair{ __int128(static_cast<const ir::EnumConst*>(value)->number()), value->ty };
      default: return std::nullopt;
      }
    }

    // The narrowest standard width that holds `value`.
    int immediate_width(__int128 value, bool is_signed)
    {
      for (int bits : { 8, 16, 32, 64 }) {
        __int128 one = 1;
        if (is_signed) {
          if (-(one << (bits - 1)) <= value && value < (one << (bits - 1)))
            return bits;
        } else if (0 <= value && value < (one << bits))
          return bits;
      }
      return 64;
    }

    const mc::PhysReg* as_argument(const mc::PhysReg* place, const ir::Type* ty, mc::RegisterInfo& info)
    {
      if (ir::is<ir::FloatType>(ty))
        return info.view(place->unit, float_register_bits);
      return info.view(place->unit, 64);
    }

    // The register an instruction's result is put in.
    const mc::PhysReg* result_register(const ir::Type* ty, const CallConvDesc& cconv, std::size_t index = 0)
    {
      if (ir::is<ir::FloatType>(ty))
        return cconv.float_ret_regs.at(index);
      return cconv.int_ret_regs.at(index);
    }

    // How many registers the answer of a result takes.
    std::size_t answer_registers(const ir::Value* value)
    {
      auto result = ir::as<ir::ResultType>(value->ty);
      if (result == nullptr)
        return 1;
      return ir::leaves_of(result->ok).size();
    }

    // A register for a value the function computes, wanted in `hint`.
    Reg new_value(const ir::Type* ty, mc::RegisterInfo& info, const mc::PhysReg* hint = nullptr)
    {
      if (ir::is<ir::ProductType>(ty) && ! ir::held_in_memory(ty)) {
        auto inner = ir::parts_of(ty);
        if (inner.size() == 1 && inner[0] != ty && ! ir::made_of_parts(inner[0]))
          return new_value(inner[0], info, hint);
        throw UnsupportedOperation(std::format("a value of type '{}'", ty->written()));
      }
      if (ir::is<ir::FloatType>(ty))
        return info.new_virtual(registers().fpr, float_register_bits, hint);
      return info.new_virtual(registers().gpr, 64, hint);
    }

    // The value the function returns, where it computes one.
    const ir::Value* returned_value(const ir::Function& func)
    {
      for (const auto& block : func.blocks)
        for (auto inst : block->insts)
          if (inst->kind == InstKind::RetInst && ! inst->operands.empty())
            return inst->operands[0];
      return nullptr;
    }

    std::optional<Span> valid(Span span)
    {
      return span.is_valid() ? std::optional(span) : std::nullopt;
    }

    Span or_invalid(std::optional<Span> span) { return span.value_or(invalid_span); }

    const mc::Op* float_operation(ir::BinOp op)
    {
      switch (op) {
      case ir::BinOp::add: return mc::ops::plus;
      case ir::BinOp::sub: return mc::ops::minus;
      case ir::BinOp::mul: return mc::ops::times;
      case ir::BinOp::fdiv: return mc::ops::divide;
      case ir::BinOp::smax: return mc::ops::larger;
      case ir::BinOp::smin: return mc::ops::smaller;
      default: return nullptr;
      }
    }

    const mc::Op* plain_operation(ir::BinOp op)
    {
      switch (op) {
      case ir::BinOp::add:
      case ir::BinOp::wrap_add: return mc::ops::plus;
      case ir::BinOp::sub:
      case ir::BinOp::wrap_sub: return mc::ops::minus;
      case ir::BinOp::mul:
      case ir::BinOp::wrap_mul: return mc::ops::times;
      case ir::BinOp::and_: return mc::ops::and_;
      case ir::BinOp::or_: return mc::ops::or_;
      case ir::BinOp::xor_: return mc::ops::xor_;
      default: return nullptr;
      }
    }

    std::optional<Rounding> rounding_of(ir::UnOp op)
    {
      switch (op) {
      case ir::UnOp::floor: return Rounding::down;
      case ir::UnOp::ceil: return Rounding::up;
      case ir::UnOp::nearest: return Rounding::nearest;
      case ir::UnOp::rounded: return Rounding::current;
      default: return std::nullopt;
      }
    }

    // Above this a floating-point value of each width is a whole number.
    double whole_above(int bits) { return bits == 32 ? double(1 << 23) : double(std::int64_t(1) << 52); }


    // Everything about one function being lowered.
    class Lowering final : public Operands, public Scratch {
    public:
      Lowering(mc::Assembler& asm_, const ir::Function& func, const CallConvDesc& cconv, mc::RegisterInfo& info,
               Messages* messages, const SourceManager* sources, Constants* constants, const Destroyed* known,
               const ir::DataLayout& layout)
        : asm_(asm_), func(func), cconv(cconv), info(info), messages(messages), sources(sources),
          constants(constants), known(known), layout(layout)
      {
      }

      void run();

      // -- what the shared lowerings ask --------------------------------------

      MCOperand value(ir::Value* value, Span span) override
      {
        if (value->vkind == ir::ValueKind::float_const)
          return mcreg(floating(static_cast<const ir::FloatConst*>(value), span));
        if (auto constant = number_of(value)) {
          bool sign = is_signed(constant->second);
          return imm(constant->first, immediate_width(constant->first, sign), sign);
        }
        return in_register(value, span);
      }

      MCOperand in_register(ir::Value* value, Span span) override
      {
        if (value->vkind == ir::ValueKind::float_const)
          return mcreg(floating(static_cast<const ir::FloatConst*>(value), span));
        if (auto constant = number_of(value)) {
          auto carried = new_value(constant->second, info);
          asm_.loadreg(carried, imm(constant->first, 12, is_signed(constant->second)), span);
          return mcreg(carried);
        }
        auto found = held.find(value);
        if (found == held.end()) [[unlikely]]
          throw UnsupportedOperation("a value this backend did not compute", valid(span));
        return mcreg(found->second);
      }

      Reg destination(ir::Value* value) override
      {
        auto found = held.find(value);
        if (found == held.end()) [[unlikely]]
          throw UnsupportedOperation("a value this backend did not compute");
        return found->second;
      }

      Reg part_of(ir::Value* value, std::size_t index, std::optional<Span> span) override
      {
        if (index == 0)
          return register_of(value, span);
        auto found = extra.find(value);
        if (found == extra.end() || index > found->second.size()) [[unlikely]]
          throw UnsupportedOperation("a value of several parts this backend did not compute", span);
        return found->second[index - 1];
      }

      Reg scratch() override { return info.new_virtual(registers().gpr, 64); }

    private:
      Reg register_of(const ir::Value* value, std::optional<Span> span)
      {
        auto found = held.find(value);
        if (found == held.end()) [[unlikely]]
          throw UnsupportedOperation("a value this backend did not compute", span);
        return found->second;
      }

      Reg flag_of(const ir::Value* value, std::optional<Span> span)
      {
        return part_of(const_cast<ir::Value*>(value), answer_registers(value), span);
      }

      // The operand for a value nothing may read: the undefined one is nought.
      MCOperand undefined(ir::Value* value, const ir::Type* ty, Span span)
      {
        if (value->vkind == ir::ValueKind::undef)
          return imm(0, std::max(32, width_of(ty)), false);
        return this->value(value, span);
      }

      // A register holding the floating-point constant `value`, read out of
      // the image.
      Reg floating(const ir::FloatConst* value, Span span)
      {
        if (constants == nullptr) [[unlikely]]
          throw UnsupportedOperation("a floating-point constant, with nowhere to put it");
        int bits = width_of(value->ty);
        auto symbol = constants->symbol(ir::encode_float(value->value, value->ty, plain_layout()), std::uint64_t(bits / 8));
        auto made = new_value(value->ty, info);
        MCMem read;
        read.disp_sym = mc::sym_expr(asm_.streamer().symbol(symbol));
        read.rip_relative = true;
        read.size_bits = bits;
        asm_.loadreg(made, read, span);
        return made;
      }

      // One part of a value being put into a result.
      MCOperand part_source(ir::Value* value, std::size_t index, const ir::Type* ty, std::size_t how_many,
                            Span span)
      {
        if (how_many == 1)
          return undefined(value, ty, span);
        if (value->vkind == ir::ValueKind::undef)
          return imm(0, std::max(32, width_of(ty)), false);
        return mcreg(part_of(value, index, span));
      }

      // Where in memory an address names.
      MCMem place_of(const ir::Value* address, std::optional<Span> span, std::int64_t disp = 0,
                     std::optional<int> size_bits = std::nullopt, bool sign = false)
      {
        MCMem made;
        if (address->vkind == ir::ValueKind::global) {
          made.disp_sym =
            mc::sym_expr(asm_.streamer().symbol(symbol_of(static_cast<const ir::GlobalVar*>(address))));
          made.rip_relative = true;
        } else
          made.base = register_of(address, span);
        made.disp = disp;
        made.size_bits = size_bits;
        made.is_signed = sign;
        return made;
      }

      // The register for an instruction's own value, wanted where the
      // function answers if it is what the function answers.
      Reg destination_for(const ir::Instruction* inst, const ir::Type* ty)
      {
        auto made = new_value(ty, info, inst == returned ? result_register(ty, cconv) : nullptr);
        held[inst] = made;
        return made;
      }

      // What is emitted where an answer will not fit its type.
      class Told final : public Fault {
      public:
        Told(Lowering& owner, std::string_view what, Span span, int status = statuses::OVERFLOW)
          : owner(owner), text(describe(what, owner.func.name, span, owner.sources)), status(status)
        {
        }

        // Report the fault and come back, for a build that observes.
        void observed(mc::Assembler& asm_, Span span)
        {
          if (owner.messages == nullptr) [[unlikely]]
            throw UnsupportedOperation("an operation that can fault, with nowhere to report it");
          auto symbol = owner.messages->symbol(text);
          const auto& system = lookup_cconv(ir::system_cconv);
          auto first = system.int_arg_regs[0];
          auto second = system.int_arg_regs[1];
          asm_.address(first, symbol, span);
          asm_.loadreg(second, imm(__int128(text.size()), 32, false), span);
          auto destroyed = destroyed_by(nullptr, system, owner.info, nullptr);
          asm_.call(std::string(report_symbol), span, std::vector<Reg>(destroyed.begin(), destroyed.end()));
        }

        // Report the fault and stop; this does not come back.
        void out_of_range(mc::Assembler& asm_, Span span) override
        {
          if (owner.messages == nullptr) [[unlikely]]
            throw UnsupportedOperation("an operation that can fault, with nowhere to report it");
          auto symbol = owner.messages->symbol(text);
          const auto& system = lookup_cconv(ir::system_cconv);
          asm_.address(system.int_arg_regs[0], symbol, span);
          asm_.loadreg(system.int_arg_regs[1], imm(__int128(text.size()), 32, false), span);
          asm_.loadreg(system.int_arg_regs[2], imm(status, 32, false), span);
          asm_.call(std::string(abort_symbol), span);
        }

      private:
        Lowering& owner;
        std::string text;
        int status;
      };

      void parameters();
      void lower(const ir::Instruction* inst, std::size_t index);
      void lower_float_binary(const ir::Instruction* inst, std::optional<Span> span);
      void lower_integer_binary(const ir::Instruction* inst, std::optional<Span> span);
      void lower_rounding(const ir::Instruction* inst, Rounding how);
      void lower_store(const ir::Instruction* inst, std::optional<Span> span);
      void lower_return(const ir::Instruction* inst, std::optional<Span> span);
      void lower_call(const ir::Instruction* inst, std::optional<Span> span);
      void lower_wrap(const ir::Instruction* inst);

      mc::Assembler& asm_;
      const ir::Function& func;
      const CallConvDesc& cconv;
      mc::RegisterInfo& info;
      Messages* messages;
      const SourceManager* sources;
      Constants* constants;
      const Destroyed* known;
      const ir::DataLayout& layout;
      bool rounds_in_one = false;
      // Where each value is held, and the registers after the first of a value
      // of several.
      std::unordered_map<const ir::Value*, Reg> held;
      std::unordered_map<const ir::Value*, std::vector<Reg>> extra;
      const ir::Value* returned = nullptr;
      std::vector<std::string> labels;
    };


    void Lowering::run()
    {
      auto selector = dynamic_cast<RVSelector*>(&asm_.selector());
      rounds_in_one = selector != nullptr && selector->isa.has("zfa");
      asm_.begin_function(ir::symbol_name(func), func.linkage == ir::Linkage::visible, 0,
                          mc::AllocationOrder::per_class(cconv.orders(registers().gpr->name, registers().fpr->name)),
                          cconv.callee_saved);
      returned = returned_value(func);
      labels = labels_of(ir::symbol_name(func), func);
      parameters();
      for (std::size_t index = 0; index < func.blocks.size(); ++index) {
        if (index > 0)
          asm_.block(labels[index]);
        for (auto inst : func.blocks[index]->insts)
          lower(inst, index);
      }
      asm_.end_function();
    }


    void Lowering::parameters()
    {
      // Every block parameter gets its register before any block is walked;
      // the entry block's arrive where the convention says and are copied out.
      const ir::BasicBlock* entry = func.blocks.empty() ? nullptr : func.blocks[0].get();
      std::vector<std::vector<const mc::PhysReg*>> arriving;
      if (entry != nullptr) {
        std::vector<const ir::Type*> types;
        for (const auto& param : entry->params)
          types.push_back(param->ty);
        try {
          arriving = argument_places(cconv, types, func.attrs.chained);
        } catch (const TooManyArguments&) {
          throw UnsupportedOperation("a function with more arguments than the convention passes in registers");
        }
      }
      for (std::size_t index = 0; index < func.blocks.size(); ++index) {
        const auto& block = func.blocks[index];
        for (std::size_t position = 0; position < block->params.size(); ++position) {
          const ir::BlockParam* param = block->params[position].get();
          auto pieces = ir::leaves_of(param->ty);
          std::vector<Reg> given;
          if (index != 0) {
            if (param->ty == ir::mem_type())
              continue;
            for (auto part : pieces)
              given.push_back(new_value(part, info));
          } else {
            const auto& places = arriving[position];
            for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at)
              given.push_back(new_value(pieces[at], info, as_argument(places[at], pieces[at], info)));
          }
          held[param] = given[0];
          if (given.size() > 1)
            extra[param] = std::vector<Reg>(given.begin() + 1, given.end());
        }
      }
      if (entry != nullptr)
        for (std::size_t position = 0; position < entry->params.size(); ++position) {
          ir::BlockParam* param = entry->params[position].get();
          auto pieces = ir::leaves_of(param->ty);
          const auto& places = arriving[position];
          for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at)
            asm_.loadreg(part_of(param, at, std::nullopt), mcreg(as_argument(places[at], pieces[at], info)),
                         func.span);
        }
    }


    void Lowering::lower(const ir::Instruction* inst, std::size_t index)
    {
      auto span = valid(inst->span);
      if (auto refused = cannot_order(inst)) [[unlikely]]
        throw UnsupportedOperation(*refused, inst->span);
      switch (inst->kind) {
      case InstKind::FrameInst: {
        // Room of this function's own: the stack pointer and how far in.
        auto destination = destination_for(inst, inst->ty);
        auto slot = asm_.frame_slot(std::int64_t(ir::size_of(inst->held, layout)),
                                    std::int64_t(ir::align_of(inst->held, layout)));
        asm_.op(mc::ops::plus, destination, { mcreg(registers().sp), imm(slot, 32, false) }, inst->span);
        return;
      }
      case InstKind::AssertInst: {
        // Failing is the case that jumps, since it does not come back.
        auto holds = asm_.reserve_label("holds");
        auto [condition, lhs, rhs] = condition_of(func, inst, *this, zero_immediate, inst->span);
        asm_.branch(condition, lhs, rhs, holds, inst->span);
        Told told(*this, inst->what, inst->span, inst->status);
        if (inst->observing)
          told.observed(asm_, or_invalid(span));
        else
          told.out_of_range(asm_, or_invalid(span));
        asm_.block(holds);
        return;
      }
      case InstKind::MemStartInst:
        return;
      case InstKind::CodeInst: {
        auto destination = destination_for(inst, inst->ty);
        asm_.address(destination, ir::symbol_name(*inst->callee), inst->span);
        return;
      }
      case InstKind::AddressInst: {
        auto destination = destination_for(inst, inst->ty);
        asm_.address(destination, symbol_of(static_cast<const ir::GlobalVar*>(inst->operands[0])), inst->span);
        return;
      }
      case InstKind::LoadInst: {
        const ir::Value* address = inst->operands[1];
        if (ir::made_of_parts(inst->ty)) {
          // One read per part, at the offset the layout gives it.
          auto pieces = ir::leaves_of(inst->ty);
          auto offsets = ir::leaf_offsets_of(inst->ty, layout);
          std::vector<Reg> taken;
          for (std::size_t at = 0; at < pieces.size() && at < offsets.size(); ++at) {
            const ir::Type* one = pieces[at];
            auto into = new_value(one, info, inst == returned ? result_register(one, cconv, at) : nullptr);
            asm_.loadreg(into, place_of(address, span, std::int64_t(offsets[at]), width_of(one), is_signed(one)),
                         inst->span);
            taken.push_back(into);
          }
          held[inst] = taken[0];
          extra[inst] = std::vector<Reg>(taken.begin() + 1, taken.end());
          return;
        }
        auto destination = destination_for(inst, inst->ty);
        auto read = place_of(address, span, 0, width_of(inst->ty), is_signed(inst->ty));
        if (inst->ordering == ir::Ordering::acquire)
          asm_.acquire(destination, read, inst->span);
        else
          asm_.loadreg(destination, read, inst->span);
        return;
      }
      case InstKind::StoreInst:
        lower_store(inst, span);
        return;
      case InstKind::RetInst:
        lower_return(inst, span);
        return;
      case InstKind::BinaryInst:
        if (inst->bin == ir::BinOp::wide_mul) {
          // Both halves, the upper one first as the tuple has them.
          Reg high = info.new_virtual(registers().gpr, 64);
          Reg low = info.new_virtual(registers().gpr, 64);
          held[inst] = high;
          extra[inst] = { low };
          auto left = value(inst->operands[0], inst->span);
          auto right = value(inst->operands[1], inst->span);
          asm_.wide_product(low, high, left, right, is_signed(inst->operands[0]->ty), inst->span);
          return;
        }
        if (is_floating(inst->ty))
          lower_float_binary(inst, span);
        else
          lower_integer_binary(inst, span);
        return;
      case InstKind::TupleInst: {
        // Several registers with nothing between them; a member of several
        // values is that many here.
        auto pieces = ir::leaves_of(inst->ty);
        std::vector<Reg> taken;
        for (auto part : pieces)
          taken.push_back(new_value(part, info));
        held[inst] = taken[0];
        extra[inst] = std::vector<Reg>(taken.begin() + 1, taken.end());
        std::vector<MCOperand> members;
        for (auto one : inst->operands) {
          auto parts = ir::leaves_of(one->ty).size();
          if (parts == 1)
            members.push_back(value(one, inst->span));
          else
            for (std::size_t at = 0; at < parts; ++at)
              members.push_back(mcreg(part_of(one, at, inst->span)));
        }
        for (std::size_t at = 0; at < taken.size() && at < members.size(); ++at)
          asm_.loadreg(taken[at], members[at], inst->span);
        return;
      }
      case InstKind::ExtractInst: {
        ir::Value* whole = inst->operands[0];
        if (ir::is<ir::ResultType>(whole->ty)) {
          // The answer, which may be several registers, the truth value after
          // them, and the error after that.
          std::size_t first = inst->index == 0 ? 0 : answer_registers(whole) + std::size_t(inst->index) - 1;
          auto count = ir::leaves_of(inst->ty).size();
          held[inst] = part_of(whole, first, span);
          if (count > 1) {
            std::vector<Reg> more;
            for (std::size_t at = 1; at < count; ++at)
              more.push_back(part_of(whole, first + at, span));
            extra[inst] = std::move(more);
          }
          return;
        }
        held[inst] = part_of(whole, std::size_t(inst->index), span);
        return;
      }
      case InstKind::WrapInst:
        lower_wrap(inst);
        return;
      case InstKind::UnwrapInst: {
        ir::Value* whole = inst->operands[0];
        auto how_many = answer_registers(whole);
        held[inst] = register_of(whole, span);
        if (how_many > 1) {
          std::vector<Reg> more;
          for (std::size_t at = 1; at < how_many; ++at)
            more.push_back(part_of(whole, at, span));
          extra[inst] = std::move(more);
        }
        return;
      }
      case InstKind::FailedInst:
        held[inst] = flag_of(inst->operands[0], span);
        return;
      case InstKind::ErrorInst: {
        ir::Value* whole = inst->operands[0];
        auto first = answer_registers(whole) + 1;
        held[inst] = part_of(whole, first, span);
        if (ir::made_of_parts(inst->ty)) {
          std::vector<Reg> more;
          for (std::size_t at = 1; at < ir::parts_of(inst->ty).size(); ++at)
            more.push_back(part_of(whole, first + at, span));
          extra[inst] = std::move(more);
        }
        return;
      }
      case InstKind::UnaryInst: {
        if (is_counting(inst->un)) {
          auto destination = destination_for(inst, inst->ty);
          const ir::Type* counted = inst->operands[0]->ty;
          auto operand = value(inst->operands[0], inst->span);
          lower_count(asm_, inst->un, operand, width_of(counted), is_signed(counted), destination, *this, inst->span);
          return;
        }
        if (auto how = rounding_of(inst->un)) {
          lower_rounding(inst, *how);
          return;
        }
        if (inst->un == ir::UnOp::fabs) {
          auto destination = destination_for(inst, inst->ty);
          auto operand = in_register(inst->operands[0], inst->span);
          asm_.float_abs(destination, operand, bits_of(inst->ty), inst->span);
          return;
        }
        const mc::Op* unary = inst->un == ir::UnOp::not_ ? mc::ops::not_
                              : inst->un == ir::UnOp::neg ? mc::ops::neg : nullptr;
        if (unary == nullptr) [[unlikely]]
          throw UnsupportedOperation(std::format("the operation '{}'", pl4g::describe(inst->un)), span);
        auto destination = destination_for(inst, inst->ty);
        auto operand = in_register(inst->operands[0], inst->span);
        asm_.op(unary, destination, { operand }, inst->span);
        // The complement of a narrow unsigned value sets the bits above it.
        normalize(asm_, inst->ty, destination, 64, inst->span);
        return;
      }
      case InstKind::CastInst:
        switch (inst->cast) {
        case ir::CastKind::bitcast: {
          // The bits already in the register, read as something else.
          auto found = in_register(inst->operands[0], inst->span);
          held[inst] = std::get<MCReg>(found).reg;
          return;
        }
        case ir::CastKind::zext:
        case ir::CastKind::sext: {
          auto destination = destination_for(inst, inst->ty);
          auto operand = in_register(inst->operands[0], inst->span);
          asm_.widen(destination, operand, width_of(inst->operands[0]->ty), inst->cast == ir::CastKind::sext,
                     inst->span);
          return;
        }
        case ir::CastKind::trunc: {
          auto destination = destination_for(inst, inst->ty);
          auto operand = in_register(inst->operands[0], inst->span);
          asm_.widen(destination, operand, width_of(inst->ty), is_signed(inst->ty), inst->span);
          return;
        }
        case ir::CastKind::fext: {
          auto destination = destination_for(inst, inst->ty);
          auto operand = in_register(inst->operands[0], inst->span);
          asm_.float_extend(destination, operand, bits_of(inst->operands[0]->ty), bits_of(inst->ty), inst->span);
          return;
        }
        case ir::CastKind::itof: {
          // Every whole number widened to a word by its own sign and converted
          // from there, a `u64` by the conversion that reads a word without one.
          auto destination = destination_for(inst, inst->ty);
          auto counted = ir::as<ir::IntType>(inst->operands[0]->ty);
          auto given = in_register(inst->operands[0], inst->span);
          int width = width_of(counted);
          MCReg number{ std::get<MCReg>(given).reg, 64 };
          if (width < 64) {
            number = MCReg{ info.new_virtual(registers().gpr, 64), 64 };
            asm_.widen(number.reg, given, width, counted->is_signed, inst->span);
          }
          if (counted->is_signed || width < 64)
            asm_.int_to_float(destination, number, bits_of(inst->ty), inst->span);
          else
            asm_.unsigned_to_float(destination, number, bits_of(inst->ty), inst->span);
          return;
        }
        case ir::CastKind::ftoi: {
          // Toward zero into a whole word, whose low bits are the number.
          auto destination = destination_for(inst, inst->ty);
          auto into = ir::as<ir::IntType>(inst->ty);
          auto answer = info.new_virtual(registers().gpr, 64);
          auto given = in_register(inst->operands[0], inst->span);
          asm_.truncate_to_int(answer, given, bits_of(inst->operands[0]->ty), into->is_signed || width_of(into) < 64,
                               inst->span);
          asm_.loadreg(destination, MCReg{ answer, destination.bits() }, inst->span);
          return;
        }
        case ir::CastKind::fbits: {
          // The same bits in the other kind of register.
          auto destination = destination_for(inst, inst->ty);
          auto given = in_register(inst->operands[0], inst->span);
          int bits = ir::is<ir::FloatType>(inst->ty) ? bits_of(inst->ty) : int(ir::as<ir::IntType>(inst->ty)->bits);
          asm_.move_bits(destination, given, bits, inst->span);
          // The narrower move fills the doubleword with copies of the top bit,
          // and a `u32` is held with zeroes there.
          normalize(asm_, inst->ty, destination, 64, inst->span);
          return;
        }
        }
        break;
      case InstKind::SelectInst: {
        auto destination = destination_for(inst, inst->ty);
        bool floating = ir::is<ir::FloatType>(inst->ty);
        std::vector<MCOperand> chosen;
        for (std::size_t at = 1; at < inst->operands.size(); ++at)
          chosen.push_back(floating ? in_register(inst->operands[at], inst->span)
                                    : value(inst->operands[at], inst->span));
        auto chooser = in_register(inst->operands[0], inst->span);
        asm_.select(destination, chooser, chosen[0], chosen[1], floating, floating ? bits_of(inst->ty) : 64,
                    inst->span);
        return;
      }
      case InstKind::CmpInst: {
        if (ir::is<ir::FloatType>(inst->operands[0]->ty)) {
          // Never folded into a branch.
          auto destination = destination_for(inst, inst->ty);
          auto left = in_register(inst->operands[0], inst->span);
          auto right = in_register(inst->operands[1], inst->span);
          asm_.float_compare(condition_for(inst->pred), destination, left, right, bits_of(inst->operands[0]->ty),
                             inst->span);
          return;
        }
        // Read exactly once, a comparison is folded into the branch reading it.
        if (folded_into_branch(func, inst))
          return;
        auto destination = destination_for(inst, inst->ty);
        lower_comparison(asm_, inst, *this, destination);
        return;
      }
      case InstKind::SyscallInst: {
        // The kernel names the registers.
        const auto& calls = syscalls();
        std::vector<Move> handed;
        handed.push_back(Move{ info.view(calls.number.phys()->unit, 64), value(inst->operands[0], or_invalid(span)) });
        for (std::size_t at = 1; at < inst->operands.size(); ++at)
          handed.push_back(Move{ calls.arguments[at - 1], value(inst->operands[at], or_invalid(span)) });
        for (const auto& move : sequenced(handed, [this](Reg like) { return asm_.temporary(like); }))
          asm_.loadreg(move.into, move.source, or_invalid(span));
        std::vector<Reg> clobbers{ calls.answer };
        clobbers.insert(clobbers.end(), calls.clobbers.begin(), calls.clobbers.end());
        std::vector<Reg> reads;
        for (const auto& move : handed)
          reads.push_back(move.into);
        asm_.kernel(calls.enter, or_invalid(span), clobbers, reads);
        auto taken_back = new_value(inst->ty, info);
        held[inst] = taken_back;
        asm_.loadreg(taken_back, mcreg(calls.answer), or_invalid(span));
        return;
      }
      case InstKind::CallInst:
        lower_call(inst, span);
        return;
      case InstKind::UnreachableInst:
        asm_.op(mc::ops::trap, Reg{}, {}, inst->span);
        return;
      case InstKind::BrInst:
      case InstKind::CondBrInst:
      case InstKind::SwitchInst:
        try {
          lower_branch(asm_, func, labels, index, inst, *this, zero_immediate);
        } catch (const UnsupportedBranch& unsupported) {
          throw UnsupportedOperation(unsupported.what_, valid(unsupported.span));
        }
        return;
      default:
        break;
      }
      throw UnsupportedOperation(std::format("the instruction '{}'", inst->opcode()), span);
    }


    void Lowering::lower_float_binary(const ir::Instruction* inst, std::optional<Span> span)
    {
      auto result = ir::as<ir::ResultType>(inst->ty);
      const ir::Type* answer = result != nullptr ? result->ok : inst->ty;
      auto bits = bits_of(answer);
      const mc::Op* operation = float_operation(inst->bin);
      if (operation == nullptr) [[unlikely]]
        throw UnsupportedOperation(std::format("'{}' on a floating-point value", pl4g::describe(inst->bin)), span);
      auto destination = new_value(answer, info, inst == returned ? result_register(answer, cconv) : nullptr);
      held[inst] = destination;
      auto divisor = in_register(inst->operands[1], inst->span);
      Reg failed;
      if (result != nullptr) {
        // The one pair a division has no answer for is a divisor of nought.
        failed = new_value(ir::bool_type(), info,
                           inst == returned ? result_register(ir::bool_type(), cconv, 1) : nullptr);
        extra[inst] = { failed };
        ir::FloatConst zero(answer, 0.0);
        auto nought = floating(&zero, inst->span);
        asm_.float_compare(Condition::eq, failed, divisor, mcreg(nought), bits, inst->span);
      }
      auto left = in_register(inst->operands[0], inst->span);
      asm_.float_op(operation, destination, left, divisor, bits, inst->span);
      if (operation == mc::ops::larger || operation == mc::ops::smaller)
        return;
      // An infinity or a not-a-number is an answer the operation did not have.
      auto carry_on = asm_.reserve_label("is.finite");
      if (! failed.is_none())
        asm_.branch(Condition::ne, mcreg(failed), zero_immediate, carry_on, inst->span);
      asm_.branch_if_finite(destination, bits, carry_on, inst->span);
      Told(*this, std::format("{} with no number for an answer", operation_name(inst->bin)), inst->span,
           statuses::NO_ANSWER)
        .out_of_range(asm_, inst->span);
      asm_.block(carry_on);
    }


    void Lowering::lower_integer_binary(const ir::Instruction* inst, std::optional<Span> span)
    {
      auto op = inst->bin;
      if (ir::is<ir::PtrType>(inst->ty)) {
        // Arithmetic on an address goes unchecked: past its place it names no
        // place, and there is nothing to answer with.
        auto destination = destination_for(inst, inst->ty);
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        asm_.op(plain_operation(op), destination, { left, right }, inst->span);
        return;
      }
      if (is_trapping(op)) {
        auto destination = destination_for(inst, inst->ty);
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        Told fault(*this, std::format("{} that does not fit", operation_name(op)), inst->span);
        try {
          lower_trapping(asm_, op, inst->ty, left, right, destination, *this, 64, fault, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (is_saturating(op)) {
        auto destination = destination_for(inst, inst->ty);
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        try {
          lower_saturating(asm_, op, inst->ty, left, right, destination, *this, 64, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (is_shift(op)) {
        auto destination = destination_for(inst, inst->ty);
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        Told fault(*this, std::format("{} by more than the width of the type", operation_name(op)), inst->span);
        try {
          lower_shift(asm_, op, inst->ty, left, right, destination, *this, 64, fault, inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (is_division(op)) {
        // The number where there is one, and the truth value beside it.
        auto result = ir::as<ir::ResultType>(inst->ty);
        const ir::Type* answer = result != nullptr ? result->ok : inst->ty;
        auto destination = new_value(answer, info, inst == returned ? result_register(answer, cconv) : nullptr);
        auto failed = new_value(ir::bool_type(), info,
                                inst == returned ? result_register(ir::bool_type(), cconv, 1) : nullptr);
        held[inst] = destination;
        extra[inst] = { failed };
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        try {
          lower_division_result(asm_, op, answer, left, right, destination, failed, *this,
                                std::max(32, width_of(answer)), inst->span);
        } catch (const Unsupported& unsupported) {
          throw UnsupportedOperation(unsupported.what_, span);
        }
        return;
      }
      if (extremum_condition(op)) {
        auto destination = destination_for(inst, inst->ty);
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        lower_extremum(asm_, op, left, right, destination, *this, inst->span);
        return;
      }
      if (is_wrapping(op)) {
        auto destination = destination_for(inst, inst->ty);
        auto left = value(inst->operands[0], inst->span);
        auto right = value(inst->operands[1], inst->span);
        lower_wrapping(asm_, op, inst->ty, left, right, destination, 64, inst->span);
        return;
      }
      const mc::Op* operation = plain_operation(op);
      if (operation == nullptr) [[unlikely]]
        throw UnsupportedOperation(std::format("the operation '{}'", pl4g::describe(op)), span);
      auto destination = destination_for(inst, inst->ty);
      auto left = value(inst->operands[0], inst->span);
      auto right = value(inst->operands[1], inst->span);
      asm_.op(operation, destination, { left, right }, inst->span);
    }


    void Lowering::lower_rounding(const ir::Instruction* inst, Rounding how)
    {
      // Without Zfa, out to a whole number and back below the point where the
      // format has room for a fraction, the value itself above it.
      int bits = bits_of(inst->ty);
      auto destination = destination_for(inst, inst->ty);
      auto source = in_register(inst->operands[0], inst->span);
      if (rounds_in_one) {
        asm_.float_round(how, destination, source, bits, inst->span);
        return;
      }
      asm_.loadreg(destination, source, inst->span);
      Reg magnitude = info.new_virtual(registers().fpr, float_register_bits);
      asm_.float_abs(magnitude, source, bits, inst->span);
      Reg small = info.new_virtual(registers().gpr, 64);
      ir::FloatConst limit(inst->ty, whole_above(bits));
      auto bound = floating(&limit, inst->span);
      asm_.float_compare(Condition::slt, small, mcreg(magnitude), mcreg(bound), bits, inst->span);
      auto whole = asm_.reserve_label("already.whole");
      asm_.branch(Condition::eq, mcreg(small), zero_immediate, whole, inst->span);
      Reg counted = info.new_virtual(registers().gpr, 64);
      asm_.float_to_int(how, counted, source, bits, inst->span);
      Reg back = info.new_virtual(registers().fpr, float_register_bits);
      asm_.int_to_float(back, mcreg(counted), bits, inst->span);
      // Rounding never changes a sign, and the round trip loses minus nought.
      asm_.float_copysign(destination, mcreg(back), source, bits, inst->span);
      asm_.block(whole);
    }


    void Lowering::lower_store(const ir::Instruction* inst, std::optional<Span> span)
    {
      const ir::Value* address = inst->operands[1];
      ir::Value* written = inst->operands[2];
      if (ir::made_of_parts(written->ty)) {
        // One write per part, at the offsets a read uses.
        auto pieces = ir::leaves_of(written->ty);
        auto offsets = ir::leaf_offsets_of(written->ty, layout);
        for (std::size_t at = 0; at < pieces.size() && at < offsets.size(); ++at) {
          auto place = place_of(address, span, std::int64_t(offsets[at]), width_of(pieces[at]), is_signed(pieces[at]));
          asm_.store(place, mcreg(part_of(written, at, span)), inst->span);
        }
        return;
      }
      if (ir::is<ir::FloatType>(written->ty)) {
        auto place = place_of(address, span, 0, bits_of(written->ty));
        auto source = in_register(written, inst->span);
        asm_.store(place, source, inst->span);
        return;
      }
      auto place = place_of(address, span, 0, width_of(written->ty), is_signed(written->ty));
      MCOperand put;
      if (auto constant = number_of(written)) {
        bool sign = is_signed(written->ty);
        put = imm(constant->first, immediate_width(constant->first, sign), sign);
      } else
        put = mcreg(register_of(written, span));
      if (inst->ordering == ir::Ordering::release)
        asm_.release(place, put, inst->span);
      else
        asm_.store(place, put, inst->span);
    }


    void Lowering::lower_return(const ir::Instruction* inst, std::optional<Span> span)
    {
      if (inst->operands.empty()) {
        asm_.ret(inst->span);
        return;
      }
      ir::Value* value = inst->operands[0];
      const ir::Type* ty = value->ty;
      auto pieces = ir::leaves_of(ty);
      if (ir::made_of_parts(ty)) {
        std::vector<const mc::PhysReg*> places;
        try {
          places = result_places(cconv, ty);
        } catch (const TooManyArguments&) {
          throw UnsupportedOperation(
            std::format("answering with '{}', which wants more registers than the convention answers in",
                        ty->written()),
            span);
        }
        std::vector<Reg> answering;
        for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at)
          answering.push_back(as_argument(places[at], pieces[at], info));
        for (std::size_t at = 0; at < answering.size(); ++at)
          asm_.loadreg(answering[at], mcreg(part_of(value, at, span)), inst->span);
        asm_.ret(inst->span, answering);
        return;
      }
      if (ir::is<ir::FloatType>(ty)) {
        auto answer = result_register(ty, cconv);
        auto source = in_register(value, inst->span);
        asm_.loadreg(answer, source, inst->span);
        asm_.ret(inst->span, { answer });
        return;
      }
      if (! ir::is<ir::IntType>(ty) && ! ir::is<ir::BoolType>(ty) && ! ir::is<ir::EnumType>(ty)
          && ! ir::is<ir::PtrType>(ty) && ! ir::is<ir::SetType>(ty) && ! ir::is<ir::DictType>(ty)) [[unlikely]]
        throw UnsupportedOperation(std::format("returning a value of type '{}'", ty->written()), span);
      auto result = result_register(ty, cconv);
      if (auto constant = number_of(value))
        asm_.loadreg(result, imm(constant->first, 12, is_signed(ty)), inst->span);
      else
        asm_.loadreg(result, mcreg(register_of(value, span)), inst->span);
      asm_.ret(inst->span, { result });
    }


    void Lowering::lower_call(const ir::Instruction* inst, std::optional<Span> span)
    {
      const ir::Function* callee = inst->callee;
      // A call through a value: the convention is the one every function of
      // this language follows, nothing here knowing which callee it is.
      ir::Value* through = callee == nullptr ? inst->operands[0] : nullptr;
      auto given = inst->arguments();
      const auto& theirs = lookup_cconv(through != nullptr ? ir::default_cconv : std::string_view(callee->cconv));
      std::vector<const ir::Type*> types;
      for (auto argument : given)
        types.push_back(argument->ty);
      std::vector<std::vector<const mc::PhysReg*>> going;
      try {
        going = argument_places(theirs, types, through != nullptr || callee->attrs.chained);
      } catch (const TooManyArguments&) {
        throw UnsupportedOperation("a call with more arguments than the convention passes in registers", span);
      }
      // A parallel copy, a value perhaps being in the register another
      // argument is moved into.
      std::vector<Move> handed;
      for (std::size_t position = 0; position < given.size(); ++position) {
        ir::Value* argument = given[position];
        auto pieces = ir::leaves_of(argument->ty);
        if (ir::made_of_parts(argument->ty)) {
          for (std::size_t at = 0; at < pieces.size() && at < going[position].size(); ++at)
            handed.push_back(
              Move{ as_argument(going[position][at], pieces[at], info), mcreg(part_of(argument, at, span)) });
          continue;
        }
        auto into = as_argument(going[position][0], argument->ty, info);
        handed.push_back(Move{ into, value(argument, inst->span) });
      }
      Reg called;
      if (through != nullptr) {
        // Read by the call, so it goes into the parallel copy too.
        called = asm_.temporary(register_of(through, inst->span));
        handed.push_back(Move{ called, value(through, inst->span) });
      }
      for (const auto& move : sequenced(handed, [this](Reg like) { return asm_.temporary(like); }))
        asm_.loadreg(move.into, move.source, inst->span);
      auto destroyed = destroyed_by(through == nullptr ? callee : nullptr, theirs, info, known);
      std::vector<Reg> clobbers(destroyed.begin(), destroyed.end());
      std::vector<Reg> reads;
      for (const auto& move : handed)
        if (through == nullptr || move.into != called)
          reads.push_back(move.into);
      if (through != nullptr)
        asm_.call(MCOperand(mcreg(called)), inst->span, clobbers, reads);
      else
        asm_.call(ir::symbol_name(*callee), inst->span, clobbers, reads);
      if (ir::made_of_parts(inst->ty)) {
        auto pieces = ir::leaves_of(inst->ty);
        std::vector<const mc::PhysReg*> places;
        try {
          places = result_places(theirs, inst->ty);
        } catch (const TooManyArguments&) {
          throw UnsupportedOperation(
            std::format("a call answering with '{}', which wants more registers than the convention answers in",
                        inst->ty->written()),
            span);
        }
        std::vector<Reg> taken;
        for (std::size_t at = 0; at < pieces.size() && at < places.size(); ++at) {
          auto into = new_value(pieces[at], info);
          asm_.loadreg(into, mcreg(as_argument(places[at], pieces[at], info)), inst->span);
          taken.push_back(into);
        }
        held[inst] = taken[0];
        extra[inst] = std::vector<Reg>(taken.begin() + 1, taken.end());
      } else if (inst->ty != ir::void_type()) {
        auto destination =
          new_value(inst->ty, info, result_register(inst->ty, inst == returned ? cconv : theirs));
        held[inst] = destination;
        asm_.loadreg(destination, mcreg(result_register(inst->ty, theirs)), inst->span);
      }
    }


    void Lowering::lower_wrap(const ir::Instruction* inst)
    {
      // The answer's own registers, the truth value after them, and what the
      // error carries after that.
      auto result = static_cast<const ir::ResultType*>(inst->ty);
      const ir::Type* answer = result->ok;
      auto answer_parts = ir::parts_of(answer);
      auto slots = ir::leaves_of(inst->ty);
      bool simple = answer_parts.size() == 1;
      std::vector<Reg> taken;
      for (std::size_t index = 0; index < slots.size(); ++index)
        taken.push_back(new_value(slots[index], info,
                                  inst == returned && simple ? result_register(slots[index], cconv, index) : nullptr));
      held[inst] = taken[0];
      extra[inst] = std::vector<Reg>(taken.begin() + 1, taken.end());
      for (std::size_t at = 0; at < answer_parts.size(); ++at) {
        auto source = part_source(inst->operands[0], at, answer_parts[at], answer_parts.size(), inst->span);
        asm_.loadreg(taken[at], source, inst->span);
      }
      auto flag = value(inst->operands[1], inst->span);
      asm_.loadreg(taken[answer_parts.size()], flag, inst->span);
      if (inst->operands.size() > 2) {
        auto carried = ir::parts_of(result->err);
        for (std::size_t at = 0; at < carried.size(); ++at) {
          auto source = part_source(inst->operands[2], at, carried[at], carried.size(), inst->span);
          asm_.loadreg(taken[answer_parts.size() + 1 + at], source, inst->span);
        }
      }
    }

  } // anonymous namespace


  void lower_function(mc::Assembler& asm_, const ir::Function& func, const CallConvDesc& cconv,
                      mc::RegisterInfo& registers, Messages* messages, const SourceManager* sources,
                      Constants* constants, const Destroyed* known_clobbers, const ir::DataLayout& layout)
  {
    Lowering(asm_, func, cconv, registers, messages, sources, constants, known_clobbers, layout).run();
  }

} // namespace pl4g::target::riscv64
