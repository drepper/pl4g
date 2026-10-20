// Reporting a fault, and stopping.
#include "target/faults.hh"
#include "target/emit.hh"

#include <format>


namespace pl4g::target {

  std::string Messages::symbol(const std::string& text)
  {
    if (auto found = by_text.get(text))
      return *found;
    auto name = ".Lfault." + std::to_string(by_text.size());
    by_text[text] = name;
    return name;
  }


  void Messages::emit(mc::Assembler& asm_) const
  {
    if (by_text.empty())
      return;
    asm_.section(std::string(messages_section), placed(false, 1));
    for (const auto& [text, name] : by_text) {
      auto symbol = asm_.label(name, mc::SymBinding::local, mc::SymKind::object, mc::SymVisibility::hidden);
      asm_.bytes(mc::Bytes(text.begin(), text.end()));
      asm_.end_label(symbol);
    }
  }


  std::string describe(std::string_view what, std::string_view function, Span span, const SourceManager* sources)
  {
    std::string where;
    if (sources != nullptr && span.is_valid())
      if (auto located = sources->position(span.start))
        where = std::format("{}:{}:{}: ", located->path, located->line, located->column);
    return std::format("{}pl4g: {} in '{}'\n", where, what, function);
  }

} // namespace pl4g::target
