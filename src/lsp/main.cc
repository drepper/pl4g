// The stand-alone PL4G language server: the library's, on two descriptors.
// `pl4g-lsp` speaks the Language Server Protocol on its standard input and
// output unless told other descriptors.
#include <argp.h>

#include <cstdlib>

#include "lsp/server.hh"


namespace {

  struct Arguments {
    int input = 0;
    int output = 1;
  };

  constexpr ::argp_option options[] = {
    { "input-fd", 'i', "FD", 0, "Read the protocol from descriptor FD (the standard input)", 0 },
    { "output-fd", 'o', "FD", 0, "Write the protocol to descriptor FD (the standard output)", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case 'i':
      args.input = std::atoi(arg);
      return 0;
    case 'o':
      args.output = std::atoi(arg);
      return 0;
    case ARGP_KEY_ARG:
      ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, nullptr, "Speak the Language Server Protocol for PL4G.", nullptr,
                              nullptr, nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  return pl4g::lsp::serve(args.input, args.output);
}
