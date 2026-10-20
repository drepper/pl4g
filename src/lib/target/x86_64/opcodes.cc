// The x86-64 encoding table.  Each row states how one form of one instruction
// is encoded; the rows are pypl4g's, in pypl4g's order, which is the order the
// table is searched in.
#include "target/x86_64/opcodes.hh"

#include <deque>
#include <vector>

#include "target/x86_64/regs.hh"


namespace pl4g::target::x86_64 {

  namespace {

    using mc::InstFlags;
    using mc::OperandKind;
    using mc::OperandRole;
    using mc::OperandSpec;

    // A slot of the given kinds, class and width, and the width of the place
    // where it takes one in memory at a different width.
    OperandSpec spec(OperandKind kinds, const mc::RegClass* rclass, std::optional<int> bits,
                     std::optional<int> mem_bits = std::nullopt)
    {
      return { .kinds = kinds, .rclass = rclass, .bits = bits, .fixed = nullptr, .mem_bits = mem_bits,
               .imm_max = std::nullopt, .imm_min = std::nullopt };
    }

    // A general-purpose register of the given width.
    OperandSpec r(int bits) { return spec(OperandKind::reg, registers().gpr, bits); }

    // A general-purpose register or a memory operand of the given width.
    OperandSpec rm(int bits) { return spec(OperandKind::reg | OperandKind::mem, registers().gpr, bits); }

    // An immediate of at most the given width.
    OperandSpec imm(int bits) { return spec(OperandKind::imm, nullptr, bits); }

    // A branch target, given as a symbol reference.
    OperandSpec rel(int bits) { return spec(OperandKind::rel | OperandKind::sym, nullptr, bits); }

    // The one register a variable shift takes its count in.
    OperandSpec cl() { return spec(OperandKind::reg, registers().gpr, 8); }

    // One of the vector registers, named at the width the whole of it has.
    OperandSpec x() { return spec(OperandKind::reg, registers().vec, 128); }

    // A vector register, or a place in memory holding `bits` of it: the
    // register named as the whole of one, the place holding exactly the format
    // the mnemonic names.
    OperandSpec xm(int bits = 64)
    {
      return spec(OperandKind::reg | OperandKind::mem, registers().vec, 128, bits);
    }

    // One of the vector registers, named at the wider width the newer levels
    // give it.
    OperandSpec y() { return spec(OperandKind::reg, registers().vec, 256); }

    // The same, or thirty-two bytes of memory.
    OperandSpec ym() { return spec(OperandKind::reg | OperandKind::mem, registers().vec, 256, 256); }

    // A memory operand, of any width or of the one named.
    OperandSpec mem(std::optional<int> bits = std::nullopt) { return spec(OperandKind::mem, nullptr, bits); }

    // This architecture's arithmetic takes two operands, so the destination
    // is also the first source; a comparison reads both and writes only the
    // flags; the newer forms name their destination apart from both sources.
    const std::vector<OperandRole> accumulate{ OperandRole::def_use, OperandRole::use };
    const std::vector<OperandRole> reads_both{ OperandRole::use, OperandRole::use };
    const std::vector<OperandRole> three_operand{ OperandRole::def, OperandRole::use, OperandRole::use };

    // One row, said field by field as pypl4g's keyword arguments say it.
    class Row {
    public:
      Row(std::string mnemonic, std::vector<OperandSpec> operands)
      {
        d.mnemonic = std::move(mnemonic);
        d.operands = std::move(operands);
      }
      Row& opcode(int v) { d.opcode = v; return *this; }
      Row& map(OpMap v) { d.map = v; return *this; }
      Row& enc(EncKind v) { d.enc = v; return *this; }
      Row& modrm_byte(int v) { d.modrm_byte = v; return *this; }
      Row& opsize(OpSize v) { d.opsize = v; return *this; }
      Row& mandatory_prefix(int v) { d.mandatory_prefix = v; return *this; }
      Row& modrm(ModRMUse v) { d.modrm = v; return *this; }
      Row& ext(int v) { d.ext = v; return *this; }
      Row& reg_op(int v) { d.reg_op = v; return *this; }
      Row& rm_op(int v) { d.rm_op = v; return *this; }
      Row& plus_reg() { d.plus_reg = true; return *this; }
      Row& imm_op(int v) { d.imm_op = v; return *this; }
      Row& imm_bits(int v) { d.imm_bits = v; return *this; }
      Row& rel_op(int v) { d.rel_op = v; return *this; }
      Row& rel_bits(int v) { d.rel_bits = v; return *this; }
      Row& vex(int length, int vvvv_op)
      {
        VexInfo info;
        info.length = length;
        if (vvvv_op >= 0)
          info.vvvv_op = vvvv_op;
        d.vex = info;
        return *this;
      }
      Row& implicit_defs(std::vector<const mc::PhysReg*> v) { d.implicit_defs = std::move(v); return *this; }
      Row& implicit_uses(std::vector<const mc::PhysReg*> v) { d.implicit_uses = std::move(v); return *this; }
      Row& flags(InstFlags v) { d.flags = v; return *this; }
      Row& est_size(int v) { d.est_size = v; return *this; }
      Row& roles(std::vector<OperandRole> v) { d.roles = std::move(v); return *this; }

      X86InstDesc d;
    };

    Row row(std::string mnemonic, std::vector<OperandSpec> operands)
    {
      return Row(std::move(mnemonic), std::move(operands));
    }

    std::vector<Row> rows()
    {
      auto& regs = registers();
      return {
        // mov r32, imm32                     B8+rd id
        row("mov", { r(32), imm(32) }).opcode(0xB8).plus_reg().reg_op(0).imm_op(1).imm_bits(32).flags(InstFlags::zext32)
          .est_size(5),
        // mov r64, imm64                     REX.W B8+rd io
        row("mov", { r(64), imm(64) }).opcode(0xB8).plus_reg().reg_op(0).opsize(OpSize::rexw).imm_op(1).imm_bits(64)
          .est_size(10),
        // mov r/m64, imm32 (sign extended)   REX.W C7 /0 id
        row("mov", { rm(64), imm(32) }).opcode(0xC7).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
            .imm_op(1)
          .imm_bits(32).est_size(7),
        // mov r/m8, imm8                     C6 /0 ib
        row("mov", { rm(8), imm(8) }).opcode(0xC6).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1).imm_bits(8)
          .est_size(3),
        // mov r/m16, imm16                   66 C7 /0 iw
        row("mov", { rm(16), imm(16) }).opcode(0xC7).opsize(OpSize::p66).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1)
          .imm_bits(16).est_size(5),
        // mov r/m32, imm32                   C7 /0 id
        row("mov", { rm(32), imm(32) }).opcode(0xC7).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1).imm_bits(32)
          .est_size(6),
        // mov r/m16, r16                     66 89 /r
        row("mov", { rm(16), r(16) }).opcode(0x89).opsize(OpSize::p66).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .est_size(3).flags(InstFlags::move),
        // mov r/m8, r8                       88 /r
        row("mov", { rm(8), r(8) }).opcode(0x88).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).est_size(2)
          .flags(InstFlags::move),
        // mov r8, r/m8                       8A /r
        row("mov", { r(8), rm(8) }).opcode(0x8A).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).est_size(2)
          .flags(InstFlags::move),
        // mov r/m32, r32                     89 /r
        row("mov", { rm(32), r(32) }).opcode(0x89).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .flags(InstFlags::move | InstFlags::zext32).est_size(2),
        // mov r32, r/m32                     8B /r   (the load direction)
        row("mov", { r(32), rm(32) }).opcode(0x8B).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .flags(InstFlags::move | InstFlags::zext32).est_size(2),
        // mov r/m64, r64                     REX.W 89 /r
        row("mov", { rm(64), r(64) }).opcode(0x89).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .est_size(3).flags(InstFlags::move),
        // mov r64, r/m64                     REX.W 8B /r
        row("mov", { r(64), rm(64) }).opcode(0x8B).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .est_size(3).flags(InstFlags::move),
        // xor r/m32, r32                     31 /r
        row("xor", { rm(32), r(32) }).opcode(0x31).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .flags(InstFlags::zext32).est_size(2).roles(accumulate),
        // xor r/m64, r64                     REX.W 31 /r
        row("xor", { rm(64), r(64) }).opcode(0x31).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // add r/m8, r8                       00 /r
        // The narrow widths are here because arithmetic on a narrow type is done at
        // that width: what the flags then say is whether the answer went past the
        // end of *that* type, which is the question being asked.
        row("add", { rm(8), r(8) }).opcode(0x00).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .est_size(2).roles(accumulate),
        // add r/m16, r16                     66 01 /r
        row("add", { rm(16), r(16) }).opcode(0x01).opsize(OpSize::p66).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // sub r/m8, r8                       28 /r
        row("sub", { rm(8), r(8) }).opcode(0x28).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .est_size(2).roles(accumulate),
        // sub r/m16, r16                     66 29 /r
        row("sub", { rm(16), r(16) }).opcode(0x29).opsize(OpSize::p66).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // add r/m32, r32                     01 /r
        row("add", { rm(32), r(32) }).opcode(0x01).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .flags(InstFlags::zext32).est_size(2).roles(accumulate),
        // add r/m64, r64                     REX.W 01 /r
        row("add", { rm(64), r(64) }).opcode(0x01).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // sub r/m32, r32                     29 /r
        row("sub", { rm(32), r(32) }).opcode(0x29).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .flags(InstFlags::zext32).est_size(2).roles(accumulate),
        // sub r/m64, r64                     REX.W 29 /r
        row("sub", { rm(64), r(64) }).opcode(0x29).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // movzx r32, r/m8                    0F B6 /r
        row("movzx", { r(32), rm(8) }).opcode(0xB6).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .flags(InstFlags::zext32).est_size(3),
        // movzx r32, r/m16                   0F B7 /r
        row("movzx", { r(32), rm(16) }).opcode(0xB7).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .flags(InstFlags::zext32).est_size(3),
        // movsx r32, r/m8                    0F BE /r
        row("movsx", { r(32), rm(8) }).opcode(0xBE).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .flags(InstFlags::zext32).est_size(3),
        // movsx r32, r/m16                   0F BF /r
        row("movsx", { r(32), rm(16) }).opcode(0xBF).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .flags(InstFlags::zext32).est_size(3),
        // movsx r64, r/m8                    REX.W 0F BE /r
        row("movsx", { r(64), rm(8) }).opcode(0xBE).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4),
        // movsx r64, r/m16                   REX.W 0F BF /r
        row("movsx", { r(64), rm(16) }).opcode(0xBF).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4),
        // movsxd r64, r/m32                  REX.W 63 /r
        row("movsxd", { r(64), rm(32) }).opcode(0x63).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .est_size(4),
        // lea r64, m                         REX.W 8D /r
        row("lea", { r(64), mem() }).opcode(0x8D).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .est_size(7),
        // and r/m32, r32                     21 /r
        row("and", { rm(32), r(32) }).opcode(0x21).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .flags(InstFlags::zext32).est_size(2).roles(accumulate),
        // and r/m64, r64                     REX.W 21 /r
        // and r/m32, imm32                   81 /4 id
        row("and", { rm(32), imm(32) }).opcode(0x81).modrm(ModRMUse::ext_rm).ext(4).rm_op(0).imm_op(1).imm_bits(32)
          .implicit_defs({ regs.eflags }).flags(InstFlags::zext32).est_size(6).roles(accumulate),
        // and r/m32, imm8 (sign extended)    83 /4 ib
        row("and", { rm(32), imm(8) }).opcode(0x83).modrm(ModRMUse::ext_rm).ext(4).rm_op(0).imm_op(1).imm_bits(8)
          .implicit_defs({ regs.eflags }).flags(InstFlags::zext32).est_size(3).roles(accumulate),
        row("and", { rm(64), r(64) }).opcode(0x21).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // or r/m32, r32                      09 /r
        row("or", { rm(32), r(32) }).opcode(0x09).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .flags(InstFlags::zext32).est_size(2).roles(accumulate),
        // or r/m64, r64                      REX.W 09 /r
        row("or", { rm(64), r(64) }).opcode(0x09).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // not r/m32                          F7 /2
        row("not", { rm(32) }).opcode(0xF7).modrm(ModRMUse::ext_rm).ext(2).rm_op(0).flags(InstFlags::zext32).est_size(2)
          .roles({ OperandRole::def_use }),
        // not r/m64                          REX.W F7 /2
        row("not", { rm(64) }).opcode(0xF7).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(2).rm_op(0).est_size(3)
          .roles({ OperandRole::def_use }),
        // add r/m8, imm8                     80 /0 ib
        // The narrow immediate forms, for the same reason the narrow register forms
        // are here: arithmetic on a narrow type is done at that type's width.
        row("add", { rm(8), imm(8) }).opcode(0x80).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1).imm_bits(8)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // sub r/m8, imm8                     80 /5 ib
        row("sub", { rm(8), imm(8) }).opcode(0x80).modrm(ModRMUse::ext_rm).ext(5).rm_op(0).imm_op(1).imm_bits(8)
          .implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // add r/m16, imm8 (sign extended)    66 83 /0 ib
        row("add", { rm(16), imm(8) }).opcode(0x83).opsize(OpSize::p66).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1)
          .imm_bits(8).implicit_defs({ regs.eflags }).est_size(4).roles(accumulate),
        // add r/m16, imm16                   66 81 /0 iw
        row("add", { rm(16), imm(16) }).opcode(0x81).opsize(OpSize::p66).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1)
          .imm_bits(16).implicit_defs({ regs.eflags }).est_size(5).roles(accumulate),
        // sub r/m16, imm8 (sign extended)    66 83 /5 ib
        row("sub", { rm(16), imm(8) }).opcode(0x83).opsize(OpSize::p66).modrm(ModRMUse::ext_rm).ext(5).rm_op(0).imm_op(1)
          .imm_bits(8).implicit_defs({ regs.eflags }).est_size(4).roles(accumulate),
        // sub r/m16, imm16                   66 81 /5 iw
        row("sub", { rm(16), imm(16) }).opcode(0x81).opsize(OpSize::p66).modrm(ModRMUse::ext_rm).ext(5).rm_op(0).imm_op(1)
          .imm_bits(16).implicit_defs({ regs.eflags }).est_size(5).roles(accumulate),
        // add r/m64, imm8 (sign extended)    REX.W 83 /0 ib
        row("add", { rm(64), imm(8) }).opcode(0x83).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).imm_op(1)
          .imm_bits(8).implicit_defs({ regs.eflags }).est_size(4).roles(accumulate),
        // add r/m64, imm32 (sign extended)   REX.W 81 /0 id
        row("add", { rm(64), imm(32) }).opcode(0x81).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
            .imm_op(1)
          .imm_bits(32).implicit_defs({ regs.eflags }).est_size(7).roles(accumulate),
        // sub r/m64, imm8 (sign extended)    REX.W 83 /5 ib
        row("sub", { rm(64), imm(8) }).opcode(0x83).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(5).rm_op(0).imm_op(1)
          .imm_bits(8).implicit_defs({ regs.eflags }).est_size(4).roles(accumulate),
        // sub r/m64, imm32 (sign extended)   REX.W 81 /5 id
        row("sub", { rm(64), imm(32) }).opcode(0x81).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(5).rm_op(0)
            .imm_op(1)
          .imm_bits(32).implicit_defs({ regs.eflags }).est_size(7).roles(accumulate),
        // cmp r/m32, r32                     39 /r
        row("cmp", { rm(32), r(32) }).opcode(0x39).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .est_size(2).roles(reads_both),
        // cmp r/m64, r64                     REX.W 39 /r
        row("cmp", { rm(64), r(64) }).opcode(0x39).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(reads_both),
        // cmp r/m8, imm8                     80 /7 ib
        row("cmp", { rm(8), imm(8) }).opcode(0x80).modrm(ModRMUse::ext_rm).ext(7).rm_op(0).imm_op(1).imm_bits(8)
          .implicit_defs({ regs.eflags }).est_size(3).roles(reads_both),
        // cmp r/m32, imm32                   81 /7 id
        row("cmp", { rm(32), imm(32) }).opcode(0x81).modrm(ModRMUse::ext_rm).ext(7).rm_op(0).imm_op(1).imm_bits(32)
          .implicit_defs({ regs.eflags }).est_size(6).roles(reads_both),
        // cmp r/m64, imm32 (sign extended)   REX.W 81 /7 id
        row("cmp", { rm(64), imm(32) }).opcode(0x81).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(7).rm_op(0)
            .imm_op(1)
          .imm_bits(32).implicit_defs({ regs.eflags }).est_size(7).roles(reads_both),
        // test r/m32, r32                    85 /r
        row("test", { rm(32), r(32) }).opcode(0x85).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).implicit_defs({ regs.eflags })
          .est_size(2).roles(reads_both),
        // test r/m64, r64                    REX.W 85 /r
        row("test", { rm(64), r(64) }).opcode(0x85).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0)
          .implicit_defs({ regs.eflags }).est_size(3).roles(reads_both),
        // The condition is part of the opcode, as it is for a jump, so one row per
        // condition.  What is written is a byte, and only a byte: the rest of the
        // register keeps whatever it held, which is why a truth value is widened
        // into it afterwards rather than being taken from here directly.
        // sete r/m8                        0F 94 /0
        row("sete", { rm(8) }).opcode(0x94).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setne r/m8                       0F 95 /0
        row("setne", { rm(8) }).opcode(0x95).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setl r/m8                        0F 9C /0
        row("setl", { rm(8) }).opcode(0x9C).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setle r/m8                       0F 9E /0
        row("setle", { rm(8) }).opcode(0x9E).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setg r/m8                        0F 9F /0
        row("setg", { rm(8) }).opcode(0x9F).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setge r/m8                       0F 9D /0
        row("setge", { rm(8) }).opcode(0x9D).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setb r/m8                        0F 92 /0
        row("setb", { rm(8) }).opcode(0x92).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setbe r/m8                       0F 96 /0
        row("setbe", { rm(8) }).opcode(0x96).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // seta r/m8                        0F 97 /0
        row("seta", { rm(8) }).opcode(0x97).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setae r/m8                       0F 93 /0
        row("setae", { rm(8) }).opcode(0x93).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // imul r32, r/m32                    0F AF /r
        row("imul", { r(32), rm(32) }).opcode(0xAF).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .implicit_defs({ regs.eflags }).flags(InstFlags::zext32).est_size(3).roles(accumulate),
        // imul r64, r/m64                    REX.W 0F AF /r
        row("imul", { r(64), rm(64) }).opcode(0xAF).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).implicit_defs({ regs.eflags }).est_size(4).roles(accumulate),
        // cmove r64, r/m64                   REX.W 0F 44 /r
        row("cmove", { r(64), rm(64) }).opcode(0x44).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovne r64, r/m64                  REX.W 0F 45 /r
        row("cmovne", { r(64), rm(64) }).opcode(0x45).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovl r64, r/m64                   REX.W 0F 4C /r
        row("cmovl", { r(64), rm(64) }).opcode(0x4C).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovle r64, r/m64                  REX.W 0F 4E /r
        row("cmovle", { r(64), rm(64) }).opcode(0x4E).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovg r64, r/m64                   REX.W 0F 4F /r
        row("cmovg", { r(64), rm(64) }).opcode(0x4F).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovge r64, r/m64                  REX.W 0F 4D /r
        row("cmovge", { r(64), rm(64) }).opcode(0x4D).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovb r64, r/m64                   REX.W 0F 42 /r
        row("cmovb", { r(64), rm(64) }).opcode(0x42).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovbe r64, r/m64                  REX.W 0F 46 /r
        row("cmovbe", { r(64), rm(64) }).opcode(0x46).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmova r64, r/m64                   REX.W 0F 47 /r
        row("cmova", { r(64), rm(64) }).opcode(0x47).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // cmovae r64, r/m64                  REX.W 0F 43 /r
        row("cmovae", { r(64), rm(64) }).opcode(0x43).map(OpMap::m0f).opsize(OpSize::rexw).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).implicit_uses({ regs.eflags }).est_size(4).roles({ OperandRole::def_use, OperandRole::use }),
        // The division instructions take their dividend in a fixed pair of
        // registers and write their quotient and remainder to the same pair.  That
        // is said here as implicit uses and defs, which is all the allocator needs:
        // it already keeps a value out of a register whose life overlaps its own, so
        // the divisor cannot land in one of the two.
        // cdq                                99          (sign-extends eax into edx)
        row("cdq", {}).opcode(0x99).implicit_uses({ regs.rax }).implicit_defs({ regs.rdx }).est_size(1),
        // cqo                                REX.W 99
        row("cqo", {}).opcode(0x99).opsize(OpSize::rexw).implicit_uses({ regs.rax }).implicit_defs({ regs.rdx }).est_size(2),
        // idiv r/m32                         F7 /7
        row("idiv", { rm(32) }).opcode(0xF7).modrm(ModRMUse::ext_rm).ext(7).rm_op(0).implicit_uses({ regs.rax, regs.rdx })
          .implicit_defs({ regs.rax, regs.rdx, regs.eflags }).est_size(2).roles({ OperandRole::use }),
        // idiv r/m64                         REX.W F7 /7
        row("idiv", { rm(64) }).opcode(0xF7).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(7).rm_op(0)
          .implicit_uses({ regs.rax, regs.rdx }).implicit_defs({ regs.rax, regs.rdx, regs.eflags }).est_size(3)
          .roles({ OperandRole::use }),
        // div r/m32                          F7 /6
        row("div", { rm(32) }).opcode(0xF7).modrm(ModRMUse::ext_rm).ext(6).rm_op(0).implicit_uses({ regs.rax, regs.rdx })
          .implicit_defs({ regs.rax, regs.rdx, regs.eflags }).est_size(2).roles({ OperandRole::use }),
        // div r/m64                          REX.W F7 /6
        row("div", { rm(64) }).opcode(0xF7).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(6).rm_op(0)
          .implicit_uses({ regs.rax, regs.rdx }).implicit_defs({ regs.rax, regs.rdx, regs.eflags }).est_size(3)
          .roles({ OperandRole::use }),
        // Counting bits.  `popcnt` is in the second microarchitecture level and
        // `lzcnt` in the third, so which of them may be emitted is what the level
        // the program was built for says; a program built for the first gets the
        // sequence that stands in for them instead.
        // popcnt r64, r/m64                  F3 REX.W 0F B8 /r
        row("popcnt", { r(64), rm(64) }).opcode(0xB8).map(OpMap::m0f).mandatory_prefix(0xF3).opsize(OpSize::rexw)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).implicit_defs({ regs.eflags }).est_size(5),
        // lzcnt r64, r/m64                   F3 REX.W 0F BD /r
        row("lzcnt", { r(64), rm(64) }).opcode(0xBD).map(OpMap::m0f).mandatory_prefix(0xF3).opsize(OpSize::rexw)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).implicit_defs({ regs.eflags }).est_size(5),
        // The one-operand multiplies are the only way to see the upper half of a
        // product on this architecture.  They read one factor in a fixed register
        // and write both halves of the answer to a fixed pair, which is said here
        // the way the divisions above say it -- and needs nothing else, since a
        // value whose life overlaps a register the instruction writes is already
        // kept out of it.
        // mul r/m64                          REX.W F7 /4    (unsigned)
        row("mul", { rm(64) }).opcode(0xF7).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(4).rm_op(0)
          .implicit_uses({ regs.rax }).implicit_defs({ regs.rax, regs.rdx, regs.eflags }).est_size(3).roles({ OperandRole::use }),
        // imul r/m64                         REX.W F7 /5    (signed)
        row("imul", { rm(64) }).opcode(0xF7).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(5).rm_op(0)
          .implicit_uses({ regs.rax }).implicit_defs({ regs.rax, regs.rdx, regs.eflags }).est_size(3).roles({ OperandRole::use }),
        // The third microarchitecture level adds a shift that takes its count in any
        // register and writes somewhere else again: one instruction where the older
        // form is a move into the fixed register, a move of the value, and the
        // shift -- and no register held away from everything else while it waits.
        // The count is in the prefix's own register field, which is what lets these
        // name three registers in two operand bytes.
        // shlx r64a, r/m64, r64b             VEX.LZ.66.0F38.W1 F7 /r
        row("shlx", { r(64), rm(64), r(64) }).opcode(0xF7).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0x66)
          .opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(128, 2).est_size(5)
          .roles({ OperandRole::def, OperandRole::use, OperandRole::use }),
        // shrx r64a, r/m64, r64b             VEX.LZ.F2.0F38.W1 F7 /r
        row("shrx", { r(64), rm(64), r(64) }).opcode(0xF7).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0xF2)
          .opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(128, 2).est_size(5)
          .roles({ OperandRole::def, OperandRole::use, OperandRole::use }),
        // sarx r64a, r/m64, r64b             VEX.LZ.F3.0F38.W1 F7 /r
        row("sarx", { r(64), rm(64), r(64) }).opcode(0xF7).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0xF3)
          .opsize(OpSize::rexw).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(128, 2).est_size(5)
          .roles({ OperandRole::def, OperandRole::use, OperandRole::use }),
        // The variable-count shifts take their count in the low byte of one fixed
        // register, which is said here as an implicit use: the allocator then keeps
        // every other value out of it while the shift wants it.
        row("shl", { rm(32), cl() }).opcode(0xD3).modrm(ModRMUse::ext_rm).ext(4).rm_op(0).implicit_uses({ regs.rcx })
          .implicit_defs({ regs.eflags }).est_size(2).roles(accumulate),
        row("shl", { rm(64), cl() }).opcode(0xD3).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(4).rm_op(0)
          .implicit_uses({ regs.rcx }).implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        row("shr", { rm(32), cl() }).opcode(0xD3).modrm(ModRMUse::ext_rm).ext(5).rm_op(0).implicit_uses({ regs.rcx })
          .implicit_defs({ regs.eflags }).est_size(2).roles(accumulate),
        row("shr", { rm(64), cl() }).opcode(0xD3).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(5).rm_op(0)
          .implicit_uses({ regs.rcx }).implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        row("sar", { rm(32), cl() }).opcode(0xD3).modrm(ModRMUse::ext_rm).ext(7).rm_op(0).implicit_uses({ regs.rcx })
          .implicit_defs({ regs.eflags }).est_size(2).roles(accumulate),
        row("sar", { rm(64), cl() }).opcode(0xD3).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(7).rm_op(0)
          .implicit_uses({ regs.rcx }).implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        row("rol", { rm(32), cl() }).opcode(0xD3).modrm(ModRMUse::ext_rm).ext(0).rm_op(0).implicit_uses({ regs.rcx })
          .implicit_defs({ regs.eflags }).est_size(2).roles(accumulate),
        row("rol", { rm(64), cl() }).opcode(0xD3).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.rcx }).implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        row("ror", { rm(32), cl() }).opcode(0xD3).modrm(ModRMUse::ext_rm).ext(1).rm_op(0).implicit_uses({ regs.rcx })
          .implicit_defs({ regs.eflags }).est_size(2).roles(accumulate),
        row("ror", { rm(64), cl() }).opcode(0xD3).opsize(OpSize::rexw).modrm(ModRMUse::ext_rm).ext(1).rm_op(0)
          .implicit_uses({ regs.rcx }).implicit_defs({ regs.eflags }).est_size(3).roles(accumulate),
        // The scalar floating-point instructions of SSE2, which is in the base of
        // the x86-64 ABI: a binary that uses them requires nothing a binary that
        // does not would not already have.  Each names the width in its own opcode
        // through the prefix byte, so the register operand is the same either way.
        row("movss", { x(), xm(32) }).opcode(0x10).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        row("movsd", { x(), xm() }).opcode(0x10).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        row("addss", { x(), xm(32) }).opcode(0x58).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("addsd", { x(), xm() }).opcode(0x58).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        // The larger and the smaller of two, which these machines have as
        // instructions of their own.  What they answer where one of the two is not
        // a number differs between architectures; nothing here can be, an operation
        // whose answer is not a number having stopped the program where it arose.
        row("maxss", { x(), xm(32) }).opcode(0x5F).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("maxsd", { x(), xm() }).opcode(0x5F).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("minss", { x(), xm(32) }).opcode(0x5D).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("minsd", { x(), xm() }).opcode(0x5D).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        // The whole number a floating-point number rounds to.  These are SSE4.1,
        // which is what the architecture's second level promises and the first does
        // not; the immediate says which way to go, and the value four says to ask
        // the processor's own rounding mode rather than to name one.
        // roundss xmm, xmm/m32, imm8        66 0F 3A 0A /r ib
        row("roundss", { x(), xm(32), imm(8) }).opcode(0x0A).map(OpMap::m0f3a).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).imm_op(2).imm_bits(8).est_size(6)
          .roles({ OperandRole::def, OperandRole::use, OperandRole::use }),
        // roundsd xmm, xmm/m64, imm8        66 0F 3A 0B /r ib
        row("roundsd", { x(), xm(64), imm(8) }).opcode(0x0B).map(OpMap::m0f3a).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).imm_op(2).imm_bits(8).est_size(6)
          .roles({ OperandRole::def, OperandRole::use, OperandRole::use }),
        row("subss", { x(), xm(32) }).opcode(0x5C).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("subsd", { x(), xm() }).opcode(0x5C).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("mulss", { x(), xm(32) }).opcode(0x59).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("mulsd", { x(), xm() }).opcode(0x59).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("divss", { x(), xm(32) }).opcode(0x5E).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        row("divsd", { x(), xm() }).opcode(0x5E).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        // movss m, x                           F3 0F 11 /r
        row("movss", { mem(32), x() }).opcode(0x11).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
          .reg_op(1).rm_op(0).est_size(4).flags(InstFlags::may_store).roles({ OperandRole::use, OperandRole::use }),
        // movsd m, x                           F2 0F 11 /r
        row("movsd", { mem(64), x() }).opcode(0x11).map(OpMap::m0f).mandatory_prefix(0xF2).modrm(ModRMUse::reg_rm)
          .reg_op(1).rm_op(0).est_size(4).flags(InstFlags::may_store).roles({ OperandRole::use, OperandRole::use }),
        // andps xmm, xmm/m128                   0F 54 /r
        // andpd xmm, xmm/m128                66 0F 54 /r
        // The magnitude of a floating-point number is the number with its sign bit
        // cleared, and there is no instruction that clears one bit of a vector
        // register: the mask is a constant in the image, and a place in memory that
        // one of these reads must be sixteen bytes aligned.
        row("andps", { x(), xm(128) }).opcode(0x54).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).est_size(4)
          .roles(accumulate),
        row("andpd", { x(), xm(128) }).opcode(0x54).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // -- a whole run of elements in one register -------------------------------
        // These are the SSE2 integer instructions, which every x86-64 processor has:
        // the architecture's own oldest level includes them, so a run of elements is
        // done this way at every level rather than only at the newer ones.  A read
        // names the number of bytes it actually reads, which is what lets a run
        // shorter than a register be read without touching what lies beyond it, and
        // what makes the lanes above it zero rather than whatever was there.
        //
        // movdqu xmm, xmm/m128               F3 0F 6F /r
        row("movdqu", { x(), xm(128) }).opcode(0x6F).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        // movdqu m128, xmm                   F3 0F 7F /r
        row("movdqu", { mem(128), x() }).opcode(0x7F).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
          .reg_op(1).rm_op(0).est_size(4).roles(reads_both),
        // movq xmm, xmm/m64                  F3 0F 7E /r
        // Eight bytes read into the low half and the high half cleared, which is
        // what makes a run of eight bytes a value with nothing of its neighbours in
        // it.
        row("movq", { x(), xm(64) }).opcode(0x7E).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        // movq xmm/m64, xmm                  66 0F D6 /r
        row("movq", { mem(64), x() }).opcode(0xD6).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
            .reg_op(1)
          .rm_op(0).est_size(4).roles(reads_both),
        // movd xmm, r/m32                    66 0F 6E /r
        row("movd", { x(), mem(32) }).opcode(0x6E).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        row("movd", { x(), r(32) }).opcode(0x6E).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        // movd r/m32, xmm                    66 0F 7E /r
        row("movd", { mem(32), x() }).opcode(0x7E).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
            .reg_op(1)
          .rm_op(0).est_size(4).roles(reads_both),
        row("movd", { r(32), x() }).opcode(0x7E).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm).reg_op(1)
          .rm_op(0).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        // movq xmm, r/m64                    66 REX.W 0F 6E /r
        row("movq", { x(), r(64) }).opcode(0x6E).map(OpMap::m0f).mandatory_prefix(0x66).opsize(OpSize::rexw)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).est_size(5).roles({ OperandRole::def, OperandRole::use }),
        // movq r/m64, xmm                    66 REX.W 0F 7E /r
        // The bits of a double in an ordinary register, which is where a choice
        // between two of them is made: there is no conditional move of these.
        row("movq", { r(64), x() }).opcode(0x7E).map(OpMap::m0f).mandatory_prefix(0x66).opsize(OpSize::rexw)
          .modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).est_size(5).roles({ OperandRole::def, OperandRole::use }),
        // The arithmetic over a whole run, one instruction per lane width.  What
        // each does to a lane it does to that lane alone: there is no carry between
        // them, which is what makes a run of additions one addition.
        // paddb xmm, xmm/m128                66 0F FC /r
        row("paddb", { x(), xm(128) }).opcode(0xFC).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // paddw xmm, xmm/m128                66 0F FD /r
        row("paddw", { x(), xm(128) }).opcode(0xFD).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // paddd xmm, xmm/m128                66 0F FE /r
        row("paddd", { x(), xm(128) }).opcode(0xFE).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // paddq xmm, xmm/m128                66 0F D4 /r
        row("paddq", { x(), xm(128) }).opcode(0xD4).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubb xmm, xmm/m128                66 0F F8 /r
        row("psubb", { x(), xm(128) }).opcode(0xF8).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubw xmm, xmm/m128                66 0F F9 /r
        row("psubw", { x(), xm(128) }).opcode(0xF9).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubd xmm, xmm/m128                66 0F FA /r
        row("psubd", { x(), xm(128) }).opcode(0xFA).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubq xmm, xmm/m128                66 0F FB /r
        row("psubq", { x(), xm(128) }).opcode(0xFB).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // pmullw xmm, xmm/m128               66 0F D5 /r
        // The low half of the product in every lane, which is all a multiplication
        // that may wrap wants.  There is no byte-wide form and the word-wide one is
        // SSE4.1, which is why which widths this exists at is a property of the
        // level and not of the architecture.
        row("pmullw", { x(), xm(128) }).opcode(0xD5).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // pmulld xmm, xmm/m128               66 0F 38 40 /r
        row("pmulld", { x(), xm(128) }).opcode(0x40).map(OpMap::m0f38).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(5).roles(accumulate),
        // The saturating forms, which answer with the nearest value the lane's type
        // can hold rather than going past it.  Only the two narrow widths have them,
        // which is why the wider ones are still done an element at a time.
        // paddusb xmm, xmm/m128              66 0F DC /r
        row("paddusb", { x(), xm(128) }).opcode(0xDC).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // paddusw xmm, xmm/m128              66 0F DD /r
        row("paddusw", { x(), xm(128) }).opcode(0xDD).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubusb xmm, xmm/m128              66 0F D8 /r
        row("psubusb", { x(), xm(128) }).opcode(0xD8).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubusw xmm, xmm/m128              66 0F D9 /r
        row("psubusw", { x(), xm(128) }).opcode(0xD9).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // paddsb xmm, xmm/m128               66 0F EC /r
        row("paddsb", { x(), xm(128) }).opcode(0xEC).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // paddsw xmm, xmm/m128               66 0F ED /r
        row("paddsw", { x(), xm(128) }).opcode(0xED).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubsb xmm, xmm/m128               66 0F E8 /r
        row("psubsb", { x(), xm(128) }).opcode(0xE8).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // psubsw xmm, xmm/m128               66 0F E9 /r
        row("psubsw", { x(), xm(128) }).opcode(0xE9).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // pmovmskb r32, xmm                  66 0F D7 /r
        // The top bit of every byte, gathered into an ordinary register.  This is
        // how a question asked in every lane at once becomes the one question a
        // branch asks: the lanes that went past have their top bit set, and what is
        // wanted is whether any of them did.
        row("pmovmskb", { r(32), x() }).opcode(0xD7).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        // pand xmm, xmm/m128                 66 0F DB /r
        row("pand", { x(), xm(128) }).opcode(0xDB).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        // por xmm, xmm/m128                  66 0F EB /r
        row("por", { x(), xm(128) }).opcode(0xEB).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm).reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        // pxor xmm, xmm/m128                 66 0F EF /r
        row("pxor", { x(), xm(128) }).opcode(0xEF).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).est_size(4).roles(accumulate),
        // pcmpeqd xmm, xmm/m128              66 0F 76 /r
        // Asked of a register and itself it is how every bit of one is set, there
        // being no instruction that puts a constant in one of these.
        row("pcmpeqd", { x(), xm(128) }).opcode(0x76).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // punpcklbw xmm, xmm/m128            66 0F 60 /r
        // The three that double the width of what is in the low half by taking every
        // value twice, which is how one value is spread over a whole register: a
        // byte becomes a pair, the pair a quadruple, and so on until the register is
        // full.
        row("punpcklbw", { x(), xm(128) }).opcode(0x60).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // punpcklwd xmm, xmm/m128            66 0F 61 /r
        row("punpcklwd", { x(), xm(128) }).opcode(0x61).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // punpcklqdq xmm, xmm/m128           66 0F 6C /r
        row("punpcklqdq", { x(), xm(128) }).opcode(0x6C).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles(accumulate),
        // pshufd xmm, xmm/m128, imm8         66 0F 70 /r ib
        row("pshufd", { x(), xm(128), imm(8) }).opcode(0x70).map(OpMap::m0f).mandatory_prefix(0x66)
            .modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).imm_op(2).imm_bits(8).est_size(5)
          .roles({ OperandRole::def, OperandRole::use, OperandRole::use }),
        // -- the same, thirty-two bytes at a time -----------------------------------
        // These are AVX2, which the architecture's third level includes.  They are
        // the same operations over twice as many lanes, with two differences that
        // come from the prefix rather than from the operation: the destination is
        // named separately from both sources, so nothing has to be moved into place
        // first, and the width is a field rather than a different opcode.
        //
        // The mnemonics are the ones the narrow forms have.  Which of the two a row
        // is, is said by how wide its registers are, and that is what the table
        // matches on -- so the lowering asks for "and" and gets whichever it has
        // registers for.
        //
        // vmovdqu ymm, ymm/m256              VEX.256.F3.0F 6F /r
        row("movdqu", { y(), ym() }).opcode(0x6F).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0xF3)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(256, -1).est_size(5)
          .roles({ OperandRole::def, OperandRole::use }),
        // vmovdqu m256, ymm                  VEX.256.F3.0F 7F /r
        row("movdqu", { mem(256), y() }).opcode(0x7F).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0xF3)
          .modrm(ModRMUse::reg_rm).reg_op(1).rm_op(0).vex(256, -1).est_size(5).roles(reads_both),
        // vpmovmskb r32, ymm                 VEX.256.66.0F D7 /r
        row("pmovmskb", { r(32), y() }).opcode(0xD7).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(256, -1).est_size(5)
          .roles({ OperandRole::def, OperandRole::use }),
        // The bitwise three and the comparison that sets every bit.
        row("pand", { y(), y(), ym() }).opcode(0xDB).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("por", { y(), y(), ym() }).opcode(0xEB).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("pxor", { y(), y(), ym() }).opcode(0xEF).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("pcmpeqd", { y(), y(), ym() }).opcode(0x76).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        // The arithmetic, one per lane width.
        row("paddb", { y(), y(), ym() }).opcode(0xFC).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("paddw", { y(), y(), ym() }).opcode(0xFD).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("paddd", { y(), y(), ym() }).opcode(0xFE).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("paddq", { y(), y(), ym() }).opcode(0xD4).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubb", { y(), y(), ym() }).opcode(0xF8).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubw", { y(), y(), ym() }).opcode(0xF9).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubd", { y(), y(), ym() }).opcode(0xFA).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubq", { y(), y(), ym() }).opcode(0xFB).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("pmullw", { y(), y(), ym() }).opcode(0xD5).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("pmulld", { y(), y(), ym() }).opcode(0x40).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(6).roles(three_operand),
        // And the saturating forms the two narrow widths have.
        row("paddusb", { y(), y(), ym() }).opcode(0xDC).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("paddusw", { y(), y(), ym() }).opcode(0xDD).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubusb", { y(), y(), ym() }).opcode(0xD8).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubusw", { y(), y(), ym() }).opcode(0xD9).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("paddsb", { y(), y(), ym() }).opcode(0xEC).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("paddsw", { y(), y(), ym() }).opcode(0xED).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubsb", { y(), y(), ym() }).opcode(0xE8).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        row("psubsw", { y(), y(), ym() }).opcode(0xE9).map(OpMap::m0f).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(2).vex(256, 1).est_size(5).roles(three_operand),
        // vpbroadcastb/w/d/q ymm, xmm        VEX.256.66.0F38.W0 78/79/58/59 /r
        // One value in every lane, in one instruction: the value goes into the low
        // lane of a narrow register first, which is the only way to get an ordinary
        // register's value into one of these at all.
        row("pbroadcastb", { y(), x() }).opcode(0x78).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(256, -1).est_size(6)
          .roles({ OperandRole::def, OperandRole::use }),
        row("pbroadcastw", { y(), x() }).opcode(0x79).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(256, -1).est_size(6)
          .roles({ OperandRole::def, OperandRole::use }),
        row("pbroadcastd", { y(), x() }).opcode(0x58).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(256, -1).est_size(6)
          .roles({ OperandRole::def, OperandRole::use }),
        row("pbroadcastq", { y(), x() }).opcode(0x59).map(OpMap::m0f38).enc(EncKind::vex).mandatory_prefix(0x66)
          .modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1).vex(256, -1).est_size(6)
          .roles({ OperandRole::def, OperandRole::use }),
        // cvtss2sd xmm, xmm/m32              F3 0F 5A /r
        row("cvtss2sd", { x(), xm(32) }).opcode(0x5A).map(OpMap::m0f).mandatory_prefix(0xF3).modrm(ModRMUse::reg_rm)
          .reg_op(0).rm_op(1).est_size(4).roles({ OperandRole::def, OperandRole::use }),
        row("ucomiss", { x(), xm(32) }).opcode(0x2E).map(OpMap::m0f).modrm(ModRMUse::reg_rm).reg_op(0).rm_op(1)
          .implicit_defs({ regs.eflags }).est_size(4).roles(reads_both),
        row("ucomisd", { x(), xm() }).opcode(0x2E).map(OpMap::m0f).mandatory_prefix(0x66).modrm(ModRMUse::reg_rm)
            .reg_op(0)
          .rm_op(1).implicit_defs({ regs.eflags }).est_size(4).roles(reads_both),
        // setp r/m8                         0F 9A /0   (the parity flag, which a
        // floating-point comparison sets where the two are not ordered at all)
        row("setp", { rm(8) }).opcode(0x9A).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // setnp r/m8                         0F 9B /0   (the parity flag, which a
        // floating-point comparison sets where the two are not ordered at all)
        row("setnp", { rm(8) }).opcode(0x9B).map(OpMap::m0f).modrm(ModRMUse::ext_rm).ext(0).rm_op(0)
          .implicit_uses({ regs.eflags }).est_size(3),
        // jmp rel32                          E9 cd
        row("jmp", { rel(32) }).opcode(0xE9).rel_op(0).rel_bits(32).flags(InstFlags::terminator | InstFlags::barrier)
          .est_size(5),
        // je rel32                           0F 84 cd
        row("je", { rel(32) }).opcode(0x84).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jne rel32                          0F 85 cd
        row("jne", { rel(32) }).opcode(0x85).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jl rel32                           0F 8C cd
        row("jl", { rel(32) }).opcode(0x8C).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jle rel32                          0F 8E cd
        row("jle", { rel(32) }).opcode(0x8E).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jg rel32                           0F 8F cd
        row("jg", { rel(32) }).opcode(0x8F).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jge rel32                          0F 8D cd
        row("jge", { rel(32) }).opcode(0x8D).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jno rel32                          0F 81 cd
        // What it reads is the overflow flag, which the arithmetic before it wrote:
        // for a signed operation that is the whole of the question.  It is the
        // negative form because what it jumps over is the report of the fault, which
        // is how every check here is written: going past is what does not come back.
        row("jno", { rel(32) }).opcode(0x81).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jb rel32                           0F 82 cd
        row("jb", { rel(32) }).opcode(0x82).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jbe rel32                          0F 86 cd
        row("jbe", { rel(32) }).opcode(0x86).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // ja rel32                           0F 87 cd
        row("ja", { rel(32) }).opcode(0x87).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jae rel32                          0F 83 cd
        row("jae", { rel(32) }).opcode(0x83).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // jnp rel32                          0F 8B cd   (the parity flag again: a
        // branch taken where the two compared were ordered, which is what says
        // neither of them was a not-a-number)
        row("jnp", { rel(32) }).opcode(0x8B).map(OpMap::m0f).rel_op(0).rel_bits(32).implicit_uses({ regs.eflags })
          .flags(InstFlags::terminator).est_size(6),
        // What a call destroys is the *callee's* to say: two functions of one
        // compilation may follow different conventions, and a function that destroys
        // little is one a caller has to save little around -- neither of which the
        // table can know.  So the call carries it, per call, and what is named here
        // is only what the instruction itself writes.
        // call rel32                         E8 cd
        row("call", { rel(32) }).opcode(0xE8).rel_op(0).rel_bits(32).flags(InstFlags::call).est_size(5),
        // call r/m64                         FF /2
        // A call through a register, which is what a function held in a value is
        // called by.  No REX.W: in long mode the operand of a near call is already
        // sixty-four bits and saying so again is refused by the assembler.
        row("call", { rm(64) }).opcode(0xFF).modrm(ModRMUse::ext_rm).ext(2).rm_op(0).flags(InstFlags::call).est_size(3),
        // ret                                C3
        row("ret", {}).opcode(0xC3).flags(InstFlags::terminator | InstFlags::return_).est_size(1),
        // syscall                            0F 05
        row("syscall", {}).opcode(0x05).map(OpMap::m0f).implicit_defs({ regs.rcx, regs.r11, regs.eflags }).est_size(2),
        // cpuid                              0F A2
        // It reads the leaf in EAX and the subleaf in ECX and answers in all four,
        // which is why every one of them is written down: nothing else tells the
        // register allocator that a value in EBX does not survive it.
        row("cpuid", {}).opcode(0xA2).map(OpMap::m0f).implicit_uses({ regs.rax, regs.rcx })
          .implicit_defs({ regs.rax, regs.rbx, regs.rcx, regs.rdx }).est_size(2),
        // xgetbv                             0F 01 D0
        // What the *operating system* has enabled, which `CPUID` does not answer:
        // it says the processor lets the system turn the wide registers on, and this
        // says whether it did.  The register to read is named in ECX and the answer
        // comes back in EDX:EAX; nought is the one this asks for, which holds the
        // bits for every kind of state a context switch saves.
        row("xgetbv", {}).opcode(0x01).map(OpMap::m0f).modrm_byte(0xD0).implicit_uses({ regs.rcx })
          .implicit_defs({ regs.rax, regs.rdx }).est_size(3),
        // ud2                                0F 0B
        row("ud2", {}).opcode(0x0B).map(OpMap::m0f).flags(InstFlags::terminator | InstFlags::barrier).est_size(2),
      };
    }

  } // anonymous namespace


  std::span<const mc::InstDesc* const> x86_instrs()
  {
    static const std::deque<X86InstDesc> kept = [] {
      std::deque<X86InstDesc> made;
      for (auto& one : rows())
        made.push_back(std::move(one.d));
      return made;
    }();
    static const std::vector<const mc::InstDesc*> table = [] {
      std::vector<const mc::InstDesc*> made;
      for (const auto& one : kept)
        made.push_back(&one);
      return made;
    }();
    return table;
  }

} // namespace pl4g::target::x86_64
