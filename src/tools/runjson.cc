// Run a program pypl4g compiled, read as JSON (src/test/irjson.py), and exit as
// it exits -- the way src/test/run_agrees.py holds the interpreter to every
// test program before this implementation can compile one itself.
//
// Usage: pl4g-runjson FILE.json [ARG...]
#include <argp.h>

#include <cstdlib>

#include <csignal>
#include <string>
#include <vector>

#include "interp/interp.hh"
#include "ir/json.hh"
#include "opt/passes.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


extern char** environ;


namespace {

  struct Arguments {
    bool trace = false;
    bool passes = false;
    int level = 0;
    std::string file;
    std::vector<std::string> rest;
  };

  constexpr ::argp_option options[] = {
    { "trace", 't', nullptr, 0, "Say on standard error which function is entered", 0 },
    { "passes", 'p', nullptr, 0, "Run the passes of level nought first: the file is the module before them", 0 },
    { "level", 'O', "N", 0, "Run the passes of optimization level N, where --passes asks for them (0)", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case 't':
      args.trace = true;
      return 0;
    case 'p':
      args.passes = true;
      return 0;
    case 'O':
      args.level = std::atoi(arg);
      return 0;
    case ARGP_KEY_ARG:
      if (args.file.empty())
        args.file = arg;
      else
        args.rest.emplace_back(arg);
      return 0;
    case ARGP_KEY_END:
      if (args.file.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, "FILE.json [ARG...]",
                              "Run a program pypl4g compiled, read from JSON.", nullptr, nullptr, nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, ARGP_IN_ORDER, nullptr, &args);
  pl4g::SourceManager sources;
  auto read = sources.read(args.file);
  if (! read) [[unlikely]] {
    pl4g::write_all(2, "cannot read " + args.file + "\n");
    return 1;
  }
  auto module = pl4g::ir::read_json((*read)->text);
  if (! module) [[unlikely]] {
    pl4g::write_all(2, args.file + ": " + module.error() + "\n");
    return 1;
  }
  if (args.passes)
    pl4g::opt::run_pipeline(**module, args.level);
  pl4g::interp::Options options;
  options.args.push_back(args.file);
  options.args.insert(options.args.end(), args.rest.begin(), args.rest.end());
  if (args.trace)
    options.trace = 2;
  for (char** e = environ; *e != nullptr; ++e)
    options.environment.emplace_back(*e);
  auto outcome = pl4g::interp::run(**module, options);
  if (outcome.signal != 0) {
    std::signal(outcome.signal, SIG_DFL);
    std::raise(outcome.signal);
  }
  return outcome.status;
}
