// Fuzzing the checker: any text that is UTF-8 is read, expanded and checked
// as a program, every diagnostic written out.  What is wrong with the text is
// said and is no finding; a fault, a sanitizer's complaint or a run that does
// not end is.  An internal error is the checker refusing what it did not
// expect, said and caught where the driver would catch it.
#include <cstddef>
#include <cstdint>
#include <string>

#include "diag/engine.hh"
#include "driver/compile.hh"
#include "front/lexer.hh"
#include "front/parser.hh"
#include "source/manager.hh"
#include "util/unicode.hh"


extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size)
{
  std::string text(reinterpret_cast<const char*>(data), size);
  if (pl4g::unicode::first_invalid(text))
    return -1;
  pl4g::SourceManager sources;
  const auto& file = sources.add("fuzz.pl4g", std::move(text));
  pl4g::DiagEngine diags([](const pl4g::Diagnostic& d) { (void) d.text(); });
  pl4g::driver::Compilation compilation({}, diags, sources);
  try {
    std::vector<const pl4g::ast::SourceUnit*> units{ pl4g::parse(compilation.arena, pl4g::tokenize(file, diags, false),
                                                                 "fuzz.pl4g", diags, false, &file) };
    auto before = diags.error_count;
    auto expanded = compilation.expand(units);
    if (diags.error_count == before)
      (void) compilation.analyze(expanded);
  } catch (const pl4g::InternalError&) {
  }
  return 0;
}
