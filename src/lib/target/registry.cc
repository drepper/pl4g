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
      // The back end that answers to it, and whether the triple names a
      // target rather than abbreviating one.
      std::string_view backend;
      bool canonical;
    };

    // In the order pypl4g registers them.
    const Registered registered[] = {
      { "x86_64-linux-none", &x86_64_known, "x86_64", true },
      { "x86_64-linux", &x86_64_known, "x86_64", false },
      { "x86_64", &x86_64_known, "x86_64", false },
      { "aarch64-linux-none", &aarch64_known, "aarch64", true },
      { "aarch64-linux", &aarch64_known, "aarch64", false },
      { "aarch64", &aarch64_known, "aarch64", false },
      { "arm64", &aarch64_known, "aarch64", false },
      { "riscv64-linux-none", &riscv64_known, "riscv64", true },
      { "riscv64-linux", &riscv64_known, "riscv64", false },
      { "riscv64", &riscv64_known, "riscv64", false },
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


  std::string_view backend_of(std::string_view triple) noexcept
  {
    auto found = std::ranges::find(registered, triple, &Registered::triple);
    return found == std::ranges::end(registered) ? std::string_view() : found->backend;
  }


  std::vector<std::string_view> canonical_triples()
  {
    std::vector<std::string_view> found;
    for (const auto& one : registered)
      if (one.canonical)
        found.push_back(one.triple);
    return found;
  }


  std::vector<std::string_view> known_triples()
  {
    std::vector<std::string_view> found;
    for (const auto& one : registered)
      found.push_back(one.triple);
    std::ranges::sort(found);
    return found;
  }

} // namespace pl4g::target
