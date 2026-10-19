// Positions in the source text.
//
// A position is one integer offset into a coordinate space that holds every
// file of a compilation end to end, so a span is two integers and refers to no
// file object.  The way back to a file, a line and a column is taken only when
// something is shown to a reader, which is the rare case.
//
// Offsets count bytes of UTF-8.  Columns are counted in characters, as the
// Python implementation counts them, so the two report the same places.
#pragma once

#include <algorithm>
#include <cstdint>
#include <string_view>


namespace pl4g {

  // An offset into the coordinate space of one compilation.
  using Loc = std::int64_t;

  // The index of a file in its source manager.
  using FileId = std::uint32_t;

  inline constexpr Loc no_loc = -1;


  // A half-open range of source text, [start, end).
  struct Span {
    Loc start = no_loc;
    Loc end = no_loc;

    // Whether the span refers to real source text.
    constexpr bool is_valid() const noexcept { return start >= 0; }

    // The smallest span covering this one and `other`.
    constexpr Span to(Span other) const noexcept
    {
      return { std::min(start, other.start), std::max(end, other.end) };
    }

    constexpr bool operator==(const Span&) const noexcept = default;
  };

  inline constexpr Span invalid_span{};


  // A position resolved for a reader: path, 1-based line and column.
  struct Position {
    std::string_view path;
    unsigned line = 0;
    unsigned column = 0;
  };

} // namespace pl4g
