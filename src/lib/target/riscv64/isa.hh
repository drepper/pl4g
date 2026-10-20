// What a RISC-V program is built for, written the way the architecture writes
// it: pypl4g's target/riscv64/isa.py.
//
// An ISA string -- `rv64gc`, `rv64imafd_zicsr`, `rv64gc_zba_zbb_zbs` -- or a
// profile, `rva23u64`.  The compiler uses `f`, `d` and `zfa` today and reads
// the rest so that a program naming an extension for a future compiler is
// not refused by this one; an unknown name is refused, a misspelling being the
// likeliest name that is not an extension.
#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>

#include "util/ordered.hh"


namespace pl4g::target::riscv64::isa {

  // The order the single-letter extensions are written in.
  inline constexpr std::string_view single = "imafdqlcbkjtpvh";
  // What a program is built for unless it says otherwise.
  inline constexpr std::string_view default_name = "rva23";

  // A name that is neither a well-formed ISA string nor a profile; what it
  // says is what is wrong rather than what was written.
  struct BadName : std::invalid_argument {
    using std::invalid_argument::invalid_argument;
  };

  // A version as written: the major number and the minor one, each in decimal
  // without leading zeroes.  Nothing reads them as numbers, and a number
  // written in a string may be any length.
  using Version = std::optional<std::pair<std::string, std::string>>;

  // How wide an address is, whether the base is the reduced one, what was
  // asked for as written, and every extension -- the implied ones as well --
  // with its version where one was written.
  struct ISA {
    int bits = 64;
    bool embedded = false;
    std::string named;
    OrderedMap<std::string, Version> versions;

    bool has(std::string_view extension) const { return versions.contains(std::string(extension)); }
    // Whether floating point is there at all.
    bool floats() const { return has("d"); }
    // The spelling the ELF attribute wants: every extension, in order, each
    // with the version it is at.
    std::string normalized() const;
  };

  // What version an extension is at where nothing says otherwise.
  std::pair<int, int> version_of(std::string_view extension);

  // What `text` says a program is built for; `BadName` where it says nothing.
  ISA parse(std::string_view text);

} // namespace pl4g::target::riscv64::isa
