// Which tests a binary runs, and what it says when one fails.  A port of
// pypl4g/target/tests.py.
#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "ir/ir.hh"
#include "target/faults.hh"


namespace pl4g::target {

  // What a binary says when one test answers that it did not pass: where the
  // text is and how long it is, which the helper a fault leaves through takes.
  struct Failure {
    std::string symbol;
    std::uint64_t length = 0;
  };

  // The tests this binary's entry runs, in the order it runs them: what a
  // test binary was built to run, or the ones marked `always`.
  std::vector<const ir::Function*> run_by(const ir::Module& module);

  // Whether anything in the module reports and goes on: an assertion that
  // observes.
  bool observes(const ir::Module& module);

  // The message each test reports if it fails, by the test.
  std::unordered_map<const ir::Function*, Failure> failures_of(const ir::Module& module, Messages& messages);

} // namespace pl4g::target
