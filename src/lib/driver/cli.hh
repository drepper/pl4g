// Parsing the command line: pypl4g's driver/cli.py.
//
// Every refusal is a numbered diagnostic of the shared catalog, said through
// the engine the caller hands in, so that a build reading the errors sees the
// same numbers whichever compiler produced them.
#pragma once

#include <span>
#include <string>

#include "diag/engine.hh"
#include "driver/options.hh"


namespace pl4g::driver {

  // The options `argv` -- the words after the program's name -- says, every
  // way it is wrong said through `diags`.
  Options parse_command_line(std::span<const std::string> argv, DiagEngine& diags);

} // namespace pl4g::driver
