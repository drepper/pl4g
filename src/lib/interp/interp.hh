// Running a program from its IR.
//
// What runs is the module as the back ends receive it, after the passes that
// are not optimizations have lowered it: generators split into frames, large
// answers through the caller's storage.  So every instruction is one a
// machine runs, and what the interpreter does for each is what the x86-64
// back end emits for it -- the same faults, the same statuses, the same
// layout of every value in memory, which is the host's own memory.
//
// The runtime a compiled program carries is linked into the library and
// called as it is: input and output go through the same io_uring code.  The
// descriptors the program's devices start on are the caller's to choose.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "ir/ir.hh"
#include "source/manager.hh"


namespace pl4g::interp {

  // The descriptors a program starts with as its three devices.  A program
  // embedded in something else is given that something's choice, not
  // necessarily the process's own 0, 1 and 2.
  struct Devices {
    int input = 0;
    int output = 1;
    int error = 2;
  };

  struct Options {
    // What the program was started with, its own name first.
    std::vector<std::string> args;
    // Its environment, each `NAME=VALUE`.
    std::vector<std::string> environment;
    Devices devices;
    // Where the spans of the module's instructions are, for a fault's message
    // to say where it happened; nothing where they are not known.
    const SourceManager* sources = nullptr;
    // Where to say which function is entered, for finding out what a program
    // did; no descriptor where nothing is to be said.
    int trace = -1;
  };

  // How a run ended: the status it exited with, or the signal it would have
  // died of.
  struct Outcome {
    int status = 0;
    int signal = 0;
  };

  // Run the program `module` is, as its entry point would: the environment,
  // the constructors, the tests that always run, the startup function, the
  // destructors, and the drain of what is still in flight.
  Outcome run(const ir::Module& module, const Options& options);

} // namespace pl4g::interp
