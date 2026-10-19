// The diagnostic catalog.
//
// The number, the severity and the text of a diagnostic come from the shared
// catalog and never from where it is raised, so that two implementations
// cannot drift apart.  The table is generated from share/diagnostics.json.
#pragma once

#include <cstdint>
#include <span>
#include <string_view>

#include "diag/ids.hh"
#include "util/reflect.hh"


namespace pl4g {

  using DiagId = D::Id;

  // How serious a diagnostic is.  The enumerators are named as the catalog
  // names them, which is also how they are printed.
  enum class Severity : std::uint8_t { note, warning, error, fatal };


  // One entry of the catalog.
  struct DiagInfo {
    DiagId number;
    std::string_view name;
    Severity severity = Severity::error;
    // The message, with `{name}` where an argument goes.
    std::string_view message;
    std::string_view cause;
    // The name of the warning option that controls it, or empty.
    std::string_view option;
    bool default_enabled = true;
    bool internal = false;
    // An error the construct is still whole despite: a rule about how code is
    // written rather than about whether there is anything to compile.
    bool well_formed = false;

    // Whether this diagnostic prevents a successful compilation.
    constexpr bool is_error() const noexcept { return severity == Severity::error || severity == Severity::fatal; }

    // Whether silencing it leaves nothing worth compiling.
    constexpr bool spoils_the_construct() const noexcept { return is_error() && ! well_formed; }
  };


  // Every entry, sorted by number.
  extern const DiagInfo catalog_entries[D::count];

  // The entry for `id`.
  const DiagInfo& catalog_entry(DiagId id) noexcept;

  // The entry called `name`, if there is one.
  const DiagInfo* catalog_by_name(std::string_view name) noexcept;

  // The entry the warning option `option` controls, if there is one.
  const DiagInfo* catalog_by_option(std::string_view option) noexcept;

  // The whole catalog.
  inline std::span<const DiagInfo> catalog() noexcept { return catalog_entries; }

} // namespace pl4g
