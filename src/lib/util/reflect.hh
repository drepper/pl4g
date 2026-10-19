// Small uses of reflection shared by the whole library.
//
// Enumerations carry what is said about each of their members as an annotation
// on the member, so that a member and its description are written once and side
// by side rather than in an enumeration and a table that must be kept in step.
#pragma once

#include <meta>
#include <string_view>


namespace pl4g {

  // An annotation saying how an enumerator is described to a reader.
  struct Describe {
    const char* text;

    // Keep the text where a constant expression may point at it.
    consteval Describe(std::string_view t) : text(std::define_static_string(t)) {}
  };


  // The name an enumerator is declared with.
  template<typename E>
    requires std::is_enum_v<E>
  constexpr std::string_view enum_name(E e)
  {
    template for (constexpr auto m : std::define_static_array(std::meta::enumerators_of(^^E)))
      if (e == [:m:])
        return std::meta::identifier_of(m);
    return "<invalid>";
  }


  // How an enumerator is described: its `Describe` annotation, or else its name.
  template<typename E>
    requires std::is_enum_v<E>
  constexpr std::string_view describe(E e)
  {
    template for (constexpr auto m : std::define_static_array(std::meta::enumerators_of(^^E)))
      if (e == [:m:]) {
        constexpr auto ann = std::define_static_array(std::meta::annotations_of_with_type(m, ^^Describe));
        if constexpr (ann.size() > 0)
          return std::meta::extract<Describe>(ann[0]).text;
        else
          return std::meta::identifier_of(m);
      }
    return "<invalid>";
  }


  // The number of enumerators of E.
  template<typename E>
    requires std::is_enum_v<E>
  consteval std::size_t enum_count()
  {
    return std::meta::enumerators_of(^^E).size();
  }

} // namespace pl4g
