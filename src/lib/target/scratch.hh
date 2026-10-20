// What the shared lowerings ask of a back end while they emit: registers for
// values with no name, and what to do where an answer went past the end of its
// type.  The `Scratch` and `Fault` protocols of pypl4g's target/saturate.py,
// runs.py and bitcount.py, one interface for all of them.
#pragma once

#include <stdexcept>

#include "mc/asmbuilder.hh"


namespace pl4g::target {

  // How a back end hands out registers for values with no name.
  class Scratch {
  public:
    virtual ~Scratch() = default;
    // A register of the widest kind, for something computed on the way.
    virtual mc::Reg scratch() = 0;
    // A register of `bits` holding a run, for a value worked out on the way;
    // only a back end with such registers has one.
    virtual mc::Reg run_scratch(int bits)
    {
      (void) bits;
      throw std::logic_error("this target has no registers holding a run");
    }
  };

  // What a back end does where an answer went past the end of its type: emit
  // the report and the stop, code that does not return.
  class Fault {
  public:
    virtual ~Fault() = default;
    virtual void out_of_range(mc::Assembler& asm_, Span span) = 0;
  };

  // A saturating, trapping or wrapping operation no back end can yet emit.
  struct Unsupported : std::runtime_error {
    std::string what_;
    explicit Unsupported(std::string what) : std::runtime_error(what), what_(std::move(what)) {}
  };

} // namespace pl4g::target
