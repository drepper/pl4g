// PL4G embedded in a C++ program.
//
// A thin layer over repl::run_file, which compiles a program from its source
// for the interpreter and runs it: what this adds is a way to say what a
// program is started with that does not need the compiler's own headers, and
// a program's text handed over in memory.
#include "embed/embed.hh"

#include <utility>

#include "repl/repl.hh"
#include "state.h"


namespace pl4g::embed {

  struct Interpreter::State {
    repl::Settings settings;
    // What the runtime remembers between calls -- the heap, the seed of
    // every table -- for the runs of this interpreter and no other's, and
    // what it took from the system, which is given back with it.
    ::pl4g_runtime_state runtime{};

    ~State() { ::pl4g_runtime_release(&runtime); }
  };


  namespace {

    // The runtime working in `state` for as long as this lives, on this
    // thread, and in whatever it worked in before afterwards.
    class Selected {
    public:
      explicit Selected(::pl4g_runtime_state& state) : before(::pl4g_runtime_current)
      {
        ::pl4g_runtime_current = &state;
      }
      Selected(const Selected&) = delete;
      Selected& operator=(const Selected&) = delete;
      ~Selected() { ::pl4g_runtime_current = before; }

    private:
      ::pl4g_runtime_state* before = nullptr;
    };

  } // anonymous namespace


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


  Interpreter& Interpreter::define(std::string_view symbol, void* function, void* context)
  {
    state->settings.defined.insert_or_assign(std::string(symbol), interp::HostFunction{ function, context });
    return *this;
  }


  Ended Interpreter::run_file(const std::filesystem::path& path, std::optional<std::vector<std::string>> args)
  {
    // A copy, so that what one run names is not left for the next.
    repl::Settings settings = state->settings;
    settings.compile.inputs = { path };
    if (args)
      settings.args = std::move(*args);
    Selected selected(state->runtime);
    auto ended = repl::run_file(settings);
    return { ended.status, ended.signal };
  }


  Ended Interpreter::run_source(std::string_view text, std::filesystem::path name,
                                std::optional<std::vector<std::string>> args)
  {
    repl::Settings settings = state->settings;
    settings.compile.inputs = { name };
    if (args)
      settings.args = std::move(*args);
    settings.held.emplace_back(std::move(name), std::string(text));
    Selected selected(state->runtime);
    auto ended = repl::run_file(settings);
    return { ended.status, ended.signal };
  }

} // namespace pl4g::embed
