// What a condition written in a signature does in this build.
//
// A port of pypl4g's sema/clauses.py.  A clause says what must be true and a
// build says what to do about it, as g++ divides it with
// `-fcontract-evaluation-semantic`; the choice is the build's, never the
// clause's, and a requirement (a clause about types) is never a choice at all.
#pragma once

#include <cstdint>
#include <optional>
#include <string_view>

#include "util/reflect.hh"


namespace pl4g::sema {

  // The three things a build may ask a condition to do (`Conditions`).
  enum class Conditions : std::uint8_t {
    // Evaluate it and stop where it does not hold, with 89 for a `pre` and 90
    // for a `post`.  The default.
    check,
    // Evaluate nothing and emit nothing; the report log says what was dropped.
    ignore,
    // Evaluate it, say so where it does not hold, and go on.
    observe,
  };

  // The semantic a command line names, as `Conditions(value)` reads it.
  std::optional<Conditions> conditions_named(std::string_view value) noexcept;

  // What `assume` would be, and why it is not offered: the one semantic that
  // could make a program go wrong silently (`NOT_OFFERED`).
  inline constexpr std::string_view not_offered[] = { "assume" };

} // namespace pl4g::sema
