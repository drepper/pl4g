// Running PL4G without building a binary.
#include "repl/repl.hh"

#include "diag/render.hh"
#include "opt/passes.hh"


namespace pl4g::repl {

  Ended run_file(const Settings& settings)
  {
    SourceManager sources;
    TextRenderer renderer(sources, settings.devices.error);
    DiagEngine diags([&](const Diagnostic& d) { renderer(d); }, settings.warnings);
    renderer.use(diags);
    renderer.recolour(settings.colour, settings.devices.output);
    driver::Compilation compilation(settings.compile, diags, sources);
    auto module = compilation.front_end();
    if (module == nullptr || diags.failed()) [[unlikely]]
      return { 1, 0 };
    opt::run_pipeline(*module);

    interp::Options options;
    options.args.push_back(settings.compile.inputs.empty() ? std::string() : settings.compile.inputs.front().string());
    options.args.insert(options.args.end(), settings.args.begin(), settings.args.end());
    options.environment = settings.environment;
    options.devices = settings.devices;
    options.sources = &sources;
    auto outcome = interp::run(*module, options);
    return { outcome.status, outcome.signal };
  }

} // namespace pl4g::repl
