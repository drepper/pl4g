// Read modules written as JSON (src/test/irjson.py) and write their textual
// form, which src/test/ir_agrees.py compares with what pypl4g writes.  With
// --check-layout it says instead where its layout of a type differs from the
// one pypl4g wrote beside it.
//
// Usage: pl4g-irdump [--check-layout] FILE.json...
#include <argp.h>

#include <format>
#include <string>
#include <vector>

#include <simdjson.h>

#include "ir/json.hh"
#include "ir/layout.hh"
#include "ir/printer.hh"
#include "source/manager.hh"
#include "util/fdio.hh"


namespace {

  struct Arguments {
    bool check_layout = false;
    std::vector<std::string> files;
  };

  constexpr ::argp_option options[] = {
    { "check-layout", 'l', nullptr, 0, "Compare the layout of every type with the one the file records", 0 },
    {},
  };

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& args = *static_cast<Arguments*>(state->input);
    switch (key) {
    case 'l':
      args.check_layout = true;
      return 0;
    case ARGP_KEY_ARG:
      args.files.emplace_back(arg);
      return 0;
    case ARGP_KEY_END:
      if (args.files.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser = { options, parse_option, "FILE.json...", "Print modules written as JSON as IR text.",
                              nullptr, nullptr, nullptr };


  // The numbers in a JSON array.
  std::vector<std::uint64_t> numbers(::simdjson::dom::element e)
  {
    std::vector<std::uint64_t> out;
    for (auto one : e.get_array())
      out.push_back(std::uint64_t(std::int64_t(one)));
    return out;
  }


  // How many types of `module` are laid out otherwise than `text` records.
  unsigned check_layout(const pl4g::ir::Module& module, const std::vector<const pl4g::ir::Type*>& types,
                        std::string_view text, const std::string& path, pl4g::FdWriter& out)
  {
    ::simdjson::dom::parser parser;
    ::simdjson::padded_string padded(text);
    auto doc = parser.parse(padded);
    pl4g::ir::DataLayout layout;
    layout.frames = &module.frame_sizes;
    unsigned wrong = 0;
    std::size_t id = 0;
    for (auto entry : doc["types"].get_array()) {
      const auto* ty = types[id++];
      auto recorded = entry["layout"];
      if (recorded.is_null())
        continue;
      try {
        std::vector<std::pair<std::string, bool>> checks{
          { "size", pl4g::ir::size_of(ty, layout) == std::uint64_t(std::int64_t(recorded["size"])) },
          { "align", pl4g::ir::align_of(ty, layout) == std::uint64_t(std::int64_t(recorded["align"])) },
          { "parts", pl4g::ir::part_offsets_of(ty, layout) == numbers(recorded["parts"]) },
          { "leaves", pl4g::ir::leaf_offsets_of(ty, layout) == numbers(recorded["leaves"]) },
        };
        if (auto product = pl4g::ir::as<pl4g::ir::ProductType>(ty))
          checks.emplace_back("offsets", pl4g::ir::offsets_of(product, layout) == numbers(recorded["offsets"]));
        if (auto tuple = pl4g::ir::as<pl4g::ir::TupleType>(ty))
          checks.emplace_back("offsets", pl4g::ir::member_offsets_of(tuple, layout) == numbers(recorded["offsets"]));
        for (const auto& [what, same] : checks)
          if (! same) {
            out.println("{}: {} of '{}' differs", path, what, ty->render());
            ++wrong;
          }
      } catch (const pl4g::ir::NoLayout&) {
        out.println("{}: '{}' has no layout here and has one there", path, ty->render());
        ++wrong;
      }
    }
    return wrong;
  }

} // anonymous namespace


int main(int argc, char* argv[])
{
  Arguments args;
  ::argp_parse(&parser, argc, argv, 0, nullptr, &args);
  pl4g::FdWriter out(1);
  int status = 0;
  for (const auto& path : args.files) {
    pl4g::SourceManager sources;
    auto read = sources.read(path);
    if (! read) [[unlikely]] {
      pl4g::write_all(2, "cannot read " + path + "\n");
      status = 1;
      continue;
    }
    std::vector<const pl4g::ir::Type*> types;
    auto module = pl4g::ir::read_json((*read)->text, &types);
    if (! module) [[unlikely]] {
      pl4g::write_all(2, path + ": " + module.error() + "\n");
      status = 1;
      continue;
    }
    if (args.check_layout) {
      if (check_layout(**module, types, (*read)->text, path, out) > 0)
        status = 1;
    } else
      out.write(pl4g::ir::render_module(**module));
    out.flush();
  }
  return status;
}
