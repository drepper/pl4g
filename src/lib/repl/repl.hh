// Running PL4G without building a binary: a program read from source and
// interpreted, and the interactive session.
//
// Everything reads and writes the descriptors it is handed, never 0, 1 and 2
// by assumption: whoever embeds this may have put something else there.
#pragma once

#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "diag/style.hh"
#include "driver/compile.hh"
#include "interp/interp.hh"


namespace pl4g::repl {

  // What a run or a session is given.
  struct Settings {
    // How the program is compiled.  What it is made into is the interpreter's
    // to say: a program it runs has a host, the program it was embedded in.
    driver::Options compile;
    // Files whose text is given here rather than read from the disk, each
    // with the path it is known by: a program held in memory by whatever
    // embeds the interpreter, named among the inputs by that path.
    std::vector<std::pair<std::filesystem::path, std::string>> held;
    // Functions the host hands over by name, asked before `dlsym` is.
    interp::HostFunctions defined;
    // What the host sets to stop the run, from another thread; nothing where
    // nobody does.
    const std::atomic<bool>* stop = nullptr;
    // What the program is started with, beside its own name.
    std::vector<std::string> args;
    std::vector<std::string> environment;
    // Where the program reads and writes, and where diagnostics go.
    interp::Devices devices;
    // Warnings asked for or turned off by name, and whether they are errors.
    WarningControl warnings;
    // When diagnostics are coloured: where the output descriptor is a
    // terminal, unless said otherwise.
    ColourWhen colour = ColourWhen::automatic;
  };

  // How a run ended, as a process would: the status, or the signal.
  struct Ended {
    int status = 0;
    int signal = 0;
  };

  // Compile the inputs of `settings` from source and run the program they
  // make.  A program that does not compile ends with status 1, what was wrong
  // having been said on the error descriptor.
  Ended run_file(const Settings& settings);


  // The interactive session: what is typed, an input at a time.
  //
  // A definition -- a function, a type, an import, a unit, a bundle, a macro
  // -- is kept for the rest of the session, a later one of the same name
  // taking its place.  Anything else is statements, run once in a function
  // made for them, the startup function of a program made of the session so
  // far; a variable they bind at their top is the session's from then on,
  // holding what it held; and what the last of them comes to, where it is a
  // value, is shown with its type.  `@help` says what the session's own
  // commands are.
  class Session {
  public:
    explicit Session(Settings settings);
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
    ~Session();

    // Take one input, all of what was typed for it.  False once the input
    // asked the session to end.
    bool feed(std::string_view text);
    // Read inputs from the input descriptor, a line at a time, and take
    // them, until the input ends or asks the session to end.  Answers the
    // status to exit with.
    int interact();

  private:
    struct State;
    std::unique_ptr<State> state;
  };

} // namespace pl4g::repl
