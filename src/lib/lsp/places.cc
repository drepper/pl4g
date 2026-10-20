// Where the compiler's idea of a place and the editor's meet.
#include "lsp/places.hh"

#include <algorithm>
#include <array>
#include <format>

#include "sema/modules.hh"
#include "util/unicode.hh"


namespace pl4g::lsp {

  namespace {

    // Which to ask for, best first: a character count is what the compiler
    // has; a byte count costs one encode; sixteen-bit units the same, and
    // they are what a client that says nothing gets.
    constexpr std::array preferred{ Encoding::utf32, Encoding::utf8, Encoding::utf16 };

    // How many units of `encoding` the character `d` takes.
    std::size_t units(const unicode::Decoded& d, Encoding encoding)
    {
      switch (encoding) {
      case Encoding::utf8:
        return d.length;
      case Encoding::utf16:
        return d.cp >= 0x10000 ? 2 : 1;
      case Encoding::utf32:
        break;
      }
      return 1;
    }

    int hex(char c)
    {
      if (c >= '0' && c <= '9')
        return c - '0';
      if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
      if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
      return -1;
    }

    // Python's `unquote`: every `%XX` the byte it stands for.
    std::string unquoted(std::string_view text)
    {
      std::string out;
      for (std::size_t i = 0; i < text.size(); ++i)
        if (text[i] == '%' && i + 2 < text.size() && hex(text[i + 1]) >= 0 && hex(text[i + 2]) >= 0) {
          out += char(hex(text[i + 1]) * 16 + hex(text[i + 2]));
          i += 2;
        } else
          out += text[i];
      return out;
    }

  } // anonymous namespace


  std::string_view name_of(Encoding encoding) noexcept
  {
    switch (encoding) {
    case Encoding::utf8: return "utf-8";
    case Encoding::utf16: return "utf-16";
    case Encoding::utf32: return "utf-32";
    }
    return "utf-16";
  }


  Encoding agreed(const Json* offered)
  {
    auto list = offered != nullptr ? offered->elements() : nullptr;
    if (list == nullptr)
      return default_encoding;
    for (auto one : preferred)
      if (std::ranges::any_of(*list, [&](const Json& e) { return e.string() != nullptr && *e.string() == name_of(one); }))
        return one;
    return default_encoding;
  }


  std::size_t along(std::string_view line, std::size_t characters, Encoding encoding)
  {
    std::size_t at = 0;
    std::size_t counted = 0;
    for (std::size_t i = 0; i < characters && at < line.size(); ++i) {
      auto d = unicode::decode(line, at);
      counted += units(d, encoding);
      at += std::max(d.length, 1u);
    }
    return counted;
  }


  std::size_t characters(std::string_view line, std::int64_t offset, Encoding encoding)
  {
    std::size_t length = unicode::length(line);
    if (encoding == Encoding::utf32)
      return offset < 0 ? 0 : std::min(std::size_t(offset), length);
    std::int64_t so_far = 0;
    std::size_t index = 0;
    for (std::size_t at = 0; at < line.size(); ++index) {
      if (so_far >= offset)
        return index;
      auto d = unicode::decode(line, at);
      so_far += std::int64_t(units(d, encoding));
      at += std::max(d.length, 1u);
    }
    return length;
  }


  std::optional<Json> position_of(const SourceManager& sources, Loc loc, Encoding encoding)
  {
    auto found = sources.position(loc);
    auto line = sources.line_text(loc);
    if (! found || ! line)
      return std::nullopt;
    return Json::object({ { "line", std::int64_t(found->line) - 1 },
                          { "character", std::int64_t(along(*line, found->column - 1, encoding)) } });
  }


  std::optional<Json> range_of(const SourceManager& sources, Span span, Encoding encoding)
  {
    if (! span.is_valid())
      return std::nullopt;
    auto start = position_of(sources, span.start, encoding);
    auto end = position_of(sources, span.end, encoding);
    if (! start)
      return std::nullopt;
    return Json::object({ { "start", *start }, { "end", end ? *end : *start } });
  }


  Json whole_file()
  {
    auto origin = Json::object({ { "line", 0 }, { "character", 0 } });
    return Json::object({ { "start", origin }, { "end", origin } });
  }


  std::string to_uri(const std::filesystem::path& path)
  {
    std::error_code ignored;
    auto resolved = std::filesystem::weakly_canonical(std::filesystem::absolute(path, ignored), ignored);
    std::string out = "file://";
    // As Python's `quote` writes it: letters, digits, `_.-~` and `/` as they
    // are, every other byte as `%XX`.
    for (unsigned char c : resolved.string()) {
      if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.'
          || c == '-' || c == '~' || c == '/')
        out += char(c);
      else
        out += std::format("%{:02X}", unsigned(c));
    }
    return out;
  }


  std::optional<std::filesystem::path> from_uri(std::string_view uri)
  {
    // As Python's `urlparse` takes it apart: the scheme, the authority after
    // `//`, the path, and nothing of a query or a fragment.
    auto colon = uri.find(':');
    if (colon == std::string_view::npos || colon == 0)
      return std::nullopt;
    std::string scheme;
    for (char c : uri.substr(0, colon)) {
      if (! ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '+' || c == '-'
             || c == '.'))
        return std::nullopt;
      scheme += char(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
    }
    if (scheme != "file")
      return std::nullopt;
    auto rest = uri.substr(colon + 1);
    rest = rest.substr(0, rest.find_first_of("?#"));
    std::string_view netloc;
    if (rest.starts_with("//")) {
      rest.remove_prefix(2);
      auto slash = rest.find('/');
      netloc = rest.substr(0, slash);
      rest = slash == std::string_view::npos ? std::string_view() : rest.substr(slash);
    }
    if (! netloc.empty() && netloc != "localhost")
      return std::nullopt;
    return sema::pure_path(unquoted(rest));
  }

} // namespace pl4g::lsp
