// PL4G embedded in a C++ program.
//
// A thin layer over repl::run_file, which compiles a program from its source
// for the interpreter and runs it: what this adds is a way to say what a
// program is started with that does not need the compiler's own headers, and
// a program's text handed over in memory.
#include "embed/embed.hh"

#include <utility>

#include "repl/repl.hh"


namespace pl4g::embed {

  struct Interpreter::State {
    repl::Settings settings;
  };


  Interpreter::Interpreter() : state(std::make_unique<State>()) {}

  Interpreter::Interpreter(Interpreter&&) noexcept = default;

  Interpreter& Interpreter::operator=(Interpreter&&) noexcept = default;

  Interpreter::~Interpreter() = default;


  Interpreter& Interpreter::arguments(std::vector<std::string> args)
  {
    state->settings.args = std::move(args);
    return *this;
  }


  Interpreter& Interpreter::environment(std::vector<std::string> variables)
  {
    state->settings.environment = std::move(variables);
    return *this;
  }


  Interpreter& Interpreter::devices(int input, int output, int error)
  {
    state->settings.devices = { input, output, error };
    return *this;
  }


  Interpreter& Interpreter::warning(std::string_view name, bool enabled)
  {
    state->settings.warnings.enabled[std::string(name)] = enabled;
    return *this;
  }


  Interpreter& Interpreter::warnings_are_errors(bool yes)
  {
    state->settings.warnings.warnings_are_errors = yes;
    return *this;
  }


  Interpreter& Interpreter::colour(Colour when)
  {
    state->settings.colour = when == Colour::always ? ColourWhen::yes
                             : when == Colour::never ? ColourWhen::no
                                                     : ColourWhen::automatic;
    return *this;
  }


  Interpreter& Interpreter::module_path(std::filesystem::path directory)
  {
    state->settings.compile.module_path.push_back(std::move(directory));
    return *this;
  }


  Ended Interpreter::run_file(const std::filesystem::path& path)
  {
    // A copy, so that what one run names is not left for the next.
    repl::Settings settings = state->settings;
    settings.compile.inputs = { path };
    auto ended = repl::run_file(settings);
    return { ended.status, ended.signal };
  }


  Ended Interpreter::run_source(std::string_view text, std::filesystem::path name)
  {
    repl::Settings settings = state->settings;
    settings.compile.inputs = { name };
    settings.held.emplace_back(std::move(name), std::string(text));
    auto ended = repl::run_file(settings);
    return { ended.status, ended.signal };
  }

} // namespace pl4g::embed
