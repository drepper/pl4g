// Generate the C++ form of the diagnostic catalog from share/diagnostics.json.
//
// Two files come out: a header with an enumerator for every diagnostic, named
// as the catalog names it, and a source file with the table the engine looks
// entries up in.  The catalog is the contract between the implementations, so
// nothing about a diagnostic is written by hand on this side.
//
// Usage: pl4g-gen-diag CATALOG.json OUT.hh OUT.cc
#include <algorithm>
#include <cstdio>
#include <fstream>
#include <print>
#include <string>
#include <string_view>
#include <vector>

#include <simdjson.h>
#include <uniname.h>
#include <unistr.h>


namespace {

  // One entry of the catalog, as much of it as the engine needs.
  struct Entry {
    std::int64_t number = 0;
    std::string name;
    std::string severity;
    std::string message;
    std::string cause;
    std::string option;
    bool default_enabled = true;
    bool internal = false;
    bool well_formed = false;
  };


  // `text` as a C++ string literal, with every character outside ASCII written
  // by its Unicode name.
  std::string literal(std::string_view text)
  {
    std::string out = "\"";
    auto bytes = reinterpret_cast<const ::uint8_t*>(text.data());
    for (std::size_t at = 0; at < text.size();) {
      ::ucs4_t cp;
      int n = ::u8_mbtouc(&cp, bytes + at, text.size() - at);
      at += std::size_t(n);
      switch (cp) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\t': out += "\\t"; break;
      default:
        if (cp >= 0x20 && cp < 0x7f)
          out += char(cp);
        else {
          char name[UNINAME_MAX];
          if (cp >= 0x80 && ::unicode_character_name(cp, name) != nullptr)
            out += std::format("\\N{{{}}}", name);
          else
            out += std::format("\\U{:08x}", unsigned(cp));
        }
      }
    }
    return out += '"';
  }


  // The string member `key` of `obj`, or "" where it is absent or null.
  std::string string_of(simdjson::dom::object obj, std::string_view key)
  {
    std::string_view value;
    if (obj[key].get(value) != simdjson::SUCCESS)
      return {};
    return std::string(value);
  }


  // The boolean member `key` of `obj`, or `otherwise`.
  bool bool_of(simdjson::dom::object obj, std::string_view key, bool otherwise)
  {
    bool value;
    if (obj[key].get(value) != simdjson::SUCCESS)
      return otherwise;
    return value;
  }

} // anonymous namespace


int main(int argc, char* argv[])
{
  if (argc != 4) [[unlikely]] {
    std::println(stderr, "usage: {} CATALOG.json OUT.hh OUT.cc", argv[0]);
    return 2;
  }
  simdjson::dom::parser parser;
  simdjson::dom::element doc;
  if (auto error = parser.load(argv[1]).get(doc)) [[unlikely]] {
    std::println(stderr, "{}: {}", argv[1], simdjson::error_message(error));
    return 1;
  }
  std::vector<Entry> entries;
  for (simdjson::dom::object item : doc["diagnostics"].get_array()) {
    Entry e;
    e.number = std::int64_t(item["number"]);
    e.name = string_of(item, "name");
    e.severity = string_of(item, "severity");
    e.message = string_of(item, "message");
    e.cause = string_of(item, "cause");
    e.option = string_of(item, "option");
    e.default_enabled = bool_of(item, "default_enabled", true);
    e.internal = bool_of(item, "internal", false);
    e.well_formed = bool_of(item, "well_formed", false);
    entries.push_back(std::move(e));
  }
  std::ranges::sort(entries, {}, &Entry::number);

  std::ofstream hh(argv[2]);
  std::println(hh, "// Generated from share/diagnostics.json by pl4g-gen-diag.  Do not edit.");
  std::println(hh, "#pragma once\n\n#include <cstdint>\n\n\nnamespace pl4g::D {{\n");
  std::println(hh, "  // Every diagnostic of the catalog, by its name there.");
  std::println(hh, "  enum Id : std::uint16_t {{");
  for (const auto& e : entries)
    std::println(hh, "    {} = {},", e.name, e.number);
  std::println(hh, "  }};\n\n  // How many entries the catalog has.");
  std::println(hh, "  inline constexpr unsigned count = {};\n\n}} // namespace pl4g::D", entries.size());

  std::ofstream cc(argv[3]);
  std::println(cc, "// Generated from share/diagnostics.json by pl4g-gen-diag.  Do not edit.");
  std::println(cc, "#include \"diag/catalog.hh\"\n\n\nnamespace pl4g {{\n");
  std::println(cc, "  const DiagInfo catalog_entries[D::count] = {{");
  for (const auto& e : entries)
    std::println(cc, "    {{ D::{}, \"{}\", Severity::{}, {}, {}, {}, {}, {}, {} }},", e.name, e.name, e.severity,
                 literal(e.message), literal(e.cause), literal(e.option), e.default_enabled, e.internal,
                 e.well_formed);
  std::println(cc, "  }};\n\n}} // namespace pl4g");
}
