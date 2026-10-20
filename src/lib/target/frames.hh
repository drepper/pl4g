// Where every function's return address is, so that a fault can say who
// called: one row per function that calls, in address order, and a sentinel.
// A port of pypl4g/target/frames.py.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "mc/asmbuilder.hh"


namespace pl4g::target {

  inline constexpr std::string_view frames_section = ".rodata";
  // What the table is called, and the label after the last function.
  inline constexpr std::string_view frames_symbol = "__pl4g_frames";
  inline constexpr std::string_view frames_end_symbol = ".Lpl4g.frames.end";
  // How many bytes one row is.
  inline constexpr std::uint64_t row_bytes = 24;
  // What a row's return-address field holds where there is none.
  inline constexpr std::uint32_t no_return_address = 0x7FFFFFFF;

  // What is printed for a frame of the function called `name`.
  std::string line_for(std::string_view name);

  // One function's row, as it stands before the addresses are known.
  struct Frame {
    std::string symbol;
    std::optional<std::int64_t> return_at;
    std::int64_t caller_at = 0;
    std::string line;
  };

  // The rows a compilation collects, and the table they are emitted as.
  struct Frames {
    std::vector<Frame> rows;
    // Whether anything will walk, which is whether a fault can happen at all.
    bool wanted = false;

    // Note where the function called `name` keeps its return address; one
    // that keeps it nowhere gets no row.
    void record(std::string symbol, std::string_view name, std::optional<std::int64_t> return_at,
                std::int64_t caller_at);
    // Put the table and the lines it names into the image.
    void emit(mc::Assembler& asm_) const;
  };

} // namespace pl4g::target
