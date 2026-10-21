// What a compilation is asked to do: pypl4g's driver/options.py.
//
// The option table lives in `share/options.json` because both implementations
// accept the same command line -- a build system should not have to know which
// compiler it runs.  It is built into the library.
#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "diag/style.hh"
#include "ir/output.hh"
#include "sema/clauses.hh"
#include "util/ordered.hh"


namespace pl4g::driver {

  // The suffix a source file must have.
  inline constexpr std::string_view source_suffix = ".pl4g";

  // How far the compilation runs and what it writes.
  enum class EmitKind : std::uint8_t { tokens, ast, expanded, ir, asm_, elf };

  // How diagnostics are rendered.
  enum class DiagFormat : std::uint8_t { text, json };

  // The statuses the compiler exits with.
  namespace exit_code {
    inline constexpr int success = 0;
    inline constexpr int errors = 1;
    inline constexpr int usage = 2;
    inline constexpr int internal = 70;
  }

  // What the compiler can be asked to do.  `build` is what a command line with
  // no command means.
  inline constexpr std::string_view build_command = "build";
  inline constexpr std::string_view test_command = "test";
  inline constexpr std::string_view lsp_command = "lsp";
  // What a build is described in, where a command line names no source.
  inline constexpr std::string_view build_file = "build.pl4g";


  // Everything the command line said.  The front end's own -- what the
  // interactive session and the language server ask of a compilation -- are
  // the first few.
  struct Options {
    std::vector<std::filesystem::path> inputs;
    std::vector<std::filesystem::path> module_path;
    sema::Conditions conditions = sema::Conditions::check;
    std::string triple = "x86_64-linux-none";
    // How much room the program's stack has and how much unreachable room is
    // below it; nothing for what a module has where nothing is said.
    std::optional<std::int64_t> stack_size;
    std::optional<std::int64_t> guard_size;
    // Whether `@[system]` is read and not acted on.
    bool ignore_system = false;

    std::optional<std::filesystem::path> output;
    int opt_level = 0;
    EmitKind emit = EmitKind::elf;
    // Which of the target's microarchitecture levels the program may use.
    std::optional<std::string> mclevel;
    // Warnings asked for or turned off by name, in the order they were said.
    OrderedMap<std::string, bool> warnings;
    bool warnings_are_errors = false;
    bool debug_info = false;
    bool verbose = false;
    bool time_report = false;
    DiagFormat diag_format = DiagFormat::text;
    ColourWhen colour = ColourWhen::automatic;
    // How many threads parse the bodies of the program's functions.
    int jobs = 1;
    std::optional<std::filesystem::path> report_log;
    // What to run a test binary through, where this machine does not run it.
    std::optional<std::string> test_runner;
    bool incremental = false;
    // Whether the IR is checked after every stage that changed it.
    bool verify = false;
    // What `-Dname=value` said, for a build function's `std.option`.
    OrderedMap<std::string, std::string> defines;
    // Whether this compilation is one a build function asked for, which never
    // looks for a build function of its own.
    bool from_build = false;
    // Whether the image is position-independent (`-fpie`): a static
    // executable that relocates itself where it was loaded.  pypl4g makes a
    // fixed-address one only, which is the default.
    bool pie = false;
    // What the program is made into: an executable, or with `-shared` a
    // shared object a program loads.  An interpreter embedded in a program
    // says it runs one interpreted, which has a host as a shared object has.
    ir::Output output_kind = ir::Output::executable;
    bool show_help = false;
    bool show_help_json = false;
    bool show_targets = false;
    bool show_version = false;
    std::string command{ build_command };
  };


  // The shared option table, as the JSON text it is.
  std::string_view option_table();

  // The help text, rendered from the table.
  std::string render_help();

  // The name of an emit kind, a diagnostic format, a colour choice, as the
  // command line writes them.
  std::string_view name_of(EmitKind kind) noexcept;
  std::optional<EmitKind> emit_named(std::string_view name) noexcept;
  std::optional<DiagFormat> diag_format_named(std::string_view name) noexcept;

} // namespace pl4g::driver
