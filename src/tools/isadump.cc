// Read RISC-V ISA strings written one to a line and print what each says --
// what src/test/isa_agrees_riscv64.py holds the C++ reading of them, the
// attributes section made from them and the target's choice of level to,
// against pypl4g's.
//
// Usage: pl4g-isadump < LINES
//
// What is printed per line is the width, whether the base is the reduced one,
// the name as written, every extension with its version, the normalized form
// and the attributes section in hex -- or `refused` and why -- and after a tab
// what `--mclevel` makes of it: `ok`, or `refused` and why.
#include <cstdio>
#include <format>
#include <print>
#include <ranges>
#include <stdexcept>
#include <string>

#include "target/riscv64/attributes.hh"
#include "target/riscv64/isa.hh"
#include "target/target.hh"


namespace {

  namespace isa = pl4g::target::riscv64::isa;

  // What `text` reads as.
  std::string read(const std::string& text)
  {
    try {
      auto found = isa::parse(text);
      std::string out = std::format("{} {} {}", found.bits, found.embedded ? 1 : 0, found.named);
      for (const auto& [name, version] : found.versions)
        out += version ? std::format(" {}={}p{}", name, version->first, version->second) : " " + name;
      auto normalized = found.normalized();
      out += " " + normalized + " ";
      for (auto byte : pl4g::target::riscv64::build_attributes(normalized))
        out += std::format("{:02x}", byte);
      return out;
    } catch (const isa::BadName& error) {
      return std::format("refused {}", error.what());
    }
  }

  // What the target makes of `text` as the level to build for.
  std::string level(const std::string& text)
  {
    auto target = pl4g::target::lookup_target("riscv64-linux-none");
    try {
      target->use_mclevel(text);
      return "ok";
    } catch (const std::invalid_argument& error) {
      return std::format("refused {}", error.what());
    }
  }

} // anonymous namespace


int main()
{
  std::string input;
  char buffer[4096];
  for (std::size_t got; (got = std::fread(buffer, 1, sizeof buffer, stdin)) > 0;)
    input.append(buffer, got);
  for (auto one : std::views::split(input, '\n')) {
    std::string line(one.begin(), one.end());
    if (! line.empty())
      std::println("{}\t{}", read(line), level(line));
  }
}
