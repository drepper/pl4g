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

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
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

  // A function a host hands the interpreter by name: the address of a
  // function of the system's convention, and what it is handed first, before
  // the arguments a program's declaration gives it -- where that is not
  // null.  A null context is no context: the function takes what the
  // declaration says and nothing more.
  struct HostFunction {
    void* function = nullptr;
    void* context = nullptr;
  };

  // The functions a host hands over, by the symbol a program names each with.
  using HostFunctions = std::map<std::string, HostFunction, std::less<>>;

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
    // Functions the host defines, by the symbol a program's `@[host]` names
    // them with: asked before `dlsym` is, so that a host can hand over what
    // it does not export, or put something of its own where an exported
    // symbol is.  Nothing where it hands over nothing.
    const HostFunctions* defined = nullptr;
    // Set, from any thread, where the host wants the program stopped.  It is
    // read at every jump back to a block at or before the one jumped from --
    // so in every turn of every loop -- at every call back to a function at
    // or before the caller, or through a value -- so in every turn of every
    // recursion -- and when a host function returns; a run it ends answers
    // `STOPPED_BY_HOST`.  Nothing where nobody stops the program.
    const std::atomic<bool>* stop = nullptr;
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


  // A module placed in memory and ready to run, kept for as long as anything
  // may still point into what it placed.  The interactive session makes one
  // per input and keeps the earlier ones: what a variable of the session holds
  // may point into their room, and into the heap, which nothing gives back.
  class Program {
  public:
    // Place `module`, which has to outlive this, its variables given what
    // their initializers say.
    Program(const ir::Module& module, Options options);
    Program(const Program&) = delete;
    Program& operator=(const Program&) = delete;
    ~Program();

    // Where the variable `var` of the module is.
    std::byte* place_of(const ir::GlobalVar* var) const;
    // Run what a process of the program runs, once.
    Outcome start();

  private:
    struct Placed;
    std::unique_ptr<Placed> placed;
  };

} // namespace pl4g::interp
