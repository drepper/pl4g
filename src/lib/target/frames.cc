// Where every function's return address is.
#include "target/frames.hh"
#include "target/emit.hh"

#include "util/ordered.hh"


namespace pl4g::target {

  namespace {

    // Where each distinct line goes and how long it is.
    OrderedMap<std::string, std::pair<std::uint64_t, std::uint64_t>> lines_of(const std::vector<Frame>& rows)
    {
      OrderedMap<std::string, std::pair<std::uint64_t, std::uint64_t>> found;
      std::uint64_t at = 0;
      for (const auto& row : rows) {
        if (found.contains(row.line))
          continue;
        found[row.line] = { at, row.line.size() };
        at += row.line.size();
      }
      return found;
    }

    void put(mc::Bytes& out, std::uint64_t value, unsigned bytes)
    {
      for (unsigned i = 0; i < bytes; ++i)
        out.push_back(std::uint8_t(value >> (8 * i)));
    }

    // One row's bytes, with the address left for the fixup to fill in.
    mc::Bytes row_of(const Frame& row, std::uint64_t line_at, std::uint64_t line_length)
    {
      if ((row.return_at && *row.return_at >= no_return_address) || row.caller_at >= no_return_address) [[unlikely]]
        throw InternalError("a frame too large for its row");
      mc::Bytes out;
      put(out, 0, 8);
      put(out, row.return_at ? std::uint64_t(*row.return_at) : no_return_address, 4);
      put(out, std::uint64_t(row.caller_at), 4);
      put(out, line_at, 4);
      put(out, line_length, 4);
      return out;
    }

    std::vector<mc::MCFixup> address_of(mc::Assembler& asm_, const std::string& symbol)
    {
      mc::MCFixup fixup;
      fixup.offset = 0;
      fixup.kind = &mc::abs64;
      fixup.target = mc::sym_expr(asm_.symbol_named(symbol));
      return { std::move(fixup) };
    }

  } // anonymous namespace


  std::string line_for(std::string_view name)
  {
    return "  called from '" + std::string(name) + "'\n";
  }


  void Frames::record(std::string symbol, std::string_view name, std::optional<std::int64_t> return_at,
                      std::int64_t caller_at)
  {
    if (! return_at)
      return;
    rows.push_back({ std::move(symbol), return_at, caller_at, line_for(name) });
  }


  void Frames::emit(mc::Assembler& asm_) const
  {
    if (! wanted)
      return;
    asm_.section(std::string(frames_section), placed(false, 8));
    asm_.align(8);
    auto table = asm_.label(std::string(frames_symbol), mc::SymBinding::local, mc::SymKind::object,
                            mc::SymVisibility::hidden);
    auto lines = lines_of(rows);
    auto at = (rows.size() + 1) * row_bytes;
    for (const auto& row : rows) {
      auto [offset, length] = *lines.get(row.line);
      asm_.bytes(row_of(row, at + offset, length), address_of(asm_, row.symbol));
    }
    // The sentinel, emitted even where there are no rows at all.
    Frame sentinel{ std::string(frames_end_symbol), std::nullopt, 0, "" };
    asm_.bytes(row_of(sentinel, 0, 0), address_of(asm_, sentinel.symbol));
    for (const auto& [text, _] : lines)
      asm_.bytes(mc::Bytes(text.begin(), text.end()));
    asm_.end_label(table);
  }

} // namespace pl4g::target
