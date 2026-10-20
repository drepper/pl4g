// The language server: what an editor asks, and what the compiler answers.
#include "lsp/server.hh"

#include <algorithm>
#include <format>

#include "diag/catalog.hh"
#include "front/doccomment.hh"
#include "util/unicode.hh"


namespace pl4g::lsp {

  namespace {

    // How the editor is to keep the server up to date: the whole text each time.
    constexpr int full_sync = 1;

    // What each kind of definition is, in the protocol's numbering.
    constexpr int kind_function = 12;
    constexpr int kind_variable = 13;
    constexpr int kind_struct = 23;
    constexpr int kind_enum = 10;
    constexpr int kind_enum_member = 22;
    constexpr int kind_field = 8;
    constexpr int kind_module = 2;
    constexpr int kind_unit = 11;

    // What each severity of the compiler's comes to in the protocol's
    // numbering; fatal is an error to an editor.
    int severity_of(Severity severity)
    {
      switch (severity) {
      case Severity::fatal:
      case Severity::error: return 1;
      case Severity::warning: return 2;
      case Severity::note: return 4;
      }
      return 1;
    }

    // What a definition is called, what its name's span is and what its
    // documentation comment says, for the kinds of node that have them.
    std::optional<std::string_view> name_of(const ast::Node* item)
    {
      return ast::visit(*item, [](const auto& n) -> std::optional<std::string_view> {
        if constexpr (requires { std::string_view(n.name); })
          return std::string_view(n.name);
        else
          return std::nullopt;
      });
    }

    std::optional<Span> name_span_of(const ast::Node* item)
    {
      return ast::visit(*item, [](const auto& n) -> std::optional<Span> {
        if constexpr (requires { Span(n.name_span); })
          return n.name_span;
        else
          return std::nullopt;
      });
    }

    std::optional<std::string_view> doc_of(const ast::Node* item)
    {
      return ast::visit(*item, [](const auto& n) -> std::optional<std::string_view> {
        if constexpr (requires { n.doc.has_value(); })
          return n.doc ? std::optional<std::string_view>(*n.doc) : std::nullopt;
        else
          return std::nullopt;
      });
    }

    // How each kind of part of a comment is introduced where an editor shows it.
    std::string_view heading_of(std::string_view command)
    {
      static constexpr std::pair<std::string_view, std::string_view> headings[] = {
        { "param", "Takes" },  { "return", "Answers with" }, { "raises", "Refuses with" }, { "pre", "Before" },
        { "post", "After" },   { "invariant", "Always" },    { "note", "Note" },           { "warning", "Warning" },
        { "example", "Example" }, { "see", "See" },          { "since", "Since" },         { "deprecated", "Deprecated" },
        { "todo", "To do" },   { "author", "Author" },       { "file", "File" },           { "details", "" },
      };
      for (const auto& [name, heading] : headings)
        if (name == command)
          return heading;
      return "";
    }

    bool blank(std::string_view text) { return text.find_first_not_of(" \t\n\r\f\v") == std::string_view::npos; }

    // A documentation comment as the parts an editor shows: the summary, then
    // what it takes, answers with and refuses with, then the remarks.  A
    // command not known is shown as it was written.
    std::vector<std::string> rendered(std::optional<std::string_view> text)
    {
      if (! text || text->empty())
        return {};
      auto found = doc::parse(*text);
      std::vector<std::string> out;
      if (! found.summary.empty())
        out.push_back(found.summary);
      for (auto command : doc::order) {
        auto parts = found.of(command);
        if (parts.empty())
          continue;
        auto heading = heading_of(command);
        if (command == "param") {
          std::string made = "**Takes**";
          for (auto one : parts)
            made += std::format("\n- `{}` {}", one->subject, one->text);
          out.push_back(std::move(made));
        } else if (! heading.empty()) {
          std::vector<std::string_view> said;
          for (auto one : parts)
            if (! one->text.empty())
              said.push_back(one->text);
          if (said.size() == 1 && said[0].find('\n') == std::string_view::npos)
            out.push_back(std::format("**{}** {}", heading, said[0]));
          else {
            std::string made = std::format("**{}**", heading);
            for (auto one : said)
              made += std::format("\n\n{}", one);
            out.push_back(std::move(made));
          }
        } else
          for (auto one : parts)
            if (! one->text.empty())
              out.push_back(one->text);
      }
      std::string unknown;
      for (const auto& one : found.parts)
        if (! one.known()) {
          if (! unknown.empty())
            unknown += '\n';
          unknown += one.written() + " " + one.text;
        }
      if (! unknown.empty())
        out.push_back(std::move(unknown));
      std::erase_if(out, [](const std::string& one) { return blank(one); });
      return out;
    }

    bool same_file(const std::filesystem::path& one, const std::filesystem::path& other)
    {
      if (one == other)
        return true;
      std::error_code ignored;
      return std::filesystem::weakly_canonical(std::filesystem::absolute(one, ignored), ignored)
             == std::filesystem::weakly_canonical(std::filesystem::absolute(other, ignored), ignored);
    }

  } // anonymous namespace


  // -- the loop ---------------------------------------------------------------

  int Server::run()
  {
    while (running) {
      Json message;
      try {
        message = wire.read();
      } catch (const Closed&) {
        break;
      }
      if (auto members = message.members(); members != nullptr && ! members->empty())
        dispatch(message);
    }
    return 0;
  }


  void Server::dispatch(const Json& message)
  {
    auto method = message.get("method");
    if (method == nullptr || method->string() == nullptr)
      return;
    const Json* ident = message.get("id");
    if (ident != nullptr && ident->is_null())
      ident = nullptr;
    auto params = message.get("params");
    static const Json nothing = Json::object();
    bool known = true;
    auto result = handle(*method->string(), params != nullptr && params->is_object() ? *params : nothing, known);
    if (! known) {
      // A request has to be answered even where the answer is that there is
      // no such method; a notification does not.
      if (ident != nullptr)
        error(*ident, -32601, "no such method: " + *method->string());
      return;
    }
    if (ident != nullptr)
      answer(*ident, result ? std::move(*result) : Json());
  }


  std::optional<Json> Server::handle(std::string_view method, const Json& params, bool& known)
  {
    if (method == "initialize")
      return initialize(params);
    if (method == "initialized" || method == "$/setTrace" || method == "$/cancelRequest")
      return std::nullopt;
    if (method == "shutdown") {
      asked_to_stop = true;
      return std::nullopt;
    }
    if (method == "exit") {
      running = false;
      return std::nullopt;
    }
    if (method == "textDocument/didOpen")
      did_open(params);
    else if (method == "textDocument/didChange")
      did_change(params);
    else if (method == "textDocument/didSave")
      did_save(params);
    else if (method == "textDocument/didClose")
      did_close(params);
    else if (method == "textDocument/documentSymbol")
      return document_symbol(params);
    else if (method == "textDocument/hover")
      return hover(params);
    else if (method == "textDocument/definition")
      return definition(params);
    else
      known = false;
    return std::nullopt;
  }


  void Server::answer(const Json& ident, Json result)
  {
    wire.write(Json::object({ { "jsonrpc", "2.0" }, { "id", ident }, { "result", std::move(result) } }));
  }


  void Server::error(const Json& ident, int code, std::string message)
  {
    wire.write(Json::object({ { "jsonrpc", "2.0" },
                              { "id", ident },
                              { "error", Json::object({ { "code", code }, { "message", std::move(message) } }) } }));
  }


  void Server::notify(std::string_view method, Json params)
  {
    wire.write(Json::object({ { "jsonrpc", "2.0" }, { "method", method }, { "params", std::move(params) } }));
  }


  // -- the session --------------------------------------------------------------

  Json Server::initialize(const Json& params)
  {
    auto capabilities = params.get("capabilities");
    auto general = capabilities != nullptr ? capabilities->get("general") : nullptr;
    encoding = agreed(general != nullptr ? general->get("positionEncodings") : nullptr);
    return Json::object({
      { "capabilities",
        Json::object({
          { "positionEncoding", name_of(encoding) },
          { "textDocumentSync",
            Json::object({ { "openClose", true },
                           { "change", full_sync },
                           // Without the text: the server has it already.
                           { "save", Json::object({ { "includeText", false } }) } }) },
          { "documentSymbolProvider", true },
          { "hoverProvider", true },
          { "definitionProvider", true },
        }) },
      { "serverInfo", Json::object({ { "name", server_name }, { "version", server_version } }) },
    });
  }


  // -- documents ----------------------------------------------------------------

  void Server::did_open(const Json& params)
  {
    auto document = params.get("textDocument");
    if (document == nullptr || ! document->is_object())
      return;
    auto path = path_of(document->get("uri"));
    auto text = document->get("text");
    if (! path || text == nullptr || text->string() == nullptr)
      return;
    held[*path] = *text->string();
    publish(*path, true);
  }


  void Server::did_change(const Json& params)
  {
    auto path = path_of(uri_of(params));
    auto changes = params.get("contentChanges");
    auto list = changes != nullptr ? changes->elements() : nullptr;
    if (! path || list == nullptr || list->empty())
      return;
    auto text = list->back().get("text");
    if (text == nullptr || text->string() == nullptr)
      return;
    held[*path] = *text->string();
    publish(*path, false);
  }


  void Server::did_save(const Json& params)
  {
    if (auto path = path_of(uri_of(params)))
      publish(*path, true);
  }


  void Server::did_close(const Json& params)
  {
    auto path = path_of(uri_of(params));
    if (! path)
      return;
    // Its diagnostics go with it: what an editor shows for a file it is not
    // showing is nothing.
    held.erase(*path);
    seen.erase(*path);
    notify("textDocument/publishDiagnostics",
           Json::object({ { "uri", to_uri(*path) }, { "diagnostics", Json::array() } }));
  }


  void Server::publish(const std::filesystem::path& path, bool whole)
  {
    auto found = analyse(path, held, whole, { path.parent_path() });
    Json::Array diagnostics;
    for (auto diag : found->diagnostics)
      if (auto one = as_diagnostic(*found, *diag))
        diagnostics.push_back(std::move(*one));
    auto& kept = seen[path];
    kept = std::move(found);
    notify("textDocument/publishDiagnostics",
           Json::object({ { "uri", to_uri(path) }, { "diagnostics", Json(std::move(diagnostics)) } }));
  }


  std::optional<Json> Server::as_diagnostic(const Analysis& found, const Diagnostic& diag) const
  {
    // Only the ones about this file: a diagnostic in a module the program
    // imports belongs to that file.
    auto where = range_in(found, diag.span, found.path);
    if (! where)
      return std::nullopt;
    auto out = Json::object({ { "range", std::move(*where) },
                              { "severity", severity_of(diag.info->severity) },
                              { "code", std::int64_t(diag.info->number) },
                              { "source", server_name },
                              { "message", diag.text() } });
    Json::Array related;
    for (auto note : diag.notes) {
      auto spot = range_anywhere(found, note->span);
      if (! spot)
        continue;
      related.push_back(Json::object(
        { { "location", Json::object({ { "uri", to_uri(spot->first) }, { "range", std::move(spot->second) } }) },
          { "message", note->text() } }));
    }
    if (! related.empty())
      out.set("relatedInformation", Json(std::move(related)));
    return out;
  }


  std::optional<Json> Server::range_in(const Analysis& found, Span span, const std::filesystem::path& path) const
  {
    if (span.is_valid()) {
      auto at = found.sources->file_of(span.start);
      if (at != nullptr && at->path != path)
        return std::nullopt;
    }
    auto made = range_of(*found.sources, span, encoding);
    return made ? std::move(*made) : whole_file();
  }


  std::optional<std::pair<std::filesystem::path, Json>> Server::range_anywhere(const Analysis& found, Span span) const
  {
    if (! span.is_valid())
      return std::nullopt;
    auto at = found.sources->file_of(span.start);
    auto made = range_of(*found.sources, span, encoding);
    if (at == nullptr || ! made)
      return std::nullopt;
    return std::pair{ at->path, std::move(*made) };
  }


  // -- what is in a file --------------------------------------------------------

  Json Server::document_symbol(const Json& params)
  {
    auto path = path_of(uri_of(params));
    if (! path)
      return Json::array();
    auto it = seen.find(*path);
    if (it == seen.end() || it->second->unit == nullptr)
      return Json::array();
    Json::Array out;
    for (auto item : it->second->unit->items)
      if (auto one = symbol(*it->second, item))
        out.push_back(std::move(*one));
    return Json(std::move(out));
  }


  std::optional<Json> Server::symbol(const Analysis& found, const ast::Node* item) const
  {
    int kind;
    switch (item->tag) {
    case ast::NodeKind::FuncDef: kind = kind_function; break;
    case ast::NodeKind::TypeDef: kind = kind_struct; break;
    case ast::NodeKind::EnumDef: kind = kind_enum; break;
    case ast::NodeKind::ModuleImport: kind = kind_module; break;
    case ast::NodeKind::UnitDef: kind = kind_unit; break;
    case ast::NodeKind::VarDef: kind = kind_variable; break;
    default: return std::nullopt;
    }
    auto name = name_of(item);
    if (! name || name->empty())
      return std::nullopt;
    auto whole = range_of(*found.sources, item->span, encoding);
    auto named = range_of(*found.sources, name_span_of(item).value_or(invalid_span), encoding);
    if (! whole || ! named)
      return std::nullopt;
    auto out = Json::object(
      { { "name", *name }, { "kind", kind }, { "range", std::move(*whole) }, { "selectionRange", std::move(*named) } });
    if (auto text = doc_of(item); text && ! text->empty()) {
      // What it is for, in one line.
      auto brief = doc::parse(*text).brief();
      if (! brief.empty())
        out.set("detail", brief);
    }
    auto parts = inside(found, item);
    if (! parts.elements()->empty())
      out.set("children", std::move(parts));
    return out;
  }


  Json Server::inside(const Analysis& found, const ast::Node* item) const
  {
    std::vector<std::tuple<std::string_view, Span, Span, int>> parts;
    if (auto type = ast::as<ast::TypeDef>(item))
      for (auto one : type->fields)
        parts.emplace_back(one->name, one->span, one->name_span, kind_field);
    else if (auto values = ast::as<ast::EnumDef>(item))
      for (auto one : values->members)
        parts.emplace_back(one->name, one->span, one->name_span, kind_enum_member);
    Json::Array out;
    for (const auto& [name, span, named, kind] : parts) {
      auto whole = range_of(*found.sources, span, encoding);
      auto where = range_of(*found.sources, named, encoding);
      if (! whole || ! where)
        continue;
      out.push_back(Json::object(
        { { "name", name }, { "kind", kind }, { "range", std::move(*whole) }, { "selectionRange", std::move(*where) } }));
    }
    return Json(std::move(out));
  }


  // -- what a name is, and where it was defined ---------------------------------

  Json Server::hover(const Json& params)
  {
    auto [found, loc] = asked_about(params);
    if (found == nullptr || ! loc)
      return Json();
    if (auto note = found->notes->at(*loc))
      return as_hover(*found, note->span, note->kind, note->name, note->detail,
                      note->doc ? std::optional<std::string_view>(*note->doc) : std::nullopt);
    auto made = definition_at(*found, *loc);
    if (! made)
      return Json();
    auto [item, kind] = *made;
    return as_hover(*found, name_span_of(item).value_or(invalid_span), kind, name_of(item).value_or(""), "",
                    doc_of(item));
  }


  Json Server::as_hover(const Analysis& found, Span span, std::string_view kind, std::string_view name,
                        std::string_view detail, std::optional<std::string_view> doc) const
  {
    // A type whose name is what it is says it once.
    std::string said = detail.empty() || detail == name ? std::format("{} {}", kind, name)
                                                        : std::format("{} {} : {}", kind, name, detail);
    std::vector<std::string> lines{ std::format("```pl4g\n{}\n```", said) };
    for (auto& one : rendered(doc))
      lines.push_back(std::move(one));
    std::string value;
    for (std::size_t i = 0; i < lines.size(); ++i)
      value += (i > 0 ? "\n\n" : "") + lines[i];
    auto answer = Json::object({ { "contents", Json::object({ { "kind", "markdown" }, { "value", value } }) } });
    if (auto where = range_of(*found.sources, span, encoding))
      answer.set("range", std::move(*where));
    return answer;
  }


  Json Server::definition(const Json& params)
  {
    auto [found, loc] = asked_about(params);
    if (found == nullptr || ! loc)
      return Json();
    auto note = found->notes->at(*loc);
    if (note != nullptr && note->defined.is_valid())
      if (auto spot = range_anywhere(*found, note->defined))
        return Json::object({ { "uri", to_uri(spot->first) }, { "range", std::move(spot->second) } });
    auto made = definition_at(*found, *loc);
    if (! made)
      return Json();
    auto where = range_of(*found->sources, name_span_of(made->first).value_or(invalid_span), encoding);
    if (! where)
      return Json();
    return Json::object({ { "uri", to_uri(found->path) }, { "range", std::move(*where) } });
  }


  std::optional<std::pair<const ast::Node*, std::string_view>> Server::definition_at(const Analysis& found,
                                                                                    Loc loc) const
  {
    if (found.unit == nullptr)
      return std::nullopt;
    for (auto item : found.unit->items) {
      std::string_view kind;
      switch (item->tag) {
      case ast::NodeKind::FuncDef: kind = "function"; break;
      case ast::NodeKind::TypeDef:
      case ast::NodeKind::EnumDef: kind = "type"; break;
      case ast::NodeKind::ModuleImport: kind = "module"; break;
      case ast::NodeKind::UnitDef: kind = "unit"; break;
      case ast::NodeKind::VarDef: kind = "variable"; break;
      default: continue;
      }
      auto named = name_span_of(item);
      if (named && named->is_valid() && named->start <= loc && loc < named->end)
        return std::pair{ item, kind };
    }
    return std::nullopt;
  }


  std::pair<const Analysis*, std::optional<Loc>> Server::asked_about(const Json& params) const
  {
    auto path = path_of(uri_of(params));
    if (! path)
      return { nullptr, std::nullopt };
    auto it = seen.find(*path);
    auto position = params.get("position");
    if (it == seen.end() || position == nullptr || ! position->is_object())
      return { nullptr, std::nullopt };
    const Analysis* found = it->second.get();
    auto line = position->get("line");
    auto character = position->get("character");
    if (line == nullptr || line->integer() == nullptr || character == nullptr || character->integer() == nullptr)
      return { found, std::nullopt };
    for (const auto& one : found->sources->files()) {
      if (! same_file(one.path, *path))
        continue;
      auto at = *line->integer();
      if (at < 0 || std::size_t(at) >= one.line_starts.size())
        return { found, std::nullopt };
      auto start = one.line_starts[std::size_t(at)];
      auto ends = one.text.find('\n', start);
      std::string_view text = std::string_view(one.text).substr(
        start, ends == std::string::npos ? std::string_view::npos : ends - start);
      // A place is counted in bytes here and in characters there.
      auto count = characters(text, *character->integer(), encoding);
      std::size_t bytes = 0;
      for (std::size_t i = 0; i < count && bytes < text.size(); ++i)
        bytes += std::max(unicode::decode(text, bytes).length, 1u);
      return { found, one.base + Loc(start) + Loc(bytes) };
    }
    return { found, std::nullopt };
  }


  // -- odds and ends ------------------------------------------------------------

  const Json* Server::uri_of(const Json& params)
  {
    auto document = params.get("textDocument");
    return document != nullptr ? document->get("uri") : nullptr;
  }


  std::optional<std::filesystem::path> Server::path_of(const Json* uri)
  {
    if (uri == nullptr || uri->string() == nullptr)
      return std::nullopt;
    return from_uri(*uri->string());
  }


  int serve(int reader, int writer)
  {
    Wire wire(reader, writer);
    return Server(wire).run();
  }

} // namespace pl4g::lsp
