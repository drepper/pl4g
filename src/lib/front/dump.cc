#include "front/dump.hh"

#include <algorithm>
#include <bit>
#include <format>
#include <utility>
#include <vector>


namespace pl4g::ast {

  namespace {

    consteval auto unchecked() { return std::meta::access_context::unchecked(); }

    // A field's name as pypl4g spells it: without the underscore a C++
    // keyword made us add.
    constexpr std::string_view python_name(std::string_view name)
    {
      return name.ends_with('_') ? name.substr(0, name.size() - 1) : name;
    }

    void value(std::string& out, const Node* node);

    template<typename T>
    void value(std::string& out, const T& v);

    // The decimal digits of a 128-bit integer.
    std::string decimal(__int128 v)
    {
      if (v == 0)
        return "0";
      bool negative = v < 0;
      unsigned __int128 m = negative ? -(unsigned __int128)(v) : (unsigned __int128)(v);
      std::string digits;
      for (; m > 0; m /= 10)
        digits.insert(digits.begin(), char('0' + int(m % 10)));
      return negative ? "-" + digits : digits;
    }

    void text(std::string& out, std::string_view s)
    {
      out += '"';
      for (unsigned char c : s)
        if (c == '\\' || c == '"' || c < 0x20 || c >= 0x7f)
          out += std::format("\\x{:02x}", unsigned(c));
        else
          out += char(c);
      out += '"';
    }

    // The members of a struct that is not a node, as a tuple.
    template<typename T>
    void tuple(std::string& out, const T& v)
    {
      out += '[';
      bool first = true;
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked()))) {
        if (! first)
          out += ' ';
        first = false;
        value(out, v.[:m:]);
      }
      out += ']';
    }

    template<typename T>
    void value(std::string& out, const T& v)
    {
      if constexpr (std::is_pointer_v<T>) {
        if (v == nullptr)
          out += "None";
        else
          value(out, static_cast<const Node*>(v));
      } else if constexpr (std::is_same_v<T, std::string_view>)
        text(out, v);
      else if constexpr (std::is_same_v<T, bool>)
        out += v ? "true" : "false";
      else if constexpr (std::is_same_v<T, Integer>)
        out += v.too_large ? std::string("big") : decimal(v.value);
      else if constexpr (std::is_same_v<T, double>)
        out += std::format("f:{:x}", std::bit_cast<std::uint64_t>(v));
      else if constexpr (std::is_same_v<T, Span>)
        out += std::format("@{}-{}", v.start, v.end);
      else if constexpr (std::is_enum_v<T>) {
        auto name = python_name(enum_name(v));
        out += name;
      } else if constexpr (std::is_integral_v<T>)
        out += decimal(v);
      else if constexpr (requires { v.has_value(); *v; }) {
        if (v.has_value())
          value(out, *v);
        else
          out += "None";
      } else if constexpr (requires { v.begin(); v.end(); v.size(); }) {
        out += '[';
        bool first = true;
        for (const auto& one : v) {
          if (! first)
            out += ' ';
          first = false;
          value(out, one);
        }
        out += ']';
      } else
        tuple(out, v);
    }

    // The fields of `v` and its bases, as name and text.
    template<typename T>
    void fields(std::vector<std::pair<std::string_view, std::string>>& found, const T& v)
    {
      template for (constexpr auto b : std::define_static_array(std::meta::bases_of(^^T, unchecked())))
        fields(found, static_cast<const [:std::meta::type_of(b):]&>(v));
      template for (constexpr auto m : std::define_static_array(std::meta::nonstatic_data_members_of(^^T, unchecked()))) {
        if constexpr (std::meta::annotations_of_with_type(m, ^^Hidden).size() == 0
                      && std::meta::identifier_of(m) != "tag") {
          std::string one;
          value(one, v.[:m:]);
          found.emplace_back(python_name(std::meta::identifier_of(m)), std::move(one));
        }
      }
    }

    void value(std::string& out, const Node* node)
    {
      visit(*node, [&]<typename T>(const T& n) {
        std::vector<std::pair<std::string_view, std::string>> found;
        if constexpr (std::is_same_v<T, Block>) {
          // What a block holds is asked for, which parses a body set aside.
          found.emplace_back("span", std::format("@{}-{}", n.span.start, n.span.end));
          std::string style;
          value(style, n.style);
          found.emplace_back("style", std::move(style));
          std::string stmts;
          value(stmts, n.stmts());
          found.emplace_back("stmts", std::move(stmts));
        } else
          fields(found, n);
        std::ranges::sort(found, {}, &std::pair<std::string_view, std::string>::first);
        out += '(';
        out += std::meta::identifier_of(^^T);
        for (const auto& [name, text] : found)
          out += std::format(" {}={}", name, text);
        out += ')';
      });
    }

  } // anonymous namespace


  std::string dump(const Node* node)
  {
    std::string out;
    if (node == nullptr)
      out = "None";
    else
      value(out, node);
    return out;
  }

} // namespace pl4g::ast
