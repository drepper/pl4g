// The microarchitecture levels x86-64 defines, and what asking for one means:
// pypl4g's target/x86_64/levels.py.
//
// The architecture's documentation names four sets of features, `x86-64-v1`
// through `x86-64-v4`, each holding the one before it.  A program built for
// more than the oldest says at its own entry point whether the processor it
// was started on can run it, and stops if it cannot.  The features are asked
// of the processor, with `CPUID`, and not of the system.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>


namespace pl4g::target::x86_64::levels {

  // One question put to `CPUID`, and the bits the answer must have set:
  // `leaf` and `subleaf` go in EAX and ECX, `reg` is which of the four
  // answers holds the bits, and `names` what they are.
  struct Requirement {
    std::uint32_t leaf = 0;
    std::uint32_t subleaf = 0;
    std::string reg;
    std::uint32_t bits = 0;
    std::vector<std::string> names;
  };

  // The levels, oldest first, which is the order one contains another in.
  inline constexpr std::array<std::string_view, 4> names{ "v1", "v2", "v3", "v4" };
  // What a program is built for unless it says otherwise: the newest.
  inline constexpr std::string_view default_level = "v4";

  // Whether a level's processors hold a run in a register twice as wide.
  bool wide(std::string_view level) noexcept;
  // Whether `level` is one of them.
  bool known(std::string_view level) noexcept;

  // Everything a program of `level` needs, its own and what it contains, one
  // question per leaf and register.
  std::vector<Requirement> requirements(std::string_view level);
  // The `XCR0` bits it needs the system to have set, and what they are called.
  std::uint32_t state(std::string_view level);
  std::vector<std::string> state_names(std::string_view level);

  // What a program of `level` tells a processor that cannot run it, and a
  // system that has turned off the registers it uses.
  std::string described(std::string_view level);
  std::string disabled(std::string_view level);

} // namespace pl4g::target::x86_64::levels
