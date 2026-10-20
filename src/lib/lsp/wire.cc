// The messages an editor and a server exchange, and how they are framed.
#include "lsp/wire.hh"

#include <unistd.h>

#include <cerrno>
#include <charconv>
#include <format>

#include "util/fdio.hh"


namespace pl4g::lsp {

  namespace {

    // What every message says about itself, and the one field that matters.
    constexpr std::string_view length_header = "content-length";

    std::string_view stripped(std::string_view text)
    {
      auto first = text.find_first_not_of(" \t\r\n\f\v");
      if (first == std::string_view::npos)
        return {};
      auto last = text.find_last_not_of(" \t\r\n\f\v");
      return text.substr(first, last - first + 1);
    }

  } // anonymous namespace


  bool Wire::more()
  {
    char buffer[65536];
    while (true) {
      auto got = ::read(reader, buffer, sizeof buffer);
      if (got < 0 && errno == EINTR)
        continue;
      if (got <= 0)
        return false;
      pending.append(buffer, std::size_t(got));
      return true;
    }
  }


  std::string Wire::line()
  {
    while (true) {
      if (auto end = pending.find('\n'); end != std::string::npos) {
        std::string found = pending.substr(0, end + 1);
        pending.erase(0, end + 1);
        return found;
      }
      if (! more())
        return std::exchange(pending, {});
    }
  }


  std::string Wire::exactly(std::size_t length)
  {
    while (pending.size() < length)
      if (! more()) [[unlikely]]
        throw Closed();
    std::string found = pending.substr(0, length);
    pending.erase(0, length);
    return found;
  }


  Json Wire::read()
  {
    long long length = -1;
    while (true) {
      auto found = line();
      if (found.empty())
        throw Closed();
      auto header = stripped(found);
      if (header.empty())
        break;
      auto colon = header.find(':');
      auto name = stripped(header.substr(0, colon));
      std::string lower;
      for (char c : name)
        lower += char(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
      if (lower == length_header) {
        auto value = colon == std::string_view::npos ? std::string_view() : stripped(header.substr(colon + 1));
        long long n = 0;
        auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), n);
        if (error != std::errc() || end != value.data() + value.size() || value.empty()) [[unlikely]]
          throw Closed();
        length = n;
      }
    }
    if (length < 0) [[unlikely]]
      throw Closed();
    auto body = exactly(std::size_t(length));
    // A message that is not JSON is not something to answer: an answer names
    // the request it answers and there is no name to be had.
    auto parsed = Json::parse(body);
    if (! parsed || ! parsed->is_object())
      return Json::object();
    return std::move(*parsed);
  }


  void Wire::write(const Json& message)
  {
    auto body = message.dump();
    write_all(writer, std::format("Content-Length: {}\r\n\r\n", body.size()) + body);
  }

} // namespace pl4g::lsp
