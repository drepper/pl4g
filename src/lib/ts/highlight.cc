// The tree-sitter service.  A port of pypl4g's diag/highlight.py, with the
// folds beside it.
#include "ts/highlight.hh"

#include <tree_sitter/api.h>

#include <algorithm>
#include <optional>
#include <span>


extern "C" const ::TSLanguage* tree_sitter_pl4g();


namespace pl4g::ts {

  namespace {

    // The queries, as tree-sitter-pl4g has them.
    constexpr unsigned char highlights_query[] = {
#embed "highlights.scm"
    };
    constexpr unsigned char folds_query[] = {
#embed "folds.scm"
    };

    std::string_view as_text(std::span<const unsigned char> bytes)
    {
      return { reinterpret_cast<const char*>(bytes.data()), bytes.size() };
    }


    // What `#match?` asks, for the patterns the grammar's queries write: text,
    // with `^` and `$` to tie it to either end and `\` before a character
    // that stands for itself.  A pattern asking for more matches nothing --
    // it would be a query this service does not read, and the cost is colour.
    class Pattern {
    public:
      explicit Pattern(std::string_view written)
      {
        if (written.starts_with('^')) {
          at_start = true;
          written.remove_prefix(1);
        }
        if (written.ends_with('$') && ! written.ends_with("\\$")) {
          at_end = true;
          written.remove_suffix(1);
        }
        for (std::size_t i = 0; i < written.size(); ++i) {
          char c = written[i];
          if (c == '\\' && i + 1 < written.size())
            c = written[++i];
          else if (std::string_view(".*+?[]()|{}^$").contains(c)) {
            understood = false;
            return;
          }
          literal += c;
        }
      }

      bool found_in(std::string_view text) const
      {
        if (! understood)
          return false;
        if (at_start && at_end)
          return text == literal;
        if (at_start)
          return text.starts_with(literal);
        if (at_end)
          return text.ends_with(literal);
        return text.contains(literal);
      }

    private:
      std::string literal;
      bool at_start = false;
      bool at_end = false;
      bool understood = true;
    };


    // One query and what its patterns ask of the text besides their shape:
    // `#match?` and `#eq?`, which the core of tree-sitter leaves to whoever
    // runs the query.
    class Query {
    public:
      Query(const ::TSLanguage* language, std::string_view source)
      {
        std::uint32_t at = 0;
        ::TSQueryError error = ::TSQueryErrorNone;
        query = ::ts_query_new(language, source.data(), std::uint32_t(source.size()), &at, &error);
        if (query == nullptr) [[unlikely]]
          return;
        for (std::uint32_t pattern = 0; pattern < ::ts_query_pattern_count(query); ++pattern) {
          std::uint32_t count = 0;
          auto steps = ::ts_query_predicates_for_pattern(query, pattern, &count);
          std::vector<Test> tests;
          std::vector<const ::TSQueryPredicateStep*> one;
          for (std::uint32_t i = 0; i < count; ++i) {
            if (steps[i].type == ::TSQueryPredicateStepTypeDone) {
              if (auto made = test_of(one))
                tests.push_back(std::move(*made));
              one.clear();
            } else
              one.push_back(&steps[i]);
          }
          predicates.push_back(std::move(tests));
        }
      }
      Query(const Query&) = delete;
      Query& operator=(const Query&) = delete;
      ~Query()
      {
        if (query != nullptr)
          ::ts_query_delete(query);
      }

      ::TSQuery* get() const noexcept { return query; }

      // Whether a match of pattern `pattern` passes what the pattern tests.
      bool passes(const ::TSQueryMatch& match, std::string_view text) const
      {
        for (const auto& test : predicates[match.pattern_index]) {
          std::string_view captured;
          for (std::uint16_t i = 0; i < match.capture_count; ++i)
            if (match.captures[i].index == test.capture) {
              auto node = match.captures[i].node;
              captured = text.substr(::ts_node_start_byte(node), ::ts_node_end_byte(node) - ::ts_node_start_byte(node));
            }
          bool holds = test.pattern ? test.pattern->found_in(captured) : captured == test.value;
          if (holds == test.negated)
            return false;
        }
        return true;
      }

    private:
      struct Test {
        std::uint32_t capture = 0;
        std::string value;
        std::optional<Pattern> pattern;
        bool negated = false;
      };

      std::optional<Test> test_of(std::span<const ::TSQueryPredicateStep* const> steps) const
      {
        if (steps.size() != 3 || steps[0]->type != ::TSQueryPredicateStepTypeString
            || steps[1]->type != ::TSQueryPredicateStepTypeCapture
            || steps[2]->type != ::TSQueryPredicateStepTypeString)
          return std::nullopt;
        std::uint32_t length = 0;
        const char* said = ::ts_query_string_value_for_id(query, steps[0]->value_id, &length);
        std::string_view name(said, length);
        const char* given = ::ts_query_string_value_for_id(query, steps[2]->value_id, &length);
        std::string_view value(given, length);
        Test made;
        made.capture = steps[1]->value_id;
        made.value = std::string(value);
        if (name == "match?" || name == "not-match?") {
          made.pattern.emplace(made.value);
          made.negated = name == "not-match?";
        } else if (name == "eq?" || name == "not-eq?")
          made.negated = name == "not-eq?";
        else
          return std::nullopt;
        return made;
      }

      ::TSQuery* query = nullptr;
      std::vector<std::vector<Test>> predicates;
    };

  } // anonymous namespace


  struct Highlighter::Grammar {
    ::TSParser* parser = ::ts_parser_new();
    Query highlights{ tree_sitter_pl4g(), as_text(highlights_query) };
    Query folding{ tree_sitter_pl4g(), as_text(folds_query) };
    // What was found of the text asked about last, by where it is and how
    // long, kept with a copy so that another text in its place is not taken
    // for it.
    std::string cached_text;
    const char* cached_at = nullptr;
    std::vector<Run> cached;

    Grammar() { ::ts_parser_set_language(parser, tree_sitter_pl4g()); }
    ~Grammar() { ::ts_parser_delete(parser); }

    ::TSTree* parse(std::string_view text)
    {
      return ::ts_parser_parse_string(parser, nullptr, text.data(), std::uint32_t(text.size()));
    }
  };


  Highlighter::Highlighter() : grammar(std::make_unique<Grammar>()) {}

  Highlighter::~Highlighter() = default;


  const std::vector<Run>& Highlighter::runs(std::string_view text)
  {
    auto& g = *grammar;
    if (g.cached_at == text.data() && g.cached_text == text)
      return g.cached;
    g.cached.clear();
    g.cached_text = std::string(text);
    g.cached_at = text.data();
    if (g.highlights.get() == nullptr) [[unlikely]]
      return g.cached;
    ::TSTree* tree = g.parse(text);
    if (tree == nullptr) [[unlikely]]
      return g.cached;
    struct Found {
      std::size_t begin;
      std::size_t end;
      std::uint32_t pattern;
      std::string name;
    };
    std::vector<Found> found;
    ::TSQueryCursor* cursor = ::ts_query_cursor_new();
    ::ts_query_cursor_exec(cursor, g.highlights.get(), ::ts_tree_root_node(tree));
    ::TSQueryMatch match;
    while (::ts_query_cursor_next_match(cursor, &match)) {
      if (! g.highlights.passes(match, text))
        continue;
      for (std::uint16_t i = 0; i < match.capture_count; ++i) {
        std::uint32_t length = 0;
        const char* name = ::ts_query_capture_name_for_id(g.highlights.get(), match.captures[i].index, &length);
        auto node = match.captures[i].node;
        found.push_back({ ::ts_node_start_byte(node), ::ts_node_end_byte(node), match.pattern_index,
                          std::string(name, length) });
      }
    }
    ::ts_query_cursor_delete(cursor);
    ::ts_tree_delete(tree);
    std::ranges::stable_sort(found, [](const Found& a, const Found& b) {
      if (a.begin != b.begin)
        return a.begin < b.begin;
      if (a.end != b.end)
        return a.end > b.end;
      return a.pattern > b.pattern;
    });
    for (auto& one : found)
      g.cached.push_back({ one.begin, one.end, std::move(one.name) });
    return g.cached;
  }


  std::vector<Run> Highlighter::of_line(std::string_view text, std::size_t start, std::size_t length)
  {
    std::vector<Run> found;
    std::size_t end = start + length;
    std::size_t last = 0;
    for (const auto& run : runs(text)) {
      if (run.end <= start || run.begin >= end)
        continue;
      std::size_t here = run.begin > start ? run.begin - start : 0;
      std::size_t there = std::min(run.end - start, length);
      // Inside something already written, or empty: the outer run was the
      // coarser answer, but both cannot be written.
      if (here < last || there <= here)
        continue;
      found.push_back({ here, there, run.name });
      last = there;
    }
    return found;
  }


  std::vector<Fold> Highlighter::folds(std::string_view text)
  {
    auto& g = *grammar;
    std::vector<Fold> found;
    if (g.folding.get() == nullptr) [[unlikely]]
      return found;
    ::TSTree* tree = g.parse(text);
    if (tree == nullptr) [[unlikely]]
      return found;
    ::TSQueryCursor* cursor = ::ts_query_cursor_new();
    ::ts_query_cursor_exec(cursor, g.folding.get(), ::ts_tree_root_node(tree));
    ::TSQueryMatch match;
    while (::ts_query_cursor_next_match(cursor, &match))
      for (std::uint16_t i = 0; i < match.capture_count; ++i) {
        auto node = match.captures[i].node;
        auto first = ::ts_node_start_point(node).row;
        auto last = ::ts_node_end_point(node).row;
        if (last > first)
          found.push_back({ first, last });
      }
    ::ts_query_cursor_delete(cursor);
    ::ts_tree_delete(tree);
    return found;
  }

} // namespace pl4g::ts
