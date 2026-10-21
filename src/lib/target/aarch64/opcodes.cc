// The AArch64 encoding table.  The rows are pypl4g's, row for row and in the
// same order, which is the order the table chooses between rows that accept
// the same operands in.
#include "target/aarch64/opcodes.hh"

#include <deque>
#include <optional>
#include <vector>

#include "target/aarch64/desc.hh"
#include "target/aarch64/fixups.hh"
#include "target/aarch64/regs.hh"


namespace pl4g::target::aarch64 {

  namespace {

    using enum mc::FieldKind;

    // A slot accepting `kinds`, of a class, a width, and a range of values.
    mc::OperandSpec spec(mc::OperandKind kinds, const mc::RegClass* rclass = nullptr,
                         std::optional<int> bits = std::nullopt, std::optional<__int128> imm_min = std::nullopt,
                         std::optional<__int128> imm_max = std::nullopt)
    {
      mc::OperandSpec made;
      made.kinds = kinds;
      made.rclass = rclass;
      made.bits = bits;
      made.imm_min = imm_min;
      made.imm_max = imm_max;
      return made;
    }

    // A general-purpose register of the given width.
    mc::OperandSpec r(int bits)
    {
      return spec(mc::OperandKind::reg, regs().gpr, bits);
    }

    // An immediate the encoding can actually hold.  What matters on a
    // fixed-width architecture is whether the value fits the field, not how wide
    // the operand was declared: there is no shorter encoding to fall back to.
    mc::OperandSpec imm(__int128 maximum)
    {
      return spec(mc::OperandKind::imm, nullptr, std::nullopt, 0, maximum);
    }

    // A branch target or an address, given as a symbol reference.
    mc::OperandSpec sym()
    {
      return spec(mc::OperandKind::rel | mc::OperandKind::sym);
    }

    // One of the floating-point registers, named at the width of the value.
    mc::OperandSpec v(int bits)
    {
      return spec(mc::OperandKind::reg, regs().vec, bits);
    }

    // How far a move-wide instruction shifts its sixteen bits, in bits as the
    // assembly writes it: `lsl #32` and not `hw=2`.
    mc::OperandSpec shift()
    {
      return spec(mc::OperandKind::imm, nullptr, std::nullopt, 0, 48);
    }

    // The offset of a load, which the encoding holds divided by the access size.
    mc::OperandSpec off(int scale)
    {
      return spec(mc::OperandKind::imm, nullptr, std::nullopt, 0, __int128(0xFFF) << scale);
    }

    mc::Field field(mc::FieldKind kind, int operand, int lsb, int width = 5, int shift = 0, bool is_signed = false,
                    const mc::FixupKind* reloc = nullptr)
    {
      mc::Field made;
      made.kind = kind;
      made.operand = operand;
      made.lsb = lsb;
      made.width = width;
      made.shift = shift;
      made.is_signed = is_signed;
      made.reloc = reloc;
      return made;
    }

    // A five-bit register number.
    mc::Field reg_field(int operand, int lsb)
    {
      return field(register_, operand, lsb);
    }

    // The twelve bits a load's offset occupies, scaled by the access size.
    mc::Field offset_field(int operand, int scale)
    {
      return field(immediate, operand, 10, 12, scale);
    }

    // Rd, Rn and Rm always sit in the same places.
    constexpr int rd = 0;
    constexpr int rn = 5;
    constexpr int rm = 16;

    // A store names the value, the base register and the offset, and reads all
    // of them: nothing it names is written, only the place they point at.
    std::vector<mc::OperandRole> reads_all_three()
    {
      return { mc::OperandRole::use, mc::OperandRole::use, mc::OperandRole::use };
    }

    // A comparison writes only the flags, which it declares separately.
    std::vector<mc::OperandRole> reads_both()
    {
      return { mc::OperandRole::use, mc::OperandRole::use };
    }


    // One row being made, for what a row says beside its shape.
    class Row {
    public:
      explicit Row(A64InstDesc& made) : made(made) {}
      Row& defs(std::vector<const mc::PhysReg*> regs)
      {
        made.implicit_defs = std::move(regs);
        return *this;
      }
      Row& uses(std::vector<const mc::PhysReg*> regs)
      {
        made.implicit_uses = std::move(regs);
        return *this;
      }
      Row& flags(mc::InstFlags flags)
      {
        made.flags = flags;
        return *this;
      }
      Row& roles(std::vector<mc::OperandRole> roles)
      {
        made.roles = std::move(roles);
        return *this;
      }

    private:
      A64InstDesc& made;
    };


    // The rows, kept where they do not move.
    struct Table {
      std::deque<A64InstDesc> rows;
      std::vector<const mc::InstDesc*> pointers;

      Row add(std::string mnemonic, std::vector<mc::OperandSpec> operands, std::uint64_t template_word,
              std::vector<mc::Field> fields)
      {
        auto& made = rows.emplace_back();
        made.mnemonic = std::move(mnemonic);
        made.operands = std::move(operands);
        made.template_word = template_word;
        made.fields = std::move(fields);
        made.est_size = instruction_size;
        pointers.push_back(&made);
        return Row(made);
      }

      Table();
    };


    Table::Table()
    {
      // movz Wd, #imm16                    sf=0 10 100101 hw=00
      add("movz", { r(32), imm(0xFFFF) }, 0x52800000,
          { reg_field(0, rd), field(immediate, 1, 5, 16) })
          .flags(mc::InstFlags::zext32);
      // movz Xd, #imm16                    sf=1
      add("movz", { r(64), imm(0xFFFF) }, 0xD2800000,
          { reg_field(0, rd), field(immediate, 1, 5, 16) });
      // A constant wider than sixteen bits is written a quarter of a word at a
      // time: one instruction sets a quarter and clears the rest, and each that
      // follows sets a quarter and leaves the rest alone.  `movn` sets a quarter
      // and turns every bit round, which is what makes a small negative number one
      // instruction instead of four.
      //
      // The shift is written in bits and encoded as the count of quarters, which
      // is what the field's own shift is for.
      // movz Xd, #imm16, lsl #shift
      add("movz", { r(64), imm(0xFFFF), shift() }, 0xD2800000,
          { reg_field(0, rd), field(immediate, 1, 5, 16), field(immediate, 2, 21, 2, 4) });
      // movk Xd, #imm16, lsl #shift
      add("movk", { r(64), imm(0xFFFF), shift() }, 0xF2800000,
          { reg_field(0, rd), field(immediate, 1, 5, 16), field(immediate, 2, 21, 2, 4) })
          .roles({ mc::OperandRole::def_use, mc::OperandRole::use, mc::OperandRole::use });
      // movn Xd, #imm16, lsl #shift
      add("movn", { r(64), imm(0xFFFF), shift() }, 0x92800000,
          { reg_field(0, rd), field(immediate, 1, 5, 16), field(immediate, 2, 21, 2, 4) });
      // mov Wd, Wm   is  orr Wd, WZR, Wm
      add("mov", { r(32), r(32) }, 0x2A0003E0,
          { reg_field(0, rd), reg_field(1, rm) })
          .flags(mc::InstFlags::move | mc::InstFlags::zext32);
      // mov Xd, Xm   is  orr Xd, XZR, Xm
      add("mov", { r(64), r(64) }, 0xAA0003E0,
          { reg_field(0, rd), reg_field(1, rm) })
          .flags(mc::InstFlags::move);
      // add Wd, Wn, Wm
      add("add", { r(32), r(32), r(32) }, 0x0B000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .flags(mc::InstFlags::zext32);
      // add Xd, Xn, Xm
      add("add", { r(64), r(64), r(64) }, 0x8B000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // add Wd, Wn, #imm12
      add("add", { r(32), r(32), imm(0xFFF) }, 0x11000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) })
          .flags(mc::InstFlags::zext32);
      // add Xd, Xn, #imm12
      add("add", { r(64), r(64), imm(0xFFF) }, 0x91000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) });
      // adds Wd, Wn, Wm / subs, and their immediate forms.  The flag-setting
      // pair, which differ from the plain ones by one bit and by leaving behind
      // whether the answer went past the end of the width they were done at.
      add("adds", { r(32), r(32), r(32) }, 0x2B000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .defs({ regs().nzcv })
          .flags(mc::InstFlags::zext32);
      // adds Xd, Xn, Xm
      add("adds", { r(64), r(64), r(64) }, 0xAB000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .defs({ regs().nzcv });
      // adds Wd, Wn, #imm12
      add("adds", { r(32), r(32), imm(0xFFF) }, 0x31000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) })
          .defs({ regs().nzcv })
          .flags(mc::InstFlags::zext32);
      // adds Xd, Xn, #imm12
      add("adds", { r(64), r(64), imm(0xFFF) }, 0xB1000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) })
          .defs({ regs().nzcv });
      // subs Wd, Wn, Wm
      add("subs", { r(32), r(32), r(32) }, 0x6B000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .defs({ regs().nzcv })
          .flags(mc::InstFlags::zext32);
      // subs Xd, Xn, Xm
      add("subs", { r(64), r(64), r(64) }, 0xEB000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .defs({ regs().nzcv });
      // subs Wd, Wn, #imm12
      add("subs", { r(32), r(32), imm(0xFFF) }, 0x71000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) })
          .defs({ regs().nzcv })
          .flags(mc::InstFlags::zext32);
      // subs Xd, Xn, #imm12
      add("subs", { r(64), r(64), imm(0xFFF) }, 0xF1000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) })
          .defs({ regs().nzcv });
      // -- a whole run of elements in one register -------------------------------
      // The Advanced SIMD instructions, which are in the base this compiler builds
      // for: a processor of this architecture that runs Linux has them, so a run of
      // elements is done this way and there is no level to ask for it with.
      //
      // The arrangement -- how the sixteen bytes are divided into lanes -- is part
      // of the instruction and not of its operands, so it is part of the mnemonic
      // here, written the way the assembly writes it.  Two registers named the same
      // way differ only in how the bits in them are grouped, which no operand can
      // say.
      //
      // ldr Qt, [Xn, #imm12]
      add("ldr", { v(128), r(64), off(4) }, 0x3DC00000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 4) })
          .flags(mc::InstFlags::may_load);
      // str Qt, [Xn, #imm12]
      add("str", { v(128), r(64), off(4) }, 0x3D800000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 4) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      // The bitwise three, whose answer does not depend on how the bits are
      // divided into lanes, so there is one arrangement and it is bytes.
      add("and.16b", { v(128), v(128), v(128) }, 0x4E201C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("orr.16b", { v(128), v(128), v(128) }, 0x4EA01C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("eor.16b", { v(128), v(128), v(128) }, 0x6E201C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // not Vd.16B, Vn.16B
      add("not.16b", { v(128), v(128) }, 0x6E205800,
          { reg_field(0, rd), reg_field(1, rn) });
      // The arithmetic, one instruction per arrangement.  What each does to a
      // lane it does to that lane alone: there is no carry between them.
      add("add.16b", { v(128), v(128), v(128) }, 0x4E208400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("add.8h", { v(128), v(128), v(128) }, 0x4E608400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("add.4s", { v(128), v(128), v(128) }, 0x4EA08400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("add.2d", { v(128), v(128), v(128) }, 0x4EE08400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sub.16b", { v(128), v(128), v(128) }, 0x6E208400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sub.8h", { v(128), v(128), v(128) }, 0x6E608400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sub.4s", { v(128), v(128), v(128) }, 0x6EA08400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sub.2d", { v(128), v(128), v(128) }, 0x6EE08400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // mul Vd.<T>, Vn.<T>, Vm.<T> -- the low half of the product in every lane,
      // which is all a multiplication that may wrap wants.  There is no form for
      // the widest lane.
      add("mul.16b", { v(128), v(128), v(128) }, 0x4E209C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("mul.8h", { v(128), v(128), v(128) }, 0x4E609C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("mul.4s", { v(128), v(128), v(128) }, 0x4EA09C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // The saturating forms, which stop at the end of the lane's type rather
      // than going past it.  Unlike the other machine here, this one has them
      // at every width.
      add("uqadd.16b", { v(128), v(128), v(128) }, 0x6E200C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqadd.8h", { v(128), v(128), v(128) }, 0x6E600C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqadd.4s", { v(128), v(128), v(128) }, 0x6EA00C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqadd.2d", { v(128), v(128), v(128) }, 0x6EE00C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqadd.16b", { v(128), v(128), v(128) }, 0x4E200C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqadd.8h", { v(128), v(128), v(128) }, 0x4E600C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqadd.4s", { v(128), v(128), v(128) }, 0x4EA00C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqadd.2d", { v(128), v(128), v(128) }, 0x4EE00C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqsub.16b", { v(128), v(128), v(128) }, 0x6E202C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqsub.8h", { v(128), v(128), v(128) }, 0x6E602C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqsub.4s", { v(128), v(128), v(128) }, 0x6EA02C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("uqsub.2d", { v(128), v(128), v(128) }, 0x6EE02C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqsub.16b", { v(128), v(128), v(128) }, 0x4E202C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqsub.8h", { v(128), v(128), v(128) }, 0x4E602C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqsub.4s", { v(128), v(128), v(128) }, 0x4EA02C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sqsub.2d", { v(128), v(128), v(128) }, 0x4EE02C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // dup Vd.<T>, Wn -- one ordinary register's value in every lane.
      add("dup.16b", { v(128), r(32) }, 0x4E010C00,
          { reg_field(0, rd), reg_field(1, rn) });
      add("dup.8h", { v(128), r(32) }, 0x4E020C00,
          { reg_field(0, rd), reg_field(1, rn) });
      add("dup.4s", { v(128), r(32) }, 0x4E040C00,
          { reg_field(0, rd), reg_field(1, rn) });
      add("dup.2d", { v(128), r(64) }, 0x4E080C00,
          { reg_field(0, rd), reg_field(1, rn) });
      // umaxv Bd, Vn.16B -- the largest byte of the register, in the low byte of
      // the destination.  This is how a question asked in every lane at once
      // becomes the one question a branch asks: the bytes that answer \"yes\" hold
      // the top bit and no others hold anything, so the largest of them is not
      // zero exactly when one of them said yes.
      add("umaxv.16b", { v(128), v(128) }, 0x6E30A800,
          { reg_field(0, rd), reg_field(1, rn) });
      // cmlt Vd.<T>, Vn.<T>, #0 -- every bit of a lane set where that lane's own
      // top bit was, and every bit clear where it was not.  That is what turns
      // \"the top bit of each lane says whether it went past\" into a value whose
      // every byte says it, which is what lets the answer be reduced by looking at
      // bytes without knowing how they were grouped.
      add("cmlt.16b", { v(128), v(128) }, 0x4E20A800,
          { reg_field(0, rd), reg_field(1, rn) });
      add("cmlt.8h", { v(128), v(128) }, 0x4E60A800,
          { reg_field(0, rd), reg_field(1, rn) });
      add("cmlt.4s", { v(128), v(128) }, 0x4EA0A800,
          { reg_field(0, rd), reg_field(1, rn) });
      add("cmlt.2d", { v(128), v(128) }, 0x4EE0A800,
          { reg_field(0, rd), reg_field(1, rn) });
      // umaxv Bd, Vn.8B -- the same over the low eight bytes only.
      add("umaxv.8b", { v(128), v(128) }, 0x2E30A800,
          { reg_field(0, rd), reg_field(1, rn) });
      // fmov Xd, Dn -- the low eight bytes into an ordinary register.
      add("fmov", { r(64), v(64) }, 0x9E660000,
          { reg_field(0, rd), reg_field(1, rn) });
      // fmov Wd, Sn -- and out of the register it ended up in.
      add("fmov", { r(32), v(32) }, 0x1E260000,
          { reg_field(0, rd), reg_field(1, rn) });
      // fmov Dd, Xn and fmov Sd, Wn -- the other way, the bits of a whole number
      // read as a floating-point one with nothing converted.
      add("fmov", { v(64), r(64) }, 0x9E670000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fmov", { v(32), r(32) }, 0x1E270000,
          { reg_field(0, rd), reg_field(1, rn) });
      // scvtf and ucvtf Dd/Sd, Xn: a signed or an unsigned 64-bit number as the
      // floating-point number nearest it, rounded as FPCR says.
      add("scvtf", { v(64), r(64) }, 0x9E620000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("scvtf", { v(32), r(64) }, 0x9E220000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("ucvtf", { v(64), r(64) }, 0x9E630000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("ucvtf", { v(32), r(64) }, 0x9E230000,
          { reg_field(0, rd), reg_field(1, rn) });
      // fcvtzs and fcvtzu Xd, Dn/Sn: and back, toward zero, a number beyond the
      // integer's ends answering the end it is beyond.
      add("fcvtzs", { r(64), v(64) }, 0x9E780000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fcvtzs", { r(64), v(32) }, 0x9E380000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fcvtzu", { r(64), v(64) }, 0x9E790000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fcvtzu", { r(64), v(32) }, 0x9E390000,
          { reg_field(0, rd), reg_field(1, rn) });
      // sub Wd, Wn, Wm
      add("sub", { r(32), r(32), r(32) }, 0x4B000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .flags(mc::InstFlags::zext32);
      // sub Xd, Xn, Xm
      add("sub", { r(64), r(64), r(64) }, 0xCB000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // sub Wd, Wn, #imm12
      add("sub", { r(32), r(32), imm(0xFFF) }, 0x51000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) })
          .flags(mc::InstFlags::zext32);
      // sub Xd, Xn, #imm12
      add("sub", { r(64), r(64), imm(0xFFF) }, 0xD1000000,
          { reg_field(0, rd), reg_field(1, rn), field(immediate, 2, 10, 12) });
      // eor Wd, Wn, Wm
      add("eor", { r(32), r(32), r(32) }, 0x4A000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .flags(mc::InstFlags::zext32);
      // eor Xd, Xn, Xm
      add("eor", { r(64), r(64), r(64) }, 0xCA000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // and Wd, Wn, Wm
      add("and", { r(32), r(32), r(32) }, 0x0A000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .flags(mc::InstFlags::zext32);
      // and Xd, Xn, Xm
      add("and", { r(64), r(64), r(64) }, 0x8A000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // orr Wd, Wn, Wm
      add("orr", { r(32), r(32), r(32) }, 0x2A000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .flags(mc::InstFlags::zext32);
      // orr Xd, Xn, Xm
      add("orr", { r(64), r(64), r(64) }, 0xAA000000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // mvn Wd, Wm   is  orn Wd, WZR, Wm
      add("mvn", { r(32), r(32) }, 0x2A2003E0,
          { reg_field(0, rd), reg_field(1, rm) })
          .flags(mc::InstFlags::zext32);
      // mvn Xd, Xm   is  orn Xd, XZR, Xm
      add("mvn", { r(64), r(64) }, 0xAA2003E0,
          { reg_field(0, rd), reg_field(1, rm) });
      // adrp Xd, page-of-symbol
      add("adrp", { r(64), sym() }, 0x90000000,
          { reg_field(0, rd), field(relocation, 1, 5, 19, 0, false, &adr_page21) });
      // add Xd, Xn, #offset-within-page
      add("add.lo12", { r(64), r(64), sym() }, 0x91000000,
          { reg_field(0, rd), reg_field(1, rn), field(relocation, 2, 10, 12, 0, false, &add_lo12) });
      // The loads.  The immediate offset is scaled by the size of the access, so
      // each row states its own shift; an offset that is not a multiple of that
      // size has no encoding and is refused rather than rounded.
      // ldrb Wt, [Xn, #imm12]
      add("ldrb", { r(32), r(64), off(0) }, 0x39400000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 0) })
          .flags(mc::InstFlags::may_load | mc::InstFlags::zext32);
      // ldrsb Wt, [Xn, #imm12]
      add("ldrsb", { r(32), r(64), off(0) }, 0x39C00000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 0) })
          .flags(mc::InstFlags::may_load);
      // ldrh Wt, [Xn, #imm12]
      add("ldrh", { r(32), r(64), off(1) }, 0x79400000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 1) })
          .flags(mc::InstFlags::may_load | mc::InstFlags::zext32);
      // ldrsh Wt, [Xn, #imm12]
      add("ldrsh", { r(32), r(64), off(1) }, 0x79C00000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 1) })
          .flags(mc::InstFlags::may_load);
      // ldr Wt, [Xn, #imm12]
      add("ldr", { r(32), r(64), off(2) }, 0xB9400000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 2) })
          .flags(mc::InstFlags::may_load | mc::InstFlags::zext32);
      // ldrsw Xt, [Xn, #imm12]
      add("ldrsw", { r(64), r(64), off(2) }, 0xB9800000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 2) })
          .flags(mc::InstFlags::may_load);
      // ldr Xt, [Xn, #imm12]
      add("ldr", { r(64), r(64), off(3) }, 0xF9400000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 3) })
          .flags(mc::InstFlags::may_load);
      // The stores, which mirror the loads.  There is no signed form: what is
      // written is whatever the register holds, narrowed to the width the
      // instruction names -- which the program has already been checked to allow.
      // strb Wt, [Xn, #imm12]
      add("strb", { r(32), r(64), off(0) }, 0x39000000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 0) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      // strh Wt, [Xn, #imm12]
      add("strh", { r(32), r(64), off(1) }, 0x79000000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 1) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      // str Wt, [Xn, #imm12]
      add("str", { r(32), r(64), off(2) }, 0xB9000000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 2) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      // str Xt, [Xn, #imm12]
      add("str", { r(64), r(64), off(3) }, 0xF9000000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 3) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      // The ordered forms.  They carry no offset -- the address is a register and
      // nothing else -- which is why what selects one adds the offset first.
      // ldarb Wt, [Xn]
      add("ldarb", { r(32), r(64) }, 0x08DFFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_load | mc::InstFlags::zext32);
      // ldarh Wt, [Xn]
      add("ldarh", { r(32), r(64) }, 0x48DFFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_load | mc::InstFlags::zext32);
      // ldar Wt, [Xn]
      add("ldar", { r(32), r(64) }, 0x88DFFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_load | mc::InstFlags::zext32);
      // ldar Xt, [Xn]
      add("ldar", { r(64), r(64) }, 0xC8DFFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_load);
      // stlrb Wt, [Xn]
      add("stlrb", { r(32), r(64) }, 0x089FFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_both());
      // stlrh Wt, [Xn]
      add("stlrh", { r(32), r(64) }, 0x489FFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_both());
      // stlr Wt, [Xn]
      add("stlr", { r(32), r(64) }, 0x889FFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_both());
      // stlr Xt, [Xn]
      add("stlr", { r(64), r(64) }, 0xC89FFC00,
          { reg_field(0, rd), reg_field(1, rn) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_both());
      // b label
      add("b", { sym() }, 0x14000000,
          { field(relocation, 0, 0, 26, 2, true, &branch26) })
          .flags(mc::InstFlags::terminator | mc::InstFlags::barrier);
      // b.eq label
      add("b.eq", { sym() }, 0x54000000,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.vc label
      // The overflow flag clear, which for a flag-setting signed operation is the
      // whole of \"it did not go past the end of its width\".
      add("b.vc", { sym() }, 0x54000007,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.ne label
      add("b.ne", { sym() }, 0x54000001,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.hs label
      add("b.hs", { sym() }, 0x54000002,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.lo label
      add("b.lo", { sym() }, 0x54000003,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.hi label
      add("b.hi", { sym() }, 0x54000008,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.ls label
      add("b.ls", { sym() }, 0x54000009,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.ge label
      add("b.ge", { sym() }, 0x5400000A,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.lt label
      add("b.lt", { sym() }, 0x5400000B,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.gt label
      add("b.gt", { sym() }, 0x5400000C,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // b.le label
      add("b.le", { sym() }, 0x5400000D,
          { field(relocation, 0, 5, 19, 2, true, &branch19) })
          .uses({ regs().nzcv })
          .flags(mc::InstFlags::terminator);
      // cset Wd, <cond> is csinc Wd, wzr, wzr, <the opposite condition>, so the
      // condition in the word is the opposite of the one the name says.  One row
      // per condition, as for the branches above: the condition is a field of the
      // instruction, and baking it into the template is what lets a row be chosen
      // by naming it.
      // cset Wd, eq
      add("cset.eq", { r(32) }, 0x1A9F17E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, ne
      add("cset.ne", { r(32) }, 0x1A9F07E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, lt
      add("cset.lt", { r(32) }, 0x1A9FA7E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, le
      add("cset.le", { r(32) }, 0x1A9FC7E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, gt
      add("cset.gt", { r(32) }, 0x1A9FD7E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, ge
      add("cset.ge", { r(32) }, 0x1A9FB7E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, lo
      add("cset.lo", { r(32) }, 0x1A9F27E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, ls
      add("cset.ls", { r(32) }, 0x1A9F87E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, hi
      add("cset.hi", { r(32) }, 0x1A9F97E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // cset Wd, hs
      add("cset.hs", { r(32) }, 0x1A9F37E0,
          { reg_field(0, rd) })
          .uses({ regs().nzcv });
      // mul Wd, Wn, Wm   is  madd Wd, Wn, Wm, WZR
      add("mul", { r(32), r(32), r(32) }, 0x1B007C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // clz Xd, Xn   how many zeroes stand above the highest set bit
      // This architecture has it at every level, where x86-64 has it only from the
      // third: counting bits is not a feature here, it is the instruction set.
      add("clz", { r(64), r(64) }, 0xDAC01000,
          { reg_field(0, rd), reg_field(1, rn) });
      // rbit Xd, Xn   the bits in the other order, so that the count of the
      // zeroes below the lowest set bit is `clz` of this.
      add("rbit", { r(64), r(64) }, 0xDAC00000,
          { reg_field(0, rd), reg_field(1, rn) });
      // mul Xd, Xn, Xm
      add("mul", { r(64), r(64), r(64) }, 0x9B007C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // umulh Xd, Xn, Xm   the upper half of an unsigned product
      add("umulh", { r(64), r(64), r(64) }, 0x9BC07C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // smulh Xd, Xn, Xm   the upper half of a signed product
      add("smulh", { r(64), r(64), r(64) }, 0x9B407C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // csel Xd, Xn, Xm, eq
      add("csel.eq", { r(64), r(64), r(64) }, 0x9A800000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, ne
      add("csel.ne", { r(64), r(64), r(64) }, 0x9A801000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, lt
      add("csel.lt", { r(64), r(64), r(64) }, 0x9A80B000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, le
      add("csel.le", { r(64), r(64), r(64) }, 0x9A80D000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, gt
      add("csel.gt", { r(64), r(64), r(64) }, 0x9A80C000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, ge
      add("csel.ge", { r(64), r(64), r(64) }, 0x9A80A000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, lo
      add("csel.lo", { r(64), r(64), r(64) }, 0x9A803000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, ls
      add("csel.ls", { r(64), r(64), r(64) }, 0x9A809000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, hi
      add("csel.hi", { r(64), r(64), r(64) }, 0x9A808000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // csel Xd, Xn, Xm, hs
      add("csel.hs", { r(64), r(64), r(64) }, 0x9A802000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      // sxtb Xd, Wn   is  sbfm Xd, Xn, 0, 7
      add("sxtb", { r(64), r(32) }, 0x93401C00,
          { reg_field(0, rd), reg_field(1, rn) });
      // sxth Xd, Wn   is  sbfm Xd, Xn, 0, 15
      add("sxth", { r(64), r(32) }, 0x93403C00,
          { reg_field(0, rd), reg_field(1, rn) });
      // sxtw Xd, Wn   is  sbfm Xd, Xn, 0, 31
      add("sxtw", { r(64), r(32) }, 0x93407C00,
          { reg_field(0, rd), reg_field(1, rn) });
      // sdiv Wd, Wn, Wm / Xd, Xn, Xm    -- truncating toward zero
      add("sdiv", { r(32), r(32), r(32) }, 0x1AC00C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("sdiv", { r(64), r(64), r(64) }, 0x9AC00C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("udiv", { r(32), r(32), r(32) }, 0x1AC00800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("udiv", { r(64), r(64), r(64) }, 0x9AC00800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // msub Wd, Wn, Wm, Wa   is  Wa - Wn*Wm, which is how a remainder is had
      // from a quotient here: there is no instruction that gives one directly.
      add("msub", { r(32), r(32), r(32), r(32) }, 0x1B008000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm), reg_field(3, 10) });
      add("msub", { r(64), r(64), r(64), r(64) }, 0x9B008000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm), reg_field(3, 10) });
      add("lslv", { r(32), r(32), r(32) }, 0x1AC02000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("lslv", { r(64), r(64), r(64) }, 0x9AC02000,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("lsrv", { r(32), r(32), r(32) }, 0x1AC02400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("lsrv", { r(64), r(64), r(64) }, 0x9AC02400,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("asrv", { r(32), r(32), r(32) }, 0x1AC02800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("asrv", { r(64), r(64), r(64) }, 0x9AC02800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("rorv", { r(32), r(32), r(32) }, 0x1AC02C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("rorv", { r(64), r(64), r(64) }, 0x9AC02C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // The scalar floating-point instructions.  Floating point is in the base of
      // the AArch64 ABI, so a binary that uses them requires nothing a binary that
      // does not would not already have.
      add("fmov", { v(32), v(32) }, 0x1E204000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fmov", { v(64), v(64) }, 0x1E604000,
          { reg_field(0, rd), reg_field(1, rn) });
      // fcsel Sd, Sn, Sm, ne and fcsel Dd, Dn, Dm, ne: the first where the flags
      // say not equal and the second where they do not, which is what a choice
      // between two floating-point values made by a truth value comes to.
      add("fcsel.ne", { v(32), v(32), v(32) }, 0x1E201C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      add("fcsel.ne", { v(64), v(64), v(64) }, 0x1E601C00,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) })
          .uses({ regs().nzcv });
      add("fabs", { v(32), v(32) }, 0x1E20C000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fabs", { v(64), v(64) }, 0x1E60C000,
          { reg_field(0, rd), reg_field(1, rn) });
      // fcvt d, s: a single-precision value in the double-precision format,
      // which holds every one of them exactly.
      add("fcvt", { v(64), v(32) }, 0x1E22C000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fadd", { v(32), v(32), v(32) }, 0x1E202800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fadd", { v(64), v(64), v(64) }, 0x1E602800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // The larger and the smaller of two, which these machines have as
      // instructions of their own.  What they answer where one of the two is not
      // a number differs between architectures; nothing here can be, an operation
      // whose answer is not a number having stopped the program where it arose.
      add("fmax", { v(32), v(32), v(32) }, 0x1E204800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fmax", { v(64), v(64), v(64) }, 0x1E604800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fmin", { v(32), v(32), v(32) }, 0x1E205800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fmin", { v(64), v(64), v(64) }, 0x1E605800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      // The whole number a floating-point number rounds to.  Four of the seven
      // this architecture has: the three that name a direction and the one that
      // asks the processor's own rounding mode.
      add("frintm", { v(32), v(32) }, 0x1E254000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frintm", { v(64), v(64) }, 0x1E654000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frintp", { v(32), v(32) }, 0x1E24C000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frintp", { v(64), v(64) }, 0x1E64C000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frintn", { v(32), v(32) }, 0x1E244000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frintn", { v(64), v(64) }, 0x1E644000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frinti", { v(32), v(32) }, 0x1E27C000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("frinti", { v(64), v(64) }, 0x1E67C000,
          { reg_field(0, rd), reg_field(1, rn) });
      add("fsub", { v(32), v(32), v(32) }, 0x1E203800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fsub", { v(64), v(64), v(64) }, 0x1E603800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fmul", { v(32), v(32), v(32) }, 0x1E200800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fmul", { v(64), v(64), v(64) }, 0x1E600800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fdiv", { v(32), v(32), v(32) }, 0x1E201800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fdiv", { v(64), v(64), v(64) }, 0x1E601800,
          { reg_field(0, rd), reg_field(1, rn), reg_field(2, rm) });
      add("fcmp", { v(32), v(32) }, 0x1E202000,
          { reg_field(0, rn), reg_field(1, rm) })
          .defs({ regs().nzcv })
          .roles(reads_both());
      add("fcmp", { v(64), v(64) }, 0x1E602000,
          { reg_field(0, rn), reg_field(1, rm) })
          .defs({ regs().nzcv })
          .roles(reads_both());
      add("ldr", { v(32), r(64), off(2) }, 0xBD400000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 2) });
      add("ldr", { v(64), r(64), off(3) }, 0xFD400000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 3) });
      add("str", { v(32), r(64), off(2) }, 0xBD000000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 2) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      add("str", { v(64), r(64), off(3) }, 0xFD000000,
          { reg_field(0, rd), reg_field(1, rn), offset_field(2, 3) })
          .flags(mc::InstFlags::may_store)
          .roles(reads_all_three());
      // cbnz Wt, label
      add("cbnz", { r(32), sym() }, 0x35000000,
          { reg_field(0, rd), field(relocation, 1, 5, 19, 2, true, &branch19) })
          .flags(mc::InstFlags::terminator)
          .roles(reads_both());
      // cbnz Xt, label
      add("cbnz", { r(64), sym() }, 0xB5000000,
          { reg_field(0, rd), field(relocation, 1, 5, 19, 2, true, &branch19) })
          .flags(mc::InstFlags::terminator)
          .roles(reads_both());
      // cbz Wt, label
      add("cbz", { r(32), sym() }, 0x34000000,
          { reg_field(0, rd), field(relocation, 1, 5, 19, 2, true, &branch19) })
          .flags(mc::InstFlags::terminator)
          .roles(reads_both());
      // cbz Xt, label
      add("cbz", { r(64), sym() }, 0xB4000000,
          { reg_field(0, rd), field(relocation, 1, 5, 19, 2, true, &branch19) })
          .flags(mc::InstFlags::terminator)
          .roles(reads_both());
      // cmp Wn, #imm12   is  subs WZR, Wn, #imm12
      add("cmp", { r(32), imm(0xFFF) }, 0x7100001F,
          { reg_field(0, rn), field(immediate, 1, 10, 12) })
          .defs({ regs().nzcv })
          .roles(reads_both());
      // cmp Xn, #imm12   is  subs XZR, Xn, #imm12
      add("cmp", { r(64), imm(0xFFF) }, 0xF100001F,
          { reg_field(0, rn), field(immediate, 1, 10, 12) })
          .defs({ regs().nzcv })
          .roles(reads_both());
      // bl label
      // What a call destroys is the *callee's* to say: two functions of one
      // compilation may follow different conventions, and a function that destroys
      // little is one a caller has to save little around -- neither of which the
      // table can know.  So the call carries it, per call, and what is named here
      // is only what the instruction itself writes.
      add("bl", { sym() }, 0x94000000,
          { field(relocation, 0, 0, 26, 2, true, &branch26) })
          .defs({ regs().x30 })
          .flags(mc::InstFlags::call);
      // blr Xn
      // A call through a register, which is what a function held in a value is
      // called by.  It writes the link register as `bl` does; what it destroys
      // beyond that is the callee's to say and travels on the instruction.
      add("blr", { r(64) }, 0xD63F0000,
          { reg_field(0, rn) })
          .defs({ regs().x30 })
          .flags(mc::InstFlags::call);
      // br Xn
      // A jump through a register, which is how a stub that found what it stands
      // for goes there, with the arguments and the link register as they came.
      add("br", { r(64) }, 0xD61F0000,
          { reg_field(0, rn) })
          .flags(mc::InstFlags::terminator | mc::InstFlags::barrier);
      // ret   (returns through the link register)
      add("ret", {}, 0xD65F03C0,
          {})
          .uses({ regs().x30 })
          .flags(mc::InstFlags::terminator | mc::InstFlags::return_);
      // svc #imm16
      add("svc", { imm(0xFFFF) }, 0xD4000001,
          { field(immediate, 0, 5, 16) });
      // brk #imm16
      // udf #imm16   permanently undefined, which is what a trap should be: the
      // program is dying, and it should die the same way on every architecture.
      // `brk` would raise a different signal here than the other two raise.
      add("udf", { imm(0xFFFF) }, 0x00000000,
          { field(immediate, 0, 0, 16) })
          .flags(mc::InstFlags::terminator | mc::InstFlags::barrier);
      add("brk", { imm(0xFFFF) }, 0xD4200000,
          { field(immediate, 0, 5, 16) })
          .flags(mc::InstFlags::terminator | mc::InstFlags::barrier);
      // nop
      add("nop", {}, 0xD503201F,
          {});
      // cmp Wn, Wm   is  subs WZR, Wn, Wm
      add("cmp", { r(32), r(32) }, 0x6B00001F,
          { reg_field(0, rn), reg_field(1, rm) })
          .defs({ regs().nzcv })
          .roles(reads_both());
      // cmp Xn, Xm   is  subs XZR, Xn, Xm
      add("cmp", { r(64), r(64) }, 0xEB00001F,
          { reg_field(0, rn), reg_field(1, rm) })
          .defs({ regs().nzcv })
          .roles(reads_both());
    }

  } // anonymous namespace


  std::span<const mc::InstDesc* const> aarch64_instrs()
  {
    static const Table made;
    return made.pointers;
  }

} // namespace pl4g::target::aarch64
