// Showing diagnostics to a reader.
#pragma once

#include <memory>

#include "diag/engine.hh"
#include "diag/style.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


namespace pl4g {

  namespace ts {
    class Highlighter;
  }

  // Renders diagnostics in the customary `file:line:column: severity:` form,
  // with the source line and the span underlined, to a descriptor the caller
  // chose.
  class TextRenderer {
  public:
    // Render to `fd`; `engine`, where given, decides what -Werror makes of a
    // warning.
    TextRenderer(const SourceManager& sources, int fd, const DiagEngine* engine = nullptr);
    ~TextRenderer();

    // Say when to write colour, `watched` being the descriptor whose being a
    // terminal says someone is watching -- the standard output, in a compiler.
    // Where there is colour the snippets are coloured as the grammar says.
    void recolour(ColourWhen when, int watched);

    // Say which engine decides the severity shown, the renderer having been
    // made before it.
    void use(const DiagEngine& e) noexcept { engine = &e; }

    // Render one diagnostic.  Notes arrive as calls of their own.
    void operator()(const Diagnostic& diag);

  private:
    void snippet(Span span, unsigned line, unsigned column);
    // The source line, written as the grammar says its pieces are.
    std::string coloured(Span span, std::string_view text);

    const SourceManager& sources;
    FdWriter out;
    const DiagEngine* engine;
    Palette palette;
    std::unique_ptr<ts::Highlighter> highlighter;
  };



  // Collects diagnostics and writes them as one JSON array at the end, which
  // is what makes the numbers reachable from outside: pypl4g's JSONRenderer.
  class JsonRenderer {
  public:
    JsonRenderer(const SourceManager& sources, int fd) : sources(sources), out(fd) {}

    // Keep `diag`; a note is written inside what it was attached to.
    void operator()(const Diagnostic& diag);
    // Write everything kept so far.
    void finish();

  private:
    const SourceManager& sources;
    FdWriter out;
    std::vector<const Diagnostic*> collected;
  };

} // namespace pl4g
