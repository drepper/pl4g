// Parsing the command line.  A port of pypl4g's driver/cli.py.
//
// pypl4g reads its command line with Python's `argparse`, and the two
// compilers accept the same command lines and refuse them with the same
// diagnostics.  So the words are read first the way `argparse` reads them --
// which word is an option, which value goes with which, what is left over --
// and what that reading settled is handed to argp in a form of its own, every
// option spelled `--name=value`, for argp to put into the options.  What
// `argparse` would have exited over is a numbered diagnostic, as pypl4g makes it.
#include "driver/cli.hh"

#include <argp.h>

#include <algorithm>
#include <cerrno>
#include <format>
#include <limits>

#include "diag/ids.hh"
#include "ir/verify.hh"
#include "sema/modules.hh"
#include "util/pyrepr.hh"


namespace pl4g::driver {

  namespace {

    // What an option does with what follows it.
    enum class Takes : std::uint8_t { nothing, value };

    // One option as pypl4g's parser declares it: the spellings, in the order
    // declared, whether it takes a value, and the key argp is given it under.
    struct Spec {
      std::vector<std::string_view> spellings;
      Takes takes;
      int key;
    };

    enum Key : int {
      key_output = 1000,
      key_emit,
      key_target,
      key_mclevel,
      key_conditions,
      key_opt_level,
      key_debug_info,
      key_pie,
      key_no_pie,
      key_shared,
      key_warning,
      key_werror,
      key_color,
      key_diag_format,
      key_module_path,
      key_define,
      key_jobs,
      key_stack_size,
      key_guard_size,
      key_report_log,
      key_test_runner,
      key_incremental,
      key_ignore_system,
      key_verbose,
      key_time_report,
      key_verify,
      key_help,
      key_help_json,
      key_print_targets,
      key_version,
    };

    // In the order pypl4g's parser declares them.
    const std::vector<Spec>& specs()
    {
      static const std::vector<Spec> all{
        { { "-o", "--output" }, Takes::value, key_output },
        { { "--emit" }, Takes::value, key_emit },
        { { "--target" }, Takes::value, key_target },
        { { "--mclevel" }, Takes::value, key_mclevel },
        { { "--conditions" }, Takes::value, key_conditions },
        { { "-O" }, Takes::value, key_opt_level },
        { { "-g" }, Takes::nothing, key_debug_info },
        { { "-fpie" }, Takes::nothing, key_pie },
        { { "-fno-pie" }, Takes::nothing, key_no_pie },
        { { "-shared" }, Takes::nothing, key_shared },
        { { "-W" }, Takes::value, key_warning },
        { { "-Werror", "--Werror" }, Takes::nothing, key_werror },
        { { "--color" }, Takes::value, key_color },
        { { "--diag-format" }, Takes::value, key_diag_format },
        { { "--module-path" }, Takes::value, key_module_path },
        { { "-D" }, Takes::value, key_define },
        { { "-j", "--jobs" }, Takes::value, key_jobs },
        { { "--stack-size" }, Takes::value, key_stack_size },
        { { "--guard-size" }, Takes::value, key_guard_size },
        { { "--report-log" }, Takes::value, key_report_log },
        { { "--test-runner" }, Takes::value, key_test_runner },
        { { "--incremental" }, Takes::nothing, key_incremental },
        { { "--ignore-system" }, Takes::nothing, key_ignore_system },
        { { "-v", "--verbose" }, Takes::nothing, key_verbose },
        { { "--time-report" }, Takes::nothing, key_time_report },
        { { "--verify" }, Takes::nothing, key_verify },
        { { "--help" }, Takes::nothing, key_help },
        { { "--help-json" }, Takes::nothing, key_help_json },
        { { "--print-targets" }, Takes::nothing, key_print_targets },
        { { "--version" }, Takes::nothing, key_version },
      };
      return all;
    }

    const Spec* spelled(std::string_view word)
    {
      for (const auto& one : specs())
        if (std::ranges::find(one.spellings, word) != one.spellings.end())
          return &one;
      return nullptr;
    }

    // What `argparse` calls an action in a message: its spellings joined.
    std::string action_name(const Spec& spec)
    {
      std::string out;
      for (auto one : spec.spellings)
        out += (out.empty() ? "" : "/") + std::string(one);
      return out;
    }


    // One word read as an option: which, as spelled, and the value stuck to
    // it, where one was.
    struct Optional {
      const Spec* spec = nullptr;
      std::string spelling;
      std::optional<std::string> explicit_arg;
      // Stuck on with `=`, rather than after a one-letter option.
      bool with_equals = false;
    };

    // Whether `word` looks like a negative number, which `argparse` takes for
    // a positional while no option looks like one.
    bool negative_number(std::string_view word)
    {
      if (word.size() < 2 || word[0] != '-')
        return false;
      auto rest = word.substr(1);
      auto dot = rest.find('.');
      auto digits = [](std::string_view s) { return std::ranges::all_of(s, [](char c) { return c >= '0' && c <= '9'; }); };
      if (dot == std::string_view::npos)
        return digits(rest);
      return digits(rest.substr(0, dot)) && rest.size() > dot + 1 && digits(rest.substr(dot + 1));
    }

    // `argparse`'s `_parse_optional`: nothing for a positional, an optional
    // with no spec for an option it does not know.
    std::optional<Optional> parse_optional(std::string_view word)
    {
      if (word.empty() || word[0] != '-')
        return std::nullopt;
      if (auto spec = spelled(word))
        return Optional{ spec, std::string(word), std::nullopt, false };
      if (word.size() == 1)
        return std::nullopt;
      if (auto equals = word.find('='); equals != std::string_view::npos)
        if (auto spec = spelled(word.substr(0, equals)))
          return Optional{ spec, std::string(word.substr(0, equals)), std::string(word.substr(equals + 1)), true };
      // A one-letter option with what follows it stuck on; no abbreviation of
      // a long one is taken.
      if (word[1] != '-')
        if (auto spec = spelled(word.substr(0, 2)))
          return Optional{ spec, std::string(word.substr(0, 2)), std::string(word.substr(2)), false };
      if (negative_number(word))
        return std::nullopt;
      if (word.contains(' '))
        return std::nullopt;
      return Optional{ nullptr, std::string(word), std::nullopt, false };
    }


    // What `argparse` would have exited over.
    struct Refused {};


    // What reading the words came to: each option taken, with its value, in
    // the order taken; the sources; and what was left over.
    struct Reading {
      std::vector<std::pair<const Spec*, std::optional<std::string>>> taken;
      std::vector<std::string> inputs;
      std::vector<std::string> extras;
    };


    // Read `words` -- the command's own, after the command -- as `argparse`'s
    // `parse_known_args` reads them for a parser of `specs()` and one
    // positional of any number of sources.
    Reading read(std::span<const std::string> words, DiagEngine& diags)
    {
      // What each word is: an option, a positional, or the `--` after which
      // everything is a positional.
      enum class Is : std::uint8_t { option, positional, rest };
      std::vector<Is> pattern;
      std::vector<std::optional<Optional>> optionals;
      bool rest = false;
      for (const auto& word : words) {
        if (rest) {
          pattern.push_back(Is::positional);
          optionals.emplace_back();
        } else if (word == "--") {
          rest = true;
          pattern.push_back(Is::rest);
          optionals.emplace_back();
        } else if (auto found = parse_optional(word)) {
          pattern.push_back(Is::option);
          optionals.push_back(std::move(found));
        } else {
          pattern.push_back(Is::positional);
          optionals.emplace_back();
        }
      }

      Reading made;
      bool inputs_taken = false;
      // The positionals from `start` up to the next option, `--` left out.
      auto positionals = [&](std::size_t start) {
        std::size_t end = start;
        std::vector<std::string> found;
        while (end < words.size() && pattern[end] != Is::option) {
          if (pattern[end] == Is::positional)
            found.push_back(words[end]);
          ++end;
        }
        return std::pair{ found, end };
      };

      std::size_t at = 0;
      while (at < words.size()) {
        if (pattern[at] != Is::option) {
          auto [found, end] = positionals(at);
          // The one positional takes the first run of them that is not empty;
          // every later one is left over.
          if (! inputs_taken && ! found.empty()) {
            made.inputs = std::move(found);
            inputs_taken = true;
          } else
            for (std::size_t i = at; i < end; ++i)
              if (pattern[i] == Is::positional)
                made.extras.push_back(words[i]);
          at = end;
          continue;
        }
        auto option = *optionals[at];
        if (option.spec == nullptr) {
          made.extras.push_back(words[at]);
          ++at;
          continue;
        }
        // What `argparse`'s `consume_optional` does with it.
        std::vector<std::pair<const Spec*, std::optional<std::string>>> actions;
        std::size_t stop = at + 1;
        while (true) {
          const Spec& spec = *option.spec;
          if (option.explicit_arg) {
            // A one-letter flag with more stuck to it: the rest is more flags.
            if (spec.takes == Takes::nothing && option.spelling[1] != '-' && ! option.explicit_arg->empty()) {
              if (option.with_equals || option.explicit_arg->front() == '-') {
                diags.emit(D::IMPL_CLI_BAD_COMMAND_LINE, invalid_span,
                           { { "detail", std::format("argument {}: ignored explicit argument {}", action_name(spec),
                                                     python_str_repr(*option.explicit_arg)) } });
                throw Refused{};
              }
              actions.emplace_back(&spec, std::nullopt);
              std::string next = std::format("-{}", option.explicit_arg->front());
              if (auto found = spelled(next)) {
                auto more = option.explicit_arg->substr(1);
                Optional made_next{ found, next, std::nullopt, false };
                if (! more.empty() && more.front() == '=') {
                  made_next.explicit_arg = more.substr(1);
                  made_next.with_equals = true;
                } else if (! more.empty())
                  made_next.explicit_arg = more;
                option = std::move(made_next);
                continue;
              }
              made.extras.push_back("-" + *option.explicit_arg);
              break;
            }
            if (spec.takes == Takes::value) {
              actions.emplace_back(&spec, *option.explicit_arg);
              break;
            }
            diags.emit(D::IMPL_CLI_BAD_COMMAND_LINE, invalid_span,
                       { { "detail", std::format("argument {}: ignored explicit argument {}", action_name(spec),
                                                 python_str_repr(*option.explicit_arg)) } });
            throw Refused{};
          }
          if (spec.takes == Takes::nothing) {
            actions.emplace_back(&spec, std::nullopt);
            break;
          }
          // A value: the word after it, which has to be no option and no `--`.
          std::size_t next = at + 1;
          if (next >= words.size() || pattern[next] != Is::positional) {
            diags.emit(D::IMPL_CLI_MISSING_ARGUMENT, invalid_span, { { "option", spec.spellings.front() } });
            throw Refused{};
          }
          actions.emplace_back(&spec, words[next]);
          stop = next + 1;
          break;
        }
        made.taken.insert(made.taken.end(), actions.begin(), actions.end());
        at = stop;
      }
      return made;
    }


    // The command line with the forms `argparse` misreads written out: `-O`
    // is `-O1` and `--color` `--color=yes`, up to a `--`.
    std::vector<std::string> normalised(std::span<const std::string> argv)
    {
      std::vector<std::string> out;
      bool rest = false;
      for (const auto& word : argv) {
        if (rest)
          out.push_back(word);
        else if (word == "--") {
          rest = true;
          out.push_back(word);
        } else if (word == "-O")
          out.emplace_back("-O1");
        else if (word == "--color")
          out.emplace_back("--color=yes");
        else
          out.push_back(word);
      }
      return out;
    }


    // -- what argp is handed ------------------------------------------------

    // The options as argp knows them, one long name each.
    struct ArgpTable {
      std::vector<std::string> names;
      std::vector<::argp_option> options;

      ArgpTable()
      {
        names.reserve(specs().size());
        for (const auto& spec : specs())
          names.emplace_back(std::format("k{}", spec.key));
        for (std::size_t i = 0; i < specs().size(); ++i)
          options.push_back({ names[i].c_str(), specs()[i].key,
                              specs()[i].takes == Takes::value ? "VALUE" : nullptr, 0, nullptr, 0 });
        options.push_back({});
      }
    };

    // Where argp puts what it parsed: the options, as pypl4g's `_take` gathers
    // them before checking any.
    struct Gathered {
      std::vector<std::string> output;
      std::optional<std::string> emit, target, mclevel, conditions, opt_level, color, diag_format, jobs, stack_size,
        guard_size, report_log, test_runner;
      std::vector<std::string> warnings, module_path, defines;
      bool debug_info = false, pie = false, shared = false, werror = false, incremental = false, ignore_system = false,
           verbose = false, time_report = false, verify = false, help = false, help_json = false, targets = false,
           version = false;
    };

    ::error_t gather(int key, char* arg, ::argp_state* state)
    {
      auto& g = *static_cast<Gathered*>(state->input);
      std::string value = arg != nullptr ? arg : "";
      switch (key) {
      case key_output: g.output.push_back(value); return 0;
      case key_emit: g.emit = value; return 0;
      case key_target: g.target = value; return 0;
      case key_mclevel: g.mclevel = value; return 0;
      case key_conditions: g.conditions = value; return 0;
      case key_opt_level: g.opt_level = value; return 0;
      case key_debug_info: g.debug_info = true; return 0;
      case key_pie: g.pie = true; return 0;
      case key_no_pie: g.pie = false; return 0;
      case key_shared: g.shared = true; return 0;
      case key_warning: g.warnings.push_back(value); return 0;
      case key_werror: g.werror = true; return 0;
      case key_color: g.color = value; return 0;
      case key_diag_format: g.diag_format = value; return 0;
      case key_module_path: g.module_path.push_back(value); return 0;
      case key_define: g.defines.push_back(value); return 0;
      case key_jobs: g.jobs = value; return 0;
      case key_stack_size: g.stack_size = value; return 0;
      case key_guard_size: g.guard_size = value; return 0;
      case key_report_log: g.report_log = value; return 0;
      case key_test_runner: g.test_runner = value; return 0;
      case key_incremental: g.incremental = true; return 0;
      case key_ignore_system: g.ignore_system = true; return 0;
      case key_verbose: g.verbose = true; return 0;
      case key_time_report: g.time_report = true; return 0;
      case key_verify: g.verify = true; return 0;
      case key_help: g.help = true; return 0;
      case key_help_json: g.help_json = true; return 0;
      case key_print_targets: g.targets = true; return 0;
      case key_version: g.version = true; return 0;
      default: return ARGP_ERR_UNKNOWN;
      }
    }


    // -- checking what was said ---------------------------------------------

    // The values an option of a choice accepts, for a diagnostic.
    template<typename E, std::size_t N>
    std::string accepted(const std::array<std::pair<std::string_view, E>, N>& choices)
    {
      std::string out;
      for (const auto& [name, _] : choices)
        out += (out.empty() ? "" : ", ") + std::string(name);
      return out;
    }

    template<typename E, std::size_t N>
    E choice(DiagEngine& diags, std::string_view word, std::string_view value,
             const std::array<std::pair<std::string_view, E>, N>& choices, E fallback)
    {
      for (const auto& [name, member] : choices)
        if (name == value)
          return member;
      diags.emit(D::IMPL_CLI_BAD_ARGUMENT, invalid_span,
                 { { "value", value }, { "option", std::format("{}={}", word, value) },
                   { "expected", accepted(choices) } });
      return fallback;
    }

    constexpr std::array<std::pair<std::string_view, EmitKind>, 6> emit_kinds{ {
      { "tokens", EmitKind::tokens }, { "ast", EmitKind::ast }, { "expanded", EmitKind::expanded },
      { "ir", EmitKind::ir }, { "asm", EmitKind::asm_ }, { "elf", EmitKind::elf },
    } };
    constexpr std::array<std::pair<std::string_view, sema::Conditions>, 3> condition_kinds{ {
      { "check", sema::Conditions::check }, { "ignore", sema::Conditions::ignore },
      { "observe", sema::Conditions::observe },
    } };
    constexpr std::array<std::pair<std::string_view, ColourWhen>, 3> colour_kinds{ {
      { "yes", ColourWhen::yes }, { "no", ColourWhen::no }, { "auto", ColourWhen::automatic },
    } };
    constexpr std::array<std::pair<std::string_view, DiagFormat>, 2> diag_formats{ {
      { "text", DiagFormat::text }, { "json", DiagFormat::json },
    } };

    // A number of bytes, which may be written with a letter after it.
    std::int64_t size(DiagEngine& diags, std::string_view word, std::string_view value, std::int64_t fallback)
    {
      std::string text;
      for (char c : value)
        text += char(c >= 'A' && c <= 'Z' ? c - 'A' + 'a' : c);
      auto first = text.find_first_not_of(" \t\n\r\f\v");
      auto last = text.find_last_not_of(" \t\n\r\f\v");
      text = first == std::string::npos ? std::string() : text.substr(first, last - first + 1);
      std::int64_t times = 1;
      if (! text.empty() && (text.back() == 'k' || text.back() == 'm' || text.back() == 'g')) {
        times = text.back() == 'k' ? 1 << 10 : text.back() == 'm' ? 1 << 20 : std::int64_t(1) << 30;
        text.pop_back();
      }
      bool digits = ! text.empty() && std::ranges::all_of(text, [](char c) { return c >= '0' && c <= '9'; });
      if (! digits || text.size() > 15) [[unlikely]] {
        if (digits) {
          // A number Python takes and this does not hold: its own size.
          return std::numeric_limits<std::int64_t>::max();
        }
        diags.emit(D::IMPL_CLI_BAD_SIZE, invalid_span, { { "option", word }, { "given", value } });
        return fallback;
      }
      return std::stoll(text) * times;
    }

    // What `str.isdigit` says of a word of ASCII.
    bool all_digits(std::string_view s)
    {
      return ! s.empty() && std::ranges::all_of(s, [](char c) { return c >= '0' && c <= '9'; });
    }

    // Python's `Path(word).stem` and `with_name`.
    std::filesystem::path with_suffix_of(const std::filesystem::path& first, std::string_view suffix)
    {
      return first.parent_path() / (first.stem().string() + std::string(suffix));
    }

  } // anonymous namespace


  Options parse_command_line(std::span<const std::string> argv, DiagEngine& diags)
  {
    Options options;
    auto words = normalised(argv);
    // A command line whose first word is not a command means `build`.
    if (words.empty() || (words[0] != build_command && words[0] != test_command && words[0] != lsp_command))
      words.insert(words.begin(), std::string(build_command));
    options.command = words[0];

    Reading reading;
    try {
      reading = read(std::span(words).subspan(1), diags);
    } catch (const Refused&) {
      return options;
    }
    for (const auto& word : reading.extras)
      diags.emit(D::IMPL_CLI_UNKNOWN_OPTION, invalid_span, { { "option", word } });

    // What was read, handed to argp spelled its own way.
    static const ArgpTable table;
    std::vector<std::string> canonical{ "pl4g" };
    for (const auto& [spec, value] : reading.taken) {
      std::string one = std::format("--k{}", spec->key);
      if (value)
        one += "=" + *value;
      canonical.push_back(std::move(one));
    }
    std::vector<char*> pointers;
    for (auto& one : canonical)
      pointers.push_back(one.data());
    pointers.push_back(nullptr);
    Gathered g;
    const ::argp parser{ table.options.data(), gather, nullptr, nullptr, nullptr, nullptr, nullptr };
    ::argp_parse(&parser, int(canonical.size()), pointers.data(),
                 ARGP_NO_ERRS | ARGP_NO_HELP | ARGP_NO_EXIT | ARGP_IN_ORDER | ARGP_NO_ARGS, nullptr, &g);

    // pypl4g's `_take`: each value checked, in the order it checks them.
    for (const auto& word : reading.inputs) {
      std::filesystem::path path(word);
      if (path.extension() != source_suffix) {
        diags.emit(D::IMPL_CLI_BAD_SUFFIX, invalid_span, { { "path", word } });
        continue;
      }
      options.inputs.push_back(path);
    }
    if (! g.output.empty()) {
      if (g.output.size() > 1)
        diags.emit(D::IMPL_CLI_DUPLICATE_OUTPUT, invalid_span, { { "previous", g.output.front() } });
      options.output = std::filesystem::path(g.output.back());
    }
    if (g.emit)
      options.emit = choice(diags, "--emit", *g.emit, emit_kinds, options.emit);
    if (g.target)
      options.triple = *g.target;
    if (g.mclevel)
      options.mclevel = *g.mclevel;
    if (g.conditions)
      options.conditions = choice(diags, "--conditions", *g.conditions, condition_kinds, options.conditions);
    if (g.opt_level) {
      if (g.opt_level->size() == 1 && all_digits(*g.opt_level))
        options.opt_level = (*g.opt_level)[0] - '0';
      else
        diags.emit(D::IMPL_CLI_BAD_ARGUMENT, invalid_span,
                   { { "value", *g.opt_level }, { "option", "-O" }, { "expected", "0, 1" } });
    }
    options.debug_info = g.debug_info;
    if (g.pie)
      options.pie = true;
    if (g.shared)
      options.output_kind = ir::Output::shared;
    options.warnings_are_errors = g.werror;
    for (const auto& name : g.warnings)
      if (name == "error")
        options.warnings_are_errors = true;
      else if (name.starts_with("no-"))
        options.warnings[name.substr(3)] = false;
      else
        options.warnings[name] = true;
    if (g.color)
      options.colour = choice(diags, "--color", *g.color, colour_kinds, options.colour);
    if (g.diag_format)
      options.diag_format = choice(diags, "--diag-format", *g.diag_format, diag_formats, options.diag_format);
    for (const auto& given : g.module_path)
      for (auto& one : sema::parse_search_path(given))
        options.module_path.push_back(std::move(one));
    for (const auto& given : g.defines) {
      // `-Dname=value`, and `-Dname` alone for a name whose value is that it
      // was said; the last one written wins.
      auto equals = given.find('=');
      if (equals == std::string::npos)
        options.defines[given] = "true";
      else
        options.defines[given.substr(0, equals)] = given.substr(equals + 1);
    }
    if (g.jobs) {
      if (all_digits(*g.jobs) && g.jobs->find_first_not_of('0') != std::string::npos && g.jobs->size() < 10)
        options.jobs = std::stoi(*g.jobs);
      else
        diags.emit(D::IMPL_CLI_BAD_ARGUMENT, invalid_span,
                   { { "value", *g.jobs }, { "option", "--jobs" }, { "expected", "1, 2, 3, ..." } });
    }
    if (g.stack_size)
      options.stack_size = size(diags, "--stack-size", *g.stack_size, options.stack_size.value_or(1 << 20));
    if (g.guard_size)
      options.guard_size = size(diags, "--guard-size", *g.guard_size, options.guard_size.value_or(1 << 16));
    if (g.report_log)
      options.report_log = std::filesystem::path(*g.report_log);
    options.test_runner = g.test_runner;
    options.incremental = g.incremental;
    options.ignore_system = g.ignore_system;
    if (g.incremental)
      diags.emit(D::IMPL_CLI_NOT_IMPLEMENTED, invalid_span, { { "option", "--incremental" } });
    options.verbose = g.verbose;
    options.time_report = g.time_report;
    options.verify = g.verify || ir::verifying_asked();
    options.show_help = g.help;
    options.show_help_json = g.help_json;
    options.show_targets = g.targets;
    options.show_version = g.version;
    if (options.show_help || options.show_help_json || options.show_version || options.show_targets)
      return options;

    // What the command line left out, and what it left open.
    if (options.command == lsp_command) {
      for (const auto& path : options.inputs)
        diags.emit(D::IMPL_CLI_LSP_TAKES_NO_SOURCE, invalid_span, { { "path", path.generic_string() } });
      return options;
    }
    if (options.inputs.empty()) {
      if (options.command != build_command) {
        diags.emit(D::IMPL_CLI_NO_INPUT);
        return options;
      }
      std::filesystem::path found{ std::string(build_file) };
      std::error_code ignored;
      if (! std::filesystem::is_regular_file(found, ignored)) {
        diags.emit(D::IMPL_CLI_NO_BUILD_FILE, invalid_span, { { "name", build_file } });
        return options;
      }
      options.inputs.push_back(found);
    }
    if (! options.output) {
      std::string_view suffix;
      switch (options.emit) {
      case EmitKind::tokens: suffix = ".tokens"; break;
      case EmitKind::ast: suffix = ".ast"; break;
      case EmitKind::ir: suffix = ".ir"; break;
      case EmitKind::asm_: suffix = ".s"; break;
      case EmitKind::elf: suffix = ""; break;
      case EmitKind::expanded:
        // pypl4g has no suffix for it and stops with a KeyError; the tree
        // after expansion is written as the tree before it would be.
        suffix = ".ast";
        break;
      }
      options.output = with_suffix_of(options.inputs.front(), suffix);
    }
    return options;
  }

} // namespace pl4g::driver
