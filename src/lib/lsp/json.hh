// JSON as the language server reads and writes it: read with simdjson, held as
// a value of its own, and written by this.
//
// Objects keep their members in the order they were put in, as a Python dict
// does, so that what the server writes reads in the order pypl4g's does.
#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <variant>
#include <vector>


namespace pl4g::lsp {

  class Json {
  public:
    using Array = std::vector<Json>;
    using Member = std::pair<std::string, Json>;
    using Object = std::vector<Member>;

    Json() = default;
    Json(std::nullptr_t) {}
    Json(bool b) : value(b) {}
    Json(int n) : value(std::int64_t(n)) {}
    Json(std::int64_t n) : value(n) {}
    Json(double d) : value(d) {}
    Json(std::string s) : value(std::move(s)) {}
    Json(std::string_view s) : value(std::string(s)) {}
    Json(const char* s) : value(std::string(s)) {}
    Json(Array a) : value(std::move(a)) {}
    Json(Object o) : value(std::move(o)) {}

    // An object built member by member, `{ { "a", 1 }, { "b", "c" } }`.
    static Json object(std::initializer_list<Member> members = {}) { return Json(Object(members)); }
    static Json array(std::initializer_list<Json> elements = {}) { return Json(Array(elements)); }

    bool is_null() const noexcept { return std::holds_alternative<std::monostate>(value); }
    bool is_object() const noexcept { return std::holds_alternative<Object>(value); }

    // What it holds, where it holds that; nothing otherwise.
    const std::string* string() const noexcept { return std::get_if<std::string>(&value); }
    const std::int64_t* integer() const noexcept { return std::get_if<std::int64_t>(&value); }
    const bool* boolean() const noexcept { return std::get_if<bool>(&value); }
    const Array* elements() const noexcept { return std::get_if<Array>(&value); }
    const Object* members() const noexcept { return std::get_if<Object>(&value); }
    Array* elements() noexcept { return std::get_if<Array>(&value); }

    // The member `key` of an object, the last where it is written twice;
    // nothing where this is no object or has no such member: Python's
    // `d.get(key)` on what may not be a dict.
    const Json* get(std::string_view key) const noexcept;
    // Put `member` at the end of an object, or give an existing one its value.
    Json& set(std::string key, Json member);

    // The text of it, as `json.dumps(…, ensure_ascii=False)` writes it.
    std::string dump() const;
    // What `text` says, or nothing where it is not JSON.
    static std::optional<Json> parse(std::string_view text);

    bool operator==(const Json&) const = default;

  private:
    void write(std::string& out) const;

    std::variant<std::monostate, bool, std::int64_t, double, std::string, Array, Object> value;
  };

} // namespace pl4g::lsp
