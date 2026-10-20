// What a compilation is asked to do.  A port of pypl4g's driver/options.py.
#include "driver/options.hh"

#include <format>

#include "lsp/json.hh"
#include "util/unicode.hh"


namespace pl4g::driver {

  namespace {

    // The shared table, as the file has it.
    constexpr char table_text[] = {
#embed "options.json"
      , '\0'
    };

    // `text` padded with spaces to `width` characters, as `str.ljust` pads.
    std::string ljust(std::string text, std::size_t width)
    {
      auto length = unicode::length(text);
      if (length < width)
        text.append(width - length, ' ');
      return text;
    }

    std::string text_of(const lsp::Json* value)
    {
      auto found = value != nullptr ? value->string() : nullptr;
      return found != nullptr ? *found : std::string();
    }

    // Whether the entry says it is implemented, which it is unless it says not.
    bool implemented(const lsp::Json& entry)
    {
      auto said = entry.get("implemented");
      auto flag = said != nullptr ? said->boolean() : nullptr;
      return flag == nullptr || *flag;
    }

  } // anonymous namespace


  std::string_view option_table()
  {
    return { table_text, sizeof table_text - 1 };
  }


  std::string render_help()
  {
    auto table = lsp::Json::parse(option_table());
    if (! table) [[unlikely]]
      return {};
    std::string out = std::format("usage: {}\n\n", text_of(table->get("usage")));
    auto commands = table->get("commands");
    if (commands != nullptr && commands->elements() != nullptr && ! commands->elements()->empty()) {
      out += "commands:\n";
      for (const auto& command : *commands->elements())
        out += std::format("  {}{}{}\n", ljust(text_of(command.get("name")), 28), text_of(command.get("help")),
                           implemented(command) ? "" : "  (not implemented yet)");
      out += "\noptions:\n";
    }
    if (auto options = table->get("options"); options != nullptr && options->elements() != nullptr)
      for (const auto& option : *options->elements()) {
        std::vector<std::string> names;
        for (auto key : { "short", "long" })
          if (auto name = text_of(option.get(key)); ! name.empty())
            names.push_back(name);
        std::string spelled;
        for (std::size_t i = 0; i < names.size(); ++i)
          spelled += (i > 0 ? ", " : "") + names[i];
        if (auto metavar = option.get("metavar"); metavar != nullptr && metavar->string() != nullptr) {
          // An option whose value may be left out is spelled as it is
          // written, brackets and all.
          if (text_of(option.get("arg")) == "joined_equals_optional")
            spelled += std::format("[={}]", *metavar->string());
          else
            spelled += std::format(" {}", *metavar->string());
        }
        out += std::format("  {}{}{}\n", ljust(spelled, 28), text_of(option.get("help")),
                           implemented(option) ? "" : "  (not implemented yet)");
      }
    return out;
  }


  std::string_view name_of(EmitKind kind) noexcept
  {
    switch (kind) {
    case EmitKind::tokens: return "tokens";
    case EmitKind::ast: return "ast";
    case EmitKind::expanded: return "expanded";
    case EmitKind::ir: return "ir";
    case EmitKind::asm_: return "asm";
    case EmitKind::elf: return "elf";
    }
    return "elf";
  }


  std::optional<EmitKind> emit_named(std::string_view name) noexcept
  {
    for (auto kind : { EmitKind::tokens, EmitKind::ast, EmitKind::expanded, EmitKind::ir, EmitKind::asm_,
                       EmitKind::elf })
      if (name_of(kind) == name)
        return kind;
    return std::nullopt;
  }


  std::optional<DiagFormat> diag_format_named(std::string_view name) noexcept
  {
    if (name == "text")
      return DiagFormat::text;
    if (name == "json")
      return DiagFormat::json;
    return std::nullopt;
  }

} // namespace pl4g::driver
