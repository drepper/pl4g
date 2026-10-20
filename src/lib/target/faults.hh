// Reporting a fault, and stopping.  The message is built whole at compile time
// and put in the image; what runs at the fault is a write and an exit with a
// status out of the range the runtime reserves.  A port of
// pypl4g/target/faults.py.
#pragma once

#include <string>
#include <string_view>

#include "mc/asmbuilder.hh"
#include "source/manager.hh"
#include "util/ordered.hh"


namespace pl4g::target {

  // Where the messages go.
  inline constexpr std::string_view messages_section = ".rodata";

  // The messages a compilation needs, one per distinct text, and the symbols
  // they are known by.
  struct Messages {
    OrderedMap<std::string, std::string> by_text;

    // The symbol the message `text` is stored under, adding it if new.
    std::string symbol(const std::string& text);
    // Whether anything asked for a message, which is whether the helper a
    // fault leaves through has to be emitted at all.
    bool wanted() const noexcept { return ! by_text.empty(); }
    // Put every message into the image.
    void emit(mc::Assembler& asm_) const;
  };

  // The message a fault of kind `what` in `function` reports: the place first,
  // then what happened.
  std::string describe(std::string_view what, std::string_view function, Span span, const SourceManager* sources);

} // namespace pl4g::target
