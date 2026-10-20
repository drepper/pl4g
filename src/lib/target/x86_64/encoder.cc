// The x86-64 encoder.
#include "target/x86_64/encoder.hh"

#include <format>

#include "mc/operand.hh"
#include "target/x86_64/desc.hh"
#include "target/x86_64/regs.hh"


namespace pl4g::target::x86_64 {

  namespace {

    using mc::Bytes;
    using mc::MCFixup;
    using mc::MCImm;
    using mc::MCMem;
    using mc::MCOperand;
    using mc::MCReg;
    using mc::MCSymRef;
    using mc::Reg;

    using MaybeSpan = std::optional<Span>;

    // The encoding of rsp, which in the rm field means "a SIB byte follows" and
    // in the SIB index field means "no index"; and of rbp, which in several
    // places means "a displacement follows".
    constexpr int rsp_enc = 4;
    constexpr int rbp_enc = 5;

    int scale_bits(int scale)
    {
      switch (scale) {
      case 1: return 0;
      case 2: return 1;
      case 4: return 2;
      case 8: return 3;
      default: return -1;
      }
    }

    void append(Bytes& out, std::uint64_t value, int size)
    {
      for (int i = 0; i < size; ++i)
        out.push_back(std::uint8_t(value >> (8 * i)));
    }

    // `reg` as a physical register, or a refusal to encode it: once the
    // allocator has run, no virtual register reaches the encoder.
    const mc::PhysReg* assigned(Reg reg, MaybeSpan span)
    {
      if (reg.is_virtual()) [[unlikely]]
        throw EncodingError(std::format("virtual register %v{} reached the encoder; it has not been assigned",
                                        reg.virt()->ident),
                            span);
      auto phys = reg.phys();
      if (phys->enc() >= gpr_extended_first && phys->cls()->name == "gpr") [[unlikely]]
        throw EncodingError(std::format("register '{}' needs the extended register encodings, which this compiler "
                                        "does not emit yet",
                                        phys->name),
                            span);
      return phys;
    }

    // The physical register an operand names.
    const mc::PhysReg* reg_of(const MCOperand& operand, MaybeSpan span)
    {
      auto reg = std::get_if<MCReg>(&operand);
      if (reg == nullptr) [[unlikely]]
        throw EncodingError("a register operand was expected", span);
      return assigned(reg->reg, span);
    }

    const MCMem* mem_operand(const std::vector<MCOperand>& operands)
    {
      for (const auto& operand : operands)
        if (auto mem = std::get_if<MCMem>(&operand))
          return mem;
      return nullptr;
    }

    struct Rex {
      int w = 0, r = 0, x = 0, b = 0;
      bool needed = false;
    };

    // The W, R, X and B bits, and whether a REX prefix is needed at all.
    Rex rex_bits(const X86InstDesc& desc, const std::vector<MCOperand>& operands, MaybeSpan span)
    {
      Rex made;
      made.w = desc.opsize == OpSize::rexw ? 1 : 0;
      if (desc.modrm == ModRMUse::reg_rm && desc.reg_op)
        made.r = reg_of(operands[*desc.reg_op], span)->enc() >> 3;
      if (desc.plus_reg && desc.reg_op)
        made.b = reg_of(operands[*desc.reg_op], span)->enc() >> 3;
      else if (desc.rm_op) {
        const auto& rm = operands[*desc.rm_op];
        if (auto reg = std::get_if<MCReg>(&rm))
          made.b = assigned(reg->reg, span)->enc() >> 3;
        else if (auto mem = std::get_if<MCMem>(&rm)) {
          if (! mem->base.is_none())
            made.b = assigned(mem->base, span)->enc() >> 3;
          if (! mem->index.is_none())
            made.x = assigned(mem->index, span)->enc() >> 3;
        }
      }
      bool needs_empty = false;
      for (const auto& operand : operands)
        if (auto reg = std::get_if<MCReg>(&operand); reg != nullptr && reg->reg.is_physical()
                                                        && rex_required_byte_reg(reg->reg.phys()->name))
          needs_empty = true;
      made.needed = made.w || made.r || made.x || made.b || needs_empty;
      return made;
    }

    // Refuse the high-byte registers, which no REX-carrying form may name.
    void check_high_byte(const std::vector<MCOperand>& operands, MaybeSpan span)
    {
      for (const auto& operand : operands)
        if (auto reg = std::get_if<MCReg>(&operand);
            reg != nullptr && reg->reg.is_physical() && high_byte_reg(reg->reg.phys()->name)) [[unlikely]]
          throw EncodingError(std::format("register '{}' cannot be used by an instruction that needs a REX prefix",
                                          reg->reg.phys()->name),
                              span);
    }

    void emit_modrm(Bytes& out, int mod, int reg, int rm)
    {
      out.push_back(std::uint8_t((mod << 6) | ((reg & 7) << 3) | (rm & 7)));
    }

    void emit_sib(Bytes& out, int scale, int index, int base)
    {
      out.push_back(std::uint8_t((scale_bits(scale) << 6) | ((index & 7) << 3) | (base & 7)));
    }

    // The ModRM, SIB and displacement bytes for a memory operand.
    void emit_memory(Bytes& out, std::vector<MCFixup>& fixups, const MCMem& mem, int reg_field, int trailing,
                     MaybeSpan span)
    {
      if (mem.rip_relative) {
        emit_modrm(out, 0, reg_field, rbp_enc);
        if (mem.disp_sym != nullptr) {
          // A displacement beside a symbol is part of what the fixup aims at:
          // the whole of the address goes in the one field.
          auto target = mem.disp == 0 ? mem.disp_sym : mc::bin_expr('+', mem.disp_sym, mc::const_expr(mem.disp));
          MCFixup fixup;
          fixup.offset = std::int64_t(out.size());
          fixup.kind = &mc::pcrel32;
          fixup.target = std::move(target);
          fixup.trailing = trailing;
          fixup.span = span.value_or(invalid_span);
          fixups.push_back(std::move(fixup));
          append(out, 0, 4);
        } else
          append(out, std::uint64_t(mem.disp) & 0xFFFFFFFF, 4);
        return;
      }
      const mc::PhysReg* base = mem.base.is_none() ? nullptr : assigned(mem.base, span);
      const mc::PhysReg* index = mem.index.is_none() ? nullptr : assigned(mem.index, span);
      if (index != nullptr && index->enc() == rsp_enc) [[unlikely]]
        throw EncodingError("'rsp' cannot be used as an index register", span);
      if (scale_bits(mem.scale) < 0) [[unlikely]]
        throw EncodingError(std::format("a scale of {} is not encodable; it must be 1, 2, 4 or 8", mem.scale), span);
      if (base == nullptr) {
        // No base at all: the absolute and index-only forms both need a SIB
        // byte whose base field says "no base", and a full displacement.
        emit_modrm(out, 0, reg_field, rsp_enc);
        emit_sib(out, mem.scale, index != nullptr ? index->enc() : rsp_enc, rbp_enc);
        append(out, std::uint64_t(mem.disp) & 0xFFFFFFFF, 4);
        return;
      }
      bool needs_sib = index != nullptr || (base->enc() & 7) == rsp_enc;
      // rbp and r13 have no zero-displacement form, so they always carry one.
      int mod;
      if (mem.disp == 0 && (base->enc() & 7) != rbp_enc)
        mod = 0;
      else if (-128 <= mem.disp && mem.disp <= 127)
        mod = 1;
      else
        mod = 2;
      emit_modrm(out, mod, reg_field, needs_sib ? rsp_enc : base->enc());
      if (needs_sib)
        emit_sib(out, mem.scale, index != nullptr ? index->enc() : rsp_enc, base->enc());
      if (mod == 1)
        append(out, std::uint64_t(mem.disp) & 0xFF, 1);
      else if (mod == 2)
        append(out, std::uint64_t(mem.disp) & 0xFFFFFFFF, 4);
    }

    // An immediate of the given width.  A value the field cannot hold is
    // refused, storing it cut short would mean a number nobody named.
    void emit_imm(Bytes& out, const MCOperand& operand, int bits, MaybeSpan span)
    {
      auto imm = std::get_if<MCImm>(&operand);
      if (imm == nullptr) [[unlikely]]
        throw EncodingError("an immediate operand was expected", span);
      __int128 low = -(__int128(1) << (bits - 1));
      __int128 high = __int128(1) << bits;
      if (! (low <= imm->value && imm->value < high)) [[unlikely]]
        throw EncodingError(std::format("an immediate of {} does not fit the {}-bit field of this instruction",
                                        mc::decimal(imm->value), bits),
                            span);
      unsigned __int128 masked = static_cast<unsigned __int128>(imm->value)
                                 & ((static_cast<unsigned __int128>(1) << bits) - 1);
      for (int i = 0; i < bits / 8; ++i)
        out.push_back(std::uint8_t(masked >> (8 * i)));
    }

    // A branch displacement, leaving a fixup to patch it later.
    void emit_rel(Bytes& out, std::vector<MCFixup>& fixups, const MCOperand& operand, int bits, MaybeSpan span)
    {
      auto ref = std::get_if<MCSymRef>(&operand);
      if (ref == nullptr) [[unlikely]]
        throw EncodingError("a symbol reference was expected as the branch target", span);
      MCFixup fixup;
      fixup.offset = std::int64_t(out.size());
      fixup.kind = bits == 32 ? &mc::pcrel32 : &mc::pcrel8;
      fixup.target = ref->expr;
      fixup.trailing = 0;
      fixup.span = span.value_or(invalid_span);
      fixups.push_back(std::move(fixup));
      append(out, 0, bits / 8);
    }

    // What the `pp` field of a VEX prefix says, by the prefix byte it stands
    // for; no prefix is nought.
    std::optional<int> vex_pp(std::optional<int> prefix)
    {
      if (! prefix)
        return 0;
      switch (*prefix) {
      case 0x66: return 1;
      case 0xF3: return 2;
      case 0xF2: return 3;
      default: return std::nullopt;
      }
    }

    // The VEX prefix for one instruction.  Every one of R, X, B and `vvvv` is
    // stored turned round, which is what makes a prefix naming none of the
    // extended registers come out as the two-byte form.
    void emit_vex(Bytes& out, const X86InstDesc& desc, const std::vector<MCOperand>& operands, MaybeSpan span)
    {
      if (! desc.vex) [[unlikely]]
        throw EncodingError("a VEX row that says nothing about its prefix", span);
      int w = desc.opsize == OpSize::rexw ? 1 : 0;
      int r = 0, x = 0, b = 0;
      if (desc.reg_op)
        r = reg_of(operands[*desc.reg_op], span)->enc() >> 3;
      if (desc.rm_op) {
        const auto& rm = operands[*desc.rm_op];
        if (auto reg = std::get_if<MCReg>(&rm))
          b = assigned(reg->reg, span)->enc() >> 3;
        else if (auto mem = std::get_if<MCMem>(&rm)) {
          if (! mem->base.is_none())
            b = assigned(mem->base, span)->enc() >> 3;
          if (! mem->index.is_none())
            x = assigned(mem->index, span)->enc() >> 3;
        }
      }
      int named = 0;
      if (desc.vex->vvvv_op)
        named = reg_of(operands[*desc.vex->vvvv_op], span)->enc();
      int length = desc.vex->length == 256 ? 1 : 0;
      auto pp = vex_pp(desc.mandatory_prefix);
      if (! pp) [[unlikely]]
        throw EncodingError("a VEX row whose mandatory prefix has no pp field", span);
      int last = ((~named & 0xF) << 3) | (length << 2) | *pp;
      if (x == 0 && b == 0 && w == 0 && desc.map == OpMap::m0f) {
        // The two-byte form, which says only what it has to.
        out.push_back(0xC5);
        out.push_back(std::uint8_t(((~r & 1) << 7) | last));
        return;
      }
      out.push_back(0xC4);
      out.push_back(std::uint8_t(((~r & 1) << 7) | ((~x & 1) << 6) | ((~b & 1) << 5) | int(desc.map)));
      out.push_back(std::uint8_t((w << 7) | last));
    }

    std::string_view enc_name(EncKind kind)
    {
      switch (kind) {
      case EncKind::legacy: return "legacy";
      case EncKind::rex2: return "rex2";
      case EncKind::vex: return "vex";
      case EncKind::evex: return "evex";
      }
      return "";
    }

  } // anonymous namespace


  std::pair<mc::Bytes, std::vector<mc::MCFixup>> encode(const mc::MCInst& inst)
  {
    const auto& desc = dynamic_cast<const X86InstDesc&>(*inst.desc);
    const auto& operands = inst.operands;
    MaybeSpan span;
    if (inst.span.is_valid())
      span = inst.span;
    Bytes out;
    std::vector<MCFixup> fixups;

    // -- phase 1: the legacy prefixes ------------------------------------------
    // A VEX prefix carries both of these in fields of its own.
    if (auto mem = mem_operand(operands); mem != nullptr && mem->seg != nullptr)
      out.push_back(std::uint8_t(segment_prefix(mem->seg->name)));
    if (inst.prefixes.contains("lock"))
      out.push_back(0xF0);
    if (desc.enc == EncKind::legacy) {
      if (desc.mandatory_prefix)
        out.push_back(std::uint8_t(*desc.mandatory_prefix));
      else if (desc.opsize == OpSize::p66)
        out.push_back(0x66);
    }

    // -- phase 2: the prefix carrying the register extensions ------------------
    if (desc.enc == EncKind::legacy) {
      auto rex = rex_bits(desc, operands, span);
      if (rex.needed) {
        check_high_byte(operands, span);
        out.push_back(std::uint8_t(0x40 | (rex.w << 3) | (rex.r << 2) | (rex.x << 1) | rex.b));
      }
    } else if (desc.enc == EncKind::vex)
      emit_vex(out, desc, operands, span);
    else [[unlikely]]
      throw EncodingError(std::format("the {} encodings are not emitted yet", enc_name(desc.enc)), span);

    // -- phase 3: the opcode map escape and the opcode -------------------------
    if (desc.enc == EncKind::legacy) {
      switch (desc.map) {
      case OpMap::primary: break;
      case OpMap::m0f: out.push_back(0x0F); break;
      case OpMap::m0f38: out.push_back(0x0F); out.push_back(0x38); break;
      case OpMap::m0f3a: out.push_back(0x0F); out.push_back(0x3A); break;
      }
    }
    int opcode = desc.opcode;
    if (desc.plus_reg) {
      if (! desc.reg_op) [[unlikely]]
        throw EncodingError("a row with plus_reg names no register operand", span);
      opcode |= reg_of(operands[*desc.reg_op], span)->enc() & 7;
    }
    out.push_back(std::uint8_t(opcode));

    // -- phase 4: ModRM, SIB and the displacement ------------------------------
    if (desc.modrm_byte)
      // The whole byte, written down: it is part of the opcode.
      out.push_back(std::uint8_t(*desc.modrm_byte));
    else if (desc.modrm != ModRMUse::none) {
      if (! desc.rm_op) [[unlikely]]
        throw EncodingError("a row with a ModRM byte names no rm operand", span);
      int reg_field;
      if (desc.modrm == ModRMUse::ext_rm) {
        if (! desc.ext) [[unlikely]]
          throw EncodingError("a row with an extension digit does not give one", span);
        reg_field = *desc.ext;
      } else {
        if (! desc.reg_op) [[unlikely]]
          throw EncodingError("a row with a ModRM byte names no reg operand", span);
        reg_field = reg_of(operands[*desc.reg_op], span)->enc();
      }
      const auto& rm = operands[*desc.rm_op];
      if (auto reg = std::get_if<MCReg>(&rm))
        emit_modrm(out, 3, reg_field, assigned(reg->reg, span)->enc());
      else if (auto mem = std::get_if<MCMem>(&rm))
        emit_memory(out, fixups, *mem, reg_field, desc.imm_bits ? *desc.imm_bits / 8 : 0, span);
      else [[unlikely]]
        throw EncodingError("a register or memory operand was expected", span);
    }

    // -- phase 5: the immediate or the branch displacement ---------------------
    if (desc.imm_op) {
      if (! desc.imm_bits) [[unlikely]]
        throw EncodingError("a row with an immediate does not give its width", span);
      emit_imm(out, operands[*desc.imm_op], *desc.imm_bits, span);
    }
    if (desc.rel_op) {
      if (! desc.rel_bits) [[unlikely]]
        throw EncodingError("a row with a branch target does not give its width", span);
      emit_rel(out, fixups, operands[*desc.rel_op], *desc.rel_bits, span);
    }
    return { std::move(out), std::move(fixups) };
  }

} // namespace pl4g::target::x86_64
