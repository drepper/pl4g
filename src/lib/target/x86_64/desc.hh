// How an x86-64 instruction is encoded: pypl4g's target/x86_64/desc.py.
//
// An encoding is described by data, not by code: a row states which prefixes
// it needs, which opcode map it lives in, how its operands map onto the ModRM
// byte, and which immediate it carries.  One generic emitter walks a fixed
// sequence of phases over that data, so adding an operation is a row, and
// adding a prefix family is one more emitter in the prefix phase.
#pragma once

#include <cstdint>
#include <optional>

#include "mc/desc.hh"


namespace pl4g::target::x86_64 {

  // Which prefix family an encoding belongs to.
  enum class EncKind : std::uint8_t { legacy, rex2, vex, evex };

  // Which opcode map the opcode byte lives in, numbered as the VEX and EVEX
  // `mmmmm` field numbers them.
  enum class OpMap : std::uint8_t { primary = 0, m0f = 1, m0f38 = 2, m0f3a = 3 };

  // Which operand size the encoding selects.
  enum class OpSize : std::uint8_t { default_, p66, rexw };

  // How the ModRM byte is filled in, if there is one: `reg_rm` names an operand
  // in each field, `ext_rm` holds a fixed digit in ModRM.reg.
  enum class ModRMUse : std::uint8_t { none, reg_rm, ext_rm };

  // The extra fields a VEX or EVEX encoding needs: how wide the vector
  // registers it names are, and which operand goes in the prefix's own
  // register field.
  struct VexInfo {
    int length = 128;
    std::optional<int> vvvv_op;
    std::optional<int> mask_op;
    bool broadcast = false;
  };

  // One row of the x86-64 encoding table.
  struct X86InstDesc final : mc::InstDesc {
    int opcode = 0;
    OpMap map = OpMap::primary;
    EncKind enc = EncKind::legacy;
    // A ModRM byte written out in full, for an instruction that has one and
    // names no operand in it.
    std::optional<int> modrm_byte;
    OpSize opsize = OpSize::default_;
    // A prefix byte that selects the instruction rather than the operand size.
    std::optional<int> mandatory_prefix;
    ModRMUse modrm = ModRMUse::none;
    // The digit stored in ModRM.reg for an `ext_rm` encoding.
    std::optional<int> ext;
    std::optional<int> reg_op;
    std::optional<int> rm_op;
    // The low three bits of the register are added to the opcode byte.
    bool plus_reg = false;
    std::optional<int> imm_op;
    std::optional<int> imm_bits;
    std::optional<int> rel_op;
    std::optional<int> rel_bits;
    std::optional<VexInfo> vex;
  };

} // namespace pl4g::target::x86_64
