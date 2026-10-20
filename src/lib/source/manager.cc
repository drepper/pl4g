#include "source/manager.hh"

#include <cerrno>
#include <cstring>

#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>

#include "util/unicode.hh"


namespace pl4g {

  namespace {

    // The offset of the first byte of every line of `text`.
    std::vector<std::uint32_t> compute_line_starts(std::string_view text)
    {
      std::vector<std::uint32_t> starts{ 0 };
      for (std::size_t i = text.find('\n'); i != std::string_view::npos; i = text.find('\n', i + 1))
        starts.push_back(std::uint32_t(i + 1));
      return starts;
    }

    // The whole contents of the file at `path`, or what the system said.
    std::expected<std::string, std::string> slurp(const std::filesystem::path& path)
    {
      int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
      if (fd < 0) [[unlikely]]
        return std::unexpected(std::string(::strerror(errno)));
      std::string data;
      struct ::stat st;
      if (::fstat(fd, &st) == 0 && S_ISDIR(st.st_mode)) [[unlikely]] {
        ::close(fd);
        return std::unexpected(std::string(::strerror(EISDIR)));
      }
      char buf[65536];
      while (true) {
        auto n = ::read(fd, buf, sizeof(buf));
        if (n < 0) [[unlikely]] {
          if (errno == EINTR)
            continue;
          std::string reason = ::strerror(errno);
          ::close(fd);
          return std::unexpected(std::move(reason));
        }
        if (n == 0)
          break;
        data.append(buf, std::size_t(n));
      }
      ::close(fd);
      return data;
    }

  } // anonymous namespace


  const SourceFile& SourceManager::add(std::filesystem::path path, std::string text, int line_shift,
                                       unsigned added_indent)
  {
    auto& file = all.emplace_back();
    file.line_shift = line_shift;
    file.added_indent = added_indent;
    file.ident = FileId(all.size() - 1);
    file.shown = path.generic_string();
    file.path = std::move(path);
    file.line_starts = compute_line_starts(text);
    file.text = std::move(text);
    file.base = next_base;
    // One past the end, so the end of one file is never the start of the next.
    next_base = file.end() + 1;
    return file;
  }


  std::expected<const SourceFile*, ReadFailure> SourceManager::read(const std::filesystem::path& path)
  {
    if (! held.empty()) {
      std::error_code ignored;
      auto resolved = std::filesystem::weakly_canonical(std::filesystem::absolute(path, ignored), ignored);
      for (const auto& [where, text] : held)
        if (where == path
            || std::filesystem::weakly_canonical(std::filesystem::absolute(where, ignored), ignored) == resolved)
          return &add(path, text);
    }
    auto data = slurp(path);
    if (! data) [[unlikely]]
      return std::unexpected(ReadFailure{ ReadFailure::Kind::unreadable, std::move(data.error()) });
    if (auto bad = unicode::first_invalid(*data)) [[unlikely]]
      return std::unexpected(ReadFailure{ ReadFailure::Kind::not_utf8, {}, *bad });
    return &add(path, std::move(*data));
  }


  const SourceFile* SourceManager::file_of(Loc loc) const noexcept
  {
    if (loc < 0 || all.empty())
      return nullptr;
    auto it = std::upper_bound(all.begin(), all.end(), loc,
                               [](Loc l, const SourceFile& f) { return l < f.base; });
    if (it == all.begin())
      return nullptr;
    const SourceFile& file = *std::prev(it);
    return loc <= file.end() ? &file : nullptr;
  }


  std::optional<Position> SourceManager::position(Loc loc) const noexcept
  {
    auto file = file_of(loc);
    if (file == nullptr)
      return std::nullopt;
    auto offset = std::uint32_t(loc - file->base);
    auto line = std::upper_bound(file->line_starts.begin(), file->line_starts.end(), offset) - 1;
    std::string_view before = std::string_view(file->text).substr(*line, offset - *line);
    auto column = unsigned(unicode::length(before)) + 1;
    if (file->added_indent > 0 && before.size() >= file->added_indent
        && before.substr(0, file->added_indent).find_first_not_of(' ') == std::string_view::npos)
      column -= file->added_indent;
    return Position{ file->shown, unsigned(int(line - file->line_starts.begin()) + 1 + file->line_shift), column };
  }


  std::optional<std::string_view> SourceManager::line_text(Loc loc) const noexcept
  {
    auto file = file_of(loc);
    if (file == nullptr)
      return std::nullopt;
    auto offset = std::uint32_t(loc - file->base);
    auto start = *(std::upper_bound(file->line_starts.begin(), file->line_starts.end(), offset) - 1);
    std::string_view text = file->text;
    auto end = text.find('\n', start);
    auto line = text.substr(start, end == std::string_view::npos ? std::string_view::npos : end - start);
    if (file->added_indent > 0 && line.size() >= file->added_indent
        && line.substr(0, file->added_indent).find_first_not_of(' ') == std::string_view::npos)
      line.remove_prefix(file->added_indent);
    return line;
  }


  std::optional<std::pair<std::string_view, std::size_t>> SourceManager::line_within(Loc loc) const noexcept
  {
    auto file = file_of(loc);
    auto line = line_text(loc);
    if (file == nullptr || ! line)
      return std::nullopt;
    return std::pair{ std::string_view(file->text), std::size_t(line->data() - file->text.data()) };
  }


  std::optional<std::string_view> SourceManager::snippet(Span span) const noexcept
  {
    auto file = file_of(span.start);
    if (file == nullptr)
      return std::nullopt;
    return file->slice(span);
  }

} // namespace pl4g
