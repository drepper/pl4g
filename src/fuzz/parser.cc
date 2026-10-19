// Fuzzing the parser: any text that is UTF-8 is lexed and parsed as a file of
// the program and as a module, and every tree is written out, which reads
// every field of every node and parses every body set aside.
#include <cstddef>
#include <cstdint>
#include <string>

#include "diag/engine.hh"
#include "front/dump.hh"
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
  for (bool module : { false, true }) {
    pl4g::ast::SyntaxArena arena;
    pl4g::DiagEngine diags([](const pl4g::Diagnostic& d) { (void) d.text(); });
    auto unit = pl4g::parse(arena, pl4g::tokenize(file, diags, module), "fuzz.pl4g", diags, module, &file);
    (void) pl4g::ast::dump(unit);
  }
  return 0;
}
