// What the front end asks about a target triple.
//
// The part of pypl4g's target/registry.py the checker and the expander use:
// which architecture a triple names, and which calling conventions its back
// end knows -- read off a table, without any back end.
#pragma once

#include <span>
#include <string_view>


namespace pl4g::target {

  // The triple a build is for when nothing says otherwise (`DEFAULT_TRIPLE`).
  inline constexpr std::string_view default_triple = "x86_64-linux-none";

  // The architecture named by `triple`, its first component (`architecture_of`).
  std::string_view architecture_of(std::string_view triple) noexcept;

  // Every calling convention the back end for `triple` knows, sorted, or
  // nothing where no back end answers to it (`conventions_of`).
  const std::span<const std::string_view>* conventions_of(std::string_view triple) noexcept;

  // Whether a back end answers to `triple` at all.
  bool is_known_triple(std::string_view triple) noexcept;

} // namespace pl4g::target
