// Print the tokens and the diagnostics of source files, one per line, in the
// form src/test/lexer_agrees.py compares with what pypl4g makes of them.
//
// Usage: pl4g-lexdump [--lazy] FILE...
#include <argp.h>

#include <string>
#include <vector>

#include "diag/engine.hh"
#include "front/lexer.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


namespace {

  struct Arguments {
    bool lazy = false;
    std::vector<std::string> files;
  };

  constexpr ::argp_option options[] = {
    { "lazy", 'l', nullptr, 0, "Leave top-level function bodies as text, as for a module", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case 'l':
      args.lazy = true;
      return 0;
    case ARGP_KEY_ARG:
      args.files.emplace_back(arg);
      return 0;
    case ARGP_KEY_END:
      if (args.files.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, "FILE...", "Print the tokens of PL4G source files.",
                              nullptr, nullptr, nullptr };


  // `text` with every byte outside printable ASCII, and the backslash, escaped.
  std::string escaped(std::string_view text)
  {
    std::string out;
    for (unsigned char c : text)
      if (c == '\\' || c < 0x20 || c >= 0x7f)
        out += std::format("\\x{:02x}", unsigned(c));
      else
        out += char(c);
    return out;
  }


  // The decimal digits of a 128-bit integer.
  std::string decimal(__int128 v)
  {
    if (v == 0)
      return "0";
    bool negative = v < 0;
    unsigned __int128 m = negative ? -(unsigned __int128)(v) : (unsigned __int128)(v);
    std::string out;
    for (; m > 0; m /= 10)
      out.insert(out.begin(), char('0' + int(m % 10)));
    return negative ? "-" + out : out;
  }

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  pl4g::FdWriter out(1);
  pl4g::SourceManager sources;
  for (const auto& path : args.files) {
    pl4g::CollectingEngine diags;
    auto read = sources.read(path);
    if (! read) [[unlikely]] {
      out.println("unreadable {}", path);
      continue;
    }
    const pl4g::SourceFile& file = **read;
    auto tokens = pl4g::tokenize(file, diags.engine, args.lazy);
    out.println("file {}", path);
    for (const auto& t : tokens) {
      out.print("{} {} {} {}", pl4g::enum_name(t.kind), t.span.start - file.base, t.span.end - file.base,
                escaped(t.text));
      switch (t.kind) {
      case pl4g::TokKind::int_lit:
      case pl4g::TokKind::char_lit:
      case pl4g::TokKind::exponent:
        out.print(" {}", t.int_too_large ? "big" : decimal(t.int_value));
        break;
      case pl4g::TokKind::float_lit:
        out.print(" {}", t.float_value);
        break;
      case pl4g::TokKind::string_lit:
        out.print(" {}", escaped(t.str_value));
        break;
      default:
        break;
      }
      if (! t.type_suffix.empty())
        out.print(" :{}", t.type_suffix);
      out.println("");
    }
    for (auto d : diags.collected)
      out.println("diag {} {}", unsigned(d->info->number), d->span.start - file.base);
    out.flush();
  }
}
