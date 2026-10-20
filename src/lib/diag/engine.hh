// Emitting diagnostics.
//
// Where a diagnostic is raised supplies only which one it is, the source span
// and the message arguments.  Severity, text and whether it can be turned off
// come from the catalog.
#pragma once

#include <concepts>
#include <cstdint>
#include <deque>
#include <format>
#include <functional>
#include <initializer_list>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "diag/catalog.hh"
#include "source/location.hh"


namespace pl4g {

  // An inconsistency in the implementation's own data.  Thrown rather than
  // emitted, so every internal failure unwinds to the one place that reports it.
  struct InternalError : std::runtime_error {
    using std::runtime_error::runtime_error;
  };


  // One argument of a diagnostic's message, already turned into text.
  struct DiagArg {
    std::string_view name;
    std::string value;

    DiagArg(std::string_view n, std::string_view v) : name(n), value(v) {}
    DiagArg(std::string_view n, const char* v) : name(n), value(v) {}
    DiagArg(std::string_view n, const std::string& v) : name(n), value(v) {}
    template<std::integral I>
    DiagArg(std::string_view n, I v) : name(n), value(std::to_string(v)) {}
  };

  using DiagArgs = std::vector<DiagArg>;


  // One emitted diagnostic and the notes attached to it.
  struct Diagnostic {
    const DiagInfo* info = nullptr;
    Span span;
    DiagArgs args;
    std::vector<Diagnostic*> notes;
    // Whether it was handed to the sink.
    bool reported = false;

    // The message with its arguments put in.
    std::string text() const;
  };


  // A set of diagnostics a construct says it raises.  While one is in force
  // the diagnostics it names are not reported, and it records which of them
  // were raised, so an expectation nothing meets can be reported in its turn.
  struct Expectation {
    std::vector<DiagId> numbers;
    // The ones the construct says *are* raised, rather than merely allowed.
    std::vector<DiagId> required;
    std::vector<DiagId> raised;
    // Whether anything it took in prevents a construct from being compiled.
    bool saw_error = false;

    // The numbers asserted and not raised, in order.
    std::vector<DiagId> unmet() const;
  };


  // Which warnings are enabled, and whether they count as errors.
  struct WarningControl {
    std::unordered_map<std::string, bool> enabled;
    bool warnings_are_errors = false;

    // Whether `info` is to be reported at all.
    bool is_enabled(const DiagInfo& info) const;
  };


  class DiagEngine;

  // What `emit` hands back: a way to attach notes to what was emitted.
  class DiagHandle {
  public:
    DiagHandle(DiagEngine& engine, Diagnostic& diag) noexcept : engine(&engine), diag(&diag) {}

    // Attach a note, reporting it at once if its parent was reported.
    DiagHandle& note(DiagId id, Span span = invalid_span, std::initializer_list<DiagArg> args = {});

    // The diagnostic itself.
    const Diagnostic& get() const noexcept { return *diag; }

  private:
    DiagEngine* engine;
    Diagnostic* diag;
  };


  // Collects diagnostics and hands them to a sink.
  class DiagEngine {
  public:
    using Sink = std::function<void(const Diagnostic&)>;

    explicit DiagEngine(Sink sink, WarningControl control = {}) : sink(std::move(sink)), ctl(std::move(control)) {}
    DiagEngine(const DiagEngine&) = delete;
    DiagEngine& operator=(const DiagEngine&) = delete;

    // Report `id` and return a handle on it, for notes.
    DiagHandle emit(DiagId id, Span span = invalid_span, std::initializer_list<DiagArg> args = {});
    DiagHandle emit(DiagId id, Span span, DiagArgs args);

    // Build a diagnostic without reporting it.
    Diagnostic& make(DiagId id, Span span, DiagArgs args);

    // Hand on a note of a diagnostic already reported.
    void report_note(Diagnostic& note);

    // Report here what another engine caught.
    void replay(const Diagnostic& diag);

    // Report an internal error.
    DiagHandle internal(std::string_view detail);

    // Put a new expectation in force until it is released.
    Expectation& expect(std::vector<DiagId> numbers, std::vector<DiagId> required = {});
    // Put an expectation back in force, keeping what it has taken in.
    void resume(Expectation& expectation);
    // Take an expectation out of force.
    void release(Expectation& expectation);

    // Hang a note on every error raised until `and_no_longer` is given the
    // mark returned; `family`, where given, narrows it to those errors.
    std::size_t because(DiagId id, Span span, std::initializer_list<DiagArg> args = {},
                        std::optional<std::vector<DiagId>> family = std::nullopt);
    void and_no_longer(std::size_t mark);

    // Note that the locations from `start` up to `end` are a system file's.
    void mark_system(Loc start, Loc end) { system.emplace_back(start, end); }
    bool in_system(Span span) const noexcept;

    // The severity as it is shown, with -Werror applied.
    Severity effective_severity(const DiagInfo& info) const noexcept;

    const WarningControl& control() const noexcept { return ctl; }
    void set_control(WarningControl control) { ctl = std::move(control); }

    // What `divert` puts aside: everything that makes an engine this one
    // rather than a fresh one with the same warning control.
    struct Diverted {
      Sink sink;
      Sink logger;
      unsigned error_count = 0;
      unsigned warning_count = 0;
      bool errors_only = false;
      std::vector<Expectation*> expectations;
      // The notes hung on errors, of a type of the engine's own.
      std::shared_ptr<void> because_stack;
    };

    // Hand what is emitted from here on to `sink`, as a fresh engine with
    // this one's control would take it -- no expectations, nothing counted
    // yet -- until `restore` is given what this answered: pypl4g's checker
    // swaps in such an engine where it lowers something quietly.
    Diverted divert(Sink sink);
    void restore(Diverted&& kept);

    // Whether anything was reported that prevents a successful compilation.
    bool failed() const noexcept { return error_count > 0; }

    // Hand every diagnostic reported from here on -- notes as well -- to
    // `log` beside the sink: what the driver writes down for `--report-log`,
    // in the order it happened among the module's own choices.
    void write_into(Sink log) { logger = std::move(log); }

    unsigned error_count = 0;
    unsigned warning_count = 0;
    // Whether warnings are left unsaid for now.
    bool errors_only = false;


  private:
    // Whether an expectation takes the diagnostic instead of its being reported.
    bool absorb(const DiagInfo& info);
    void count(const DiagInfo& info);

    struct Because {
      DiagId id;
      Span span;
      DiagArgs args;
      std::optional<std::vector<DiagId>> family;
    };

    Sink sink;
    Sink logger;
    WarningControl ctl;
    std::deque<Diagnostic> made;
    std::vector<Expectation*> expectations;
    std::deque<Expectation> expectation_store;
    std::vector<Because> because_stack;
    std::vector<std::pair<Loc, Loc>> system;
  };


  // An engine that keeps what it is given in a list, and the list.
  struct CollectingEngine {
    std::vector<const Diagnostic*> collected;
    DiagEngine engine{ [this](const Diagnostic& d) { collected.push_back(&d); } };
  };

} // namespace pl4g


template<>
struct std::formatter<pl4g::Severity> : std::formatter<std::string_view> {
  auto format(pl4g::Severity s, std::format_context& ctx) const
  {
    return std::formatter<std::string_view>::format(pl4g::enum_name(s), ctx);
  }
};
