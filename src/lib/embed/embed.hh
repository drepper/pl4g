// PL4G embedded in a C++ program: the interpreter, run from inside it.
//
// A program that embeds PL4G makes an `Interpreter`, says what a program it
// runs is started with, and runs programs given as a file or as text in
// memory.  Each is compiled from its source and interpreted, as `pl4g-repl
// FILE` runs one; what it reads and writes goes through the descriptors it is
// given, and nothing of the embedding program's own state is touched but what
// the program is handed.
//
// A function the PL4G program declares with `@[host("SYMBOL")]` is one the
// embedding program defines: it is looked up with `dlsym(RTLD_DEFAULT,
// SYMBOL)` the first time it is called and called with the system's
// convention.  So the embedding program has to be linked so that its symbols
// are in the dynamic symbol table -- `-rdynamic`, or CMake's ENABLE_EXPORTS --
// and a function it offers has to be `extern "C"`.  One that is not found
// stops the program with status 92, saying which.
#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <memory>
#include <string>
#include <string_view>
#include <vector>


namespace pl4g::embed {

  // When the diagnostics are coloured.
  enum class Colour : std::uint8_t {
    // Where the output descriptor is a terminal.
    automatic,
    always,
    never,
  };

  // How a run ended, as a process would have.
  struct Ended {
    // The status the program exited with: 1 where it did not compile, what
    // was wrong having been said on the error descriptor.
    int status = 0;
    // The signal it would have died of, or nought.
    int signal = 0;
  };

  // The interpreter as a program embedding it sees it: settings, and runs.
  //
  // Each run is a program of its own, compiled afresh and started as a
  // process would start it; nothing of one is left for the next but what the
  // host functions it called kept.  The settings stay for every run after
  // they were made.
  //
  // Everything a run changes that outlives an instruction is the
  // interpreter's own: the heap the program takes from, the seed its tables
  // are worked out from, and what the runtime took from the system, which is
  // given back when the interpreter is destroyed.  So several may exist at
  // once, and run one after the other or on threads of their own, none seeing
  // another's.
  class Interpreter {
  public:
    // An interpreter whose programs are started with nothing but their own
    // name, an empty environment, and the process's descriptors 0, 1 and 2.
    Interpreter();
    Interpreter(const Interpreter&) = delete;
    Interpreter& operator=(const Interpreter&) = delete;
    Interpreter(Interpreter&&) noexcept;
    Interpreter& operator=(Interpreter&&) noexcept;
    ~Interpreter();

    // Start each program with `args` after its own name.
    Interpreter& arguments(std::vector<std::string> args);
    // Start each program with the environment `variables`, each `NAME=VALUE`.
    Interpreter& environment(std::vector<std::string> variables);
    // Have each program read its input from `input`, write its output to
    // `output`, and its errors and the diagnostics to `error`.
    Interpreter& devices(int input, int output, int error);
    // Turn the warning called `name` on or off.
    Interpreter& warning(std::string_view name, bool enabled);
    // Make every warning an error, or not.
    Interpreter& warnings_are_errors(bool yes);
    // Colour the diagnostics as `when` says.
    Interpreter& colour(Colour when);
    // Look for the modules a program imports in `directory` too, after the
    // directories already named.
    Interpreter& module_path(std::filesystem::path directory);
    // Hand over `function` as the function a program's `@[host("SYMBOL")]`
    // names with `symbol`.  What is handed over is asked before `dlsym` is,
    // so a host can give a program what it does not export -- a function of
    // internal linkage, one in a program not linked with -rdynamic -- or put
    // a function of its own where an exported symbol of that name is.  It
    // has to be a function of the system's convention answering what the
    // declaration says, and taking it: where `context` is not null, it takes
    // `context` first, as a `void*`, and what the declaration says after it
    // -- which is how one function serves several objects of the host's.  A
    // later one for the same symbol takes the place of the earlier, and the
    // table holds for every run after.
    Interpreter& define(std::string_view symbol, void* function, void* context = nullptr);
    // The same for a function pointer of any type, which is what a host has.
    template<typename R, typename... A>
    Interpreter& define(std::string_view symbol, R (*function)(A...), void* context = nullptr)
    {
      return define(symbol, reinterpret_cast<void*>(function), context);
    }

    // Compile the program in the file `path` and run it.  Where `args` is
    // given, the program is started with it after its own name, as
    // `init.args`, in place of what `arguments` said -- for this run only.
    Ended run_file(const std::filesystem::path& path, std::optional<std::vector<std::string>> args = std::nullopt);
    // Compile the program `text` and run it.  `name` is the path it is known
    // by, in a diagnostic and as the program's own name; nothing is read from
    // there, and a module it imports is looked for beside it.  `args` is as
    // for `run_file`.
    Ended run_source(std::string_view text, std::filesystem::path name = "embedded.pl4g",
                     std::optional<std::vector<std::string>> args = std::nullopt);

  private:
    struct State;
    std::unique_ptr<State> state;
  };

} // namespace pl4g::embed
