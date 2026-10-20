// Generate code for a module pypl4g's passes made, read as JSON
// (src/test/irjson.py), and write what `pypl4g --emit asm` writes of it --
// what src/test/codegen_agrees.py holds each architecture's back end to.
//
// Usage: pl4g-codegen --target TRIPLE [-O LEVEL] [--mclevel LEVEL] FILE.json
#include <argp.h>

#include <cstdlib>
#include <print>
#include <string>

#include "diag/engine.hh"
#include "diag/render.hh"
#include "ir/json.hh"
#include "mc/dump.hh"
#include "mc/layout.hh"
#include "mc/streamer.hh"
#include "source/manager.hh"
#include "target/target.hh"
#include "util/fdio.hh"


namespace {

  struct Arguments {
    std::string file;
    std::string triple = "x86_64-linux-none";
    std::string mclevel;
    int level = 0;
  };

  enum Key : int { key_target = 1000, key_mclevel };

  constexpr ::argp_option options[] = {
    { "target", key_target, "TRIPLE", 0, "Generate for TRIPLE", 0 },
    { nullptr, 'O', "LEVEL", 0, "The optimization level the module was made at", 0 },
    { "mclevel", key_mclevel, "LEVEL", 0, "The microarchitecture level", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case key_target:
      args.triple = arg;
      return 0;
    case key_mclevel:
      args.mclevel = arg;
      return 0;
    case 'O':
      args.level = std::atoi(arg);
      return 0;
    case ARGP_KEY_ARG:
      if (! args.file.empty()) [[unlikely]]
        ::argp_usage(state);
      args.file = arg;
      return 0;
    case ARGP_KEY_END:
      if (args.file.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, "FILE.json",
                              "Generate code for a module read from JSON and dump it.", nullptr, nullptr, nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  pl4g::SourceManager sources;
  auto read = sources.read(args.file);
  if (! read) [[unlikely]] {
    std::print(stderr, "cannot read {}\n", args.file);
    return 2;
  }
  // The program's own files, as pypl4g read them: what a fault names.
  pl4g::SourceManager program;
  auto module = pl4g::ir::read_json((*read)->text, nullptr, &program);
  if (! module) [[unlikely]] {
    std::print(stderr, "{}: {}\n", args.file, module.error());
    return 2;
  }
  pl4g::TextRenderer renderer(program, 2);
  pl4g::DiagEngine diags([&](const pl4g::Diagnostic& d) { renderer(d); });
  auto target = pl4g::target::lookup_target(args.triple);
  if (target == nullptr) [[unlikely]] {
    std::print(stderr, "no back end for {}\n", args.triple);
    return 2;
  }
  if (! args.mclevel.empty())
    target->use_mclevel(args.mclevel);
  pl4g::mc::MCStreamer streamer(target->encoder());
  auto asm_ = target->new_assembler(streamer, args.level);
  target->generate(**module, *asm_, diags, args.level, &program);
  if (diags.failed())
    return 1;
  auto sections = streamer.section_list();
  auto symbols = streamer.symbol_list();
  target->relax(sections, symbols);
  for (auto section : sections) {
    pl4g::mc::layout_section(*section);
    pl4g::mc::resolve_symbol_offsets(*section, symbols);
  }
  pl4g::write_all(1, pl4g::mc::dump_sections(sections, symbols));
  return 0;
}
