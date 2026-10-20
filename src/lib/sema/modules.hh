// Finding modules, reading them once, and deciding what to call them.
//
// A port of pypl4g's sema/modules.py.  A name beginning with a slash names a
// file outright; otherwise the importing file's directory is looked in first,
// then the directories given on the command line, then -- for a name with no
// slash -- the installation's.  A file is read once, remembered by the path it
// resolved to.  A module is called by the shortest of the names its routes give
// it, and two files coming to one name get a few characters of their path's
// hash to tell them apart.
#pragma once

#include <exception>
#include <filesystem>
#include <functional>
#include <memory>
#include <set>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "ir/ir.hh"
#include "sema/held.hh"
#include "util/ordered.hh"


namespace pl4g::sema {

  // What a module file is called (`SUFFIX`).
  inline constexpr std::string_view module_suffix = ".pl4g";

  // How much of the hash of a path tells two modules of one name apart.
  inline constexpr std::size_t hash_length = 6;

  // A set of names, as the checker asks a module for what they may name.
  using NameSet = std::set<std::string, std::less<>>;


  // A path as pathlib holds one: no empty or `.` components, no trailing
  // slash, and `.` for nothing at all.  What `str(path)` gives in Python is
  // what `string()` gives here.
  std::filesystem::path pure_path(const std::filesystem::path& path);

  // pathlib's `path.parent`, normalized as above.
  std::filesystem::path parent_of(const std::filesystem::path& path);


  // No file of that name was found anywhere that was looked (`ModuleNotFound`).
  struct ModuleNotFound : std::exception {
    std::string name;
    // Every place looked at, in the order looked at.
    std::vector<std::filesystem::path> looked;

    ModuleNotFound(std::string name, std::vector<std::filesystem::path> looked)
      : name(std::move(name)), looked(std::move(looked)) {}
    const char* what() const noexcept override { return name.c_str(); }
  };


  // A module imports itself, directly or round a ring (`ImportCycle`).
  struct ImportCycle : std::exception {
    // The ring, beginning and ending at the file that closes it.
    std::vector<std::filesystem::path> chain;

    explicit ImportCycle(std::vector<std::filesystem::path> chain) : chain(std::move(chain)) {}
    const char* what() const noexcept override { return "import cycle"; }
  };


  // One file that has been read, and what it turned out to hold (`LoadedModule`).
  struct LoadedModule {
    std::filesystem::path path;
    // What the file exports, by the name it gave it.
    OrderedMap<std::string, Held> exports;
    // The bundles it exports, by name: neither types nor values.
    OrderedMap<std::string, Bundle*> bundles;
    // The operators it exports, by glyph and number of operands.
    OrderedMap<std::pair<std::string, std::int64_t>, Held> operators;
    // What it exports from inside a type, by the type's name and the name after
    // the dot.
    OrderedMap<std::pair<std::string, std::string>, Held> members;
    // Every name this module could be known by, one per route that reached it.
    std::set<std::string> candidates;
    // The name chosen, once every route is known.
    std::string name;
    // The functions and variables whose symbol carries the module's name, given
    // it once the names are settled.
    std::vector<ir::Value*> owned;
    // What lowers the functions of this module a set of names may name: the
    // module's checker.  Empty for a module lowered whole.
    std::function<void(const NameSet&)> want;

    // Record a name this module could go by, and return it.
    std::string add_candidate(std::string_view prefix, std::string_view base);
  };


  // Where modules are looked for, in the order they are looked for (`SearchPath`).
  struct SearchPath {
    // Directories given on the command line, in the order given.
    std::vector<std::filesystem::path> given;
    // The installation's directories, looked at last and only for a name with
    // no slash in it.
    std::vector<std::filesystem::path> system = system_modules();
    // Where the compiler was run, which a relative entry of `given` is
    // relative to second.
    std::filesystem::path working = std::filesystem::current_path();

    // Every file that might be the module `name`, in the order to try them.
    std::vector<std::filesystem::path> places(std::string_view name, const std::filesystem::path& importer) const;

    // The directories the installation provides modules in (`system_modules`):
    // `modules` in the tree the compiler was built from.
    static std::vector<std::filesystem::path> system_modules();
  };


  // Every module the compilation has read, by the path it was read from
  // (`ModuleRegistry`).
  class ModuleRegistry {
  public:
    SearchPath search;

    ModuleRegistry() = default;
    explicit ModuleRegistry(SearchPath search) : search(std::move(search)) {}

    // Where the module `name` is, as imported from `importer`; throws
    // `ModuleNotFound`.
    std::filesystem::path resolve(std::string_view name, const std::filesystem::path& importer) const;

    // The module already read from `path`, or nullptr -- also for one still
    // being read.
    LoadedModule* known(const std::filesystem::path& path) const;

    // The ring importing `path` would close, or nothing.
    std::optional<std::vector<std::filesystem::path>> cycle_through(const std::filesystem::path& path) const;

    // Record that `path` is being read; throws `ImportCycle` for a ring.
    LoadedModule& begin(const std::filesystem::path& path);

    // Record that `path` has been read.
    void finish(const std::filesystem::path& path);

    // Give every module its name, now that every route to it is known.
    void settle_names();

    // The modules read, in the order they were begun.
    const std::vector<std::unique_ptr<LoadedModule>>& modules() const noexcept { return by_path; }

  private:
    // Tell apart two modules that came to one name.
    void disambiguate();
    LoadedModule* find(const std::filesystem::path& path) const;
    bool being_read(const std::filesystem::path& path) const;

    std::vector<std::unique_ptr<LoadedModule>> by_path;
    // The files being read just now, innermost last.
    std::vector<std::filesystem::path> reading;
  };


  // What a module at `path` is called before anything is put in front of it
  // (`base_name`).
  std::string base_name(const std::filesystem::path& path);

  // A few characters standing for a whole path: BLAKE2b of its text with a
  // six-byte digest, the first six hex digits (`path_hash`).
  std::string path_hash(const std::filesystem::path& path);

  // The directories a colon-separated path names, in order (`parse_search_path`).
  std::vector<std::filesystem::path> parse_search_path(std::string_view given);

} // namespace pl4g::sema
