// Encode instructions written one to a line and print the bytes and fixups
// each comes to -- what src/test/encoding_agrees_aarch64.py and
// encoding_agrees_x86_64.py hold the C++ encoding tables to, row for row,
// against pypl4g's.
//
// Usage: pl4g-encdump --target TRIPLE < LINES
//
// A line is a mnemonic and its operands, separated by spaces: `r:NAME` or
// `r:NAME:BITS` for a register, `i:VALUE:BITS:SIGNED` for an immediate, and
// `s:NAME` for a reference to a symbol, and on x86-64
// `m:KEY=VALUE,...` for a place in memory, the keys being base, index, scale,
// disp, sym, seg, size, rip and signed.  What is printed per line is the
// bytes in hex and the name of each fixup's kind, or `refused` and why.
//
// On RISC-V a line may instead be `@KIND VALUE HEX`: the fixup of that kind
// stored with the value into the bytes the hex gives, printed as hex again --
// what encoding_agrees_riscv64.py holds the scattering of offsets to.
#include <argp.h>

#include <cstdio>
#include <deque>
#include <print>
#include <ranges>
#include <string>
#include <vector>

#include "mc/desc.hh"
#include "mc/fixedwidth.hh"
#include "mc/inst.hh"
#include "mc/symbol.hh"
#include "target/aarch64/desc.hh"
#include "target/aarch64/opcodes.hh"
#include "target/aarch64/regs.hh"
#include "target/riscv64/fixups.hh"
#include "target/riscv64/opcodes.hh"
#include "target/riscv64/regs.hh"
#include "target/x86_64/encoder.hh"
#include "target/x86_64/opcodes.hh"
#include "target/x86_64/regs.hh"


namespace {

  struct Arguments {
    std::string triple = "aarch64-linux-none";
  };

  constexpr ::argp_option options[] = {
    { "target", 1000, "TRIPLE", 0, "Encode for TRIPLE", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    if (key == 1000) {
      args.triple = arg;
      return 0;
    }
    return ARGP_ERR_UNKNOWN;
  }

  constexpr ::argp parser = { options, parse_option, nullptr, "Encode instructions written one to a line.", nullptr,
                              nullptr, nullptr };

  // The register called `name` on the target the triple names.
  const pl4g::mc::PhysReg* reg_of(const std::string& triple, const std::string& name)
  {
    if (triple == "x86_64-linux-none")
      return pl4g::target::x86_64::registers().reg(name);
    if (triple == "riscv64-linux-none")
      return pl4g::target::riscv64::registers().reg(name);
    return pl4g::target::aarch64::reg(name);
  }

  pl4g::mc::MCSymbol& symbol_of(const std::string& name, std::deque<pl4g::mc::MCSymbol>& symbols)
  {
    auto& symbol = symbols.emplace_back();
    symbol.name = name;
    return symbol;
  }

  // A place in memory, from the keys and values a word gives.
  pl4g::mc::MCMem place_of(const std::string& triple, const std::string& fields,
                           std::deque<pl4g::mc::MCSymbol>& symbols)
  {
    pl4g::mc::MCMem made;
    for (auto one : std::views::split(fields, ',')) {
      std::string field(one.begin(), one.end());
      auto equals = field.find('=');
      auto key = field.substr(0, equals);
      auto value = field.substr(equals + 1);
      if (key == "base")
        made.base = reg_of(triple, value);
      else if (key == "index")
        made.index = reg_of(triple, value);
      else if (key == "scale")
        made.scale = std::stoi(value);
      else if (key == "disp")
        made.disp = std::stoll(value);
      else if (key == "sym")
        made.disp_sym = pl4g::mc::sym_expr(&symbol_of(value, symbols));
      else if (key == "seg")
        made.seg = reg_of(triple, value);
      else if (key == "size")
        made.size_bits = std::stoi(value);
      else if (key == "rip")
        made.rip_relative = value == "1";
      else if (key == "signed")
        made.is_signed = value == "1";
    }
    return made;
  }

  // The operand a word of a line describes.
  pl4g::mc::MCOperand operand_of(const std::string& triple, const std::string& word,
                                 std::deque<pl4g::mc::MCSymbol>& symbols)
  {
    if (word.starts_with("m:"))
      return place_of(triple, word.substr(2), symbols);
    std::vector<std::string> parts;
    for (auto one : std::views::split(word, ':'))
      parts.emplace_back(one.begin(), one.end());
    if (parts.at(0) == "r") {
      pl4g::mc::MCReg made{ reg_of(triple, parts.at(1)) };
      if (parts.size() > 2)
        made.bits = std::stoi(parts[2]);
      return made;
    }
    if (parts.at(0) == "i")
      return pl4g::mc::MCImm{ std::stoll(parts.at(1)), std::stoi(parts.at(2)), parts.at(3) == "1" };
    return pl4g::mc::MCSymRef{ pl4g::mc::sym_expr(&symbol_of(parts.at(1), symbols)) };
  }

  // The bytes `hex` with the RISC-V fixup `kind` stored into them, the value
  // being `value`, in hex.
  std::string applied(const std::string& kind, const std::string& value, const std::string& hex)
  {
    pl4g::mc::Bytes data;
    for (std::size_t at = 0; at + 1 < hex.size(); at += 2)
      data.push_back(std::uint8_t(std::stoul(hex.substr(at, 2), nullptr, 16)));
    pl4g::mc::MCFixup fixup;
    fixup.kind = pl4g::target::riscv64::by_name().at(kind);
    pl4g::target::riscv64::apply_fixup(data, 0, fixup, std::stoll(value));
    std::string out;
    for (auto byte : data)
      out += std::format("{:02x}", byte);
    return out;
  }

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  bool x86 = args.triple == "x86_64-linux-none";
  bool riscv = args.triple == "riscv64-linux-none";
  if (! x86 && ! riscv && args.triple != "aarch64-linux-none") [[unlikely]] {
    std::print(stderr, "pl4g-encdump: no table for {}\n", args.triple);
    return 2;
  }
  pl4g::mc::InstrTable own(x86 ? pl4g::target::x86_64::x86_instrs()
                            : riscv ? std::span<const pl4g::mc::InstDesc* const>{}
                                    : pl4g::target::aarch64::aarch64_instrs());
  const pl4g::mc::InstrTable& table = riscv ? pl4g::target::riscv64::table() : own;
  std::deque<pl4g::mc::MCSymbol> symbols;
  std::string input;
  char buffer[4096];
  for (std::size_t got; (got = std::fread(buffer, 1, sizeof buffer, stdin)) > 0;)
    input.append(buffer, got);
  for (auto one : std::views::split(input, '\n')) {
    std::string line(one.begin(), one.end());
    if (line.empty())
      continue;
    std::vector<std::string> words;
    for (auto word : std::views::split(line, ' '))
      if (! std::ranges::empty(word))
        words.emplace_back(word.begin(), word.end());
    const std::string& mnemonic = words.at(0);
    if (riscv && mnemonic.starts_with('@')) {
      try {
        std::println("{}", applied(mnemonic.substr(1), words.at(1), words.at(2)));
      } catch (const std::exception& error) {
        std::println("refused {}", error.what());
      }
      continue;
    }
    std::vector<pl4g::mc::MCOperand> operands;
    for (std::size_t at = 1; at < words.size(); ++at)
      operands.push_back(operand_of(args.triple, words[at], symbols));
    try {
      auto desc = table.select(mnemonic, operands);
      auto inst = pl4g::mc::make_inst(desc, operands);
      auto [bytes, fixups] = x86     ? pl4g::target::x86_64::encode(*inst)
                             : riscv ? pl4g::mc::encode_fixed_width(*inst, pl4g::target::riscv64::instruction_size)
                                     : pl4g::target::aarch64::encode(*inst);
      std::string out;
      for (auto byte : bytes)
        out += std::format("{:02x}", byte);
      for (const auto& fixup : fixups)
        out += std::format(" {}@{}", fixup.kind->name, fixup.offset);
      std::println("{}", out);
    } catch (const std::exception& error) {
      std::println("refused {}", error.what());
    }
  }
}
