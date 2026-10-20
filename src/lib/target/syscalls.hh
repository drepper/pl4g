// The number each system call has, per architecture.
//
// A port of pypl4g's target/syscalls.py: what the runtime and the standard
// library ask for and nothing more, each number checked against the kernel's
// own table for the architecture.
#pragma once

#include <optional>
#include <span>
#include <string_view>


namespace pl4g::target {

  // The number `call` has on `architecture`, where it has one (`number_of`).
  std::optional<int> syscall_number(std::string_view architecture, std::string_view call) noexcept;

  // Every call any architecture names, sorted (`KNOWN`), for saying what a
  // mistyped one could have been.
  std::span<const std::string_view> known_syscalls() noexcept;

} // namespace pl4g::target
