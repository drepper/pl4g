// An instruction of the symbolic assembler.  A port of pypl4g/mc/inst.py.
//
// Instructions are held by shared pointer: a pass replaces one with another
// and the allocator remembers some by identity, so one is never freed while
// anything may still hold it.
#pragma once

#include <memory>
#include <set>
#include <string>
#include <vector>

#include "mc/desc.hh"
#include "source/location.hh"


namespace pl4g::mc {

  // One selected instruction: a table row plus its operands.
  struct MCInst {
    const InstDesc* desc = nullptr;
    std::vector<MCOperand> operands;
    Span span;
    std::set<std::string> prefixes;
    // Registers this instruction destroys beyond what its row says: what a
    // call's callee says it destroys.
    std::vector<Reg> clobbers;
    // Registers this instruction reads beyond what its row says: where a
    // call's arguments, or a return's answer, were put.
    std::vector<Reg> reads;

    // The name of the instruction in the table.
    const std::string& mnemonic() const noexcept { return desc->mnemonic; }
    // A readable form, for the debugging dump.
    std::string render() const;
  };

  using InstRef = std::shared_ptr<MCInst>;
  using Insts = std::vector<InstRef>;

  // A new instruction of `desc` over `operands`.
  InstRef make_inst(const InstDesc* desc, std::vector<MCOperand> operands, Span span = invalid_span);

} // namespace pl4g::mc
