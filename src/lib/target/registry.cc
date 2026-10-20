// What the front end asks about a target triple.
#include "target/registry.hh"

#include <algorithm>
#include <array>


namespace pl4g::target {

  namespace {

    // What each architecture's back end calls the conventions it knows, sorted:
    // the language's own, the system's, and `cdecl` meaning the system's.
    constexpr std::string_view x86_64_conventions[] = { "cdecl", "pl4g", "sysv64" };
    constexpr std::string_view aarch64_conventions[] = { "aapcs64", "cdecl", "pl4g" };
    constexpr std::string_view riscv64_conventions[] = { "cdecl", "lp64", "pl4g" };

    const std::span<const std::string_view> x86_64_known{ x86_64_conventions };
    const std::span<const std::string_view> aarch64_known{ aarch64_conventions };
    const std::span<const std::string_view> riscv64_known{ riscv64_conventions };

    // Every triple registered, and the conventions of its back end.
    struct Registered {
      std::string_view triple;
      const std::span<const std::string_view>* conventions;
    };

    const Registered registered[] = {
      { "x86_64-linux-none", &x86_64_known },
      { "x86_64-linux", &x86_64_known },
      { "x86_64", &x86_64_known },
      { "aarch64-linux-none", &aarch64_known },
      { "aarch64-linux", &aarch64_known },
      { "aarch64", &aarch64_known },
      { "arm64", &aarch64_known },
      { "riscv64-linux-none", &riscv64_known },
      { "riscv64-linux", &riscv64_known },
      { "riscv64", &riscv64_known },
    };

  } // anonymous namespace


  std::string_view architecture_of(std::string_view triple) noexcept
  {
    return triple.substr(0, triple.find('-'));
  }


  const std::span<const std::string_view>* conventions_of(std::string_view triple) noexcept
  {
    auto found = std::ranges::find(registered, triple, &Registered::triple);
    return found == std::ranges::end(registered) ? nullptr : found->conventions;
  }


  bool is_known_triple(std::string_view triple) noexcept
  {
    return conventions_of(triple) != nullptr;
  }

} // namespace pl4g::target
