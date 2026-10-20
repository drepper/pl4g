// The attribute registry.
//
// A port of pypl4g's sema/attributes.py.  An attribute is written `@[name]` or
// `@[name(arguments)]`, arguments positional first and named after; each one
// declares which kinds of object it applies to, so that one written where it
// means nothing is an error rather than an annotation silently ignored.
#pragma once

#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>

#include "front/ast.hh"
#include "ir/ir.hh"


namespace pl4g::sema {

  // The kinds of object an attribute may be applied to, as bits (`AttrTarget`).
  enum class AttrTarget : std::uint16_t {
    none = 0,
    function = 1 << 0,
    type = 1 << 1,
    variable = 1 << 2,
    module = 1 << 3,
    parameter = 1 << 4,
    statement = 1 << 5,
    // A lambda, and the function type a lambda is held by.
    callable = 1 << 6,
    // A bundle, which is neither a type nor a value.
    bundle = 1 << 7,
    // A file, said by a list standing alone at the top level.
    file = 1 << 8,
    any = (1 << 9) - 1,
  };

  constexpr AttrTarget operator|(AttrTarget a, AttrTarget b) noexcept
  {
    return AttrTarget(std::uint16_t(a) | std::uint16_t(b));
  }

  // Whether `one` is among `targets`: Python's `one in targets`.
  constexpr bool among(AttrTarget one, AttrTarget targets) noexcept
  {
    return (std::uint16_t(one) & std::uint16_t(targets)) == std::uint16_t(one);
  }

  // How a single target is named in a diagnostic (`TARGET_NAMES`).
  std::string_view target_name(AttrTarget target) noexcept;


  // What an argument of an attribute holds once checked: nothing (a parameter
  // with no default), an integer, a string or name, or a truth value.
  using AttrValue = std::variant<std::monostate, __int128, std::string_view, bool>;


  // One parameter of an attribute (`AttrParam`).
  struct AttrParam {
    std::string_view name;
    // "integer", "string", "boolean" or "name".
    std::string_view kind;
    // For a parameter whose value is one of a fixed set of names; empty for
    // the rest, no attribute having an empty set.
    std::span<const std::string_view> choices{};
    AttrValue default_{};
    bool required = true;

    // How this parameter is named in a diagnostic.
    std::string describe() const;
  };


  // The declaration of one attribute (`AttrSpec`).
  struct AttrSpec {
    std::string_view name;
    AttrTarget targets = AttrTarget::none;
    std::span<const AttrParam> params{};
    // Attributes that cannot be applied to the same object as this one.
    std::optional<std::string_view> group{};
    // Whether it may be applied to one object more than once.
    bool repeatable = false;
    std::string_view doc{};

    // The attribute's signature, as a diagnostic spells it.
    std::string signature() const;
  };


  // Every attribute the compiler knows, in the order declared (`REGISTRY`).
  std::span<const AttrSpec> registry() noexcept;

  // The declaration of the attribute `name`, if there is one (`lookup`).
  const AttrSpec* lookup(std::string_view name) noexcept;

  // Which special kind each kind of test selects (`SPECIAL_OF_TEST_KIND`).
  std::optional<ir::SpecialKind> special_of_test_kind(std::string_view kind) noexcept;


  // An attribute whose arguments have been checked against its declaration
  // (`BoundAttr`).  The values are by parameter name, in the parameters' order.
  struct BoundAttr {
    const AttrSpec* spec = nullptr;
    const ast::Attribute* node = nullptr;
    std::vector<std::pair<std::string_view, AttrValue>> values;

    // The attribute's name.
    std::string_view name() const noexcept { return spec->name; }
    // The value of `param`, or nothing where there is none.
    const AttrValue* value(std::string_view param) const noexcept;
    // The integer value of `param`.
    __int128 as_int(std::string_view param, __int128 fallback = 0) const noexcept;
    // The string or name value of `param`.
    std::string_view as_str(std::string_view param, std::string_view fallback = "") const noexcept;
    // The boolean value of `param`.
    bool as_bool(std::string_view param, bool fallback = false) const noexcept;
  };

} // namespace pl4g::sema
