// Print the syntax tree and the diagnostics of source files, in the form
// src/test/parser_agrees.py compares with what pypl4g makes of them.
//
// Usage: pl4g-astdump [--module] FILE...
#include <argp.h>

#include <string>
#include <vector>

#include "diag/engine.hh"
#include "front/dump.hh"
#include "front/lexer.hh"
#include "front/parser.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


namespace {

  struct Arguments {
    bool module = false;
    std::vector<std::string> files;
  };

  constexpr ::argp_option options[] = {
    { "module", 'm', nullptr, 0, "Read the files as modules are read: bodies when something asks for them", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case 'm':
      args.module = true;
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

  constexpr ::argp parser = { options, parse_option, "FILE...", "Print the syntax trees of PL4G source files.",
                              nullptr, nullptr, nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  pl4g::FdWriter out(1);
  for (const auto& path : args.files) {
    pl4g::SourceManager sources;
    pl4g::ast::SyntaxArena arena;
    pl4g::CollectingEngine diags;
    auto read = sources.read(path);
    if (! read) [[unlikely]] {
      out.println("unreadable {}", path);
      continue;
    }
    const pl4g::SourceFile& file = **read;
    auto tokens = pl4g::tokenize(file, diags.engine, args.module);
    auto unit = pl4g::parse(arena, std::move(tokens), path, diags.engine, args.module, &file);
    out.println("file {}", path);
    out.println("{}", pl4g::ast::dump(unit));
    for (auto d : diags.collected)
      out.println("diag {} {}", unsigned(d->info->number), d->span.start - file.base);
    out.flush();
  }
}
