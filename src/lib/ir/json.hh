// The whole of a module as JSON: every type, every constant, which function
// each call calls.  src/test/irjson.py writes it from what pypl4g compiled, so
// that what the two implementations made of a program can be compared, and so
// that the interpreter can run what pypl4g compiled before this implementation
// can compile anything itself.
#pragma once

#include "source/manager.hh"

#include <expected>
#include <memory>
#include <string>
#include <string_view>

#include "ir/ir.hh"


namespace pl4g::ir {

  // The module `text` describes, or why it could not be read.  Where `types`
  // is given it is told which type each number of the text stands for; where
  // `sources` is, it is given the files the text says pypl4g read, in the
  // order it read them, and every span is made a place in them -- pypl4g's
  // count characters and these bytes.
  std::expected<std::unique_ptr<Module>, std::string> read_json(std::string_view text,
                                                                std::vector<const Type*>* types = nullptr,
                                                                SourceManager* sources = nullptr);

} // namespace pl4g::ir
