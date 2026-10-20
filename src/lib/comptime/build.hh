// What a build function leaves behind, and what it is given to leave it in.
//
// A port of pypl4g's comptime/build.py.  The object a build function writes
// into is an ordinary record of the `std` module as far as the program is
// concerned; while the compiler runs the function it is a `Record` here, and
// its fields are read back afterwards.  The things to build are added by
// functions the compiler provides.  What the build function says nothing about
// is what the command line said.
#pragma once

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

#include "comptime/evaluate.hh"
#include "source/location.hh"


namespace pl4g {
  class DiagEngine;
}


namespace pl4g::comptime::build {

  // What the record is called, and what each of its fields is called.
  inline constexpr std::string_view type_name = "Build";
  inline constexpr std::string_view output_dir_field = "output_dir";
  inline constexpr std::string_view target_field = "target";
  inline constexpr std::string_view opt_level_field = "opt_level";
  inline constexpr std::string_view mclevel_field = "mclevel";
  inline constexpr std::string_view stack_size_field = "stack_size";
  inline constexpr std::string_view guard_size_field = "guard_size";
  inline constexpr std::string_view env_field = "env";


  // One thing to build, and where it was asked for.
  struct Artifact {
    std::string name;
    std::vector<std::string> sources;
    Span span;
  };


  // Everything a build function asked for, once it has finished.
  struct Plan {
    std::string output_dir;
    std::string target;
    std::int64_t opt_level = 0;
    std::string mclevel;
    std::int64_t stack_size = 0;
    std::int64_t guard_size = 0;
    // What the compiler was run with, which a build function reads and cannot
    // change.
    std::vector<std::pair<std::string, std::string>> env;
    std::vector<Artifact> artifacts;

    // The object to hand the build function, holding these as its fields.
    std::shared_ptr<Record> record() const;
    // Read back what the build function wrote into the object, the
    // environment aside.
    void settle(const Record& record);
  };


  // The functions the compiler provides while a build function runs: the
  // module's builtins, filed under `std` by the caller.  `plan`, `given` and
  // `diags` must outlive what is answered.
  ModuleBuiltins builtins(Plan& plan, const std::unordered_map<std::string, std::string>& given, DiagEngine& diags);

  // Where an artifact's sources are: beside the build file that named them.
  std::vector<std::filesystem::path> sources_of(const Artifact& artifact, const std::filesystem::path& beside);

  // Where what is built is written.
  std::filesystem::path named(const Artifact& artifact, const Plan& plan);

  // What was built, one line each.
  std::vector<std::string> summary(const Plan& plan);

} // namespace pl4g::comptime::build
