// Showing diagnostics to a reader.
#pragma once

#include "diag/engine.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


namespace pl4g {

  // Renders diagnostics in the customary `file:line:column: severity:` form,
  // with the source line and the span underlined, to a descriptor the caller
  // chose.
  class TextRenderer {
  public:
    // Render to `fd`; `engine`, where given, decides what -Werror makes of a
    // warning.
    TextRenderer(const SourceManager& sources, int fd, const DiagEngine* engine = nullptr)
      : sources(sources), out(fd), engine(engine) {}

    // Say which engine decides the severity shown, the renderer having been
    // made before it.
    void use(const DiagEngine& e) noexcept { engine = &e; }

    // Render one diagnostic.  Notes arrive as calls of their own.
    void operator()(const Diagnostic& diag);

  private:
    void snippet(Span span, unsigned line, unsigned column);

    const SourceManager& sources;
    FdWriter out;
    const DiagEngine* engine;
  };

} // namespace pl4g
