// Running the compiler over what the editor is holding.
#include "lsp/analysis.hh"

#include "driver/driver.hh"


namespace pl4g::lsp {

  std::unique_ptr<Analysis> analyse(const std::filesystem::path& path, const Held& held, bool whole,
                                    std::vector<std::filesystem::path> module_path)
  {
    auto found = std::make_unique<Analysis>();
    found->path = path;
    found->whole = whole;
    for (const auto& [where, text] : held)
      found->sources->hold(where, text);
    auto* collected = &found->diagnostics;
    found->diags = std::make_unique<DiagEngine>([collected](const Diagnostic& d) { collected->push_back(&d); });
    driver::Options options;
    options.inputs.push_back(path);
    options.module_path = std::move(module_path);
    found->compilation = std::make_unique<driver::Compilation>(std::move(options), *found->diags, *found->sources);
    try {
      // Typing gets the front end, which is where all but a handful of the
      // diagnostics are; saving gets the whole compiler, code generation
      // included, so that a program the back end refuses says so at the
      // moment there is a file to refuse.  Nothing of it writes to the
      // standard output, which carries the protocol.
      if (whole)
        driver::compile_whole(found->compilation->options, *found->diags, *found->sources, found->notes.get(),
                              { -1, 2, 2 }, &found->kept);
      else
        found->compilation->analyze(found->compilation->read_and_parse(), found->notes.get());
    } catch (const InternalError& error) {
      // What is lost is one analysis rather than the session; what went wrong
      // is said as pypl4g says an internal error.
      found->diags->internal(error.what());
    } catch (const std::exception&) {
      // The disk, or anything else outside the compiler.
    }
    // What the parser made of this file, which the manager records for every
    // file it read: the tree is what the features that are not diagnostics read.
    for (const auto& read : found->sources->read_units)
      if (read.path == path && read.unit != nullptr)
        found->unit = static_cast<const ast::SourceUnit*>(read.unit);
    return found;
  }

} // namespace pl4g::lsp
