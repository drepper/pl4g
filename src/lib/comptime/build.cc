#include "comptime/build.hh"

#include <algorithm>
#include <array>

#include "diag/engine.hh"


namespace pl4g::comptime::build {

  namespace {

    // What `-Dname` on its own means, and what counts as saying so.
    constexpr std::array said_so{ std::string_view("true"), std::string_view("yes"), std::string_view("on"),
                                  std::string_view("1") };

    // One field of the object, where it holds text.
    std::string as_text(const Record& record, std::string_view name, const std::string& fallback)
    {
      auto found = record.field(name);
      auto text = found != nullptr ? found->get<std::string>() : nullptr;
      return text != nullptr ? *text : fallback;
    }

    // And one where it holds a number.
    std::int64_t as_number(const Record& record, std::string_view name, std::int64_t fallback)
    {
      auto found = record.field(name);
      auto number = found != nullptr ? found->get<__int128>() : nullptr;
      return number != nullptr ? static_cast<std::int64_t>(*number) : fallback;
    }

    // The argument at `at`, or nothing where the call gave fewer.
    Value argument(std::span<const Value> args, std::size_t at)
    {
      return at < args.size() ? args[at] : Value();
    }

    // Python's `str.strip().lower()` over the ASCII the options are written in.
    std::string folded(std::string_view text)
    {
      auto first = text.find_first_not_of(" \t\n\r\f\v");
      if (first == std::string_view::npos)
        return {};
      text = text.substr(first, text.find_last_not_of(" \t\n\r\f\v") - first + 1);
      std::string out(text);
      std::ranges::transform(out, out.begin(), [](char c) { return c >= 'A' && c <= 'Z' ? char(c - 'A' + 'a') : c; });
      return out;
    }

  } // anonymous namespace


  std::shared_ptr<Record> Plan::record() const
  {
    auto env_table = std::make_shared<Table>(std::nullopt, true);
    for (const auto& [key, value] : env)
      env_table->put(key, value);
    auto made = std::make_shared<Record>();
    made->type_name = type_name;
    made->fields = { { std::string(output_dir_field), output_dir },
                     { std::string(target_field), target },
                     { std::string(opt_level_field), opt_level },
                     { std::string(mclevel_field), mclevel },
                     { std::string(stack_size_field), stack_size },
                     { std::string(guard_size_field), guard_size },
                     { std::string(env_field), Value(std::move(env_table)) } };
    return made;
  }


  void Plan::settle(const Record& record)
  {
    output_dir = as_text(record, output_dir_field, output_dir);
    target = as_text(record, target_field, target);
    mclevel = as_text(record, mclevel_field, mclevel);
    opt_level = as_number(record, opt_level_field, opt_level);
    stack_size = as_number(record, stack_size_field, stack_size);
    guard_size = as_number(record, guard_size_field, guard_size);
  }


  ModuleBuiltins builtins(Plan& plan, const std::unordered_map<std::string, std::string>& given, DiagEngine& diags)
  {
    ModuleBuiltins made;
    // `add_executable(b, name, sources)`: one more thing to build.
    made.emplace("add_executable", Builtin{ "add_executable", [&plan, &diags](Span span, std::span<const Value> args) {
      auto name = to_str(argument(args, 1));
      std::vector<std::string> wanted;
      auto sources = argument(args, 2);
      if (auto run = sources.get<std::shared_ptr<Run>>())
        for (const auto& one : **run)
          wanted.push_back(to_str(one));
      if (wanted.empty()) [[unlikely]] {
        diags.emit(D::LANG_BUILD_NO_SOURCES, span, { { "name", name } });
        return Value();
      }
      plan.artifacts.push_back({ std::move(name), std::move(wanted), span });
      return Value();
    } });
    // `option(b, name, fallback)`: what the command line said, as text.
    made.emplace("option", Builtin{ "option", [&given](Span, std::span<const Value> args) {
      auto found = given.find(to_str(argument(args, 1)));
      return found != given.end() ? Value(found->second) : Value(to_str(argument(args, 2)));
    } });
    // And the same question asked of something that is either so or not.
    made.emplace("option_flag", Builtin{ "option_flag", [&given](Span, std::span<const Value> args) {
      auto found = given.find(to_str(argument(args, 1)));
      if (found == given.end())
        return Value(truthy(argument(args, 2)));
      return Value(std::ranges::find(said_so, folded(found->second)) != said_so.end());
    } });
    return made;
  }


  std::vector<std::filesystem::path> sources_of(const Artifact& artifact, const std::filesystem::path& beside)
  {
    std::vector<std::filesystem::path> out;
    for (const auto& text : artifact.sources) {
      std::filesystem::path one(text);
      out.push_back(one.is_absolute() ? one : beside / one);
    }
    return out;
  }


  std::filesystem::path named(const Artifact& artifact, const Plan& plan)
  {
    return (plan.output_dir.empty() ? std::filesystem::path() : std::filesystem::path(plan.output_dir)) / artifact.name;
  }


  std::vector<std::string> summary(const Plan& plan)
  {
    std::vector<std::string> out;
    for (const auto& artifact : plan.artifacts) {
      std::string line = artifact.name + " (";
      for (std::size_t i = 0; i < artifact.sources.size(); ++i)
        line += (i == 0 ? "" : ", ") + artifact.sources[i];
      out.push_back(line + ")");
    }
    return out;
  }

} // namespace pl4g::comptime::build
