// What the interactive session shows of a value.
#pragma once

#include <cstddef>
#include <string>

#include "ir/layout.hh"
#include "ir/types.hh"


namespace pl4g::repl {

  // The value of type `ty` held in the bytes at `at`, written as the
  // language writes a literal of it where it has one: `⟦1, 2⟧`,
  // `Point(.x ← 1, .y ← 2)`, `"text"`.  What has no literal -- a list, a
  // function -- is shown by its type.
  std::string shown(const ir::Type* ty, const std::byte* at, const ir::DataLayout& layout);

} // namespace pl4g::repl
