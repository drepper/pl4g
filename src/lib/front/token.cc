#include "front/token.hh"

#include <algorithm>
#include <array>
#include <charconv>
#include <format>
#include <utility>


namespace pl4g {

  std::string Token::describe() const
  {
    if (kind == TokKind::ident || kind == TokKind::int_lit || kind == TokKind::float_lit)
      return std::format("{} '{}'", pl4g::describe(kind), text);
    return std::string(pl4g::describe(kind));
  }


  namespace {

    constexpr std::array<std::pair<std::string_view, TokKind>, 30> keywords{ {
      { "fn", TokKind::kw_fn },
      { "return", TokKind::kw_return },
      { "yield", TokKind::kw_yield },
      { "let", TokKind::kw_let },
      { "constinit", TokKind::kw_constinit },
      { "mut", TokKind::kw_mut },
      { "singleton", TokKind::kw_singleton },
      { "unit", TokKind::kw_unit },
      { "type", TokKind::kw_type },
      { "pre", TokKind::kw_pre },
      { "post", TokKind::kw_post },
      { "bundle", TokKind::kw_bundle },
      { "macro", TokKind::kw_macro },
      { "match", TokKind::kw_match },
      { "enum", TokKind::kw_enum },
      { "if", TokKind::kw_if },
      { "elif", TokKind::kw_elif },
      { "else", TokKind::kw_else },
      { "while", TokKind::kw_while },
      { "unless", TokKind::kw_unless },
      { "foreach", TokKind::kw_foreach },
      { "comptime", TokKind::kw_comptime },
      { "break", TokKind::kw_break },
      { "continue", TokKind::kw_continue },
      { "defer", TokKind::kw_defer },
      { "in", TokKind::kw_in },
      { "true", TokKind::kw_true },
      { "false", TokKind::kw_false },
      { "and", TokKind::kw_and },
      { "or", TokKind::kw_or },
    } };

  } // anonymous namespace


  TokKind keyword_kind(std::string_view text) noexcept
  {
    auto it = std::ranges::find(keywords, text, &std::pair<std::string_view, TokKind>::first);
    return it == keywords.end() ? TokKind::ident : it->second;
  }


  bool is_integer_type_name(std::string_view name) noexcept
  {
    if (name.size() < 2 || (name[0] != 'u' && name[0] != 'i') || name[1] == '0')
      return false;
    unsigned bits = 0;
    auto [end, ec] = std::from_chars(name.data() + 1, name.data() + name.size(), bits);
    if (ec != std::errc{} || end != name.data() + name.size())
      return false;
    // Unsigned from one bit, signed from two: a signed bit holds nought and
    // minus one, which nobody wants.
    unsigned lowest = name[0] == 'u' ? 1 : 2;
    return (bits >= lowest && bits <= 32) || bits == 64;
  }

} // namespace pl4g
