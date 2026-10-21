// The RISC-V 64-bit encoding table.
#include "target/riscv64/opcodes.hh"

#include <deque>
#include <vector>

#include "target/riscv64/fixups.hh"
#include "target/riscv64/regs.hh"


namespace pl4g::target::riscv64 {

  namespace {

    // Where each operand of a standard format sits.
    constexpr int rd = 7;
    constexpr int rs1 = 15;
    constexpr int rs2 = 20;
    constexpr int imm12 = 20;
    constexpr int imm20 = 12;

    using mc::Field;
    using mc::FieldKind;
    using mc::InstFlags;
    using mc::OperandKind;
    using mc::OperandRole;
    using mc::OperandSpec;

    // An integer register; there is only one width.
    OperandSpec r()
    {
      OperandSpec made;
      made.kinds = OperandKind::reg;
      made.rclass = registers().gpr;
      made.bits = 64;
      return made;
    }

    // A floating-point register, the wider of the two formats.
    OperandSpec f()
    {
      OperandSpec made;
      made.kinds = OperandKind::reg;
      made.rclass = registers().fpr;
      made.bits = 64;
      return made;
    }

    OperandSpec imm(std::int64_t min, std::int64_t max)
    {
      OperandSpec made;
      made.kinds = OperandKind::imm;
      made.imm_min = min;
      made.imm_max = max;
      return made;
    }

    // The signed twelve-bit immediate of the I-type instructions.
    OperandSpec i12() { return imm(imm12_min, imm12_max); }
    // The twenty-bit immediate of the U-type instructions.
    OperandSpec i20() { return imm(0, 0xFFFFF); }
    // How far a shift shifts: six bits at this width.
    OperandSpec shamt() { return imm(0, 63); }
    // Which way a conversion rounds, as the three-bit field.
    OperandSpec rm() { return imm(0, 7); }
    // Which accesses a fence orders, as the twelve bits holding all of it.
    OperandSpec fence_bits() { return imm(0, 0xFFF); }

    // A branch target, given as a symbol reference.
    OperandSpec sym()
    {
      OperandSpec made;
      made.kinds = OperandKind::rel | OperandKind::sym;
      return made;
    }

    // A field of `width` bits at `lsb`, filled from operand `operand`.
    Field field(FieldKind kind, int operand, int lsb, int width = 5)
    {
      Field made;
      made.kind = kind;
      made.operand = operand;
      made.lsb = lsb;
      made.width = width;
      return made;
    }

    // A five-bit register number.
    Field reg(int operand, int lsb) { return field(FieldKind::register_, operand, lsb); }

    // A signed immediate held in one run of bits.
    Field simm(int operand, int lsb, int width)
    {
      Field made = field(FieldKind::immediate, operand, lsb, width);
      made.is_signed = true;
      return made;
    }

    // An unsigned immediate held in one run of bits.
    Field uimm(int operand, int lsb, int width) { return field(FieldKind::immediate, operand, lsb, width); }

    // The three-bit rounding-mode field.
    Field mode(int operand) { return uimm(operand, 12, 3); }

    Field reloc(int operand, int lsb, int width, const mc::FixupKind* kind, std::int64_t adjust = 0)
    {
      Field made = field(FieldKind::relocation, operand, lsb, width);
      made.reloc = kind;
      made.base_adjust = adjust;
      return made;
    }

    // Where a store puts its value, its base and the two halves of its offset.
    std::vector<Field> store_fields()
    {
      Field low = field(FieldKind::immediate, 2, 7, 5);
      low.is_signed = true;
      low.value_bits = 12;
      Field high = field(FieldKind::immediate, 2, 25, 7);
      high.shift = 5;
      high.is_signed = true;
      high.value_bits = 12;
      return { reg(0, rs2), reg(1, rs1), low, high };
    }

    // A store and a branch read every operand they name.
    const std::vector<OperandRole> reads_all_three{ OperandRole::use, OperandRole::use, OperandRole::use };

    struct Built {
      std::deque<mc::FixedWidthInstDesc> rows;
      std::vector<const mc::InstDesc* > order;

      mc::FixedWidthInstDesc& add(std::string mnemonic, std::vector<OperandSpec> operands, std::uint64_t word,
                                  std::vector<Field> fields, InstFlags flags = InstFlags::none)
      {
        auto& row = rows.emplace_back();
        row.mnemonic = std::move(mnemonic);
        row.operands = std::move(operands);
        row.template_word = word;
        row.fields = std::move(fields);
        row.flags = flags;
        row.est_size = instruction_size;
        order.push_back(&row);
        return row;
      }

      // The ordinary three-register row.
      void rrr(std::string mnemonic, std::uint64_t word, bool floating = false)
      {
        auto k = floating ? f() : r();
        add(std::move(mnemonic), { k, k, k }, word, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
      }

      Built()
      {
        const auto& regs = registers();
        add("li", { r(), i12() }, 0x00000013, { reg(0, rd), simm(1, imm12, 12) });
        add("mv", { r(), r() }, 0x00000013, { reg(0, rd), reg(1, rs1) }, InstFlags::move);
        add("addi", { r(), r(), i12() }, 0x00000013, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) });
        add("addiw", { r(), r(), i12() }, 0x0000001B, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) });
        rrr("add", 0x00000033);
        rrr("addw", 0x0000003B);
        rrr("sub", 0x40000033);
        rrr("subw", 0x4000003B);
        rrr("xor", 0x00004033);
        rrr("and", 0x00007033);
        rrr("or", 0x00006033);
        add("not", { r(), r() }, 0xFFF04013, { reg(0, rd), reg(1, rs1) });
        rrr("slt", 0x00002033);
        rrr("sltu", 0x00003033);
        add("sltiu", { r(), r(), i12() }, 0x00003013, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) });
        add("xori", { r(), r(), i12() }, 0x00004013, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) });
        add("srai", { r(), r(), shamt() }, 0x40005013, { reg(0, rd), reg(1, rs1), uimm(2, 20, 6) });
        add("slli", { r(), r(), shamt() }, 0x00001013, { reg(0, rd), reg(1, rs1), uimm(2, 20, 6) });
        rrr("mul", 0x02000033);
        rrr("mulh", 0x02001033);
        rrr("mulhu", 0x02003033);
        rrr("div", 0x02004033);
        rrr("divu", 0x02005033);
        rrr("rem", 0x02006033);
        rrr("remu", 0x02007033);
        rrr("sll", 0x00001033);
        rrr("srl", 0x00005033);
        rrr("sra", 0x40005033);
        rrr("fadd.s", 0x00007053, true);
        rrr("fadd.d", 0x02007053, true);
        rrr("fmin.s", 0x28000053, true);
        rrr("fmax.s", 0x28001053, true);
        rrr("fmin.d", 0x2A000053, true);
        rrr("fmax.d", 0x2A001053, true);
        rrr("fsub.s", 0x08007053, true);
        rrr("fsub.d", 0x0A007053, true);
        rrr("fmul.s", 0x10007053, true);
        rrr("fmul.d", 0x12007053, true);
        rrr("fdiv.s", 0x18007053, true);
        rrr("fdiv.d", 0x1A007053, true);
        add("fcvt.d.s", { f(), f() }, 0x42000053, { reg(0, rd), reg(1, rs1) });
        add("fcvt.l.d", { r(), f(), rm() }, 0xC2200053, { reg(0, rd), reg(1, rs1), mode(2) });
        add("fcvt.w.s", { r(), f(), rm() }, 0xC0000053, { reg(0, rd), reg(1, rs1), mode(2) });
        add("fround.s", { f(), f(), rm() }, 0x40400053, { reg(0, rd), reg(1, rs1), mode(2) });
        add("fround.d", { f(), f(), rm() }, 0x42400053, { reg(0, rd), reg(1, rs1), mode(2) });
        add("fcvt.d.l", { f(), r() }, 0xD2207053, { reg(0, rd), reg(1, rs1) });
        add("fcvt.s.w", { f(), r() }, 0xD0007053, { reg(0, rd), reg(1, rs1) });
        add("fmv.x.w", { r(), f() }, 0xE0000053, { reg(0, rd), reg(1, rs1) });
        add("fmv.w.x", { f(), r() }, 0xF0000053, { reg(0, rd), reg(1, rs1) });
        add("fmv.x.d", { r(), f() }, 0xE2000053, { reg(0, rd), reg(1, rs1) });
        add("fmv.d.x", { f(), r() }, 0xF2000053, { reg(0, rd), reg(1, rs1) });
        rrr("fsgnj.s", 0x20000053, true);
        rrr("fsgnj.d", 0x22000053, true);
        rrr("fsgnjx.s", 0x20002053, true);
        rrr("fsgnjx.d", 0x22002053, true);
        add("feq.s", { r(), f(), f() }, 0xA0002053, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
        add("feq.d", { r(), f(), f() }, 0xA2002053, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
        add("flt.s", { r(), f(), f() }, 0xA0001053, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
        add("flt.d", { r(), f(), f() }, 0xA2001053, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
        add("fle.s", { r(), f(), f() }, 0xA0000053, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
        add("fle.d", { r(), f(), f() }, 0xA2000053, { reg(0, rd), reg(1, rs1), reg(2, rs2) });
        add("flw", { f(), r(), i12() }, 0x00002007, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) });
        add("fld", { f(), r(), i12() }, 0x00003007, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) });
        add("fsw", { f(), r(), i12() }, 0x00002027, store_fields(), InstFlags::may_store).roles = reads_all_three;
        add("fsd", { f(), r(), i12() }, 0x00003027, store_fields(), InstFlags::may_store).roles = reads_all_three;
        add("lui", { r(), i20() }, 0x00000037, { reg(0, rd), uimm(1, imm20, 20) });
        add("auipc", { r(), i20() }, 0x00000017, { reg(0, rd), uimm(1, imm20, 20) });
        add("auipc.hi20", { r(), sym() }, 0x00000017, { reg(0, rd), reloc(1, imm20, 20, &pcrel_hi20) });
        add("addi.lo12", { r(), r(), sym() }, 0x00000013,
            { reg(0, rd), reg(1, rs1), reloc(2, imm12, 12, &pcrel_lo12_i, -pcrel_pair_distance) });
        auto load = [&](std::string mnemonic, std::uint64_t word) {
          add(std::move(mnemonic), { r(), r(), i12() }, word, { reg(0, rd), reg(1, rs1), simm(2, imm12, 12) },
              InstFlags::may_load);
        };
        load("lbu", 0x00004003);
        load("lb", 0x00000003);
        load("lhu", 0x00005003);
        load("lh", 0x00001003);
        load("lwu", 0x00006003);
        load("lw", 0x00002003);
        load("ld", 0x00003003);
        auto store = [&](std::string mnemonic, std::uint64_t word) {
          add(std::move(mnemonic), { r(), r(), i12() }, word, store_fields(), InstFlags::may_store).roles =
            reads_all_three;
        };
        store("sb", 0x00000023);
        store("sh", 0x00001023);
        store("sw", 0x00002023);
        store("sd", 0x00003023);
        add("j", { sym() }, 0x0000006F, { reloc(0, 12, 20, &jal_fixup) }, InstFlags::terminator | InstFlags::barrier);
        auto branch = [&](std::string mnemonic, std::uint64_t word) {
          add(std::move(mnemonic), { r(), r(), sym() }, word,
              { reg(0, 15), reg(1, 20), reloc(2, 0, 32, &branch_fixup) }, InstFlags::terminator).roles =
            reads_all_three;
        };
        branch("beq", 0x00000063);
        branch("bne", 0x00001063);
        branch("blt", 0x00004063);
        branch("bge", 0x00005063);
        branch("bltu", 0x00006063);
        branch("bgeu", 0x00007063);
        // What a call destroys is the callee's to say, per call; the table
        // names only what the instruction itself writes.
        add("jal", { sym() }, 0x000000EF, { reloc(0, 12, 20, &jal_fixup) }, InstFlags::call).implicit_defs = {
          regs.ra
        };
        add("jalr", { r() }, 0x000000E7, { reg(0, rs1) }, InstFlags::call).implicit_defs = { regs.ra };
        // jr rs, which is jalr zero, rs, 0: a jump through a register, which is how
        // a stub that found what it stands for goes there, with the arguments and
        // the return address as they came.
        add("jr", { r() }, 0x00000067, { reg(0, rs1) }, InstFlags::terminator | InstFlags::barrier);
        add("ret", {}, 0x00008067, {}, InstFlags::terminator | InstFlags::return_).implicit_uses = { regs.ra };
        add("ecall", {}, 0x00000073, {});
        add("fence", { fence_bits() }, 0x0000000F, { uimm(0, imm12, 12) }, InstFlags::may_load | InstFlags::may_store);
        add("ebreak", {}, 0x00100073, {});
        add("unimp", {}, 0xC0001073, {}, InstFlags::terminator | InstFlags::barrier);
        add("nop", {}, 0x00000013, {});
      }
    };

    const Built& built()
    {
      static const Built made;
      return made;
    }

  } // anonymous namespace


  std::span<const mc::InstDesc* const> instructions()
  {
    return built().order;
  }


  const mc::InstrTable& table()
  {
    static const mc::InstrTable made(instructions());
    return made;
  }


  const mc::Op* environment_call()
  {
    static const mc::Op* made = mc::register_op(mc::Op{ "riscv.ecall", 0, false });
    return made;
  }

} // namespace pl4g::target::riscv64
