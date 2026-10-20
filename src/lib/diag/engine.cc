#include "diag/engine.hh"

#include <algorithm>


namespace pl4g {

  std::string Diagnostic::text() const
  {
    // The catalog's messages are Python format strings with named fields and
    // nothing else: `{name}`, and `{{` and `}}` for a brace.
    std::string out;
    std::string_view message = info->message;
    for (std::size_t at = 0; at < message.size(); ++at) {
      char c = message[at];
      if ((c == '{' || c == '}') && at + 1 < message.size() && message[at + 1] == c) {
        out += c;
        ++at;
        continue;
      }
      if (c != '{') {
        out += c;
        continue;
      }
      auto close = message.find('}', at);
      if (close == std::string_view::npos) [[unlikely]]
        throw InternalError(std::format("diagnostic {} has an unclosed field", info->name));
      auto field = message.substr(at + 1, close - at - 1);
      auto found = std::ranges::find(args, field, &DiagArg::name);
      if (found == args.end()) [[unlikely]]
        throw InternalError(std::format("diagnostic {} is missing the argument '{}'", info->name, field));
      out += found->value;
      at = close;
    }
    return out;
  }


  std::vector<DiagId> Expectation::unmet() const
  {
    std::vector<DiagId> out;
    for (auto id : required)
      if (std::ranges::find(raised, id) == raised.end())
        out.push_back(id);
    std::ranges::sort(out);
    return out;
  }


  bool WarningControl::is_enabled(const DiagInfo& info) const
  {
    if (info.option.empty())
      return info.default_enabled;
    auto it = enabled.find(std::string(info.option));
    return it == enabled.end() ? info.default_enabled : it->second;
  }


  DiagHandle& DiagHandle::note(DiagId id, Span span, std::initializer_list<DiagArg> args)
  {
    auto& made = engine->make(id, span, DiagArgs(args));
    diag->notes.push_back(&made);
    if (diag->reported)
      engine->report_note(made);
    return *this;
  }


  Diagnostic& DiagEngine::make(DiagId id, Span span, DiagArgs args)
  {
    return made.emplace_back(&catalog_entry(id), span, std::move(args));
  }


  bool DiagEngine::absorb(const DiagInfo& info)
  {
    for (auto it = expectations.rbegin(); it != expectations.rend(); ++it) {
      auto& e = **it;
      if (std::ranges::find(e.numbers, info.number) == e.numbers.end())
        continue;
      if (std::ranges::find(e.raised, info.number) == e.raised.end())
        e.raised.push_back(info.number);
      e.saw_error = e.saw_error || info.spoils_the_construct();
      return true;
    }
    return false;
  }


  void DiagEngine::count(const DiagInfo& info)
  {
    if (info.is_error() || (info.severity == Severity::warning && ctl.warnings_are_errors))
      ++error_count;
    else if (info.severity == Severity::warning)
      ++warning_count;
  }


  DiagHandle DiagEngine::emit(DiagId id, Span span, std::initializer_list<DiagArg> args)
  {
    return emit(id, span, DiagArgs(args));
  }


  DiagHandle DiagEngine::emit(DiagId id, Span span, DiagArgs args)
  {
    auto& diag = make(id, span, std::move(args));
    const DiagInfo& info = *diag.info;
    if (absorb(info))
      return { *this, diag };
    if (! because_stack.empty() && info.is_error())
      // Something is compiled because something else asked for it, and the
      // note says what asked: the thing a reader cannot see from where the
      // message points.
      for (const auto& b : because_stack)
        if (! b.family || std::ranges::find(*b.family, id) != b.family->end())
          diag.notes.push_back(&make(b.id, b.span, b.args));
    if (info.severity == Severity::warning && (errors_only || ! ctl.is_enabled(info) || in_system(span)))
      return { *this, diag };
    count(info);
    diag.reported = true;
    if (logger)
      logger(diag);
    sink(diag);
    for (auto note : diag.notes)
      report_note(*note);
    return { *this, diag };
  }


  void DiagEngine::report_note(Diagnostic& note)
  {
    note.reported = true;
    if (logger)
      logger(note);
    sink(note);
  }


  void DiagEngine::replay(const Diagnostic& diag)
  {
    auto& copy = made.emplace_back(diag);
    if (diag.info->severity == Severity::note) {
      report_note(copy);
      return;
    }
    count(*diag.info);
    copy.reported = true;
    if (logger)
      logger(copy);
    sink(copy);
  }


  DiagHandle DiagEngine::internal(std::string_view detail)
  {
    return emit(D::IMPL_INTERNAL_ERROR, invalid_span, { { "detail", detail } });
  }


  Expectation& DiagEngine::expect(std::vector<DiagId> numbers, std::vector<DiagId> required)
  {
    auto& e = expectation_store.emplace_back(std::move(numbers), std::move(required));
    expectations.push_back(&e);
    return e;
  }


  void DiagEngine::resume(Expectation& expectation)
  {
    expectations.push_back(&expectation);
  }


  void DiagEngine::release(Expectation& expectation)
  {
    auto it = std::ranges::find(expectations, &expectation);
    if (it != expectations.end())
      expectations.erase(it);
  }


  std::size_t DiagEngine::because(DiagId id, Span span, std::initializer_list<DiagArg> args,
                                  std::optional<std::vector<DiagId>> family)
  {
    because_stack.emplace_back(id, span, DiagArgs(args), std::move(family));
    return because_stack.size();
  }


  DiagEngine::Diverted DiagEngine::divert(Sink to)
  {
    Diverted kept{ std::move(sink), std::move(logger), error_count, warning_count, errors_only, std::move(expectations),
                   std::make_shared<std::vector<Because>>(std::move(because_stack)) };
    sink = std::move(to);
    error_count = 0;
    warning_count = 0;
    errors_only = false;
    expectations.clear();
    because_stack.clear();
    return kept;
  }


  void DiagEngine::restore(Diverted&& kept)
  {
    sink = std::move(kept.sink);
    logger = std::move(kept.logger);
    error_count = kept.error_count;
    warning_count = kept.warning_count;
    errors_only = kept.errors_only;
    expectations = std::move(kept.expectations);
    because_stack = std::move(*std::static_pointer_cast<std::vector<Because>>(kept.because_stack));
  }


  void DiagEngine::and_no_longer(std::size_t mark)
  {
    because_stack.resize(mark - 1);
  }


  bool DiagEngine::in_system(Span span) const noexcept
  {
    return span.is_valid()
           && std::ranges::any_of(system, [&](auto r) { return r.first <= span.start && span.start < r.second; });
  }


  Severity DiagEngine::effective_severity(const DiagInfo& info) const noexcept
  {
    if (info.severity == Severity::warning && ctl.warnings_are_errors)
      return Severity::error;
    return info.severity;
  }



} // namespace pl4g
