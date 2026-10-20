// What a documentation comment says, beyond saying it.
#include "front/doccomment.hh"

#include <algorithm>
#include <array>
#include <utility>

#include "util/unicode.hh"


namespace pl4g::doc {

  namespace {

    // What each command may be written as, and what it is called here.
    constexpr std::pair<std::string_view, std::string_view> commands[] = {
      { "brief", "brief" }, { "short", "brief" },
      { "details", "details" }, { "detail", "details" },
      { "param", "param" }, { "arg", "param" }, { "argument", "param" },
      { "return", "return" }, { "returns", "return" }, { "result", "return" },
      { "raises", "raises" }, { "raise", "raises" }, { "throws", "raises" }, { "throw", "raises" },
      { "exception", "raises" },
      { "note", "note" }, { "remark", "note" }, { "remarks", "note" },
      { "warning", "warning" }, { "attention", "warning" },
      { "see", "see" }, { "sa", "see" },
      { "pre", "pre" }, { "post", "post" }, { "invariant", "invariant" },
      { "since", "since" }, { "deprecated", "deprecated" }, { "todo", "todo" },
      { "author", "author" }, { "file", "file" }, { "example", "example" },
    };


    // Whether `cp` ends a line for Python's `str.splitlines`.
    bool breaks_line(char32_t cp) noexcept
    {
      switch (cp) {
      case U'\n':
      case U'\r':
      case U'\v':
      case U'\f':
      case U'\x1c':
      case U'\x1d':
      case U'\x1e':
      case U'\x85':
      case U'\N{LINE SEPARATOR}':
      case U'\N{PARAGRAPH SEPARATOR}':
        return true;
      default:
        return false;
      }
    }


    // Python's `str.splitlines()`: the lines without their breaks, and no
    // empty line after a final break.
    std::vector<std::string_view> split_lines(std::string_view text)
    {
      std::vector<std::string_view> lines;
      std::size_t begin = 0;
      std::size_t at = 0;
      while (at < text.size()) {
        auto [cp, length] = unicode::decode(text, at);
        if (! breaks_line(cp)) {
          at += length;
          continue;
        }
        lines.push_back(text.substr(begin, at - begin));
        at += length;
        if (cp == U'\r' && at < text.size() && text[at] == '\n')
          ++at;
        begin = at;
      }
      if (begin < text.size())
        lines.push_back(text.substr(begin));
      return lines;
    }


    // How many bytes of white space, as `str.isspace` has it, `text` begins with.
    std::size_t leading_space(std::string_view text) noexcept
    {
      std::size_t at = 0;
      while (at < text.size()) {
        auto [cp, length] = unicode::decode(text, at);
        if (! unicode::is_space(cp))
          break;
        at += length;
      }
      return at;
    }


    // Python's `str.lstrip()`, `str.rstrip()` and `str.strip()`.
    std::string_view lstrip(std::string_view text) noexcept
    {
      return text.substr(leading_space(text));
    }

    std::string_view rstrip(std::string_view text) noexcept
    {
      // Decoding from the front is what finds where the last run of white
      // space begins; a comment's lines are short.
      std::size_t keep = 0;
      std::size_t at = 0;
      while (at < text.size()) {
        auto [cp, length] = unicode::decode(text, at);
        at += length;
        if (! unicode::is_space(cp))
          keep = at;
      }
      return text.substr(0, keep);
    }

    std::string_view strip(std::string_view text) noexcept
    {
      return rstrip(lstrip(text));
    }


    // `"\n".join(lines)`.
    std::string joined(const std::vector<std::string_view>& lines)
    {
      std::string out;
      for (std::size_t i = 0; i < lines.size(); ++i) {
        if (i > 0)
          out += '\n';
        out += lines[i];
      }
      return out;
    }


    // What `[ \t]*[\\@]([A-Za-z]+)[ \t]*(.*)` matched of a line.
    struct Command {
      std::string_view word;
      std::string_view rest;
    };

    bool blank(char c) noexcept
    {
      return c == ' ' || c == '\t';
    }

    bool letter(char c) noexcept
    {
      return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
    }

    // The command a line begins with, where it begins with one.
    std::optional<Command> command_on(std::string_view line) noexcept
    {
      std::size_t at = 0;
      while (at < line.size() && blank(line[at]))
        ++at;
      if (at == line.size() || (line[at] != '\\' && line[at] != '@'))
        return std::nullopt;
      std::size_t word = ++at;
      while (at < line.size() && letter(line[at]))
        ++at;
      if (at == word)
        return std::nullopt;
      Command found{ line.substr(word, at - word), {} };
      while (at < line.size() && blank(line[at]))
        ++at;
      found.rest = line.substr(at);
      return found;
    }


    // What follows `\param`, with `[in]`, `[out]` or `[in,out]` taken off its
    // front: the direction is in the type, so it is read and dropped.
    std::string_view without_direction(std::string_view rest) noexcept
    {
      std::size_t at = 0;
      auto skip_blanks = [&] {
        while (at < rest.size() && blank(rest[at]))
          ++at;
      };
      auto direction = [&] {
        for (std::string_view word : { std::string_view("in"), std::string_view("out") })
          if (rest.substr(at).starts_with(word)) {
            at += word.size();
            return true;
          }
        return false;
      };
      if (rest.empty() || rest[0] != '[')
        return rest;
      ++at;
      skip_blanks();
      if (! direction())
        return rest;
      // The optional second direction: backtrack if it is not all there.
      std::size_t before = at;
      skip_blanks();
      if (at < rest.size() && rest[at] == ',') {
        ++at;
        skip_blanks();
        if (! direction())
          at = before;
      } else
        at = before;
      skip_blanks();
      if (at == rest.size() || rest[at] != ']')
        return rest;
      return rest.substr(at + 1);
    }


    // A part being read, finished when the next one begins.
    struct Building {
      Part part;
      std::vector<std::string_view> more;

      // The part, with its continuation lines joined onto its text.
      Part settled() &&
      {
        std::vector<std::string_view> lines{ part.text };
        lines.insert(lines.end(), more.begin(), more.end());
        while (! lines.empty() && lines.back().empty())
          lines.pop_back();
        part.text = std::string(strip(joined(lines)));
        return std::move(part);
      }
    };


    // Begin a part from the command found on line `number`.
    Building started(int number, std::string_view line, const Command& found)
    {
      std::string lowered(found.word);
      std::ranges::transform(lowered, lowered.begin(), [](char c) { return c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c; });
      Building made;
      made.part.word = std::string(found.word);
      made.part.command = command_named(lowered);
      auto rest = found.rest;
      if (made.part.command == "param") {
        rest = lstrip(without_direction(rest));
        auto space = rest.find(' ');
        auto name = rest.substr(0, space);
        auto after = space == std::string_view::npos ? std::string_view() : rest.substr(space + 1);
        made.part.subject = std::string(strip(name));
        rest = lstrip(after);
      }
      // The sigil is the first character after the line's indentation, which is
      // what the command was matched past.
      auto column = leading_space(line);
      made.part.sigil = line[column];
      made.part.text = std::string(strip(rest));
      made.part.line = number;
      made.part.column = static_cast<int>(column);
      return made;
    }

  } // anonymous namespace


  const std::vector<std::string_view> order = {
    "details", "param", "return", "raises", "pre", "post", "invariant", "note", "warning", "example", "see", "since",
    "deprecated", "todo", "author", "file",
  };


  std::string_view command_named(std::string_view word) noexcept
  {
    auto found = std::ranges::find(commands, word, &std::pair<std::string_view, std::string_view>::first);
    return found == std::ranges::end(commands) ? std::string_view() : found->second;
  }


  std::string Doc::brief() const
  {
    if (summary.empty())
      return {};
    auto lines = split_lines(summary);
    return lines.empty() ? std::string() : std::string(strip(lines.front()));
  }


  std::vector<const Part*> Doc::of(std::string_view command) const
  {
    std::vector<const Part*> found;
    for (const auto& one : parts)
      if (one.command == command)
        found.push_back(&one);
    return found;
  }


  const Part* Doc::first(std::string_view command) const
  {
    auto found = std::ranges::find(parts, command, &Part::command);
    return found == parts.end() ? nullptr : &*found;
  }


  Doc parse(std::string_view text)
  {
    std::vector<std::string_view> summary;
    std::vector<Building> parts;
    int number = 0;
    for (auto line : split_lines(text)) {
      if (auto found = command_on(line))
        parts.push_back(started(number, line, *found));
      else if (! parts.empty())
        parts.back().more.push_back(strip(line));
      else
        summary.push_back(rstrip(line));
      ++number;
    }
    std::vector<Part> made;
    for (auto& one : parts)
      made.push_back(std::move(one).settled());
    std::string said(strip(joined(summary)));
    if (said.empty()) {
      // A comment that opens with `\brief` and nothing before it: what it says
      // is the summary, wherever it was written.
      auto first = std::ranges::find(made, std::string_view("brief"), &Part::command);
      if (first != made.end())
        said = first->text;
    }
    Doc doc{ .summary = said, .parts = {} };
    for (auto& one : made)
      if (one.command != "brief" || one.text != said)
        doc.parts.push_back(std::move(one));
    return doc;
  }

} // namespace pl4g::doc
