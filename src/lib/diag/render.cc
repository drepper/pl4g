#include "diag/render.hh"

#include <algorithm>

#include "util/unicode.hh"


namespace pl4g {

  namespace {

    // The line of carets under a span starting at `column` and `width`
    // characters wide.  What stands before it is spaces, but a tab stays a
    // tab so the carets line up with the line as a terminal shows it.
    std::string caret_line(std::string_view line, unsigned column, std::size_t width)
    {
      std::string out;
      std::size_t at = 0;
      for (unsigned i = 1; i < column && at < line.size(); ++i) {
        auto d = unicode::decode(line, at);
        out += d.cp == U'\t' ? '\t' : ' ';
        at += d.length;
      }
      out += '^';
      out.append(width > 0 ? width - 1 : 0, '~');
      return out;
    }

  } // anonymous namespace


  void TextRenderer::operator()(const Diagnostic& diag)
  {
    auto position = diag.span.is_valid() ? sources.position(diag.span.start) : std::nullopt;
    if (position)
      out.print("{}:{}:{}: ", position->path, position->line, position->column);
    Severity severity = engine != nullptr ? engine->effective_severity(*diag.info) : diag.info->severity;
    out.println("{}: {} [PL4G-{}]", severity, diag.text(), unsigned(diag.info->number));
    if (position)
      snippet(diag.span, position->line, position->column);
    out.flush();
  }


  void TextRenderer::snippet(Span span, unsigned line, unsigned column)
  {
    auto text = sources.line_text(span.start);
    if (! text)
      return;
    auto spanned = sources.snippet({ span.start, std::max(span.start, span.end) });
    std::size_t covered = spanned ? unicode::length(*spanned) : 0;
    std::size_t room = unicode::length(*text) + 1 - column;
    std::size_t width = std::max<std::size_t>(1, std::min(covered, room));
    std::string number = std::to_string(line);
    out.println(" {} | {}", number, *text);
    out.println(" {} | {}", std::string(number.size(), ' '), caret_line(*text, column, width));
  }

} // namespace pl4g
