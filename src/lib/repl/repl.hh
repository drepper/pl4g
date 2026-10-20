// Running PL4G without building a binary: a program read from source and
// interpreted, and the interactive session.
//
// Everything reads and writes the descriptors it is handed, never 0, 1 and 2
// by assumption: whoever embeds this may have put something else there.
#pragma once

#include <string>
#include <vector>

#include "driver/compile.hh"
#include "interp/interp.hh"


namespace pl4g::repl {

  // What a run or a session is given.
  struct Settings {
    // How the program is compiled.
    driver::Options compile;
    // What the program is started with, beside its own name.
    std::vector<std::string> args;
    std::vector<std::string> environment;
    // Where the program reads and writes, and where diagnostics go.
    interp::Devices devices;
    // Warnings asked for or turned off by name, and whether they are errors.
    WarningControl warnings;
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

} // namespace pl4g::repl
