// The language server: what an editor asks, and what the compiler answers.  A
// port of pypl4g/lsp/server.py.
//
// One loop, one message at a time, and no threads: a request is answered before
// the next is read.  What it can do is say what is wrong (`publishDiagnostics`),
// what is in a file (`documentSymbol`), what a name is (`hover`) and where it
// was defined (`definition`) -- all of it the compiler's own answers.
#pragma once

#include <filesystem>
#include <map>
#include <memory>
#include <string_view>

#include "lsp/analysis.hh"
#include "lsp/json.hh"
#include "lsp/places.hh"
#include "lsp/wire.hh"


namespace pl4g::lsp {

  // The name the server goes by, and its version.
  inline constexpr std::string_view server_name = "pl4g";
  inline constexpr std::string_view server_version = "0.1";


  // One session: the documents it has been sent, and what it made of them.
  class Server {
  public:
    explicit Server(Wire& wire) : wire(wire) {}

    // Answer messages until the editor says to stop, or goes away.  Answers
    // the status to exit with, which is nought either way.
    int run();

  private:
    void dispatch(const Json& message);
    // The handler of `method`, answering what a request is answered with.
    std::optional<Json> handle(std::string_view method, const Json& params, bool& known);

    void answer(const Json& ident, Json result);
    void error(const Json& ident, int code, std::string message);
    void notify(std::string_view method, Json params);

    Json initialize(const Json& params);
    void did_open(const Json& params);
    void did_change(const Json& params);
    void did_save(const Json& params);
    void did_close(const Json& params);
    void publish(const std::filesystem::path& path, bool whole);
    std::optional<Json> as_diagnostic(const Analysis& found, const Diagnostic& diag) const;
    // Where a span is: in `path`, the range alone, or nothing where it is in
    // another file; with no path, the file and the range.
    std::optional<Json> range_in(const Analysis& found, Span span, const std::filesystem::path& path) const;
    std::optional<std::pair<std::filesystem::path, Json>> range_anywhere(const Analysis& found, Span span) const;

    Json document_symbol(const Json& params);
    std::optional<Json> symbol(const Analysis& found, const ast::Node* item) const;
    Json inside(const Analysis& found, const ast::Node* item) const;

    Json hover(const Json& params);
    Json as_hover(const Analysis& found, Span span, std::string_view kind, std::string_view name,
                  std::string_view detail, std::optional<std::string_view> doc) const;
    Json definition(const Json& params);
    std::optional<std::pair<const ast::Node*, std::string_view>> definition_at(const Analysis& found, Loc loc) const;
    std::pair<const Analysis*, std::optional<Loc>> asked_about(const Json& params) const;

    static const Json* uri_of(const Json& params);
    static std::optional<std::filesystem::path> path_of(const Json* uri);

    Wire& wire;
    Encoding encoding = default_encoding;
    Held held;
    std::map<std::filesystem::path, std::unique_ptr<Analysis>> seen;
    bool running = true;
    bool asked_to_stop = false;
  };


  // Speak the protocol on the descriptors `reader` and `writer`.
  int serve(int reader, int writer);

} // namespace pl4g::lsp
