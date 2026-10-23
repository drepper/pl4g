// The number each system call has, per architecture.
#include "target/syscalls.hh"

#include <algorithm>
#include <array>
#include <utility>
#include <vector>


namespace pl4g::target {

  namespace {

    using Numbered = std::pair<std::string_view, int>;

    // What the two architectures on the kernel's generic numbering call things.
    constexpr Numbered generic[] = {
      { "openat", 56 },
      { "close", 57 },
      { "pipe2", 59 },
      { "read", 63 },
      { "write", 64 },
      { "readv", 65 },
      { "writev", 66 },
      { "exit", 93 },
      { "exit_group", 94 },
      { "waitid", 95 },
      { "clock_gettime", 113 },
      { "clock_getres", 114 },
      { "munmap", 215 },
      { "mmap", 222 },
      { "wait4", 260 },
      { "io_uring_setup", 425 },
      { "io_uring_enter", 426 },
      { "io_uring_register", 427 },
      // One the generic table has and x86-64 does not.
      { "fadvise64_64", 223 },
    };

    // And what x86-64, numbered before that table existed, calls them.
    constexpr Numbered x86_64[] = {
      { "read", 0 },
      { "write", 1 },
      { "close", 3 },
      { "open", 2 },
      { "pipe2", 293 },
      { "readv", 19 },
      { "writev", 20 },
      { "mmap", 9 },
      { "munmap", 11 },
      { "wait4", 61 },
      { "exit", 60 },
      { "exit_group", 231 },
      { "waitid", 247 },
      { "clock_gettime", 228 },
      { "clock_getres", 229 },
      { "openat", 257 },
      { "io_uring_setup", 425 },
      { "io_uring_enter", 426 },
      { "io_uring_register", 427 },
    };

    // The table of an architecture, or nothing for one this does not know.
    std::span<const Numbered> table_of(std::string_view architecture) noexcept
    {
      if (architecture == "x86_64")
        return x86_64;
      if (architecture == "aarch64" || architecture == "riscv64")
        return generic;
      return {};
    }

  } // anonymous namespace


  std::optional<int> syscall_number(std::string_view architecture, std::string_view call) noexcept
  {
    for (const auto& [name, number] : table_of(architecture))
      if (name == call)
        return number;
    return std::nullopt;
  }


  std::span<const std::string_view> known_syscalls() noexcept
  {
    static const std::vector<std::string_view> known = [] {
      std::vector<std::string_view> all;
      for (const auto& one : generic)
        all.push_back(one.first);
      for (const auto& one : x86_64)
        all.push_back(one.first);
      std::ranges::sort(all);
      auto [first, last] = std::ranges::unique(all);
      all.erase(first, last);
      return all;
    }();
    return known;
  }

} // namespace pl4g::target
