// Fuzzing the lexer: any text that is UTF-8 is lexed plainly and lazily, and
// every body left as text is lexed in its turn.  Nothing may crash, loop or
// read outside the text, and every token must lie inside the file.
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>

#include "diag/engine.hh"
#include "front/lexer.hh"
#include "source/manager.hh"
#include "util/unicode.hh"


namespace {

  // Stop the run where a token lies outside the file it came from.
  void check(const pl4g::SourceFile& file, const std::vector<pl4g::Token>& tokens)
  {
    for (const auto& t : tokens)
      if (t.span.start < file.base || t.span.end > file.end() || t.span.start > t.span.end) [[unlikely]]
        std::abort();
  }

} // anonymous namespace


extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size)
{
  std::string text(reinterpret_cast<const char*>(data), size);
  if (pl4g::unicode::first_invalid(text))
    return -1;
  pl4g::SourceManager sources;
  const auto& file = sources.add("fuzz.pl4g", std::move(text));
  pl4g::DiagEngine diags([&](const pl4g::Diagnostic& d) {
    // Rendering the message is part of what is exercised.
    (void) d.text();
  });
  check(file, pl4g::tokenize(file, diags));
  auto lazy = pl4g::tokenize(file, diags, true);
  check(file, lazy);
  for (const auto& t : lazy)
    if (t.kind == pl4g::TokKind::body)
      check(file, pl4g::tokenize_body(file, t.span, diags));
  return 0;
}
