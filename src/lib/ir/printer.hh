// The textual form of the IR, exactly as pypl4g's printer writes it.
//
// It is for a reader, and it leaves out what a reader does not need -- which
// function a call calls, what a constant array holds -- as pypl4g's does; the
// whole of a module is what ir/json.hh reads and writes.
#pragma once

#include <string>

#include "ir/ir.hh"


namespace pl4g::ir {

  // The text of a module, as `pypl4g --emit ir` writes it.
  std::string render_module(const Module& module);

  // The text of one function.
  std::string render_function(const Function& func);

  // A 128-bit integer in decimal.
  std::string decimal(__int128 value);

} // namespace pl4g::ir
