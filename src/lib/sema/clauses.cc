// What a condition written in a signature does in this build.
#include "sema/clauses.hh"

#include <meta>


namespace pl4g::sema {

  std::optional<Conditions> conditions_named(std::string_view value) noexcept
  {
    template for (constexpr auto e : std::define_static_array(std::meta::enumerators_of(^^Conditions)))
      if (describe([:e:]) == value)
        return [:e:];
    return std::nullopt;
  }

} // namespace pl4g::sema
