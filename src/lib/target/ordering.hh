// What a read or a write may say about what another observer sees: the one
// rule about which accesses may carry an ordering.  A port of
// pypl4g/target/ordering.py.
#pragma once

#include <optional>
#include <string>

#include "ir/ir.hh"


namespace pl4g::target {

  // Why `inst` cannot carry the ordering it was given, or nothing.
  std::optional<std::string> cannot_order(const ir::Instruction* inst);

} // namespace pl4g::target
