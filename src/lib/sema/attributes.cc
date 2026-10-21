// The attribute registry.
#include "sema/attributes.hh"

#include <algorithm>


namespace pl4g::sema {

  namespace {

    using T = AttrTarget;

    constexpr std::string_view test_kinds[] = { "always", "build", "suite" };
    constexpr std::string_view inline_modes[] = { "always", "never" };

    constexpr AttrParam priority[] = {
      { .name = "priority", .kind = "integer", .default_ = __int128(0), .required = false },
    };
    constexpr AttrParam test_kind[] = {
      { .name = "kind", .kind = "name", .choices = test_kinds, .default_ = std::string_view("suite"), .required = false },
    };
    constexpr AttrParam inline_mode[] = {
      { .name = "mode", .kind = "name", .choices = inline_modes, .default_ = std::string_view("always"),
        .required = false },
    };
    constexpr AttrParam variadic[] = {
      { .name = "variadic", .kind = "boolean", .default_ = false, .required = false },
    };
    constexpr AttrParam abi_params[] = {
      { .name = "name", .kind = "string", .default_ = std::string_view(""), .required = false },
      { .name = "variadic", .kind = "boolean", .default_ = false, .required = false },
    };
    constexpr AttrParam function_name[] = {
      { .name = "function", .kind = "name" },
    };
    constexpr AttrParam symbol[] = {
      { .name = "symbol", .kind = "string" },
    };
    constexpr AttrParam bytes[] = {
      { .name = "bytes", .kind = "integer" },
    };
    constexpr AttrParam section_name[] = {
      { .name = "name", .kind = "string" },
    };
    constexpr AttrParam number[] = {
      { .name = "number", .kind = "integer" },
    };

    // Every attribute the compiler knows.  Adding one is an entry here plus the
    // code that acts on it; the notation itself never changes.
    constexpr AttrSpec specs[] = {
      { .name = "startup", .targets = T::function, .group = "special",
        .doc = "the function control is transferred to once startup completes" },
      { .name = "constructor", .targets = T::function, .params = priority, .group = "special",
        .doc = "runs before control is transferred to the startup function" },
      { .name = "destructor", .targets = T::function, .params = priority, .group = "special",
        .doc = "runs when the startup function returns" },
      { .name = "test", .targets = T::function, .params = test_kind, .group = "special",
        .doc = "a test of the given kind; `suite` where none is named" },
      { .name = "build", .targets = T::function, .group = "special",
        .doc = "the function the compiler runs to find out what to build" },
      { .name = "builtin", .targets = T::function,
        .doc = "the compiler provides this function while it works out a build; there is none of it in a program" },
      { .name = "inline", .targets = T::function, .params = inline_mode,
        .doc = "guides the decision whether to inline the function" },
      { .name = "cdecl", .targets = T::function | T::variable, .params = variadic,
        .doc = "the definition follows the system's conventions" },
      { .name = "listable", .targets = T::function | T::callable,
        .doc = "an array argument is walked and the answers make an array" },
      { .name = "impure", .targets = T::function | T::callable,
        .doc = "the function may change things that outlive the call" },
      { .name = "opaque", .targets = T::function,
        .doc = "every call to the function is a black box to the optimizer: not inlined, folded, merged, moved or "
               "dropped, though the function stays pure" },
      { .name = "can_ignore", .targets = T::function | T::callable,
        .doc = "a caller need not take what the function answers with" },
      { .name = "abi", .targets = T::function | T::type, .params = abi_params,
        .doc = "the definition follows the system's ABI: a function is called the way the named convention says, and "
               "a record is laid out the way one compiled by something else is" },
      { .name = "device", .targets = T::type,
        .doc = "a value of this type is permission to do input or output: a function handed one may do it without "
               "being marked '@[impure]', which is then what says a function changes something global rather than "
               "merely reading or writing a device somebody gave it" },
      { .name = "io", .targets = T::function,
        .doc = "the function does input or output.  A caller needs the same permission, which it has by holding a "
               "device or by saying this; what it does not need is '@[impure]'" },
      { .name = "unique", .targets = T::type,
        .doc = "there is one of a value of this type and it is never copied: binding it to a second name, handing it "
               "to a call by value, answering with it or putting it inside something else are all refused, and a "
               "reference to it is how it is passed along" },
      { .name = "closed_by", .targets = T::type, .params = function_name,
        .doc = "a value of this type stands for something that has to be given back, which the named function of the "
               "type does: it is moved and never copied, and every variable holding one closes it or moves it on "
               "before its scope ends" },
      { .name = "external", .targets = T::function, .params = symbol, .group = "elsewhere",
        .doc = "the function is defined somewhere else, under this symbol, and the compiler fills it in" },
      { .name = "host", .targets = T::function, .params = symbol, .group = "elsewhere",
        .doc = "the function is defined by the program this one is loaded into, under this symbol, and is looked up "
               "when it is first called" },
      { .name = "system", .targets = T::file,
        .doc = "a system module: what it alone would be warned about or have logged is left unsaid, as a system "
               "header is in C" },
      { .name = "export", .targets = T::function | T::variable | T::type | T::bundle | T::module,
        .doc = "a file importing this module may name the definition, or the module it imported" },
      { .name = "visible", .targets = T::function | T::variable,
        .doc = "the finished image offers the definition's symbol" },
      { .name = "flag", .targets = T::type, .doc = "an enumeration whose values are meant to be combined" },
      { .name = "align", .targets = T::type | T::variable | T::function, .params = bytes,
        .doc = "requests a minimum alignment" },
      { .name = "section", .targets = T::function | T::variable, .params = section_name,
        .doc = "places the object in the named section of the image" },
      { .name = "packed", .targets = T::type, .doc = "requests a layout without padding between fields" },
      { .name = "ignore", .targets = T::statement | T::function | T::variable, .params = number, .repeatable = true,
        .doc = "this diagnostic is not reported for the construct" },
      { .name = "expect", .targets = T::statement | T::function | T::variable, .params = number, .repeatable = true,
        .doc = "the construct raises this diagnostic, and it is not reported" },
    };

    // `", ".join(...)` over a span of names.
    std::string joined(std::span<const std::string_view> names)
    {
      std::string out;
      for (auto one : names) {
        if (! out.empty())
          out += ", ";
        out += one;
      }
      return out;
    }

  } // anonymous namespace


  std::string_view target_name(AttrTarget target) noexcept
  {
    switch (target) {
    case T::function:
      return "a function";
    case T::type:
      return "a type";
    case T::variable:
      return "a variable";
    case T::module:
      return "a module";
    case T::parameter:
      return "a parameter";
    case T::statement:
      return "a statement";
    case T::callable:
      return "a lambda or a function type";
    case T::bundle:
      return "a bundle";
    case T::file:
      return "a file";
    default:
      return "";
    }
  }


  std::string AttrParam::describe() const
  {
    if (! choices.empty())
      return std::string(name) + ": one of " + joined(choices);
    return std::string(name) + ": " + std::string(kind);
  }


  std::string AttrSpec::signature() const
  {
    if (params.empty())
      return std::string(name);
    std::string out = std::string(name) + "(";
    for (std::size_t i = 0; i < params.size(); ++i) {
      if (i > 0)
        out += ", ";
      out += params[i].describe();
    }
    return out + ")";
  }


  std::span<const AttrSpec> registry() noexcept
  {
    return specs;
  }


  const AttrSpec* lookup(std::string_view name) noexcept
  {
    auto found = std::ranges::find(specs, name, &AttrSpec::name);
    return found == std::ranges::end(specs) ? nullptr : &*found;
  }


  std::optional<ir::SpecialKind> special_of_test_kind(std::string_view kind) noexcept
  {
    if (kind == "always")
      return ir::SpecialKind::test_always;
    if (kind == "build")
      return ir::SpecialKind::test_build;
    if (kind == "suite")
      return ir::SpecialKind::test_suite;
    return std::nullopt;
  }


  const AttrValue* BoundAttr::value(std::string_view param) const noexcept
  {
    auto found = std::ranges::find(values, param, &std::pair<std::string_view, AttrValue>::first);
    return found == values.end() ? nullptr : &found->second;
  }


  __int128 BoundAttr::as_int(std::string_view param, __int128 fallback) const noexcept
  {
    auto found = value(param);
    auto held = found == nullptr ? nullptr : std::get_if<__int128>(found);
    return held == nullptr ? fallback : *held;
  }


  std::string_view BoundAttr::as_str(std::string_view param, std::string_view fallback) const noexcept
  {
    auto found = value(param);
    auto held = found == nullptr ? nullptr : std::get_if<std::string_view>(found);
    return held == nullptr ? fallback : *held;
  }


  bool BoundAttr::as_bool(std::string_view param, bool fallback) const noexcept
  {
    auto found = value(param);
    auto held = found == nullptr ? nullptr : std::get_if<bool>(found);
    return held == nullptr ? fallback : *held;
  }

} // namespace pl4g::sema
