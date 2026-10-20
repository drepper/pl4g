// The compiler.  A port of pypl4g's driver/main.py.
#include "driver/driver.hh"

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstring>
#include <format>
#include <memory>
#include <optional>
#include <unordered_map>
#include <vector>

#include "comptime/build.hh"
#include "comptime/evaluate.hh"
#include "diag/ids.hh"
#include "diag/render.hh"
#include "driver/cli.hh"
#include "driver/compile.hh"
#include "driver/sbom.hh"
#include "elf/const.hh"
#include "elf/writer.hh"
#include "front/expand.hh"
#include "front/lexer.hh"
#include "front/parser.hh"
#include "front/repr.hh"
#include "ir/printer.hh"
#include "ir/verify.hh"
#include "lsp/json.hh"
#include "lsp/server.hh"
#include "mc/dump.hh"
#include "mc/layout.hh"
#include "mc/streamer.hh"
#include "opt/passes.hh"
#include "sema/check-helpers.hh"
#include "sema/modules.hh"
#include "target/registry.hh"
#include "target/target.hh"
#include "util/fdio.hh"
#include "util/pyrepr.hh"


extern char** environ;

namespace pl4g::driver {

  namespace {

    // What the compiler calls itself.
    constexpr std::string_view name = "pl4g";
    constexpr std::string_view version = "0.1";
    // The module a build function's builtins are reached through.
    constexpr std::string_view std_module = "std";
    // How long a test binary is given.
    constexpr auto test_timeout = std::chrono::seconds(120);

    // What `pypl4g test` runs, and what a finished build runs.
    constexpr ir::SpecialKind suite_run[] = { ir::SpecialKind::test_always, ir::SpecialKind::test_suite };
    constexpr ir::SpecialKind after_a_build[] = { ir::SpecialKind::test_build };


    // Everything the compiler said and chose about one run, in the order it
    // happened.  The diagnostics go where `active` points, which is the
    // module's own reports while a module is being made -- so that what it
    // chose and what was said are one list -- and here otherwise.
    struct ReportLog {
      std::vector<ir::Report> entries;
      std::vector<ir::Report>* active = &entries;
    };


    // How long one stage took.
    struct Timing {
      std::string name;
      double seconds = 0;
    };

    using Clock = std::chrono::steady_clock;

    double since(Clock::time_point start)
    {
      return std::chrono::duration<double>(Clock::now() - start).count();
    }


    // What `strerror` says of `errno`, as Python's `exc.strerror` does.
    std::string reason_of(int error)
    {
      return std::strerror(error);
    }


    // Write `data` to the file `path`; nothing, or why not.
    std::optional<std::string> write_file(const std::filesystem::path& path, std::string_view data, bool executable)
    {
      int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
      if (fd < 0) [[unlikely]]
        return reason_of(errno);
      bool written = write_all(fd, data);
      int error = errno;
      if (executable && written)
        ::fchmod(fd, 0755);
      ::close(fd);
      if (! written) [[unlikely]]
        return reason_of(error);
      return std::nullopt;
    }


    // Whether a binary for `triple` is one this machine runs: the
    // architecture and nothing else.
    bool built_for_this_machine(std::string_view triple)
    {
      auto wanted = triple.substr(0, triple.find('-'));
      ::utsname here{};
      if (::uname(&here) != 0) [[unlikely]]
        return false;
      std::string_view machine = here.machine;
      return wanted == machine || (wanted == "x86_64" && machine == "amd64")
             || (wanted == "aarch64" && machine == "arm64");
    }


    // What a process that was run came to: its status, what it wrote to
    // its standard error, and what kept it from running or finishing.
    struct Ran {
      int status = 0;
      std::string stderr_text;
      std::optional<std::string> failure;
    };

    // Python's `repr` of a list of words.
    std::string words_repr(std::span<const std::string> words)
    {
      std::string out = "[";
      for (std::size_t i = 0; i < words.size(); ++i)
        out += (i > 0 ? ", " : "") + python_str_repr(words[i]);
      return out + "]";
    }

    // Run `words`, its standard input and output the compiler's, its errors
    // collected, for at most `test_timeout`.
    Ran run_process(std::span<const std::string> words, const Descriptors& io)
    {
      Ran ran;
      int pipe_fds[2];
      if (::pipe2(pipe_fds, O_CLOEXEC) != 0) [[unlikely]] {
        ran.failure = std::format("[Errno {}] {}", errno, reason_of(errno));
        return ran;
      }
      ::posix_spawn_file_actions_t actions;
      ::posix_spawn_file_actions_init(&actions);
      ::posix_spawn_file_actions_adddup2(&actions, io.input, 0);
      ::posix_spawn_file_actions_adddup2(&actions, io.output, 1);
      ::posix_spawn_file_actions_adddup2(&actions, pipe_fds[1], 2);
      std::vector<char*> argv;
      for (const auto& one : words)
        argv.push_back(const_cast<char*>(one.c_str()));
      argv.push_back(nullptr);
      ::pid_t pid = 0;
      int spawned = ::posix_spawnp(&pid, argv[0], &actions, nullptr, argv.data(), environ);
      ::posix_spawn_file_actions_destroy(&actions);
      ::close(pipe_fds[1]);
      if (spawned != 0) [[unlikely]] {
        ::close(pipe_fds[0]);
        ran.failure = std::format("[Errno {}] {}: {}", spawned, reason_of(spawned), python_str_repr(words[0]));
        return ran;
      }
      auto deadline = Clock::now() + test_timeout;
      bool open = true;
      while (open) {
        auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
        if (left <= 0) {
          ::kill(pid, SIGKILL);
          ::waitpid(pid, nullptr, 0);
          ::close(pipe_fds[0]);
          ran.failure = std::format("Command '{}' timed out after 120.0 seconds", words_repr(words));
          return ran;
        }
        ::pollfd one{ pipe_fds[0], POLLIN, 0 };
        if (::poll(&one, 1, int(std::min<long long>(left, 1000))) > 0) {
          char buffer[4096];
          auto got = ::read(pipe_fds[0], buffer, sizeof buffer);
          if (got > 0)
            ran.stderr_text.append(buffer, std::size_t(got));
          else if (got == 0 || errno != EINTR)
            open = false;
        }
      }
      ::close(pipe_fds[0]);
      int status = 0;
      while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {
      }
      ran.status = WIFEXITED(status) ? WEXITSTATUS(status) : -WTERMSIG(status);
      return ran;
    }


    // -- one compilation ------------------------------------------------------

    class Driver {
    public:
      Driver(Options options, DiagEngine& diags, SourceManager& sources, const Descriptors& io, ReportLog& log,
             std::optional<int> result = std::nullopt, std::span<const ir::SpecialKind> wanted_tests = {})
        : options(std::move(options)), diags(diags), sources(sources), io(io), err(io.error), log(log),
          result(result), wanted_tests(wanted_tests.begin(), wanted_tests.end())
      {
      }

      // Compile, answering the status the process should exit with.
      int run();

      // What the run compiled, for what is asked of it afterwards.
      const ir::Module* made() const noexcept { return module.get(); }
      // Where the checker writes down what each name is, where anything asks.
      sema::Notes* notes = nullptr;
      std::vector<Timing> timings;

      void write_report_log();
      void report_timings();

    private:
      void timed(std::string what, Clock::time_point start)
      {
        timings.push_back({ what, since(start) });
        if (options.verbose)
          err.println("{}: {} done", name, what);
        err.flush();
      }

      std::vector<const ast::SourceUnit*> read_and_parse();
      int emit_frontend(std::span<const ast::SourceUnit* const> units);
      bool analyze(std::span<const ast::SourceUnit* const> units);
      int run_build(std::span<const ast::SourceUnit* const> units);
      int build_artifacts(comptime::build::Plan& plan);
      int generate();
      void emit_sbom(mc::Assembler& asm_);
      int write_text(std::string_view text);
      int write_binary(std::string_view image);
      lsp::Json rendered_report(const ir::Report& report) const;

      Options options;
      DiagEngine& diags;
      SourceManager& sources;
      Descriptors io;
      FdWriter err;
      ReportLog& log;
      std::optional<int> result;
      std::vector<ir::SpecialKind> wanted_tests;
      // What the stages make, kept as long as the module that names it.
      std::unique_ptr<Compilation> compilation;
      std::unique_ptr<sema::ModuleRegistry> registry;
      std::unique_ptr<ir::Module> module;
      std::vector<ir::Report>* log_before = nullptr;
    };


    sema::ModuleRegistry registry_for(const Options& options)
    {
      sema::SearchPath search;
      search.given = options.module_path;
      return sema::ModuleRegistry(std::move(search));
    }


    std::vector<const ast::SourceUnit*> Driver::read_and_parse()
    {
      std::vector<const ast::SourceUnit*> units;
      for (const auto& path : options.inputs) {
        auto start = Clock::now();
        auto read = sources.read(path);
        if (! read) [[unlikely]] {
          if (read.error().kind == ReadFailure::Kind::not_utf8)
            diags.emit(D::LANG_SYNTAX_INVALID_UTF8, invalid_span, { { "offset", read.error().offset } });
          else
            diags.emit(D::IMPL_INPUT_UNREADABLE, invalid_span,
                       { { "path", path.generic_string() }, { "reason", read.error().reason } });
          continue;
        }
        const SourceFile& file = **read;
        auto tokens = tokenize(file, diags, false);
        auto unit = parse(compilation->arena, std::move(tokens), path.generic_string(), diags, false, &file);
        sources.record(path, unit);
        units.push_back(unit);
        timed(std::format("parse {}", path.generic_string()), start);
      }
      return units;
    }


    int Driver::emit_frontend(std::span<const ast::SourceUnit* const> units)
    {
      if (diags.failed())
        return exit_code::errors;
      if (options.emit == EmitKind::tokens) {
        std::string text;
        for (const auto& file : sources.files())
          text += ast::token_lines(tokenize(file, diags, false), sources);
        return write_text(text.empty() ? "\n" : text);
      }
      std::string text;
      for (std::size_t i = 0; i < units.size(); ++i)
        text += (i > 0 ? "\n" : "") + ast::python_repr(units[i], sources);
      return write_text(text + "\n");
    }


    bool Driver::analyze(std::span<const ast::SourceUnit* const> units)
    {
      auto start = Clock::now();
      module = std::make_unique<ir::Module>();
      module->name = options.inputs.empty() ? "<none>" : options.inputs.front().filename().string();
      module->triple = options.triple;
      module->stack_size = options.stack_size.value_or(1 << 20);
      module->guard_size = options.guard_size.value_or(1 << 16);
      // One log for the run, written from both ends: what was said and what
      // the module chooses, in the order it happened.
      module->reports = std::move(*log.active);
      log_before = log.active;
      log.active = &module->reports;
      registry = std::make_unique<sema::ModuleRegistry>(registry_for(options));
      sema::check(*module, units, diags, registry.get(), &sources, notes, options.conditions, false,
                  options.ignore_system);
      // Before anything is dropped: which tests this binary runs is what
      // decides which of them are reachable at all.
      if (! wanted_tests.empty())
        for (auto one : module->tests)
          if (one->attrs.special && std::ranges::contains(wanted_tests, *one->attrs.special))
            module->test_plan.push_back(one);
      timed("semantic analysis", start);
      if (diags.failed())
        return true;
      if (options.verify)
        ir::verify(*module);
      start = Clock::now();
      std::vector<opt::PassTiming> passes;
      opt::run_pipeline(*module, options.opt_level, options.verify, &passes);
      for (const auto& one : passes)
        timings.push_back({ std::format("pass {}", one.name), one.seconds });
      timed("optimization", start);
      return true;
    }


    int Driver::run()
    {
      compilation = std::make_unique<Compilation>(options, diags, sources);
      // The log goes back to where it was, whichever way the run ends.
      struct PutBack {
        Driver& d;
        ~PutBack()
        {
          if (d.log_before != nullptr && d.module != nullptr) {
            *d.log_before = std::move(d.module->reports);
            d.log.active = d.log_before;
            d.log_before = nullptr;
          }
        }
      } put_back{ *this };

      auto units = read_and_parse();
      if (options.emit == EmitKind::tokens || options.emit == EmitKind::ast)
        return emit_frontend(units);
      // Between parsing and every check: what the checker sees has no macro
      // left in it.
      auto start = Clock::now();
      auto before = diags.error_count;
      auto expanded = compilation->expand(units);
      timed("macro expansion", start);
      if (options.emit == EmitKind::expanded)
        return emit_frontend(expanded);
      if (diags.error_count > before)
        // Nothing is checked after an expansion that failed.
        return exit_code::errors;
      if (! analyze(expanded) || diags.failed())
        return exit_code::errors;
      if (module->build != nullptr && ! options.from_build)
        // What was named describes a build rather than being one.
        return run_build(expanded);
      if (options.emit == EmitKind::ir)
        return write_text(ir::render_module(*module));
      return generate();
    }


    // The function marked `@[build]`, where the program has one.
    const ast::FuncDef* build_function(std::span<const ast::SourceUnit* const> units)
    {
      for (auto unit : units)
        for (auto item : unit->items)
          if (auto f = ast::as<ast::FuncDef>(item))
            if (std::ranges::any_of(f->attrs, [](const ast::Attribute* a) { return a->name == "build"; }))
              return f;
      return nullptr;
    }


    int Driver::run_build(std::span<const ast::SourceUnit* const> units)
    {
      auto start = Clock::now();
      auto found = build_function(units);
      if (found == nullptr) [[unlikely]] {
        diags.internal("the build function is not in the syntax tree");
        return exit_code::internal;
      }
      // What the compiler was run with, read under two names that answer
      // with the one dictionary: a copy, so that nothing a build does could
      // change what the compiler sees.
      std::vector<std::pair<std::string, std::string>> settled;
      for (char** e = environ; *e != nullptr; ++e) {
        std::string_view one = *e;
        auto equals = one.find('=');
        if (equals != std::string_view::npos)
          settled.emplace_back(std::string(one.substr(0, equals)), std::string(one.substr(equals + 1)));
      }
      comptime::build::Plan plan;
      plan.output_dir = "";
      plan.target = options.triple;
      plan.opt_level = options.opt_level;
      plan.mclevel = options.mclevel.value_or("");
      plan.stack_size = options.stack_size.value_or(1 << 20);
      plan.guard_size = options.guard_size.value_or(1 << 16);
      plan.env = settled;
      auto record = plan.record();
      std::unordered_map<std::string, std::string> defines;
      for (const auto& [key, value] : options.defines)
        defines[key] = value;
      comptime::Builtins builtins;
      builtins[std::string(std_module)] = comptime::build::builtins(plan, defines, diags);
      comptime::Evaluator evaluator(units, std::move(builtins), settled);
      try {
        auto cell = std::make_shared<comptime::Cell>(comptime::Cell{ comptime::Value(record) });
        std::vector<comptime::Value> arguments{ comptime::Value(comptime::Reference{ cell, true }) };
        evaluator.call(*found, arguments);
      } catch (const comptime::CannotEvaluate& error) {
        if (error.ran_out)
          diags.emit(D::LANG_COMPTIME_ENDLESS, error.span, { { "detail", error.detail } });
        else
          diags.emit(D::LANG_COMPTIME_CANNOT, error.span, { { "construct", error.detail } });
        return exit_code::errors;
      }
      plan.settle(*record);
      timed("the build function", start);
      if (diags.failed())
        return exit_code::errors;
      if (plan.artifacts.empty()) {
        diags.emit(D::LANG_BUILD_NOTHING);
        return exit_code::success;
      }
      return build_artifacts(plan);
    }


    int Driver::build_artifacts(comptime::build::Plan& plan)
    {
      std::error_code ignored;
      auto beside = std::filesystem::weakly_canonical(std::filesystem::absolute(options.inputs.front()), ignored)
                      .parent_path();
      int status = exit_code::success;
      for (const auto& artifact : plan.artifacts) {
        auto output = comptime::build::named(artifact, plan);
        Options given = options;
        given.inputs = comptime::build::sources_of(artifact, beside);
        given.output = output;
        given.triple = plan.target;
        given.opt_level = int(plan.opt_level);
        given.mclevel = plan.mclevel.empty() ? std::nullopt : std::optional(plan.mclevel);
        given.stack_size = plan.stack_size;
        given.guard_size = plan.guard_size;
        given.from_build = true;
        if (! output.parent_path().empty()) {
          std::error_code error;
          std::filesystem::create_directories(output.parent_path(), error);
          if (error) [[unlikely]] {
            diags.emit(D::IMPL_OUTPUT_UNWRITABLE, invalid_span,
                       { { "path", output.generic_string() }, { "reason", reason_of(error.value()) } });
            return exit_code::errors;
          }
        }
        if (options.verbose) {
          err.println("{}: building {}", name, artifact.name);
          err.flush();
        }
        Driver made(given, diags, sources, io, log);
        int answered = made.run();
        timings.insert(timings.end(), made.timings.begin(), made.timings.end());
        if (answered != exit_code::success)
          status = answered;
      }
      return status;
    }


    int Driver::generate()
    {
      auto target = target::lookup_target(options.triple);
      if (target == nullptr) [[unlikely]] {
        diags.emit(D::IMPL_CLI_UNKNOWN_TARGET, invalid_span, { { "triple", options.triple } });
        return exit_code::errors;
      }
      if (options.mclevel) {
        // What may be asked for is each architecture's own business.
        if (! target->has_mclevels()) {
          diags.emit(D::IMPL_CLI_NO_MCLEVELS, invalid_span, { { "triple", options.triple } });
          return exit_code::errors;
        }
        try {
          target->use_mclevel(*options.mclevel);
        } catch (const std::invalid_argument& error) {
          diags.emit(D::IMPL_CLI_UNKNOWN_MCLEVEL, invalid_span,
                     { { "level", *options.mclevel }, { "triple", options.triple }, { "detail", error.what() } });
          return exit_code::errors;
        }
      }
      target->position_independent(options.pie);
      auto start = Clock::now();
      mc::MCStreamer streamer(target->encoder());
      auto asm_ = target->new_assembler(streamer, options.opt_level);
      try {
        target->generate(*module, *asm_, diags, options.opt_level, &sources);
      } catch (const mc::RegisterAssignmentError& error) {
        diags.internal(error.what());
        return exit_code::errors;
      }
      timed("code generation", start);
      if (diags.failed())
        return exit_code::errors;
      {
        // A displacement that does not reach is grown before anything is laid
        // out for good -- the dump below included.
        auto sections = streamer.section_list();
        auto symbols = streamer.symbol_list();
        target->relax(sections, symbols);
        if (options.emit == EmitKind::asm_) {
          for (auto section : sections) {
            mc::layout_section(*section);
            mc::resolve_symbol_offsets(*section, symbols);
          }
          return write_text(mc::dump_sections(sections, symbols));
        }
      }
      emit_sbom(*asm_);
      start = Clock::now();
      auto defaults = target->image_defaults();
      elf::ImageSettings settings;
      settings.machine = defaults.machine;
      settings.base_vaddr = options.pie ? 0 : defaults.base_vaddr;
      settings.page_size = defaults.page_size;
      settings.entry_symbol = std::string(target->entry_symbol());
      settings.header_flags = defaults.header_flags;
      settings.kind = options.pie ? elf::ImageKind::pie : elf::ImageKind::executable;
      settings.stack_size = std::uint64_t(options.stack_size.value_or(1 << 20));
      auto sections = streamer.section_list();
      auto symbols = streamer.symbol_list();
      std::vector<std::string> paths(module->source_paths.begin(), module->source_paths.end());
      try {
        if (result) {
          elf::write_image_to(*result, settings, sections, symbols, paths, target->fixup_applier());
          timed("image generation", start);
          return exit_code::success;
        }
        auto [image, layout] = elf::write_image(settings, sections, symbols, paths, target->fixup_applier());
        timed("image generation", start);
        return write_binary(std::string_view(reinterpret_cast<const char*>(image.data()), image.size()));
      } catch (const elf::ImageError& error) {
        if (error.symbol)
          diags.emit(D::IMPL_IMAGE_UNDEFINED_SYMBOL, invalid_span, { { "name", *error.symbol } });
        else if (error.out_of_range)
          diags.emit(D::IMPL_IMAGE_VALUE_OUT_OF_RANGE, invalid_span,
                     { { "value", error.out_of_range->first }, { "field", error.out_of_range->second } });
        else
          diags.internal(error.detail);
        return exit_code::errors;
      }
    }


    void Driver::emit_sbom(mc::Assembler& asm_)
    {
      // The compiler's row names what an image is to be compared with, which
      // the environment may say: the comparisons with pypl4g's images need
      // pypl4g's own.
      auto said = std::getenv("PL4G_SBOM_COMPILER");
      std::string compiler = said != nullptr ? said : std::format("{} {}", name, version);
      auto table = sbom::build(sbom::entries_for(sources, compiler), true);
      mc::SectionOptions rows;
      rows.alloc = true;
      rows.alignment = 4;
      rows.sh_type = elf::sht_progbits;
      rows.sh_link_to = std::string(sbom::strings);
      rows.sh_entsize = sbom::row_size;
      asm_.section(std::string(sbom::section), rows);
      asm_.bytes(mc::Bytes(table.rows.begin(), table.rows.end()));
      mc::SectionOptions strings;
      strings.alloc = true;
      strings.alignment = 1;
      strings.sh_type = elf::sht_strtab;
      asm_.section(std::string(sbom::strings), strings);
      asm_.bytes(mc::Bytes(table.strings.begin(), table.strings.end()));
    }


    int Driver::write_text(std::string_view text)
    {
      if (result) {
        if (! write_all(*result, text)) [[unlikely]] {
          diags.emit(D::IMPL_OUTPUT_UNWRITABLE, invalid_span,
                     { { "path", std::format("descriptor {}", *result) }, { "reason", reason_of(errno) } });
          return exit_code::errors;
        }
        return exit_code::success;
      }
      if (auto failed = write_file(*options.output, text, false)) [[unlikely]] {
        diags.emit(D::IMPL_OUTPUT_UNWRITABLE, invalid_span,
                   { { "path", options.output->generic_string() }, { "reason", *failed } });
        return exit_code::errors;
      }
      return exit_code::success;
    }


    int Driver::write_binary(std::string_view image)
    {
      if (auto failed = write_file(*options.output, image, true)) [[unlikely]] {
        diags.emit(D::IMPL_OUTPUT_UNWRITABLE, invalid_span,
                   { { "path", options.output->generic_string() }, { "reason", *failed } });
        return exit_code::errors;
      }
      return exit_code::success;
    }


    lsp::Json Driver::rendered_report(const ir::Report& report) const
    {
      std::string_view kind_class = report.kind == "fatal" || report.kind == "error" ? "error"
                                    : report.kind == "warning"                       ? "warning"
                                                                                     : "note";
      auto entry = lsp::Json::object({ { "class", kind_class },
                                       { "kind", report.kind },
                                       { "subject", report.subject },
                                       { "reason", report.reason } });
      if (report.number)
        entry.set("number", std::int64_t(*report.number));
      auto position = report.span.is_valid() ? sources.position(report.span.start) : std::nullopt;
      if (position)
        entry.set("where", lsp::Json::object({ { "file", std::string(position->path) },
                                               { "line", std::int64_t(position->line) },
                                               { "column", std::int64_t(position->column) } }));
      return entry;
    }


    void Driver::write_report_log()
    {
      if (! options.report_log)
        return;
      lsp::Json::Array inputs;
      for (const auto& path : options.inputs)
        inputs.emplace_back(path.generic_string());
      lsp::Json::Array reports;
      for (const auto& one : *log.active)
        reports.push_back(rendered_report(one));
      std::error_code ignored;
      auto document = lsp::Json::object({ { "format_version", 4 },
                                          { "compiler", std::format("{} {}", name, version) },
                                          { "directory", std::filesystem::current_path(ignored).generic_string() },
                                          { "inputs", lsp::Json(std::move(inputs)) },
                                          { "reports", lsp::Json(std::move(reports)) } });
      write_file(*options.report_log, document.dump(2) + "\n", false);
    }


    void Driver::report_timings()
    {
      if (! options.time_report)
        return;
      err.println("stage timings:");
      for (const auto& one : timings)
        err.println("  {:8.3f} ms  {}", one.seconds * 1000, one.name);
      err.flush();
    }


    // Whether a run of `kind` passed the tests the binary at `binary` runs.
    bool ran_the_tests(const Options& options, DiagEngine& diags, const std::filesystem::path& binary,
                       std::string_view kind, const Descriptors& io)
    {
      std::vector<std::string> words;
      if (options.test_runner) {
        std::string_view runner = *options.test_runner;
        std::size_t at = 0;
        while (at < runner.size()) {
          auto begin = runner.find_first_not_of(" \t\n\r\f\v", at);
          if (begin == std::string_view::npos)
            break;
          auto end = runner.find_first_of(" \t\n\r\f\v", begin);
          words.emplace_back(runner.substr(begin, end == std::string_view::npos ? std::string_view::npos : end - begin));
          at = end == std::string_view::npos ? runner.size() : end;
        }
        words.push_back(binary.string());
      } else if (built_for_this_machine(options.triple))
        words.push_back(binary.string());
      else {
        diags.emit(D::IMPL_TESTS_NOT_RUN, invalid_span, { { "kind", kind }, { "triple", options.triple } });
        return true;
      }
      auto ran = run_process(words, io);
      if (ran.failure) {
        diags.emit(D::IMPL_TESTS_NOT_RUN, invalid_span, { { "kind", kind }, { "triple", *ran.failure } });
        return true;
      }
      if (! ran.stderr_text.empty())
        write_all(io.error, ran.stderr_text);
      if (ran.status == 0)
        return true;
      diags.emit(D::IMPL_TESTS_FAILED, invalid_span, { { "kind", kind } });
      return false;
    }


    // Build a binary that runs `wanted` and run it, from the sources over again.
    int test_binary(const Options& options, DiagEngine& diags, SourceManager& sources, ReportLog& log,
                    const Descriptors& io, std::span<const ir::SpecialKind> wanted, std::string_view kind)
    {
      char pattern[] = "/tmp/pl4g-tests-XXXXXX";
      auto room = ::mkdtemp(pattern);
      if (room == nullptr) [[unlikely]] {
        diags.internal(std::format("no directory for the tests: {}", reason_of(errno)));
        return exit_code::internal;
      }
      std::filesystem::path dir = room;
      struct Remove {
        std::filesystem::path dir;
        ~Remove()
        {
          std::error_code ignored;
          std::filesystem::remove_all(dir, ignored);
        }
      } remove{ dir };
      Options where = options;
      where.output = dir / "tests";
      where.emit = EmitKind::elf;
      where.report_log.reset();
      Driver driver(where, diags, sources, io, log, std::nullopt, wanted);
      int status = driver.run();
      if (status != exit_code::success || diags.failed())
        return status;
      if (driver.made() != nullptr && driver.made()->test_plan.empty())
        return exit_code::success;
      if (! ran_the_tests(where, diags, *where.output, kind, io))
        return exit_code::errors;
      return exit_code::success;
    }


    // The warning settings, every name that is not a warning reported.
    WarningControl warning_control(const Options& options, DiagEngine& diags)
    {
      WarningControl control;
      control.warnings_are_errors = options.warnings_are_errors;
      for (const auto& [warning, enabled] : options.warnings) {
        if (catalog_by_option(warning) == nullptr) {
          diags.emit(D::IMPL_CLI_UNKNOWN_WARNING, invalid_span,
                     { { "name", warning }, { "option", std::format("-W{}{}", enabled ? "" : "no-", warning) } });
          continue;
        }
        control.enabled[warning] = enabled;
      }
      return control;
    }


    // What `run_command_line` and `compile` share: everything after the
    // command line is read.
    int compile_with(const Options& options, std::optional<int> result, const Descriptors& io, SourceManager& sources,
                     TextRenderer& renderer, std::unique_ptr<DiagEngine> engine, ReportLog& log)
    {
      engine->set_control(warning_control(options, *engine));
      std::unique_ptr<JsonRenderer> json;
      auto logger = [&log, &engine](const Diagnostic& d) {
        log.active->push_back({ std::format("{}", engine->effective_severity(*d.info)), std::string(d.info->name),
                                d.text(), d.span, unsigned(d.info->number) });
      };
      if (options.diag_format == DiagFormat::json) {
        json = std::make_unique<JsonRenderer>(sources, io.error);
        auto control = engine->control();
        engine = std::make_unique<DiagEngine>([&json](const Diagnostic& d) { (*json)(d); }, control);
        engine->write_into(logger);
      }
      DiagEngine& diags = *engine;
      renderer.use(diags);
      auto finish = [&] {
        if (json != nullptr)
          json->finish();
      };
      if (diags.failed())
        return exit_code::usage;
      if (! target::is_known_triple(options.triple)) {
        diags.emit(D::IMPL_CLI_UNKNOWN_TARGET, invalid_span, { { "triple", options.triple } });
        FdWriter err(io.error);
        std::string known;
        for (auto one : target::known_triples())
          known += (known.empty() ? "" : ", ") + std::string(one);
        err.println("known targets: {}", known);
        err.flush();
        finish();
        return exit_code::usage;
      }
      if (options.command == lsp_command)
        // Nothing of the command line is compiled: what the server compiles is
        // what an editor sends it.
        return lsp::serve(io.input, io.output);
      Driver driver(options, diags, sources, io, log, result);
      int status;
      try {
        if (options.command == test_command)
          status = test_binary(options, diags, sources, log, io, suite_run, "suite");
        else {
          status = driver.run();
          if (status == exit_code::success && ! diags.failed() && options.emit == EmitKind::elf
              && driver.made() != nullptr
              && std::ranges::any_of(driver.made()->tests, [](const ir::Function* one) {
                   return one->attrs.special == ir::SpecialKind::test_build;
                 }))
            status = test_binary(options, diags, sources, log, io, after_a_build, "build");
        }
      } catch (const InternalError& error) {
        diags.internal(error.what());
        status = exit_code::internal;
      }
      driver.write_report_log();
      driver.report_timings();
      finish();
      if (diags.failed() && status == exit_code::success)
        return exit_code::errors;
      return status;
    }


    // An engine rendering to `renderer` and logging into `log`.
    std::unique_ptr<DiagEngine> engine_for(TextRenderer& renderer, ReportLog& log)
    {
      auto engine = std::make_unique<DiagEngine>([&renderer](const Diagnostic& d) { renderer(d); });
      auto raw = engine.get();
      engine->write_into([&log, raw](const Diagnostic& d) {
        log.active->push_back({ std::format("{}", raw->effective_severity(*d.info)), std::string(d.info->name),
                                d.text(), d.span, unsigned(d.info->number) });
      });
      renderer.use(*engine);
      return engine;
    }

  } // anonymous namespace


  int compile(const Options& options, int result, const Descriptors& io)
  {
    SourceManager sources;
    TextRenderer renderer(sources, io.error);
    renderer.recolour(options.colour, io.output);
    ReportLog log;
    auto engine = engine_for(renderer, log);
    return compile_with(options, result, io, sources, renderer, std::move(engine), log);
  }


  int compile_whole(const Options& options, DiagEngine& diags, SourceManager& sources, sema::Notes* notes,
                    const Descriptors& io, std::shared_ptr<void>* kept)
  {
    ReportLog log;
    auto raw = &diags;
    diags.write_into([&log, raw](const Diagnostic& d) {
      log.active->push_back({ std::format("{}", raw->effective_severity(*d.info)), std::string(d.info->name),
                              d.text(), d.span, unsigned(d.info->number) });
    });
    // The image is wanted for what making it says, not for itself.
    int nowhere = ::open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (nowhere < 0) [[unlikely]]
      return exit_code::errors;
    auto driver = std::make_shared<Driver>(options, diags, sources, io, log, nowhere);
    driver->notes = notes;
    int status = driver->run();
    ::close(nowhere);
    // The trees the run read are its own, and whoever reads them afterwards
    // keeps them.
    if (kept != nullptr)
      *kept = std::move(driver);
    return status;
  }


  int run_command_line(std::span<const std::string> argv, const Descriptors& io)
  {
    SourceManager sources;
    TextRenderer renderer(sources, io.error);
    // One log for the run, written into from both ends.
    ReportLog log;
    auto engine = engine_for(renderer, log);
    auto options = parse_command_line(argv, *engine);
    // Now that the command line has been read: the diagnostics a bad one
    // makes are made by the renderer built above.
    renderer.recolour(options.colour, io.output);
    FdWriter out(io.output);
    if (options.show_version) {
      out.println("{} {}", name, version);
      out.flush();
      return exit_code::success;
    }
    if (options.show_help) {
      out.print("{}", render_help());
      out.flush();
      return exit_code::success;
    }
    if (options.show_targets) {
      for (auto triple : target::canonical_triples())
        out.println("{}", triple);
      out.flush();
      return exit_code::success;
    }
    if (options.show_help_json) {
      auto table = lsp::Json::parse(option_table());
      out.println("{}", table ? table->dump(2) : std::string());
      out.flush();
      return exit_code::success;
    }
    if (engine->failed()) {
      FdWriter err(io.error);
      err.println("try '{} --help' for the accepted options", name);
      err.flush();
      return exit_code::usage;
    }
    return compile_with(options, std::nullopt, io, sources, renderer, std::move(engine), log);
  }

} // namespace pl4g::driver
