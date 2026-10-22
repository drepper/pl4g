// Show a node of the manual: `pl4g-help [NAME]`, the top where no name is
// given.  The exit status is nought where there is such a node and one where
// there is not, its text then saying what was meant.
#include <argp.h>

#include <print>
#include <string>

#include "manual/manual.hh"


namespace {

  struct Arguments {
    std::string name = "help";
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case ARGP_KEY_ARG:
      if (state->arg_num > 0) [[unlikely]]
        ::argp_usage(state);
      args.name = arg;
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { nullptr, parse_option, "[NAME]", "Show the node NAME of the PL4G manual, 'help' by default.",
                              nullptr, nullptr, nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  std::print("{}", pl4g::manual::lookup(args.name));
  return pl4g::manual::exists(args.name) ? 0 : 1;
}
