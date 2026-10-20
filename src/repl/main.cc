// The stand-alone PL4G interpreter: what the library offers, on the command
// line.  `pl4g-repl FILE [ARG...]` runs a program from its source; with no file it
// is the interactive session.
#include <argp.h>

#include <csignal>
#include <cstdlib>
#include <string>
#include <vector>

#include "repl/repl.hh"
#include "sema/modules.hh"


extern char** environ;

namespace {

  struct Arguments {
    pl4g::repl::Settings settings;
    std::vector<std::string> files;
  };

  enum Key : int {
    key_werror = 1000,
    key_module_path,
    key_conditions,
    key_stack_size,
    key_guard_size,
    key_ignore_system,
    key_color,
    key_input_fd,
    key_output_fd,
    key_error_fd,
  };

  constexpr ::argp_option options[] = {
    { nullptr, 'W', "WARNING", 0, "Enable a warning, or with no- disable it; -Werror makes every warning an error", 0 },
    { "Werror", key_werror, nullptr, 0, "Make every warning an error", 0 },
    { "module-path", key_module_path, "DIRS", 0, "Look for modules in DIRS too", 0 },
    { "conditions", key_conditions, "WHAT", 0, "check, ignore or observe the conditions", 0 },
    { "stack-size", key_stack_size, "BYTES", 0, "The size of the stack", 0 },
    { "guard-size", key_guard_size, "BYTES", 0, "The size of the stack's guard", 0 },
    { "ignore-system", key_ignore_system, nullptr, 0, "Treat system modules as any other", 0 },
    { "color", key_color, "WHEN", OPTION_ARG_OPTIONAL, "Colour the diagnostics: yes, no or auto", 0 },
    { "input-fd", key_input_fd, "FD", 0, "Read the program's or the session's input from FD (0)", 0 },
    { "output-fd", key_output_fd, "FD", 0, "Write the output to FD (1)", 0 },
    { "error-fd", key_error_fd, "FD", 0, "Write errors and diagnostics to FD (2)", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    auto& settings = args.settings;
    switch (key) {
    case 'W':
      if (std::string_view(arg) == "error")
        settings.warnings.warnings_are_errors = true;
      else if (std::string_view(arg).starts_with("no-"))
        settings.warnings.enabled[std::string(arg + 3)] = false;
      else
        settings.warnings.enabled[arg] = true;
      return 0;
    case key_werror:
      settings.warnings.warnings_are_errors = true;
      return 0;
    case key_module_path:
      for (auto& one : pl4g::sema::parse_search_path(arg))
        settings.compile.module_path.push_back(std::move(one));
      return 0;
    case key_conditions:
      if (auto found = pl4g::sema::conditions_named(arg))
        settings.compile.conditions = *found;
      else [[unlikely]]
        ::argp_error(state, "unknown --conditions %s", arg);
      return 0;
    case key_stack_size:
      settings.compile.stack_size = std::strtoll(arg, nullptr, 0);
      return 0;
    case key_guard_size:
      settings.compile.guard_size = std::strtoll(arg, nullptr, 0);
      return 0;
    case key_ignore_system:
      settings.compile.ignore_system = true;
      return 0;
    case key_color:
      if (auto found = pl4g::colour_named(arg != nullptr ? arg : "yes"))
        settings.colour = *found;
      else [[unlikely]]
        ::argp_error(state, "--color takes yes, no or auto, not %s", arg);
      return 0;
    case key_input_fd:
    case key_output_fd:
    case key_error_fd: {
      char* end = nullptr;
      long fd = std::strtol(arg, &end, 10);
      if (end == arg || *end != '\0' || fd < 0) [[unlikely]]
        ::argp_error(state, "not a descriptor: %s", arg);
      auto& devices = settings.devices;
      (key == key_input_fd ? devices.input : key == key_output_fd ? devices.output : devices.error) = int(fd);
      return 0;
    }
    case ARGP_KEY_ARG:
      if (args.files.empty())
        args.files.emplace_back(arg);
      else
        settings.args.emplace_back(arg);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, "[FILE [ARG...]]",
                              "Run a PL4G program from its source, or talk to the interpreter.", nullptr, nullptr,
                              nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, ARGP_IN_ORDER, nullptr, &args);
  for (char** e = environ; *e != nullptr; ++e)
    args.settings.environment.emplace_back(*e);
  if (args.files.empty())
    return pl4g::repl::Session(std::move(args.settings)).interact();
  args.settings.compile.inputs.emplace_back(args.files.front());
  auto ended = pl4g::repl::run_file(args.settings);
  if (ended.signal != 0) {
    std::signal(ended.signal, SIG_DFL);
    std::raise(ended.signal);
  }
  return ended.status;
}
