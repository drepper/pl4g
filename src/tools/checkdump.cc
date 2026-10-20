// Check a program as pypl4g does and print what the checker made of it: the
// diagnostics, rendered, on standard error, and the IR before any pass ran.
// src/test/check_agrees.py compares both with what pypl4g makes.
//
// Usage: pl4g-checkdump [--ir=FILE] [--passes] [PYPL4G-OPTION...] FILE...
#include <argp.h>

#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <optional>
#include <print>
#include <string>
#include <vector>

#include "diag/engine.hh"
#include "diag/render.hh"
#include "driver/compile.hh"
#include "ir/printer.hh"
#include "opt/passes.hh"
#include "sema/clauses.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


namespace {

  struct Arguments {
    std::string ir;
    bool passes = false;
    std::vector<std::string> warnings;
    bool werror = false;
    std::vector<std::string> module_path;
    std::string conditions = "check";
    std::optional<std::int64_t> stack_size;
    std::optional<std::int64_t> guard_size;
    bool ignore_system = false;
    std::vector<std::string> files;
  };

  enum Key : int {
    key_ir = 1000,
    key_passes,
    key_werror,
    key_module_path,
    key_conditions,
    key_stack_size,
    key_guard_size,
    key_ignore_system,
  };

  constexpr ::argp_option options[] = {
    { "ir", key_ir, "FILE", 0, "Write the IR the checker made to FILE", 0 },
    { "passes", key_passes, nullptr, 0, "Run the passes of level nought before writing it", 0 },
    { nullptr, 'W', "WARNING", 0, "Enable a warning, or with no- disable it", 0 },
    { "Werror", key_werror, nullptr, 0, "Make every warning an error", 0 },
    { "module-path", key_module_path, "DIRS", 0, "Look for modules in DIRS too", 0 },
    { "conditions", key_conditions, "WHAT", 0, "check, ignore or observe the conditions", 0 },
    { "stack-size", key_stack_size, "BYTES", 0, "The size of the stack", 0 },
    { "guard-size", key_guard_size, "BYTES", 0, "The size of the stack's guard", 0 },
    { "ignore-system", key_ignore_system, nullptr, 0, "Treat system modules as any other", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case key_ir:
      args.ir = arg;
      return 0;
    case key_passes:
      args.passes = true;
      return 0;
    case 'W':
      if (std::string_view(arg) == "error")
        args.werror = true;
      else
        args.warnings.emplace_back(arg);
      return 0;
    case key_werror:
      args.werror = true;
      return 0;
    case key_module_path:
      args.module_path.emplace_back(arg);
      return 0;
    case key_conditions:
      args.conditions = arg;
      return 0;
    case key_stack_size:
      args.stack_size = std::strtoll(arg, nullptr, 0);
      return 0;
    case key_guard_size:
      args.guard_size = std::strtoll(arg, nullptr, 0);
      return 0;
    case key_ignore_system:
      args.ignore_system = true;
      return 0;
    case ARGP_KEY_ARG:
      args.files.emplace_back(arg);
      return 0;
    case ARGP_KEY_END:
      if (args.files.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, "FILE...",
                              "Check PL4G source files and print what the checker made of them.", nullptr, nullptr,
                              nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);

  pl4g::SourceManager sources;
  pl4g::TextRenderer renderer(sources, 2);
  pl4g::WarningControl control;
  control.warnings_are_errors = args.werror;
  for (const auto& name : args.warnings)
    if (name.starts_with("no-"))
      control.enabled[name.substr(3)] = false;
    else
      control.enabled[name] = true;
  pl4g::DiagEngine diags([&](const pl4g::Diagnostic& d) { renderer(d); }, control);
  renderer.use(diags);

  auto conditions = pl4g::sema::conditions_named(args.conditions);
  if (! conditions) [[unlikely]] {
    std::print(stderr, "pl4g-checkdump: unknown --conditions {}\n", args.conditions);
    return 2;
  }

  pl4g::driver::Options given;
  for (const auto& path : args.files)
    given.inputs.emplace_back(path);
  for (const auto& dirs : args.module_path)
    for (auto& one : pl4g::sema::parse_search_path(dirs))
      given.module_path.push_back(std::move(one));
  given.conditions = *conditions;
  given.stack_size = args.stack_size;
  given.guard_size = args.guard_size;
  given.ignore_system = args.ignore_system;
  pl4g::driver::Compilation compilation(std::move(given), diags, sources);
  auto made = compilation.front_end();
  if (made == nullptr)
    return 1;
  auto& module = *made;

  if (! args.ir.empty() && ! diags.failed()) {
    if (args.passes)
      pl4g::opt::run_pipeline(module);
    std::FILE* out = std::fopen(args.ir.c_str(), "w");
    if (out == nullptr) [[unlikely]] {
      std::print(stderr, "pl4g-checkdump: cannot write {}\n", args.ir);
      return 2;
    }
    std::print(out, "{}", pl4g::ir::render_module(module));
    std::fclose(out);
  }
  return diags.failed() ? 1 : 0;
}
