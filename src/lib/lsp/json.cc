// JSON as the language server reads and writes it.
#include "lsp/json.hh"

#include <cmath>
#include <format>

#include <simdjson.h>

#include "util/pyfloat.hh"


namespace pl4g::lsp {

  namespace {

    // A string as Python's JSON writer writes one, leaving what is not ASCII as
    // it is.
    void quoted(std::string& out, std::string_view text)
    {
      out += '"';
      for (char c : text)
        switch (c) {
        case '"': out += "\\\""; break;
        case '\\': out += "\\\\"; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        default:
          if (static_cast<unsigned char>(c) < 0x20)
            out += std::format("\\u{:04x}", unsigned(c));
          else
            out += c;
        }
      out += '"';
    }

    Json from(::simdjson::dom::element element)
    {
      switch (element.type()) {
      case ::simdjson::dom::element_type::ARRAY: {
        Json::Array made;
        for (auto one : element.get_array().value_unsafe())
          made.push_back(from(one));
        return Json(std::move(made));
      }
      case ::simdjson::dom::element_type::OBJECT: {
        Json::Object made;
        for (auto [key, one] : element.get_object().value_unsafe())
          made.emplace_back(std::string(key), from(one));
        return Json(std::move(made));
      }
      case ::simdjson::dom::element_type::INT64:
        return Json(element.get_int64().value_unsafe());
      case ::simdjson::dom::element_type::UINT64: {
        auto n = element.get_uint64().value_unsafe();
        return n <= std::uint64_t(INT64_MAX) ? Json(std::int64_t(n)) : Json(double(n));
      }
      case ::simdjson::dom::element_type::DOUBLE:
        return Json(element.get_double().value_unsafe());
      case ::simdjson::dom::element_type::STRING:
        return Json(std::string(element.get_string().value_unsafe()));
      case ::simdjson::dom::element_type::BOOL:
        return Json(element.get_bool().value_unsafe());
      case ::simdjson::dom::element_type::NULL_VALUE:
        return Json();
      }
      return Json();
    }

  } // anonymous namespace


  const Json* Json::get(std::string_view key) const noexcept
  {
    auto object = members();
    if (object == nullptr)
      return nullptr;
    for (auto it = object->rbegin(); it != object->rend(); ++it)
      if (it->first == key)
        return &it->second;
    return nullptr;
  }


  Json& Json::set(std::string key, Json member)
  {
    if (! is_object())
      value = Object{};
    auto& object = std::get<Object>(value);
    for (auto& [k, v] : object)
      if (k == key) {
        v = std::move(member);
        return *this;
      }
    object.emplace_back(std::move(key), std::move(member));
    return *this;
  }


  std::string Json::dump() const
  {
    std::string out;
    write(out);
    return out;
  }


  void Json::write(std::string& out) const
  {
    std::visit(
      [&](const auto& v) {
        using T = std::decay_t<decltype(v)>;
        if constexpr (std::is_same_v<T, std::monostate>)
          out += "null";
        else if constexpr (std::is_same_v<T, bool>)
          out += v ? "true" : "false";
        else if constexpr (std::is_same_v<T, std::int64_t>)
          out += std::to_string(v);
        else if constexpr (std::is_same_v<T, double>)
          out += std::isfinite(v) ? python_repr(v) : (std::isnan(v) ? "NaN" : v > 0 ? "Infinity" : "-Infinity");
        else if constexpr (std::is_same_v<T, std::string>)
          quoted(out, v);
        else if constexpr (std::is_same_v<T, Array>) {
          out += '[';
          for (std::size_t i = 0; i < v.size(); ++i) {
            if (i > 0)
              out += ", ";
            v[i].write(out);
          }
          out += ']';
        } else {
          out += '{';
          for (std::size_t i = 0; i < v.size(); ++i) {
            if (i > 0)
              out += ", ";
            quoted(out, v[i].first);
            out += ": ";
            v[i].second.write(out);
          }
          out += '}';
        }
      },
      value);
  }


  std::optional<Json> Json::parse(std::string_view text)
  {
    ::simdjson::dom::parser parser;
    ::simdjson::padded_string padded(text);
    ::simdjson::dom::element element;
    if (parser.parse(padded).get(element) != ::simdjson::SUCCESS) [[unlikely]]
      return std::nullopt;
    return from(element);
  }

} // namespace pl4g::lsp
