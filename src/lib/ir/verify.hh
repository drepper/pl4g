// The IR verifier.
//
// A port of pypl4g's ir/verify.py.  It checks the structural invariants of a
// module: symbols, the special-function caches, every block terminated once,
// the types every instruction is given, branches supplying what their
// destination declares, and every use dominated by its definition.  A failure
// is a defect in the compiler, never in the program, so it throws
// `InternalError` rather than saying anything to a reader.
#pragma once

#include <optional>
#include <span>
#include <string_view>

#include "ir/ir.hh"


namespace pl4g::ir {

  // Verify `module`: every function of it, or those of `functions` that are
  // still in it (`verify`).  Throws `InternalError` naming every problem found.
  void verify(const Module& module, std::optional<std::span<Function* const>> functions = std::nullopt);

  // What asks for the IR to be verified wherever it was changed, as `--verify`
  // does (`VERIFY_VARIABLE`).
  inline constexpr std::string_view verify_variable = "PL4G_VERIFY";

  // Whether the environment asks for verifying (`verifying_asked`).
  bool verifying_asked() noexcept;

} // namespace pl4g::ir
