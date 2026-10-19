#include "front/parser.hh"

#include <algorithm>
#include <array>
#include <optional>
#include <string>

#include "front/lexer.hh"
#include "util/unicode.hh"


namespace pl4g {

  namespace {

    using namespace ast;

    // The names the parser reads by what they say rather than by their kind.
    constexpr std::string_view await_name = "\N{APL FUNCTIONAL SYMBOL QUAD}await";
    constexpr std::string_view heap_name = "\N{APL FUNCTIONAL SYMBOL QUAD}heap";
    constexpr std::string_view import_name = "\N{APL FUNCTIONAL SYMBOL QUAD}import";
    constexpr std::string_view lasting_word = "static";
    constexpr std::string_view wildcard_name = "_";


    // Unwinds to the nearest recovery point after an error nothing can be
    // made of.
    struct Bail {};


    // One row of the precedence table.
    struct Operator {
      TokKind token;
      BinaryOp op;
      // How tightly it binds: larger binds tighter.
      int precedence;
      // Whether `a op b op c` means `a op (b op c)`.
      bool right_associative = false;
      // Whether `a op b op c` means nothing and is refused: a comparison
      // answers a truth value, and a second one would ask about that.
      bool non_associative = false;
    };

    // What may stand between two operands, loosest first: the logical
    // operators, the comparisons, joining and shaping, the bitwise operators,
    // and the arithmetic, multiplication tighter than addition and the power
    // tightest.  The reasons are pypl4g's parser's, where each is argued.
    constexpr auto binary_operators = std::to_array<Operator>({
      { TokKind::logic_or, BinaryOp::logic_or, 1 },
      { TokKind::kw_or, BinaryOp::short_or, 1 },
      { TokKind::logic_xor, BinaryOp::logic_xor, 2 },
      { TokKind::logic_and, BinaryOp::logic_and, 3 },
      { TokKind::kw_and, BinaryOp::short_and, 3 },
      { TokKind::logic_nand, BinaryOp::logic_nand, 4, false, true },
      { TokKind::logic_nor, BinaryOp::logic_nor, 4, false, true },
      { TokKind::equals, BinaryOp::equal, 5, false, true },
      { TokKind::not_equal, BinaryOp::not_equal, 5, false, true },
      { TokKind::less, BinaryOp::less, 5, false, true },
      { TokKind::greater, BinaryOp::greater, 5, false, true },
      { TokKind::less_equal, BinaryOp::less_equal, 5, false, true },
      { TokKind::or_else, BinaryOp::or_else, 6, true },
      { TokKind::divides, BinaryOp::divides, 5, false, true },
      { TokKind::not_divides, BinaryOp::not_divides, 5, false, true },
      { TokKind::alike, BinaryOp::alike, 5, false, true },
      { TokKind::unalike, BinaryOp::unalike, 5, false, true },
      { TokKind::below_or_alike, BinaryOp::below_or_alike, 5, false, true },
      { TokKind::above_or_alike, BinaryOp::above_or_alike, 5, false, true },
      { TokKind::below_not_alike, BinaryOp::below_not_alike, 5, false, true },
      { TokKind::above_not_alike, BinaryOp::above_not_alike, 5, false, true },
      { TokKind::greater_equal, BinaryOp::greater_equal, 5, false, true },
      { TokKind::concat, BinaryOp::concat, 8 },
      { TokKind::shape, BinaryOp::shape, 9 },
      { TokKind::pipe, BinaryOp::bit_or, 10 },
      { TokKind::max, BinaryOp::max, 35 },
      { TokKind::min, BinaryOp::min, 35 },
      { TokKind::caret, BinaryOp::bit_xor, 20 },
      { TokKind::ampersand, BinaryOp::bit_and, 30 },
      { TokKind::plus, BinaryOp::add, 40 },
      { TokKind::minus, BinaryOp::subtract, 40 },
      { TokKind::sat_add, BinaryOp::sat_add, 40 },
      { TokKind::sat_sub, BinaryOp::sat_sub, 40 },
      { TokKind::times, BinaryOp::multiply, 50 },
      { TokKind::divide, BinaryOp::divide, 50 },
      { TokKind::percent, BinaryOp::remainder, 50 },
      { TokKind::shift_left, BinaryOp::shift_left, 50 },
      { TokKind::shift_right, BinaryOp::shift_right, 50 },
      { TokKind::rotate_left, BinaryOp::rotate_left, 50 },
      { TokKind::rotate_right, BinaryOp::rotate_right, 50 },
      { TokKind::sat_mul, BinaryOp::sat_mul, 50 },
      { TokKind::power, BinaryOp::power, 60, true },
    });

    // Where a range binds, which is not in the table: it takes two ends or
    // three.  Between the comparisons and the bitwise operators.
    constexpr int range_precedence = 7;

    // Where an operator the language gives no meaning binds: as tightly as
    // multiplying, and to the left.
    constexpr int fresh_precedence = 50;

    const Operator* binary_operator(TokKind kind) noexcept
    {
      for (const auto& op : binary_operators)
        if (op.token == kind)
          return &op;
      return nullptr;
    }

    // What may stand before an operand.
    constexpr std::array<std::pair<TokKind, UnaryOp>, 14> unary_operators{ {
      { TokKind::tilde, UnaryOp::bit_not },
      { TokKind::logic_not, UnaryOp::logic_not },
      { TokKind::length, UnaryOp::length },
      { TokKind::shape, UnaryOp::shape },
      { TokKind::max, UnaryOp::max },
      { TokKind::min, UnaryOp::min },
      { TokKind::divides, UnaryOp::divides },
      { TokKind::not_divides, UnaryOp::not_divides },
      { TokKind::floor, UnaryOp::floor },
      { TokKind::ceiling, UnaryOp::ceiling },
      { TokKind::nearest, UnaryOp::nearest },
      { TokKind::rounded, UnaryOp::rounded },
      { TokKind::next, UnaryOp::next },
      { TokKind::prev, UnaryOp::prev },
    } };

    std::optional<UnaryOp> unary_operator(TokKind kind) noexcept
    {
      for (auto [token, op] : unary_operators)
        if (token == kind)
          return op;
      return std::nullopt;
    }

    // How an operator is spelled, which is the name its head goes by in a
    // quote: the binary spelling where it is both.
    std::optional<std::string_view> operator_spelling(TokKind kind) noexcept
    {
      if (auto op = binary_operator(kind))
        return describe(op->op);
      if (auto op = unary_operator(kind))
        return describe(*op);
      return std::nullopt;
    }

    // The kinds of token an expression may begin with, which says whether `⊥`
    // carries a value or stands alone.
    bool begins_an_expression(TokKind kind) noexcept
    {
      switch (kind) {
      case TokKind::int_lit: case TokKind::float_lit: case TokKind::string_lit: case TokKind::char_lit:
      case TokKind::ident: case TokKind::kw_true: case TokKind::kw_false: case TokKind::lparen:
      case TokKind::array_open: case TokKind::lbracket: case TokKind::tuple_open: case TokKind::set_open:
      case TokKind::lift_open: case TokKind::bottom: case TokKind::tilde: case TokKind::logic_not:
      case TokKind::length: case TokKind::shape: case TokKind::max: case TokKind::min: case TokKind::floor:
      case TokKind::ceiling: case TokKind::nearest: case TokKind::rounded: case TokKind::divides:
      case TokKind::not_divides: case TokKind::ampersand: case TokKind::lambda: case TokKind::take:
      case TokKind::next: case TokKind::prev:
        return true;
      default:
        return false;
      }
    }

    // What may stand between the parentheses after a type's name where they
    // hold the types it is given.
    bool in_a_type(TokKind kind) noexcept
    {
      switch (kind) {
      case TokKind::ident: case TokKind::int_lit: case TokKind::string_lit: case TokKind::dot: case TokKind::comma:
      case TokKind::question: case TokKind::ampersand: case TokKind::kw_mut: case TokKind::kw_fn:
      case TokKind::kw_in: case TokKind::arrow: case TokKind::colon: case TokKind::lifetime: case TokKind::unit:
      case TokKind::times: case TokKind::divide: case TokKind::exponent: case TokKind::array_open:
      case TokKind::array_close: case TokKind::set_open: case TokKind::set_close: case TokKind::tuple_open:
      case TokKind::tuple_close: case TokKind::lbracket: case TokKind::rbracket: case TokKind::at_lbracket:
      case TokKind::lparen: case TokKind::rparen:
        return true;
      default:
        return false;
      }
    }

    // Tokens at which recovery stops: a new definition can begin there.
    bool is_recovery(TokKind kind) noexcept
    {
      return kind == TokKind::kw_fn || kind == TokKind::kw_let || kind == TokKind::kw_type
             || kind == TokKind::kw_unit || kind == TokKind::at_lbracket || kind == TokKind::eof;
    }

    // What ends a run of statements, whichever notation the block is in.
    bool ends_a_run(TokKind kind) noexcept
    {
      return kind == TokKind::newline || kind == TokKind::dedent || kind == TokKind::rbrace || kind == TokKind::eof;
    }

    // `found`, marked writable where `mut` was written before it: on a
    // collection, which is a handle, the mark lands on the type.
    TypeExpr* writable(TypeExpr* found, bool mutable_)
    {
      if (mutable_ && is<CollectionTypeRef>(found))
        static_cast<CollectionTypeRef*>(found)->mutable_ = true;
      return found;
    }

    // Python's `str.strip()` and `str.lstrip()`, by its notion of white space.
    std::string_view lstrip(std::string_view text)
    {
      std::size_t at = 0;
      while (at < text.size()) {
        auto d = unicode::decode(text, at);
        if (! unicode::is_space(d.cp))
          break;
        at += d.length;
      }
      return text.substr(at);
    }

    std::string_view strip(std::string_view text)
    {
      text = lstrip(text);
      std::size_t end = text.size();
      while (end > 0) {
        std::size_t start = end - 1;
        while (start > 0 && (static_cast<unsigned char>(text[start]) & 0xc0) == 0x80)
          --start;
        if (! unicode::is_space(unicode::decode(text, start).cp))
          break;
        end = start;
      }
      return text.substr(0, end);
    }


    // Whether the last block of `expr` is one written on one line.
    bool ends_inline(const Expr* expr)
    {
      const Block* last = nullptr;
      if (auto e = as<If>(expr); e != nullptr && ! e->arms.empty())
        last = e->arms.back()->body;
      else if (auto e = as<Match>(expr); e != nullptr && ! e->arms.empty())
        last = e->arms.back()->body;
      else if (auto e = as<While>(expr))
        last = e->alternative != nullptr ? e->alternative : e->body;
      else if (auto e = as<ForEach>(expr))
        last = e->alternative != nullptr ? e->alternative : e->body;
      else if (auto e = as<Lambda>(expr))
        last = e->body;
      return last != nullptr && last->style == BlockStyle::inline_;
    }

    // Whether something with an indented block of its own ends an expression.
    bool trailing_match(const Expr* expr)
    {
      while (true) {
        if (is<Match>(expr) || is<If>(expr) || is<While>(expr) || is<ForEach>(expr) || is<Lambda>(expr))
          return ! ends_inline(expr);
        if (auto b = as<Binary>(expr)) {
          expr = b->right;
          continue;
        }
        return false;
      }
    }

    // Whether a statement ends with a block of its own, which has taken the end
    // of its last line already: whatever expression it carries as its value.
    bool ends_with_a_block(const Stmt* stmt)
    {
      return visit(*stmt, [](const auto& s) {
        if constexpr (requires { { s.value } -> std::convertible_to<const Expr*>; })
          return trailing_match(s.value);
        else
          return false;
      });
    }


    class Parser {
    public:
      Parser(SyntaxArena& arena, std::span<const Token> tokens, std::string_view path, DiagEngine& diags,
             bool defer_bodies, const SourceFile* source)
        : arena(arena), tokens(tokens), path(path), diags(diags), source(source), defer_bodies(defer_bodies)
      {}

      const SourceUnit* parse_unit();
      const Block* parse_body_only();

      // The bodies set aside so far.
      std::vector<const Block*> deferred;
      // Whether the marks holding no type hold a piece of the program.
      bool quoting = false;

    private:
      // -- token access ------------------------------------------------------

      const Token& current() const noexcept { return tokens[pos]; }

      const Token& peek(std::size_t ahead = 1) const noexcept
      {
        return tokens[std::min(pos + ahead, tokens.size() - 1)];
      }

      const Token& advance() noexcept
      {
        const Token& t = tokens[pos];
        if (t.kind != TokKind::eof)
          ++pos;
        return t;
      }

      bool check(TokKind kind) const noexcept { return current().kind == kind; }

      const Token* accept(TokKind kind) noexcept { return check(kind) ? &advance() : nullptr; }

      // Consume a token of `kind`, or report and bail out.
      const Token& expect(TokKind kind, DiagId id = D::LANG_SYNTAX_UNEXPECTED_TOKEN)
      {
        if (auto t = accept(kind))
          return *t;
        if (id == D::LANG_SYNTAX_UNEXPECTED_TOKEN)
          diags.emit(id, current().span, { { "expected", describe(kind) }, { "found", current().describe() } });
        else
          diags.emit(id, current().span);
        throw Bail{};
      }

      void skip_newlines() noexcept
      {
        while (check(TokKind::newline))
          advance();
      }

      void recover() noexcept;

      template<typename T>
      T* make(Span span) { return arena.make<T>(span); }

      template<typename T>
      List<T> list(const std::vector<T>& items) { return arena.list(items); }

      // -- top level ---------------------------------------------------------

      bool attached_to_nothing() const noexcept;
      const Node* parse_item();
      Node* parse_variable(List<const Attribute*> attrs = {}, MaybeText doc = std::nullopt, List<Span> doc_lines = {});
      ModuleImport* parse_import(Span start, const Token& name, bool mutable_, const TypeExpr* declared, MaybeText doc,
                                 List<Span> doc_lines, List<const Attribute*> attrs);
      std::pair<MaybeText, List<Span>> parse_doc_comments();
      List<const Attribute*> parse_attributes();
      Attribute* parse_attribute();
      AttrArg* parse_attr_arg();
      AttrValue* parse_attr_value();

      // -- functions ---------------------------------------------------------

      FuncDef* parse_function(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines,
                              bool at_compile_time = false);
      FuncDef* parse_function_body(Span start, const Token& name, List<const Attribute*> attrs, MaybeText doc,
                                   List<Span> doc_lines, bool at_compile_time = false, bool is_macro = false,
                                   const Token* held = nullptr);
      List<const Clause*> parse_clauses();
      Clause* parse_clause(ClauseKind kind);
      std::pair<const Expr*, const TypeExpr*> parse_requirement();
      BundleDef* parse_bundle(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines);
      std::pair<List<const Clause*>, Span> parse_bundle_body();
      std::vector<const Clause*> parse_bundle_run();
      Clause* parse_bundle_clause();

      // -- macros ------------------------------------------------------------

      Node* parse_macro(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines);
      std::pair<List<const Rule*>, Span> parse_macro_rules();
      Rule* parse_macro_rule();
      Quote* parse_quote();

      // -- types -------------------------------------------------------------

      TypeExpr* parse_type_ref(bool definition = false);
      UnitRef* parse_unit_ref();
      UnitFactor* parse_unit_factor(int sign);
      UnitDef* parse_unit_definition(MaybeText doc = std::nullopt, List<Span> doc_lines = {});
      FuncTypeRef* parse_function_type();
      Lambda* parse_lambda(List<const Attribute*> attrs = {});
      Param* parse_lambda_param();
      std::pair<List<const Capture*>, std::optional<CaptureAll>> parse_captures();
      std::optional<CaptureAll> every_name();
      const Expr* parse_dimension();
      TupleTypeRef* parse_tuple_type();
      CollectionTypeRef* parse_collection_type();
      bool reading(std::string_view word) const noexcept { return check(TokKind::ident) && current().text == word; }
      bool begins_a_type(std::size_t ahead = 0) const noexcept;
      RunningTypeRef* parse_running_type();
      ListTypeRef* parse_list_type();
      TypeRef* parse_named_type();
      bool type_arguments_follow() const noexcept;
      TypeDef* parse_type_definition(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines);
      bool ends_the_parts(bool braced, bool indented) const noexcept;
      EnumDef* parse_enum_definition(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines);
      EnumMember* parse_enum_member();
      Field* parse_field();
      List<const Param*> parse_params();

      // -- blocks and statements ---------------------------------------------

      const Block* set_aside(bool quoting);
      Block* parse_body();
      std::vector<const Stmt*> parse_separated();
      Block* parse_layout_block();
      Block* parse_inline_block(Span start);
      Block* parse_explicit_block();
      Stmt* parse_statement();
      Stmt* parse_bare_statement();
      bool joins_and_assigns() const noexcept;
      Stmt* parse_unpacking(const NameRef* first);
      Expr* parse_while();
      const Block* parse_otherwise();
      const Label* parse_label();
      Stmt* parse_jump();
      bool ends_a_statement() const noexcept;
      bool binds_a_value() const noexcept;
      Expr* parse_comptime();
      ForEach* parse_iteration(Span start, Text keyword, const Label* label = nullptr, bool comptime = false);
      Text reference_mark(Text keyword);
      If* parse_if();
      bool begins_an_arm(TokKind keyword) const noexcept;
      bool takes_comptime(TokKind keyword);
      IfArm* parse_if_arm(bool comptime = false);
      Match* parse_match();
      MatchArm* parse_arm();
      Pattern* parse_pattern();
      Stmt* parse_assignment(const Expr* target);

      // -- expressions -------------------------------------------------------

      Expr* parse_expression(int minimum = 0);
      Range* parse_range(const Expr* first);
      Expr* parse_unary();
      Expr* parse_primary();
      Expr* parse_call(const Expr* callee);
      Expr* parse_named();
      Expr* parse_spreadable();
      Expr* parse_tuple();
      Expr* parse_collection();
      Expr* parse_arena();
      ArrayLit* parse_array();
      ListLit* parse_list();
      Expr* parse_lift();
      Expr* parse_atom();

      SyntaxArena& arena;
      std::span<const Token> tokens;
      Text path;
      DiagEngine& diags;
      const SourceFile* source;
      bool defer_bodies;
      std::size_t pos = 0;
      // Whether a block written on one line is being read: what closes it is
      // the end of the line, so one opened inside it would end with it.
      bool inline_ = false;
    };


    // One function's body, parsed on its own.  Everything it needs is here:
    // the file's tokens, where the body begins and ends among them, and whether
    // it runs while the compiler does.  What it says is kept until it is that
    // body's turn to be said, which keeps the messages in the order of the
    // source whichever body was parsed first.
    class BodyJob final : public BodyParse {
    public:
      BodyJob(SyntaxArena& arena, std::span<const Token> tokens, std::size_t start, std::size_t end, Text path,
              bool quoting, DiagEngine& diags, const SourceFile* source)
        : arena(arena), tokens(tokens), start(start), end(end), path(path), quoting(quoting), diags(diags),
          source(source)
      {
        quiet.engine.set_control(diags.control());
      }

      std::optional<List<const Stmt*>> parse() override
      {
        const Token& last = tokens[end - 1];
        std::vector<Token> piece(tokens.begin() + std::ptrdiff_t(start), tokens.begin() + std::ptrdiff_t(end));
        if (last.kind == TokKind::body) {
          // Left as text by the lexer: lexed here first.
          piece.pop_back();
          auto body = tokenize_body(*source, last.span, quiet.engine);
          piece.insert(piece.end(), body.begin(), body.end());
        } else {
          Token eof;
          eof.kind = TokKind::eof;
          eof.span = { last.span.end, last.span.end };
          piece.push_back(eof);
        }
        const auto& kept = arena.keep(std::move(piece));
        Parser parser(arena, kept, path, quiet.engine, false, source);
        parser.quoting = quoting;
        try {
          return parser.parse_body_only()->stmts();
        } catch (const Bail&) {
          return std::nullopt;
        }
      }

      void replay() override
      {
        for (auto one : quiet.collected)
          diags.replay(*one);
      }

    private:
      SyntaxArena& arena;
      std::span<const Token> tokens;
      std::size_t start;
      std::size_t end;
      Text path;
      bool quoting;
      DiagEngine& diags;
      const SourceFile* source;
      CollectingEngine quiet;
    };


    // -- top level -----------------------------------------------------------

    void Parser::recover() noexcept
    {
      int depth = 0;
      while (true) {
        auto kind = current().kind;
        if (kind == TokKind::eof)
          return;
        if (depth == 0 && is_recovery(kind))
          return;
        if (kind == TokKind::lparen || kind == TokKind::lbracket || kind == TokKind::lbrace
            || kind == TokKind::at_lbracket)
          ++depth;
        else if (kind == TokKind::rparen || kind == TokKind::rbracket || kind == TokKind::rbrace)
          depth = std::max(0, depth - 1);
        advance();
      }
    }


    const SourceUnit* Parser::parse_unit()
    {
      std::vector<const Node*> items;
      std::vector<const Attribute*> about_the_file;
      Span start = current().span;
      while (! check(TokKind::eof)) {
        skip_newlines();
        if (check(TokKind::eof))
          break;
        if (attached_to_nothing()) {
          try {
            auto attrs = parse_attributes();
            about_the_file.insert(about_the_file.end(), attrs.begin(), attrs.end());
          } catch (const Bail&) {
            // pypl4g does not catch this one, and stops reading the file; the
            // same here, by what is left being nothing.
            break;
          }
          continue;
        }
        try {
          if (auto item = parse_item())
            items.push_back(item);
        } catch (const Bail&) {
          recover();
        }
      }
      Span end = current().span;
      auto unit = make<SourceUnit>(start.to(end));
      unit->path = arena.text(path);
      unit->items = list(items);
      unit->attrs = list(about_the_file);
      return unit;
    }


    const Block* Parser::parse_body_only()
    {
      return parse_body();
    }


    bool Parser::attached_to_nothing() const noexcept
    {
      // An attribute list that a blank line, or the end of the file, follows:
      // one about the file and not about what comes next.
      if (! check(TokKind::at_lbracket))
        return false;
      int depth = 0;
      for (std::size_t at = pos; at < tokens.size(); ++at) {
        auto kind = tokens[at].kind;
        if (kind == TokKind::at_lbracket || kind == TokKind::lbracket)
          ++depth;
        else if (kind == TokKind::rbracket) {
          if (--depth == 0) {
            const Token* after = at + 1 < tokens.size() ? &tokens[at + 1] : nullptr;
            if (after == nullptr || after->kind == TokKind::eof)
              return true;
            if (after->kind == TokKind::at_lbracket)
              return false;
            if (after->kind == TokKind::newline) {
              const Token* following = at + 2 < tokens.size() ? &tokens[at + 2] : nullptr;
              return after->text == blank_line || following == nullptr || following->kind == TokKind::eof;
            }
            return false;
          }
        } else if (kind == TokKind::eof)
          return false;
      }
      return false;
    }


    const Node* Parser::parse_item()
    {
      auto [doc, doc_lines] = parse_doc_comments();
      auto attrs = parse_attributes();
      skip_newlines();
      if (check(TokKind::kw_fn))
        return parse_function(attrs, doc, doc_lines);
      if (check(TokKind::kw_comptime) && peek().kind == TokKind::kw_fn) {
        // `comptime` before a definition says when the function exists.
        advance();
        return parse_function(attrs, doc, doc_lines, true);
      }
      if (check(TokKind::kw_let))
        return parse_variable(attrs, doc, doc_lines);
      if (check(TokKind::kw_type))
        return parse_type_definition(attrs, doc, doc_lines);
      if (check(TokKind::kw_enum))
        return parse_enum_definition(attrs, doc, doc_lines);
      if (check(TokKind::kw_unit))
        return parse_unit_definition(doc, doc_lines);
      if (check(TokKind::kw_bundle))
        return parse_bundle(attrs, doc, doc_lines);
      if (check(TokKind::kw_macro))
        return parse_macro(attrs, doc, doc_lines);
      diags.emit(D::LANG_FILESTRUCT_UNEXPECTED_TOPLEVEL, current().span, { { "construct", current().describe() } });
      throw Bail{};
    }


    Node* Parser::parse_variable(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines)
    {
      // `let NAME ':' ['mut'] [TYPE] '=' VALUE`; names next to each other take
      // a tuple apart.
      Span start = expect(TokKind::kw_let).span;
      const Token& name = expect(TokKind::ident);
      std::vector<NameAt> more;
      while (accept(TokKind::comma) != nullptr) {
        const Token& written = expect(TokKind::ident);
        more.push_back({ written.text, written.span });
      }
      expect(TokKind::colon, D::LANG_VARDEF_EXPECTED_COLON);
      bool mutable_ = accept(TokKind::kw_mut) != nullptr;
      const TypeExpr* declared = nullptr;
      if (begins_a_type() || check(TokKind::array_open))
        declared = writable(parse_type_ref(true), mutable_);
      // `in kept` after the type receives the arena a call hands out with its
      // answer.
      std::optional<NameAt> received;
      if (declared != nullptr && accept(TokKind::kw_in) != nullptr) {
        const Token& arena_name = expect(TokKind::ident);
        received = NameAt{ arena_name.text, arena_name.span };
      }
      if (accept(TokKind::equals) == nullptr) [[unlikely]] {
        diags.emit(D::LANG_VARDEF_MISSING_INITIALIZER, name.span, { { "name", name.text } });
        throw Bail{};
      }
      if (check(TokKind::ident) && current().text == import_name)
        return parse_import(start, name, mutable_, declared, doc, doc_lines, attrs);
      auto value = parse_expression();
      auto def = make<VarDef>(start.to(value->span));
      def->name = name.text;
      def->name_span = name.span;
      def->type = declared;
      def->value = value;
      def->mutable_ = mutable_;
      // Where the documentation's lines are is not kept for a variable, as
      // pypl4g does not keep it.
      def->doc = doc;
      def->attrs = attrs;
      def->more = list(more);
      def->made_in = received;
      return def;
    }


    ModuleImport* Parser::parse_import(Span start, const Token& name, bool mutable_, const TypeExpr* declared,
                                       MaybeText doc, List<Span> doc_lines, List<const Attribute*> attrs)
    {
      // A module is not a value, so nothing about it may be qualified.
      advance();
      if (mutable_ || declared != nullptr)
        diags.emit(D::LANG_IMPORT_QUALIFIED, name.span, { { "name", name.text } });
      expect(TokKind::lparen);
      const Token& from = expect(TokKind::string_lit, D::LANG_IMPORT_EXPECTED_NAME);
      const Token& end = expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN);
      auto found = make<ModuleImport>(start.to(end.span));
      found->name = name.text;
      found->name_span = name.span;
      found->source = arena.text(from.str_value);
      found->source_span = from.span;
      found->doc = doc;
      found->doc_lines = doc_lines;
      found->attrs = attrs;
      return found;
    }


    std::pair<MaybeText, List<Span>> Parser::parse_doc_comments()
    {
      // The text, and where each line of it is without its marker and the
      // spaces after it, so a column in the text is a column in the file.
      std::string text;
      std::vector<Span> places;
      while (check(TokKind::doc_comment)) {
        const Token& token = advance();
        auto body = token.text.substr(glyph::doc_comment.size());
        Loc begins = token.span.start + Loc(token.text.size() - lstrip(body).size());
        auto line = strip(body);
        if (! places.empty())
          text += '\n';
        text += line;
        places.push_back({ begins, begins + Loc(line.size()) });
        skip_newlines();
      }
      if (places.empty())
        return { std::nullopt, {} };
      return { arena.text(text), list(places) };
    }


    // -- attributes ----------------------------------------------------------

    List<const Attribute*> Parser::parse_attributes()
    {
      // One list, not several: two say what one holding both would.
      std::vector<const Attribute*> attrs;
      unsigned lists = 0;
      while (check(TokKind::at_lbracket)) {
        Span start = current().span;
        advance();
        if (++lists > 1)
          diags.emit(D::LANG_ATTR_SEPARATE_LISTS, start, { { "count", lists } });
        do
          attrs.push_back(parse_attribute());
        while (accept(TokKind::comma) != nullptr);
        expect(TokKind::rbracket);
        skip_newlines();
      }
      return list(attrs);
    }


    Attribute* Parser::parse_attribute()
    {
      // An attribute carrying no arguments is written without parentheses.
      const Token& name = expect(TokKind::ident);
      std::vector<const AttrArg*> args;
      Span end = name.span;
      if (const Token* opening = accept(TokKind::lparen)) {
        if (check(TokKind::rparen))
          diags.emit(D::LANG_ATTR_EMPTY_ARGUMENTS, opening->span.to(current().span), { { "name", name.text } });
        else
          do
            args.push_back(parse_attr_arg());
          while (accept(TokKind::comma) != nullptr);
        end = expect(TokKind::rparen).span;
      }
      auto attr = make<Attribute>(name.span.to(end));
      attr->name = name.text;
      attr->args = list(args);
      attr->name_span = name.span;
      return attr;
    }


    AttrArg* Parser::parse_attr_arg()
    {
      if (check(TokKind::ident) && peek().kind == TokKind::equals) {
        const Token& name = advance();
        advance();
        auto value = parse_attr_value();
        auto arg = make<AttrArg>(name.span.to(value->span));
        arg->value = value;
        arg->name = name.text;
        return arg;
      }
      auto value = parse_attr_value();
      auto arg = make<AttrArg>(value->span);
      arg->value = value;
      return arg;
    }


    AttrValue* Parser::parse_attr_value()
    {
      const Token& token = current();
      switch (token.kind) {
      case TokKind::int_lit: {
        advance();
        auto v = make<AttrInt>(token.span);
        v->value = { token.int_value, token.int_too_large };
        return v;
      }
      case TokKind::string_lit: {
        advance();
        auto v = make<AttrString>(token.span);
        v->value = arena.text(token.str_value);
        return v;
      }
      case TokKind::kw_true:
      case TokKind::kw_false: {
        advance();
        auto v = make<AttrBool>(token.span);
        v->value = token.kind == TokKind::kw_true;
        return v;
      }
      case TokKind::ident: {
        advance();
        auto v = make<AttrName>(token.span);
        v->value = token.text;
        return v;
      }
      default:
        diags.emit(D::LANG_SYNTAX_UNEXPECTED_TOKEN, token.span,
                   { { "expected", "an attribute argument" }, { "found", token.describe() } });
        throw Bail{};
      }
    }


    // -- functions -----------------------------------------------------------

    FuncDef* Parser::parse_function(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines,
                                    bool at_compile_time)
    {
      Span start = expect(TokKind::kw_fn).span;
      // An operator standing where a name goes: the glyph is the name.
      const Token* name = check(TokKind::opname) ? &advance() : &expect(TokKind::ident);
      // A name of two parts belongs to the type the first part names.
      const Token* held = nullptr;
      if (name->kind == TokKind::ident && check(TokKind::dot)) {
        advance();
        held = name;
        name = &expect(TokKind::ident);
      }
      return parse_function_body(start, *name, attrs, doc, doc_lines, at_compile_time, false, held);
    }


    FuncDef* Parser::parse_function_body(Span start, const Token& name, List<const Attribute*> attrs, MaybeText doc,
                                         List<Span> doc_lines, bool at_compile_time, bool is_macro,
                                         const Token* held)
    {
      expect(TokKind::lparen);
      auto params = parse_params();
      expect(TokKind::rparen);
      // The arrow and what follows say what the function answers with;
      // leaving them off says it answers with nothing.
      const TypeExpr* ret_type = nullptr;
      bool generates = false;
      if (accept(TokKind::arrow) != nullptr) {
        bool written_mut = accept(TokKind::kw_mut) != nullptr;
        ret_type = writable(parse_type_ref(), written_mut);
      }
      const TypeExpr* takes = nullptr;
      const TypeExpr* finishes = nullptr;
      if (ret_type == nullptr && accept(TokKind::iter) != nullptr) {
        // The arrow doubled: a generator, and with the arrow the other way what
        // it takes back each time it is resumed, and with a single one what it
        // answers when it ends.
        generates = true;
        ret_type = parse_type_ref();
        if (accept(TokKind::takes) != nullptr)
          takes = parse_type_ref();
        if (accept(TokKind::arrow) != nullptr)
          finishes = parse_type_ref();
      }
      // `in a, b` after the answer: which arenas it was made in.
      std::vector<NameAt> made_in;
      if (ret_type != nullptr && accept(TokKind::kw_in) != nullptr)
        do {
          const Token& one = expect(TokKind::ident);
          made_in.push_back({ one.text, one.span });
        } while (accept(TokKind::comma) != nullptr);
      auto clauses = parse_clauses();
      auto def = make<FuncDef>(start);
      def->name = name.text;
      def->name_span = name.span;
      def->params = params;
      def->ret_type = ret_type;
      def->clauses = clauses;
      def->made_in = list(made_in);
      def->attrs = attrs;
      def->doc = doc;
      def->doc_lines = doc_lines;
      def->at_compile_time = at_compile_time;
      def->is_macro = is_macro;
      def->generates = generates;
      def->takes = takes;
      def->finishes = finishes;
      if (held != nullptr) {
        def->held = held->text;
        def->held_span = held->span;
      }
      // A function with no body is the declaration of one defined elsewhere:
      // the line ends here.
      if (check(TokKind::newline) || check(TokKind::eof)) {
        Span end = ! clauses.empty() ? clauses.back()->span : ret_type != nullptr ? ret_type->span : name.span;
        def->span = start.to(end);
        return def;
      }
      // A body that runs while the compiler does may quote.
      bool outer = quoting;
      quoting = at_compile_time;
      const Block* body = nullptr;
      try {
        body = defer_bodies ? set_aside(at_compile_time) : nullptr;
        if (body == nullptr)
          body = parse_body();
      } catch (...) {
        quoting = outer;
        throw;
      }
      quoting = outer;
      def->body = body;
      def->span = start.to(body->span);
      return def;
    }


    List<const Clause*> Parser::parse_clauses()
    {
      // As many `pre` and `post` clauses as are written, each on its own.
      std::vector<const Clause*> found;
      while (true) {
        if (check(TokKind::kw_pre))
          found.push_back(parse_clause(ClauseKind::pre));
        else if (check(TokKind::kw_post))
          found.push_back(parse_clause(ClauseKind::post));
        else
          break;
      }
      return list(found);
    }


    Clause* Parser::parse_clause(ClauseKind kind)
    {
      Span start = advance().span;
      expect(TokKind::lparen);
      auto [expr, answers] = parse_requirement();
      const Token& end = expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN);
      auto clause = make<Clause>(start.to(end.span));
      clause->kind = kind;
      clause->expr = expr;
      clause->answers = answers;
      return clause;
    }


    std::pair<const Expr*, const TypeExpr*> Parser::parse_requirement()
    {
      // An expression, and the type an arrow after it names.
      auto expr = parse_expression();
      if (accept(TokKind::arrow) == nullptr)
        return { expr, nullptr };
      if (accept(TokKind::lift_open) != nullptr) {
        auto written = parse_type_ref();
        expect(TokKind::lift_close, D::LANG_SYNTAX_EXPECTED_CLOSING_LIFT);
        return { expr, written };
      }
      return { expr, parse_type_ref() };
    }


    BundleDef* Parser::parse_bundle(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines)
    {
      Span start = expect(TokKind::kw_bundle).span;
      const Token& name = expect(TokKind::ident);
      expect(TokKind::lparen);
      std::vector<Text> names;
      std::vector<Span> places;
      do {
        const Token& written = expect(TokKind::ident);
        names.push_back(written.text);
        places.push_back(written.span);
      } while (accept(TokKind::comma) != nullptr);
      expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN);
      auto [clauses, end] = parse_bundle_body();
      auto def = make<BundleDef>(start.to(end));
      def->name = name.text;
      def->name_span = name.span;
      def->params = list(names);
      def->param_spans = list(places);
      def->clauses = clauses;
      def->attrs = attrs;
      def->doc = doc;
      def->doc_lines = doc_lines;
      return def;
    }


    std::pair<List<const Clause*>, Span> Parser::parse_bundle_body()
    {
      // Everything in a bundle is a requirement, separated as statements are.
      if (check(TokKind::lbrace)) {
        Span start = advance().span;
        auto found = parse_bundle_run();
        Span end = current().span;
        expect(TokKind::rbrace);
        return { list(found), start.to(end) };
      }
      Span start = expect(TokKind::colon, D::LANG_FUNCDEF_EXPECTED_BLOCK).span;
      if (! check(TokKind::newline)) {
        auto found = parse_bundle_run();
        return { list(found), start.to(current().span) };
      }
      expect(TokKind::newline);
      expect(TokKind::indent);
      std::vector<const Clause*> found;
      while (! check(TokKind::dedent) && ! check(TokKind::eof)) {
        skip_newlines();
        if (check(TokKind::dedent) || check(TokKind::eof))
          break;
        auto run = parse_bundle_run();
        found.insert(found.end(), run.begin(), run.end());
        if (check(TokKind::dedent) || check(TokKind::eof))
          continue;
        expect(TokKind::newline);
      }
      Span end = current().span;
      accept(TokKind::dedent);
      return { list(found), start.to(end) };
    }


    std::vector<const Clause*> Parser::parse_bundle_run()
    {
      std::vector<const Clause*> found{ parse_bundle_clause() };
      while (accept(TokKind::semicolon) != nullptr) {
        if (ends_a_run(current().kind))
          break;
        found.push_back(parse_bundle_clause());
      }
      return found;
    }


    Clause* Parser::parse_bundle_clause()
    {
      auto [expr, answers] = parse_requirement();
      Span end = answers != nullptr ? answers->span : expr->span;
      auto clause = make<Clause>(expr->span.to(end));
      clause->kind = ClauseKind::pre;
      clause->expr = expr;
      clause->answers = answers;
      return clause;
    }


    // -- macros --------------------------------------------------------------

    Node* Parser::parse_macro(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines)
    {
      Span start = expect(TokKind::kw_macro).span;
      const Token& name = expect(TokKind::ident);
      if (check(TokKind::lparen))
        // The other form: a function over the program's text, which runs while
        // the compiler does.
        return parse_function_body(start, name, attrs, doc, doc_lines, true, true);
      auto [rules, end] = parse_macro_rules();
      auto def = make<MacroDef>(start.to(end));
      def->name = name.text;
      def->name_span = name.span;
      def->rules = rules;
      def->attrs = attrs;
      def->doc = doc;
      def->doc_lines = doc_lines;
      return def;
    }


    std::pair<List<const Rule*>, Span> Parser::parse_macro_rules()
    {
      if (check(TokKind::lbrace)) {
        Span start = advance().span;
        std::vector<const Rule*> found{ parse_macro_rule() };
        while (accept(TokKind::semicolon) != nullptr) {
          if (ends_a_run(current().kind))
            break;
          found.push_back(parse_macro_rule());
        }
        Span end = current().span;
        expect(TokKind::rbrace);
        return { list(found), start.to(end) };
      }
      Span start = expect(TokKind::colon, D::LANG_FUNCDEF_EXPECTED_BLOCK).span;
      if (! check(TokKind::newline)) {
        std::vector<const Rule*> one{ parse_macro_rule() };
        return { list(one), start.to(current().span) };
      }
      expect(TokKind::newline);
      expect(TokKind::indent);
      std::vector<const Rule*> found;
      while (! check(TokKind::dedent) && ! check(TokKind::eof)) {
        skip_newlines();
        if (check(TokKind::dedent) || check(TokKind::eof))
          break;
        found.push_back(parse_macro_rule());
        if (check(TokKind::dedent) || check(TokKind::eof))
          continue;
        expect(TokKind::newline);
      }
      Span end = current().span;
      accept(TokKind::dedent);
      return { list(found), start.to(end) };
    }


    Rule* Parser::parse_macro_rule()
    {
      auto pattern = parse_quote();
      expect(TokKind::arrow);
      auto template_ = parse_quote();
      auto rule = make<Rule>(pattern->span.to(template_->span));
      rule->pattern = pattern;
      rule->template_ = template_;
      return rule;
    }


    Quote* Parser::parse_quote()
    {
      // Expressions separated by commas, or statements indented under the
      // opening mark, which is then where a block's colon would be.
      Span start = expect(TokKind::lift_open).span;
      if (check(TokKind::newline)) {
        expect(TokKind::newline);
        expect(TokKind::indent);
        std::vector<const Stmt*> stmts;
        while (! check(TokKind::dedent) && ! check(TokKind::eof)) {
          skip_newlines();
          if (check(TokKind::dedent) || check(TokKind::eof))
            break;
          auto run = parse_separated();
          stmts.insert(stmts.end(), run.begin(), run.end());
          if (check(TokKind::dedent) || check(TokKind::eof))
            continue;
          if (accept(TokKind::newline) == nullptr && ! ends_with_a_block(stmts.back()))
            expect(TokKind::newline);
        }
        Span inner = current().span;
        accept(TokKind::dedent);
        skip_newlines();
        const Token& end = expect(TokKind::lift_close, D::LANG_SYNTAX_EXPECTED_CLOSING_LIFT);
        auto body = make<Block>(start.to(inner));
        body->style = BlockStyle::layout;
        body->statements = list(stmts);
        auto quote = make<Quote>(start.to(end.span));
        quote->body = body;
        return quote;
      }
      std::vector<const Expr*> pieces;
      if (auto spelling = operator_spelling(current().kind); spelling && peek().kind == TokKind::lift_close) {
        // An operator alone between the marks, which is the operator itself:
        // a name whose spelling is the glyph.
        const Token& written = advance();
        auto name = make<NameRef>(written.span);
        name->name = *spelling;
        pieces.push_back(name);
        const Token& end = expect(TokKind::lift_close, D::LANG_SYNTAX_EXPECTED_CLOSING_LIFT);
        auto quote = make<Quote>(start.to(end.span));
        quote->pieces = list(pieces);
        return quote;
      }
      if (! check(TokKind::lift_close)) {
        // Nothing between the marks is a macro of no arguments.
        pieces.push_back(parse_expression());
        while (accept(TokKind::comma) != nullptr)
          pieces.push_back(parse_expression());
      }
      const Token& end = expect(TokKind::lift_close, D::LANG_SYNTAX_EXPECTED_CLOSING_LIFT);
      auto quote = make<Quote>(start.to(end.span));
      quote->pieces = list(pieces);
      return quote;
    }


    // -- types ---------------------------------------------------------------

    TypeExpr* Parser::parse_type_ref(bool definition)
    {
      // A type; an array is written after what it holds, `i32⟦4⟧`.
      if (check(TokKind::ampersand)) {
        // A reference.  `mut` says what may be done to the place; `static`
        // or a lifetime how long what it names lives.
        Span start = advance().span;
        bool mutable_ = accept(TokKind::kw_mut) != nullptr;
        bool lasting = reading(lasting_word) && begins_a_type(1);
        if (lasting)
          advance();
        MaybeText lifetime;
        if (! lasting && accept(TokKind::lifetime) != nullptr)
          lifetime = expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_LIFETIME_NAME).text;
        auto pointee = parse_type_ref();
        auto ref = make<RefTypeRef>(start.to(pointee->span));
        ref->pointee = pointee;
        ref->mutable_ = mutable_;
        ref->lasting = lasting;
        ref->lifetime = lifetime;
        return ref;
      }
      if (check(TokKind::kw_fn) || check(TokKind::at_lbracket))
        return parse_function_type();
      if (check(TokKind::array_open)) {
        // An array of whatever the value gives, in a definition.
        Span start = advance().span;
        if (! definition)
          diags.emit(D::LANG_SYNTAX_ARRAY_ELEMENT_LEFT_OUT, start);
        std::vector<const Expr*> shape{ parse_dimension() };
        while (accept(TokKind::comma) != nullptr)
          shape.push_back(parse_dimension());
        Span end = expect(TokKind::array_close, D::LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span;
        auto array = make<ArrayTypeRef>(start.to(end));
        array->shape = list(shape);
        return array;
      }
      if (check(TokKind::deref) && peek().kind == TokKind::iter)
        return parse_running_type();
      if (check(TokKind::lift_open)) {
        // A type handed as an argument: `kind: ⌜T'⌝`.
        Span start = advance().span;
        auto inner = parse_type_ref();
        Span end = expect(TokKind::lift_close, D::LANG_SYNTAX_EXPECTED_CLOSING_LIFT).span;
        auto lifted = make<LiftedTypeRef>(start.to(end));
        lifted->inner = inner;
        return lifted;
      }
      if (check(TokKind::iter)) {
        // Something a loop can walk, and what it gives.
        Span start = advance().span;
        auto walk = make<IterTypeRef>(start);
        if (! begins_a_type())
          return walk;
        walk->element = parse_type_ref();
        walk->span = start.to(walk->element->span);
        return walk;
      }
      TypeExpr* found;
      if (check(TokKind::set_open))
        found = parse_collection_type();
      else if (check(TokKind::tuple_open))
        found = parse_tuple_type();
      else if (check(TokKind::lbracket))
        found = parse_list_type();
      else
        found = parse_named_type();
      if (check(TokKind::unit)) {
        // A unit belongs to the type it is written after, not to an array of it.
        auto written = parse_unit_ref();
        auto unit = make<UnitTypeRef>(found->span.to(written->span));
        unit->base = found;
        unit->unit = written;
        found = unit;
      }
      while (check(TokKind::array_open)) {
        advance();
        std::vector<const Expr*> shape{ parse_dimension() };
        while (accept(TokKind::comma) != nullptr)
          shape.push_back(parse_dimension());
        Span end = expect(TokKind::array_close, D::LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span;
        auto array = make<ArrayTypeRef>(found->span.to(end));
        array->element = found;
        array->shape = list(shape);
        found = array;
      }
      if (is<ArrayTypeRef>(found) && check(TokKind::question)) {
        // A result whose answer is the array.
        auto array = static_cast<ArrayTypeRef*>(found);
        const Token* last = &advance();
        if (check(TokKind::ident)) {
          last = &advance();
          array->error = last->text;
        }
        array->span = array->span.to(last->span);
        array->result = true;
      }
      return found;
    }


    UnitRef* Parser::parse_unit_ref()
    {
      // `¤meter÷second²`, read left to right as physics writes it.
      Span start = expect(TokKind::unit).span;
      std::vector<const UnitFactor*> factors{ parse_unit_factor(1) };
      while (true) {
        int sign;
        if (accept(TokKind::times) != nullptr)
          sign = 1;
        else if (accept(TokKind::divide) != nullptr)
          sign = -1;
        else
          break;
        factors.push_back(parse_unit_factor(sign));
      }
      auto unit = make<UnitRef>(start.to(factors.back()->span));
      unit->factors = list(factors);
      return unit;
    }


    UnitFactor* Parser::parse_unit_factor(int sign)
    {
      const Token* token;
      bool quoted;
      if (check(TokKind::string_lit)) {
        token = &advance();
        quoted = true;
      } else {
        token = &expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_UNIT);
        quoted = false;
      }
      Integer exponent{ 1 };
      Span end = token->span;
      if (const Token* raised = accept(TokKind::exponent)) {
        exponent = { raised->int_value, raised->int_too_large };
        end = raised->span;
      }
      auto factor = make<UnitFactor>(token->span.to(end));
      factor->name = token->text;
      factor->quoted = quoted;
      factor->exponent = { sign * exponent.value, exponent.too_large };
      return factor;
    }


    UnitDef* Parser::parse_unit_definition(MaybeText doc, List<Span> doc_lines)
    {
      // `unit NAME`, `unit NAME = VALUE` or `unit ¤FROM → ¤TO`.
      Span start = expect(TokKind::kw_unit).span;
      if (check(TokKind::unit)) {
        auto what = parse_unit_ref();
        expect(TokKind::arrow, D::LANG_SYNTAX_EXPECTED_UNIT_ARROW);
        auto where = parse_unit_ref();
        auto def = make<UnitDef>(start.to(where->span));
        def->name_span = what->span;
        def->stands = UnitPair{ what, where };
        def->doc = doc;
        def->doc_lines = doc_lines;
        return def;
      }
      const Token* token;
      bool quoted;
      if (check(TokKind::string_lit)) {
        token = &advance();
        quoted = true;
      } else {
        token = &expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_UNIT);
        quoted = false;
      }
      auto def = make<UnitDef>(start.to(token->span));
      def->name = token->text;
      def->name_span = token->span;
      def->quoted = quoted;
      def->doc = doc;
      def->doc_lines = doc_lines;
      if (accept(TokKind::equals) == nullptr)
        return def;
      // Numbers and names mixed, one product read left to right.
      Integer over{ 1 }, under{ 1 };
      std::vector<const UnitFactor*> factors;
      int sign = 1;
      while (true) {
        if (check(TokKind::int_lit)) {
          const Token& number = advance();
          Integer& into = sign > 0 ? over : under;
          into.value *= number.int_value;
          into.too_large = into.too_large || number.int_too_large;
        } else
          factors.push_back(parse_unit_factor(sign));
        if (accept(TokKind::times) != nullptr)
          sign = 1;
        else if (accept(TokKind::divide) != nullptr)
          sign = -1;
        else
          break;
      }
      Span end = ! factors.empty() ? factors.back()->span : token->span;
      def->span = start.to(end);
      auto measured = make<UnitRef>(token->span.to(end));
      measured->factors = list(factors);
      def->measured = measured;
      def->scale = Scale{ over, under };
      return def;
    }


    FuncTypeRef* Parser::parse_function_type()
    {
      // `fn(TYPE, TYPE) → TYPE`, with what a caller reads off it before it.
      auto attrs = check(TokKind::at_lbracket) ? parse_attributes() : List<const Attribute*>{};
      Span start = expect(TokKind::kw_fn).span;
      expect(TokKind::lparen);
      std::vector<const TypeExpr*> params;
      if (! check(TokKind::rparen)) {
        params.push_back(parse_type_ref());
        while (accept(TokKind::comma) != nullptr)
          params.push_back(parse_type_ref());
      }
      Span end = expect(TokKind::rparen).span;
      auto type = make<FuncTypeRef>(start);
      if (accept(TokKind::kw_in) != nullptr) {
        // Where what a lambda of this type brought in is kept.
        const Token& kept = expect(TokKind::ident);
        type->kept_in = NameAt{ kept.text, kept.span };
        end = kept.span;
      }
      if (accept(TokKind::arrow) != nullptr) {
        type->ret = parse_type_ref();
        end = type->ret->span;
        if (accept(TokKind::kw_in) != nullptr) {
          const Token& arena_name = expect(TokKind::ident);
          type->made_in = NameAt{ arena_name.text, arena_name.span };
          end = arena_name.span;
        }
      }
      type->span = start.to(end);
      type->params = list(params);
      type->attrs = attrs;
      return type;
    }


    Lambda* Parser::parse_lambda(List<const Attribute*> attrs)
    {
      // `λ PARM: TYPE, … [CAPTURES] → TYPE` and the body after it.
      Span start = expect(TokKind::lambda).span;
      std::vector<const Param*> params;
      while (check(TokKind::ident)) {
        params.push_back(parse_lambda_param());
        if (accept(TokKind::comma) == nullptr)
          break;
      }
      auto lambda = make<Lambda>(start);
      if (check(TokKind::lbracket)) {
        auto [captures, brings_in] = parse_captures();
        lambda->captures = captures;
        lambda->brings_in = brings_in;
      }
      if (accept(TokKind::kw_in) != nullptr) {
        const Token& kept = expect(TokKind::ident);
        lambda->kept_in = NameAt{ kept.text, kept.span };
      }
      if (accept(TokKind::arrow) != nullptr) {
        lambda->ret_type = parse_type_ref();
        if (accept(TokKind::kw_in) != nullptr) {
          const Token& arena_name = expect(TokKind::ident);
          lambda->made_in = NameAt{ arena_name.text, arena_name.span };
        }
      }
      auto body = parse_body();
      lambda->span = start.to(body->span);
      lambda->params = list(params);
      lambda->body = body;
      lambda->attrs = attrs;
      return lambda;
    }


    Param* Parser::parse_lambda_param()
    {
      const Token& name = expect(TokKind::ident);
      expect(TokKind::colon);
      bool mutable_ = accept(TokKind::kw_mut) != nullptr;
      auto written = writable(parse_type_ref(), mutable_);
      auto param = make<Param>(name.span.to(written->span));
      param->name = name.text;
      param->type = written;
      param->mutable_ = mutable_;
      return param;
    }


    std::pair<List<const Capture*>, std::optional<CaptureAll>> Parser::parse_captures()
    {
      // `[a, &b]`, `[=]` or `[&]`.
      Span start = expect(TokKind::lbracket).span;
      if (auto every = every_name()) {
        expect(TokKind::rbracket, D::LANG_SYNTAX_EXPECTED_CLOSING_LIST);
        return { {}, every };
      }
      std::vector<const Capture*> found;
      while (! check(TokKind::rbracket)) {
        const Token* mark = accept(TokKind::ampersand);
        const Token& name = expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_CAPTURE);
        auto capture = make<Capture>((mark != nullptr ? mark->span : name.span).to(name.span));
        capture->name = name.text;
        capture->by_reference = mark != nullptr;
        found.push_back(capture);
        if (accept(TokKind::comma) == nullptr)
          break;
      }
      Span end = expect(TokKind::rbracket, D::LANG_SYNTAX_EXPECTED_CLOSING_LIST).span;
      if (found.empty())
        // An empty list is a second spelling of none.
        diags.emit(D::LANG_SYNTAX_EMPTY_CAPTURE, start.to(end));
      return { list(found), std::nullopt };
    }


    std::optional<CaptureAll> Parser::every_name()
    {
      if (check(TokKind::equals)) {
        advance();
        return CaptureAll::by_value;
      }
      if (check(TokKind::ampersand) && peek(1).kind == TokKind::rbracket) {
        advance();
        return CaptureAll::by_reference;
      }
      return std::nullopt;
    }


    const Expr* Parser::parse_dimension()
    {
      if (check(TokKind::array_close) || check(TokKind::comma))
        return nullptr;
      return parse_expression();
    }


    TupleTypeRef* Parser::parse_tuple_type()
    {
      Span start = expect(TokKind::tuple_open).span;
      std::vector<const TypeExpr*> members{ parse_type_ref() };
      while (accept(TokKind::comma) != nullptr)
        members.push_back(parse_type_ref());
      Span end = expect(TokKind::tuple_close, D::LANG_SYNTAX_EXPECTED_CLOSING_TUPLE).span;
      auto tuple = make<TupleTypeRef>(start.to(end));
      tuple->members = list(members);
      return tuple;
    }


    CollectionTypeRef* Parser::parse_collection_type()
    {
      Span start = expect(TokKind::set_open).span;
      auto element = parse_type_ref();
      const TypeExpr* value = nullptr;
      if (accept(TokKind::colon) != nullptr)
        value = parse_type_ref();
      Span end = expect(TokKind::set_close, D::LANG_SYNTAX_EXPECTED_CLOSING_SET).span;
      auto collection = make<CollectionTypeRef>(start.to(end));
      collection->element = element;
      collection->value = value;
      return collection;
    }


    bool Parser::begins_a_type(std::size_t ahead) const noexcept
    {
      auto kind = ahead > 0 ? peek(ahead).kind : current().kind;
      if ((kind == TokKind::set_open || kind == TokKind::lbracket || kind == TokKind::tuple_open)
          && peek(ahead + 1).kind == TokKind::spread)
        // A spread is written only in a value.
        return false;
      switch (kind) {
      case TokKind::ident: case TokKind::set_open: case TokKind::tuple_open: case TokKind::lbracket:
      case TokKind::ampersand: case TokKind::kw_fn: case TokKind::at_lbracket: case TokKind::iter:
      case TokKind::deref:
        return true;
      default:
        return false;
      }
    }


    RunningTypeRef* Parser::parse_running_type()
    {
      // `⌖⇉ TYPE [⇇ TYPE] [in ⎕heap]`: a walk under way.
      Span start = expect(TokKind::deref).span;
      expect(TokKind::iter);
      auto running = make<RunningTypeRef>(start);
      running->element = parse_type_ref();
      Span end = running->element->span;
      if (accept(TokKind::takes) != nullptr) {
        running->given = parse_type_ref();
        end = running->given->span;
      }
      if (check(TokKind::kw_in) && peek().kind == TokKind::ident && peek().text == heap_name) {
        advance();
        const Token& kept = advance();
        running->kept_in = NameAt{ kept.text, kept.span };
        end = kept.span;
      }
      running->span = start.to(end);
      return running;
    }


    ListTypeRef* Parser::parse_list_type()
    {
      Span start = expect(TokKind::lbracket).span;
      auto element = parse_type_ref();
      Span end = expect(TokKind::rbracket, D::LANG_SYNTAX_EXPECTED_CLOSING_LIST).span;
      auto type = make<ListTypeRef>(start.to(end));
      type->element = element;
      if (check(TokKind::question)) {
        // A result whose answer is the list.
        const Token* last = &advance();
        if (check(TokKind::ident)) {
          last = &advance();
          type->error = last->text;
        }
        type->span = start.to(last->span);
        type->result = true;
      }
      return type;
    }


    TypeRef* Parser::parse_named_type()
    {
      // A name, perhaps through modules, perhaps given types, perhaps with a
      // unit, perhaps a result `T?` or `T?E`.
      const Token& first = expect(TokKind::ident);
      const Token* name = &first;
      std::string module;
      bool has_module = false;
      while (check(TokKind::dot)) {
        advance();
        if (has_module)
          module += '.';
        module += name->text;
        has_module = true;
        name = &expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_MEMBER);
      }
      Span name_span = name->span;
      std::vector<const TypeExpr*> args;
      if (check(TokKind::lparen) && type_arguments_follow()) {
        // The types a type that takes type parameters is given: `Pair(u8)`.
        advance();
        args.push_back(parse_type_ref());
        while (accept(TokKind::comma) != nullptr)
          args.push_back(parse_type_ref());
        name_span = name_span.to(expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span);
      }
      // The unit before the mark: it belongs to the answer.
      const UnitRef* unit = check(TokKind::unit) ? parse_unit_ref() : nullptr;
      Span last_name = unit != nullptr ? unit->span : name_span;
      auto type = make<TypeRef>(first.span.to(last_name));
      type->name = name->text;
      if (has_module)
        type->module = arena.text(module);
      type->unit = unit;
      type->args = list(args);
      if (const Token* mark = accept(TokKind::question)) {
        const Token* last = mark;
        if (check(TokKind::ident)) {
          last = &advance();
          type->error = last->text;
        }
        type->span = first.span.to(last->span);
        type->result = true;
      }
      return type;
    }


    bool Parser::type_arguments_follow() const noexcept
    {
      // A name followed by parentheses is a call too, so they are looked
      // through: not empty, holding nothing a type could not be made of.
      if (peek().kind == TokKind::rparen)
        return false;
      int depth = 0;
      for (std::size_t ahead = 1;; ++ahead) {
        auto kind = peek(ahead).kind;
        if (kind == TokKind::lparen)
          ++depth;
        else if (kind == TokKind::rparen) {
          if (depth == 0)
            return true;
          --depth;
        } else if (! in_a_type(kind))
          return false;
      }
    }


    TypeDef* Parser::parse_type_definition(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines)
    {
      // `type NAME '=' NAME ':' TYPE (SEP NAME ':' TYPE)*`: `;` makes a
      // product and `|` a sum; one pair is a product.
      Span start = expect(TokKind::kw_type).span;
      const Token& name = expect(TokKind::ident);
      std::vector<Text> params;
      std::vector<Span> places;
      if (accept(TokKind::lparen) != nullptr) {
        do {
          const Token& written = expect(TokKind::ident);
          params.push_back(written.text);
          places.push_back(written.span);
        } while (accept(TokKind::comma) != nullptr);
        expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN);
      }
      auto clauses = parse_clauses();
      expect(TokKind::equals, D::LANG_TYPEDEF_EXPECTED_EQUALS);
      bool braced = accept(TokKind::lbrace) != nullptr;
      bool indented = false;
      if (! braced && check(TokKind::newline) && peek().kind == TokKind::indent) {
        advance();
        advance();
        indented = true;
      }
      std::vector<const Field*> fields;
      std::vector<const Field*> singletons;
      std::optional<TypeKind> kind;
      const Token* separator = nullptr;
      while (true) {
        if (indented)
          skip_newlines();
        if (fields.empty() && singletons.empty() && ends_the_parts(braced, indented)) [[unlikely]] {
          diags.emit(D::LANG_TYPEDEF_NO_PARTS, name.span);
          throw Bail{};
        }
        auto part = parse_field();
        (part->singleton ? singletons : fields).push_back(part);
        // The separator ends the line it is on.
        std::optional<TypeKind> found;
        if (check(TokKind::semicolon))
          found = TypeKind::product;
        else if (check(TokKind::pipe))
          found = TypeKind::sum;
        else
          break;
        const Token& here = advance();
        if (! kind) {
          kind = found;
          separator = &here;
        } else if (*found != *kind) [[unlikely]] {
          diags.emit(D::LANG_TYPEDEF_MIXED_SEPARATORS, here.span,
                     { { "found", here.describe() }, { "first", separator->describe() } })
            .note(D::LANG_TYPEDEF_FIRST_SEPARATOR, separator->span);
          throw Bail{};
        }
      }
      Span end = current().span;
      if (braced)
        end = expect(TokKind::rbrace).span;
      else if (indented) {
        skip_newlines();
        accept(TokKind::dedent);
      }
      if (fields.empty()) [[unlikely]] {
        // Only singletons: a type no value of which holds anything.
        diags.emit(D::LANG_TYPEDEF_NO_PARTS, name.span);
        throw Bail{};
      }
      auto def = make<TypeDef>(start.to(end));
      def->name = name.text;
      def->name_span = name.span;
      def->kind = kind.value_or(TypeKind::product);
      def->fields = list(fields);
      def->singletons = list(singletons);
      def->attrs = attrs;
      def->params = list(params);
      def->param_spans = list(places);
      def->clauses = clauses;
      def->doc = doc;
      def->doc_lines = doc_lines;
      return def;
    }


    bool Parser::ends_the_parts(bool braced, bool indented) const noexcept
    {
      if (braced)
        return check(TokKind::rbrace);
      if (indented)
        return check(TokKind::dedent) || check(TokKind::eof);
      return check(TokKind::newline) || check(TokKind::eof);
    }


    EnumDef* Parser::parse_enum_definition(List<const Attribute*> attrs, MaybeText doc, List<Span> doc_lines)
    {
      // `enum NAME [':' TYPE]` and the names of its values.
      Span start = expect(TokKind::kw_enum).span;
      const Token& name = expect(TokKind::ident);
      const TypeExpr* holder = nullptr;
      bool indented = false;
      if (accept(TokKind::colon) != nullptr) {
        if (check(TokKind::ident))
          holder = parse_type_ref();
        else
          indented = true;
      }
      bool braced = ! indented && accept(TokKind::lbrace) != nullptr;
      if (! braced && ! indented) {
        expect(TokKind::colon, D::LANG_ENUMDEF_EXPECTED_VALUES);
        indented = true;
      }
      if (indented) {
        expect(TokKind::newline);
        expect(TokKind::indent);
      }
      std::vector<const EnumMember*> members;
      while (true) {
        if (indented)
          skip_newlines();
        if (members.empty() && ends_the_parts(braced, indented)) [[unlikely]] {
          diags.emit(D::LANG_ENUMDEF_NO_VALUES, name.span);
          throw Bail{};
        }
        members.push_back(parse_enum_member());
        if (accept(TokKind::semicolon) == nullptr)
          break;
        if (indented)
          skip_newlines();
      }
      Span end = current().span;
      if (braced)
        end = expect(TokKind::rbrace).span;
      else {
        skip_newlines();
        accept(TokKind::dedent);
      }
      auto def = make<EnumDef>(start.to(end));
      def->name = name.text;
      def->name_span = name.span;
      def->members = list(members);
      def->holder = holder;
      def->attrs = attrs;
      def->doc = doc;
      def->doc_lines = doc_lines;
      return def;
    }


    EnumMember* Parser::parse_enum_member()
    {
      // `NAME` or `NAME '=' (NUMBER | NAME)`.
      const Token& written = expect(TokKind::ident);
      auto member = make<EnumMember>(written.span);
      member->name = written.text;
      member->name_span = written.span;
      if (accept(TokKind::equals) == nullptr)
        return member;
      const Token& token = current();
      if (token.kind == TokKind::int_lit) {
        advance();
        auto value = make<IntLit>(token.span);
        value->value = { token.int_value, token.int_too_large };
        if (! token.type_suffix.empty())
          value->type_name = token.type_suffix;
        member->value = value;
      } else if (token.kind == TokKind::ident) {
        advance();
        auto value = make<NameRef>(token.span);
        value->name = token.text;
        member->value = value;
      } else [[unlikely]] {
        diags.emit(D::LANG_ENUMDEF_EXPECTED_VALUE, token.span, { { "found", token.describe() } });
        throw Bail{};
      }
      member->span = written.span.to(token.span);
      return member;
    }


    Field* Parser::parse_field()
    {
      // `['mut'] NAME ':' TYPE`, or `'singleton' NAME ':' TYPE ['=' VALUE]`.
      if (const Token* single = accept(TokKind::kw_singleton)) {
        const Token& name = expect(TokKind::ident);
        expect(TokKind::colon, D::LANG_TYPEDEF_EXPECTED_COLON);
        auto written = parse_type_ref();
        const Expr* value = accept(TokKind::equals) != nullptr ? parse_expression() : nullptr;
        auto field = make<Field>(single->span.to(value != nullptr ? value->span : written->span));
        field->name = name.text;
        field->name_span = name.span;
        field->type = written;
        field->singleton = true;
        field->value = value;
        return field;
      }
      const Token* field_mut = accept(TokKind::kw_mut);
      const Token& name = expect(TokKind::ident);
      expect(TokKind::colon, D::LANG_TYPEDEF_EXPECTED_COLON);
      bool mutable_ = accept(TokKind::kw_mut) != nullptr;
      auto written = writable(parse_type_ref(), mutable_);
      auto field = make<Field>((field_mut != nullptr ? field_mut->span : name.span).to(written->span));
      field->name = name.text;
      field->name_span = name.span;
      field->type = written;
      field->mutable_ = field_mut != nullptr;
      return field;
    }


    List<const Param*> Parser::parse_params()
    {
      std::vector<const Param*> params;
      std::vector<Text> seen;
      if (check(TokKind::rparen))
        return {};
      do {
        // `⁂` before a name: the parameter stands for all the arguments from
        // here on.
        bool several = accept(TokKind::spread) != nullptr;
        const Token& name = expect(TokKind::ident);
        expect(TokKind::colon);
        bool mutable_ = accept(TokKind::kw_mut) != nullptr;
        auto written = writable(parse_type_ref(), mutable_);
        // `in a`: which arena what is handed over was made in.
        std::optional<NameAt> made_in;
        if (accept(TokKind::kw_in) != nullptr) {
          const Token& arena_name = expect(TokKind::ident);
          made_in = NameAt{ arena_name.text, arena_name.span };
        }
        // `← VALUE`: what a caller that says nothing gets.
        const Expr* default_ = nullptr;
        Span end = made_in ? made_in->span : written->span;
        if (accept(TokKind::assign) != nullptr) {
          default_ = parse_expression();
          end = default_->span;
        }
        if (std::ranges::find(seen, name.text) != seen.end()) [[unlikely]]
          diags.emit(D::LANG_FUNCDEF_DUPLICATE_PARAMETER, name.span, { { "name", name.text } });
        else
          seen.push_back(name.text);
        auto param = make<Param>(name.span.to(end));
        param->name = name.text;
        param->type = written;
        param->mutable_ = mutable_;
        param->default_ = default_;
        param->several = several;
        param->made_in = made_in;
        params.push_back(param);
      } while (accept(TokKind::comma) != nullptr);
      return list(params);
    }


    // -- blocks and statements -----------------------------------------------

    const Block* Parser::set_aside(bool quoting_body)
    {
      // Where the body that begins here ends, read off the tokens: a brace and
      // the one closing it, an indent and its dedent, or a body the lexer left
      // as text.  Nothing where it is in none of the shapes.
      std::size_t start = pos;
      const Token& first = current();
      std::size_t at = start + 1;
      BlockStyle style;
      if (first.kind == TokKind::lbrace) {
        int depth = 1;
        while (at < tokens.size() && depth > 0) {
          auto kind = tokens[at].kind;
          if (kind == TokKind::lbrace)
            ++depth;
          else if (kind == TokKind::rbrace)
            --depth;
          else if (kind == TokKind::eof)
            return nullptr;
          ++at;
        }
        style = BlockStyle::explicit_;
      } else if (first.kind == TokKind::colon && at + 1 < tokens.size() && tokens[at].kind == TokKind::newline
                 && tokens[at + 1].kind == TokKind::body) {
        at += 2;
        style = BlockStyle::layout;
      } else if (first.kind == TokKind::colon && at + 1 < tokens.size() && tokens[at].kind == TokKind::newline
                 && tokens[at + 1].kind == TokKind::indent) {
        at += 2;
        int depth = 1;
        while (at < tokens.size() && depth > 0) {
          auto kind = tokens[at].kind;
          if (kind == TokKind::indent)
            ++depth;
          else if (kind == TokKind::dedent)
            --depth;
          else if (kind == TokKind::eof)
            break;
          ++at;
        }
        style = BlockStyle::layout;
      } else
        return nullptr;
      std::size_t end = at;
      if (end <= start + 1 || tokens[end - 1].kind == TokKind::eof)
        return nullptr;
      pos = end;
      auto block = make<Block>(first.span.to(tokens[end - 1].span));
      block->style = style;
      block->job = &arena.hold<BodyJob>(arena, tokens, start, end, path, quoting_body, diags, source);
      deferred.push_back(block);
      return block;
    }


    Block* Parser::parse_body()
    {
      if (check(TokKind::colon))
        return parse_layout_block();
      if (check(TokKind::lbrace))
        return parse_explicit_block();
      diags.emit(D::LANG_FUNCDEF_EXPECTED_BLOCK, current().span);
      throw Bail{};
    }


    std::vector<const Stmt*> Parser::parse_separated()
    {
      // A semicolon separates and never ends: `a;` is two statements, the
      // second empty, which makes a body ending in one produce no value.
      std::vector<const Stmt*> found{ parse_statement() };
      while (check(TokKind::semicolon)) {
        Span semicolon = advance().span;
        if (check(TokKind::semicolon) || ends_a_run(current().kind)) {
          found.push_back(make<EmptyStmt>(semicolon));
          continue;
        }
        found.push_back(parse_statement());
      }
      return found;
    }


    Block* Parser::parse_layout_block()
    {
      // `: NEWLINE INDENT statements DEDENT`.
      Span start = expect(TokKind::colon).span;
      if (! check(TokKind::newline))
        return parse_inline_block(start);
      expect(TokKind::newline);
      expect(TokKind::indent);
      std::vector<const Stmt*> stmts;
      while (! check(TokKind::dedent) && ! check(TokKind::eof)) {
        skip_newlines();
        if (check(TokKind::dedent) || check(TokKind::eof))
          break;
        auto run = parse_separated();
        stmts.insert(stmts.end(), run.begin(), run.end());
        if (check(TokKind::dedent) || check(TokKind::eof))
          continue;
        if (accept(TokKind::newline) == nullptr && ! ends_with_a_block(stmts.back()))
          // A statement ending with an indented block has taken the end of its
          // own last line already.
          expect(TokKind::newline);
      }
      Span end = current().span;
      accept(TokKind::dedent);
      auto block = make<Block>(start.to(end));
      block->style = BlockStyle::layout;
      block->statements = list(stmts);
      return block;
    }


    Block* Parser::parse_inline_block(Span start)
    {
      // `: statement` on one line, closed by its end; such a block may not
      // open another, which would end where it does.
      if (inline_) [[unlikely]] {
        diags.emit(D::LANG_SYNTAX_INLINE_INSIDE_INLINE, current().span);
        throw Bail{};
      }
      inline_ = true;
      std::vector<const Stmt*> stmts;
      try {
        stmts = parse_separated();
      } catch (...) {
        inline_ = false;
        throw;
      }
      inline_ = false;
      auto block = make<Block>(start.to(current().span));
      block->style = BlockStyle::inline_;
      block->statements = list(stmts);
      return block;
    }


    Block* Parser::parse_explicit_block()
    {
      // `{ statement ; ... }`: no ends of lines inside braces.
      Span start = expect(TokKind::lbrace).span;
      std::vector<const Stmt*> stmts;
      if (! check(TokKind::rbrace) && ! check(TokKind::eof)) {
        stmts = parse_separated();
        if (! check(TokKind::rbrace) && ! check(TokKind::eof)) [[unlikely]] {
          diags.emit(D::LANG_SYNTAX_MIXED_BLOCK_STYLE, current().span);
          throw Bail{};
        }
      }
      Span end = current().span;
      accept(TokKind::rbrace);
      auto block = make<Block>(start.to(end));
      block->style = BlockStyle::explicit_;
      block->statements = list(stmts);
      return block;
    }


    Stmt* Parser::parse_statement()
    {
      auto attrs = check(TokKind::at_lbracket) ? parse_attributes() : List<const Attribute*>{};
      auto stmt = parse_bare_statement();
      if (! attrs.empty())
        stmt->attrs = attrs;
      return stmt;
    }


    // A statement made of an expression.
    Stmt* expression_statement(SyntaxArena& arena, const Expr* value)
    {
      auto stmt = arena.make<ExprStmt>(value->span);
      stmt->value = value;
      return stmt;
    }


    Stmt* Parser::parse_bare_statement()
    {
      if (check(TokKind::kw_unit))
        return parse_unit_definition();
      if (check(TokKind::kw_let)) {
        auto found = parse_variable();
        if (auto import = as<ModuleImport>(found)) [[unlikely]] {
          // A module is the program's, not one function's.
          diags.emit(D::LANG_IMPORT_INSIDE_FUNCTION, import->span, { { "name", import->name } });
          throw Bail{};
        }
        return static_cast<VarDef*>(found);
      }
      if (check(TokKind::kw_match))
        return expression_statement(arena, parse_match());
      if (check(TokKind::kw_if) || begins_an_arm(TokKind::kw_if))
        return expression_statement(arena, parse_if());
      if (check(TokKind::kw_comptime) && peek().kind == TokKind::kw_foreach) {
        Span start = advance().span;
        advance();
        auto label = parse_label();
        return expression_statement(arena, parse_iteration(start, "foreach", label, true));
      }
      if (check(TokKind::kw_while) || check(TokKind::kw_unless))
        return expression_statement(arena, parse_while());
      if (check(TokKind::kw_foreach)) {
        Span start = advance().span;
        auto label = parse_label();
        return expression_statement(arena, parse_iteration(start, "foreach", label));
      }
      if (check(TokKind::kw_break) || check(TokKind::kw_continue))
        return parse_jump();
      if (check(TokKind::kw_defer)) {
        // One statement after it, on the same line.
        Span start = advance().span;
        auto later = parse_bare_statement();
        auto defer = make<Defer>(start.to(later->span));
        defer->stmt = later;
        return defer;
      }
      if (check(TokKind::kw_yield)) {
        // One value handed out of a generator, or with the doubled arrow every
        // value something else gives.
        Span start = advance().span;
        bool delegates = accept(TokKind::iter) != nullptr;
        auto value = parse_expression();
        auto yield = make<Yield>(start.to(value->span));
        yield->value = value;
        yield->delegates = delegates;
        return yield;
      }
      if (check(TokKind::kw_return)) {
        Span start = advance().span;
        auto ret = make<ReturnStmt>(start);
        ret->explicit_ = true;
        if (check(TokKind::newline) || check(TokKind::semicolon) || check(TokKind::rbrace) || check(TokKind::dedent))
          return ret;
        ret->value = parse_expression();
        ret->span = start.to(ret->value->span);
        return ret;
      }
      auto value = parse_expression();
      if (check(TokKind::comma) && is<NameRef>(value))
        return parse_unpacking(static_cast<const NameRef*>(value));
      if (check(TokKind::assign))
        return parse_assignment(value);
      if (joins_and_assigns()) {
        // The join and the assignment it is short for, written once.
        advance();
        advance();
        if (! is<NameRef>(value) && ! is<Element>(value) && ! is<Member>(value) && ! is<Deref>(value)
            && ! is<Hole>(value)) [[unlikely]] {
          diags.emit(D::LANG_ASSIGN_NOT_A_PLACE, value->span);
          throw Bail{};
        }
        auto more = parse_expression();
        auto join = make<JoinAssign>(value->span.to(more->span));
        join->target = value;
        join->value = more;
        return join;
      }
      return expression_statement(arena, value);
    }


    bool Parser::joins_and_assigns() const noexcept
    {
      // `⧺←` written together is one mark.
      if (! check(TokKind::concat))
        return false;
      const Token& after = peek();
      return after.kind == TokKind::assign && after.span.start == current().span.end;
    }


    Stmt* Parser::parse_unpacking(const NameRef* first)
    {
      // `NAME, NAME ← VALUE`, which takes a tuple apart.
      std::vector<NameAt> more;
      while (accept(TokKind::comma) != nullptr) {
        const Token& written = expect(TokKind::ident);
        more.push_back({ written.text, written.span });
      }
      expect(TokKind::assign);
      auto value = parse_expression();
      auto assign = make<AssignStmt>(first->span.to(value->span));
      assign->name = first->name;
      assign->name_span = first->span;
      assign->value = value;
      assign->more = list(more);
      return assign;
    }


    Expr* Parser::parse_while()
    {
      // `while COND BODY` and `unless COND BODY`, which runs until it holds.
      bool until = check(TokKind::kw_unless);
      Span start = advance().span;
      auto label = parse_label();
      if (binds_a_value()) {
        if (until)
          diags.emit(D::LANG_UNLESS_BINDS_A_VALUE, start);
        return parse_iteration(start, "while", label);
      }
      auto condition = parse_expression();
      auto body = parse_body();
      auto otherwise = parse_otherwise();
      auto loop = make<While>(start.to((otherwise != nullptr ? otherwise : body)->span));
      loop->condition = condition;
      loop->body = body;
      loop->label = label;
      loop->alternative = otherwise;
      loop->until = until;
      return loop;
    }


    const Block* Parser::parse_otherwise()
    {
      // A loop's `else`: where it ran out.
      if (! check(TokKind::kw_else))
        return nullptr;
      advance();
      return parse_body();
    }


    const Label* Parser::parse_label()
    {
      const Token* mark = accept(TokKind::label);
      if (mark == nullptr)
        return nullptr;
      const Token& name = expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_LABEL_NAME);
      auto label = make<Label>(mark->span.to(name.span));
      label->name = name.text;
      return label;
    }


    Stmt* Parser::parse_jump()
    {
      // `break §name` or `continue §name`: the name is written every time.
      const Token& keyword = advance();
      auto label = parse_label();
      if (label == nullptr) [[unlikely]] {
        diags.emit(D::LANG_SYNTAX_JUMP_WITHOUT_A_LABEL, keyword.span, { { "keyword", keyword.text } });
        throw Bail{};
      }
      Span span = keyword.span.to(label->span);
      if (keyword.kind != TokKind::kw_break) {
        auto jump = make<Continue>(span);
        jump->label = label;
        return jump;
      }
      auto jump = make<Break>(span);
      jump->label = label;
      if (! ends_a_statement()) {
        jump->value = parse_expression();
        jump->span = span.to(jump->value->span);
      }
      return jump;
    }


    bool Parser::ends_a_statement() const noexcept
    {
      return check(TokKind::newline) || check(TokKind::semicolon) || check(TokKind::rbrace) || check(TokKind::dedent)
             || check(TokKind::eof);
    }


    bool Parser::binds_a_value() const noexcept
    {
      // A name and a comma begin nothing else; a name and a colon only a bare
      // condition, whose colon then ends its line.
      if (! check(TokKind::ident))
        return false;
      if (peek().kind == TokKind::comma)
        return true;
      return peek().kind == TokKind::colon && peek(2).kind != TokKind::newline;
    }


    Expr* Parser::parse_comptime()
    {
      if (peek().kind == TokKind::kw_if)
        return parse_if();
      Span start = advance().span;
      expect(TokKind::kw_foreach);
      auto label = parse_label();
      return parse_iteration(start, "foreach", label, true);
    }


    ForEach* Parser::parse_iteration(Span start, Text keyword, const Label* label, bool comptime)
    {
      // `NAMES ':' [TYPE] '=' EXPR BODY`, which is `let`'s shape.
      std::vector<Text> marks{ reference_mark(keyword) };
      const Token& name = expect(TokKind::ident);
      std::vector<NameAt> more;
      while (accept(TokKind::comma) != nullptr) {
        marks.push_back(reference_mark(keyword));
        const Token& written = expect(TokKind::ident);
        more.push_back({ written.text, written.span });
      }
      const TypeExpr* declared = nullptr;
      expect(TokKind::colon, D::LANG_VARDEF_EXPECTED_COLON);
      if (begins_a_type())
        declared = parse_type_ref();
      if (accept(TokKind::equals) == nullptr) [[unlikely]] {
        diags.emit(D::LANG_VARDEF_MISSING_INITIALIZER, name.span, { { "name", name.text } });
        throw Bail{};
      }
      auto iterable = parse_expression();
      auto body = parse_body();
      auto otherwise = parse_otherwise();
      auto walk = make<ForEach>(start.to((otherwise != nullptr ? otherwise : body)->span));
      walk->name = name.text;
      walk->name_span = name.span;
      walk->type = declared;
      walk->iterable = iterable;
      walk->body = body;
      walk->more = list(more);
      walk->keyword = keyword;
      walk->label = label;
      walk->alternative = otherwise;
      walk->comptime = comptime;
      if (std::ranges::any_of(marks, [](Text m) { return ! m.empty(); }))
        walk->marks = list(marks);
      return walk;
    }


    Text Parser::reference_mark(Text keyword)
    {
      // `&` or `&mut` before a name `foreach` binds.
      if (keyword != "foreach" || accept(TokKind::ampersand) == nullptr)
        return "";
      return accept(TokKind::kw_mut) != nullptr ? "&mut" : "&";
    }


    If* Parser::parse_if()
    {
      // `[comptime] if COND BODY`, its `elif`s and its `else`.
      Span start = current().span;
      std::vector<const IfArm*> arms{ parse_if_arm(takes_comptime(TokKind::kw_if)) };
      while (begins_an_arm(TokKind::kw_elif))
        arms.push_back(parse_if_arm(takes_comptime(TokKind::kw_elif)));
      if (check(TokKind::kw_else)) {
        advance();
        auto body = parse_body();
        auto arm = make<IfArm>(body->span);
        arm->body = body;
        arms.push_back(arm);
        if (begins_an_arm(TokKind::kw_elif) || check(TokKind::kw_else)) [[unlikely]] {
          diags.emit(D::LANG_IF_ELSE_IS_LAST, current().span);
          throw Bail{};
        }
      }
      auto found = make<If>(start.to(arms.back()->body->span));
      found->arms = list(arms);
      return found;
    }


    bool Parser::begins_an_arm(TokKind keyword) const noexcept
    {
      return check(keyword) || (check(TokKind::kw_comptime) && peek().kind == keyword);
    }


    bool Parser::takes_comptime(TokKind keyword)
    {
      bool found = accept(TokKind::kw_comptime) != nullptr;
      expect(keyword);
      return found;
    }


    IfArm* Parser::parse_if_arm(bool comptime)
    {
      auto condition = parse_expression();
      auto body = parse_body();
      auto arm = make<IfArm>(condition->span.to(body->span));
      arm->condition = condition;
      arm->body = body;
      arm->comptime = comptime;
      return arm;
    }


    Match* Parser::parse_match()
    {
      // `match EXPR` and its arms, indented under a colon or inside braces.
      Span start = expect(TokKind::kw_match).span;
      auto subject = parse_expression();
      std::vector<const MatchArm*> arms;
      Span end;
      if (check(TokKind::lbrace)) {
        advance();
        while (! check(TokKind::rbrace) && ! check(TokKind::eof))
          arms.push_back(parse_arm());
        end = expect(TokKind::rbrace).span;
      } else {
        expect(TokKind::colon, D::LANG_MATCH_EXPECTED_ARMS);
        expect(TokKind::newline);
        expect(TokKind::indent);
        while (! check(TokKind::dedent) && ! check(TokKind::eof)) {
          skip_newlines();
          if (check(TokKind::dedent) || check(TokKind::eof))
            break;
          arms.push_back(parse_arm());
          skip_newlines();
        }
        end = current().span;
        accept(TokKind::dedent);
      }
      auto found = make<Match>(start.to(end));
      found->subject = subject;
      found->arms = list(arms);
      return found;
    }


    MatchArm* Parser::parse_arm()
    {
      auto pattern = parse_pattern();
      auto body = parse_body();
      auto arm = make<MatchArm>(pattern->span.to(body->span));
      arm->pattern = pattern;
      arm->body = body;
      return arm;
    }


    Pattern* Parser::parse_pattern()
    {
      // `TYPE`, `TYPE(NAME)`, `⊥`, `⊥(NAME)` or `_`.
      if (check(TokKind::ident) && current().text == wildcard_name) {
        const Token& token = advance();
        auto pattern = make<Pattern>(token.span);
        pattern->wildcard = true;
        if (accept(TokKind::lparen) == nullptr)
          return pattern;
        // `_(name)` is reported where an arm's meaning is checked.
        const Token& name = expect(TokKind::ident);
        Span end = expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span;
        pattern->span = token.span.to(end);
        pattern->name = name.text;
        pattern->name_span = name.span;
        return pattern;
      }
      const Token* bottom = accept(TokKind::bottom);
      TypeExpr* written = bottom != nullptr ? nullptr : parse_type_ref();
      Span start = bottom != nullptr ? bottom->span : written->span;
      if (auto named = static_cast<TypeRef*>(is<TypeRef>(written) ? written : nullptr);
          named != nullptr && named->args.size() == 1 && ! check(TokKind::lparen)) {
        // `u8(v)` reads as a type given `v` until nothing follows it: one name
        // in parentheses is the name the value is bound to.
        auto inner = as<TypeRef>(named->args[0]);
        if (inner != nullptr && ! inner->module && inner->args.empty() && ! inner->result && inner->unit == nullptr) {
          named->args = {};
          auto pattern = make<Pattern>(named->span);
          pattern->type = named;
          pattern->name = inner->name;
          pattern->name_span = inner->span;
          return pattern;
        }
      }
      auto pattern = make<Pattern>(start);
      pattern->type = written;
      if (accept(TokKind::lparen) == nullptr)
        return pattern;
      const Token& name = expect(TokKind::ident);
      Span end = expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span;
      pattern->span = start.to(end);
      pattern->name = name.text;
      pattern->name_span = name.span;
      return pattern;
    }


    Stmt* Parser::parse_assignment(const Expr* target)
    {
      // `TARGET ← VALUE`, where the target is a name, a field, an entry, an
      // element, what a reference names, or a hole in a macro's template.
      expect(TokKind::assign);
      auto value = parse_expression();
      Span span = target->span.to(value->span);
      if (auto name = as<NameRef>(target)) {
        auto assign = make<AssignStmt>(span);
        assign->name = name->name;
        assign->name_span = name->span;
        assign->value = value;
        return assign;
      }
      if (auto index = as<Index>(target)) {
        auto assign = make<EntryAssign>(span);
        assign->base = index->base;
        assign->key = index->key;
        assign->value = value;
        return assign;
      }
      if (auto element = as<Element>(target)) {
        auto assign = make<ElementAssign>(span);
        assign->base = element->base;
        assign->indices = element->indices;
        assign->value = value;
        return assign;
      }
      if (auto deref = as<Deref>(target)) {
        auto assign = make<DerefAssign>(span);
        assign->target = deref->operand;
        assign->value = value;
        return assign;
      }
      if (auto member = as<Member>(target)) {
        auto assign = make<MemberAssign>(span);
        assign->base = member->base;
        assign->name = member->name;
        assign->name_span = member->name_span;
        assign->value = value;
        return assign;
      }
      if (is<Hole>(target)) {
        auto assign = make<HoleAssign>(span);
        assign->target = target;
        assign->value = value;
        return assign;
      }
      diags.emit(D::LANG_ASSIGN_NOT_A_PLACE, target->span);
      throw Bail{};
    }


    // -- expressions ---------------------------------------------------------

    Expr* Parser::parse_expression(int minimum)
    {
      // Precedence climbing: one function for every level.  A range is the
      // one thing not in the table: it takes two ends or three.
      Expr* left = parse_unary();
      while (true) {
        if (check(TokKind::range) && range_precedence >= minimum) {
          left = parse_range(left);
          continue;
        }
        if (check(TokKind::op)) {
          if (fresh_precedence < minimum)
            return left;
          const Token& token = advance();
          auto right = parse_expression(fresh_precedence + 1);
          auto fresh = make<Fresh>(left->span.to(right->span));
          fresh->glyph = token.text;
          fresh->operands = list(std::vector<const Expr*>{ left, right });
          left = fresh;
          continue;
        }
        const Operator* op = binary_operator(current().kind);
        if (op != nullptr && joins_and_assigns())
          // `l ⧺← more` is a statement and not a join with nothing after it.
          return left;
        if (op == nullptr || op->precedence < minimum) {
          if (minimum == 0 && check(TokKind::kw_in)) {
            // `EXPR in NAME`: which arena the expression takes room from, at
            // the loosest level only.
            auto where = parse_arena();
            auto allocated = make<Allocated>(left->span.to(where->span));
            allocated->value = left;
            allocated->arena = where;
            left = allocated;
            continue;
          }
          return left;
        }
        advance();
        // A left-associative operator takes nothing of its own level on the
        // right.
        auto right = parse_expression(op->precedence + (op->right_associative ? 0 : 1));
        auto binary = make<Binary>(left->span.to(right->span));
        binary->op = op->op;
        binary->left = left;
        binary->right = right;
        left = binary;
        if (op->non_associative) {
          const Operator* following = binary_operator(current().kind);
          if (following != nullptr && following->precedence == op->precedence) [[unlikely]] {
            diags.emit(D::LANG_SYNTAX_NOT_ASSOCIATIVE, current().span,
                       { { "first", describe(op->op) }, { "second", describe(following->op) } });
            throw Bail{};
          }
        }
      }
    }


    Range* Parser::parse_range(const Expr* first)
    {
      // `A…B` or `A…B…C`, each end parsed one level in.
      std::vector<const Expr*> ends{ first };
      while (accept(TokKind::range) != nullptr) {
        ends.push_back(parse_expression(range_precedence + 1));
        if (ends.size() > 3) [[unlikely]] {
          diags.emit(D::LANG_SYNTAX_RANGE_TOO_MANY_ENDS, first->span.to(ends.back()->span),
                     { { "count", ends.size() } });
          throw Bail{};
        }
      }
      auto range = make<Range>(first->span.to(ends.back()->span));
      range->start = ends[0];
      range->stop = ends[1];
      range->step = ends.size() > 2 ? ends[2] : nullptr;
      return range;
    }


    Expr* Parser::parse_unary()
    {
      if (check(TokKind::op)) {
        // A glyph the language gives no meaning, before one operand.
        const Token& token = advance();
        auto operand = parse_unary();
        auto fresh = make<Fresh>(token.span.to(operand->span));
        fresh->glyph = token.text;
        fresh->operands = list(std::vector<const Expr*>{ operand });
        return fresh;
      }
      if (check(TokKind::ampersand)) {
        // `&` before an operand asks for a reference to the place it names.
        const Token& token = advance();
        bool mutable_ = accept(TokKind::kw_mut) != nullptr;
        auto operand = parse_unary();
        auto address = make<AddressOf>(token.span.to(operand->span));
        address->operand = operand;
        address->mutable_ = mutable_;
        return address;
      }
      if (check(TokKind::take)) {
        // A key of a collection, an element of a list, or what a cursor is at.
        const Token& token = advance();
        auto operand = parse_unary();
        Span span = token.span.to(operand->span);
        if (auto index = as<Index>(operand)) {
          auto take = make<Take>(span);
          take->base = index->base;
          take->key = index->key;
          return take;
        }
        if (auto element = as<Element>(operand)) {
          if (element->indices.size() != 1) [[unlikely]] {
            diags.emit(D::LANG_TAKE_NOT_AN_ENTRY, span);
            return operand;
          }
          auto take = make<TakeAt>(span);
          take->base = element->base;
          take->index = element->indices[0];
          return take;
        }
        auto take = make<TakeThrough>(span);
        take->operand = operand;
        return take;
      }
      auto op = unary_operator(current().kind);
      if (! op)
        return parse_primary();
      const Token& token = advance();
      auto operand = parse_unary();
      auto unary = make<Unary>(token.span.to(operand->span));
      unary->op = *op;
      unary->operand = operand;
      return unary;
    }


    Expr* Parser::parse_primary()
    {
      // An operand, with whatever is written after it; nothing after something
      // that ends with a block of its own.
      Expr* found = parse_atom();
      if (trailing_match(found))
        return found;
      while (true) {
        if (check(TokKind::dot)) {
          advance();
          const Token& name = expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_MEMBER);
          auto member = make<Member>(found->span.to(name.span));
          member->base = found;
          member->name = name.text;
          member->name_span = name.span;
          found = member;
          continue;
        }
        if (check(TokKind::lparen)) {
          found = parse_call(found);
          continue;
        }
        if (check(TokKind::set_open)) {
          advance();
          auto key = parse_expression();
          Span end = expect(TokKind::set_close, D::LANG_SYNTAX_EXPECTED_CLOSING_SET).span;
          auto index = make<Index>(found->span.to(end));
          index->base = found;
          index->key = key;
          found = index;
          continue;
        }
        if (check(TokKind::array_open)) {
          advance();
          std::vector<const Expr*> indices{ parse_expression() };
          while (accept(TokKind::comma) != nullptr)
            indices.push_back(parse_expression());
          Span end = expect(TokKind::array_close, D::LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span;
          auto element = make<Element>(found->span.to(end));
          element->base = found;
          element->indices = list(indices);
          found = element;
          continue;
        }
        if (check(TokKind::lift_open) && (is<NameRef>(found) || is<Member>(found))) {
          // A macro invoked, by a bare name or through the module that wrote it.
          auto arguments = parse_quote();
          auto invoke = make<Invoke>(found->span.to(arguments->span));
          invoke->arguments = arguments;
          if (auto member = as<Member>(found)) {
            if (auto base = as<NameRef>(member->base))
              invoke->through = base->name;
            else
              diags.emit(D::LANG_IMPORT_NOT_A_MODULE, member->span, { { "name", "an expression" } });
            invoke->name = member->name;
            invoke->name_span = member->name_span;
          } else {
            invoke->name = static_cast<const NameRef*>(found)->name;
            invoke->name_span = found->span;
          }
          found = invoke;
          continue;
        }
        if (check(TokKind::open_op)) {
          // A pair of brackets the language gives no meaning, after what it is
          // applied to; whatever closing bracket arrives closes it.
          const Token& opener = advance();
          std::vector<const Expr*> operands{ found, parse_expression() };
          while (accept(TokKind::comma) != nullptr)
            operands.push_back(parse_expression());
          const Token& closer = expect(TokKind::close_op);
          auto fresh = make<Fresh>(found->span.to(closer.span));
          fresh->glyph = arena.text(std::string(opener.text) + std::string(closer.text));
          fresh->operands = list(operands);
          found = fresh;
          continue;
        }
        if (check(TokKind::each)) {
          Span end = advance().span;
          auto each = make<Each>(found->span.to(end));
          each->operand = found;
          found = each;
          continue;
        }
        if (check(TokKind::deref)) {
          Span end = advance().span;
          auto deref = make<Deref>(found->span.to(end));
          deref->operand = found;
          found = deref;
          continue;
        }
        if (check(TokKind::exponent)) {
          // A number written raised: a power whose exponent is written down.
          const Token& raised = advance();
          auto power = make<Raised>(found->span.to(raised.span));
          power->base = found;
          power->exponent = { raised.int_value, raised.int_too_large };
          found = power;
          continue;
        }
        if (check(TokKind::question)) {
          const Token& mark = advance();
          auto attempt = make<Try>(found->span.to(mark.span));
          attempt->operand = found;
          found = attempt;
          continue;
        }
        return found;
      }
    }


    Expr* Parser::parse_call(const Expr* callee)
    {
      // Positional arguments, `⁂` spreading a tuple, `.name ← VALUE` naming a
      // parameter.
      expect(TokKind::lparen);
      std::vector<const Expr*> args;
      if (! check(TokKind::rparen))
        do
          args.push_back(check(TokKind::dot) ? parse_named() : parse_spreadable());
        while (accept(TokKind::comma) != nullptr);
      Span end = expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN).span;
      if (auto name = as<NameRef>(callee); name != nullptr && name->name == await_name && args.size() == 1
                                           && ! is<Named>(args[0]) && ! is<Spread>(args[0])) {
        // `yield` by another name, read as one so that everything asking where
        // a coroutine stops finds it.
        auto yield = make<YieldValue>(callee->span.to(end));
        yield->value = args[0];
        return yield;
      }
      auto call = make<Call>(callee->span.to(end));
      call->callee = callee;
      call->args = list(args);
      return call;
    }


    Expr* Parser::parse_named()
    {
      Span start = expect(TokKind::dot).span;
      const Token& name = expect(TokKind::ident, D::LANG_SYNTAX_EXPECTED_PARAMETER);
      expect(TokKind::assign, D::LANG_SYNTAX_EXPECTED_NAMED_VALUE);
      auto value = parse_expression();
      auto named = make<Named>(start.to(value->span));
      named->name = name.text;
      named->name_span = name.span;
      named->value = value;
      return named;
    }


    Expr* Parser::parse_spreadable()
    {
      const Token* mark = accept(TokKind::spread);
      auto written = parse_expression();
      if (mark == nullptr)
        return written;
      auto spread = make<Spread>(mark->span.to(written->span));
      spread->operand = written;
      return spread;
    }


    Expr* Parser::parse_tuple()
    {
      Span start = expect(TokKind::tuple_open).span;
      std::vector<const Expr*> members{ parse_spreadable() };
      while (accept(TokKind::comma) != nullptr)
        members.push_back(parse_spreadable());
      Span end = expect(TokKind::tuple_close, D::LANG_SYNTAX_EXPECTED_CLOSING_TUPLE).span;
      auto tuple = make<TupleLit>(start.to(end));
      tuple->members = list(members);
      return tuple;
    }


    Expr* Parser::parse_collection()
    {
      // `⸨a, b⸩` or `⸨k: v, k: v⸩`, decided by the first entry.
      Span start = expect(TokKind::set_open).span;
      if (check(TokKind::set_close)) {
        Span end = advance().span;
        auto where = parse_arena();
        auto set = make<SetLit>(start.to(where != nullptr ? where->span : end));
        set->arena = where;
        return set;
      }
      auto first = parse_spreadable();
      if (is<Spread>(first) || accept(TokKind::colon) == nullptr) {
        std::vector<const Expr*> elements{ first };
        while (accept(TokKind::comma) != nullptr)
          elements.push_back(parse_spreadable());
        Span end = expect(TokKind::set_close, D::LANG_SYNTAX_EXPECTED_CLOSING_SET).span;
        auto where = parse_arena();
        auto set = make<SetLit>(start.to(where != nullptr ? where->span : end));
        set->elements = list(elements);
        set->arena = where;
        return set;
      }
      std::vector<ExprPair> entries{ { first, parse_expression() } };
      while (accept(TokKind::comma) != nullptr) {
        auto key = parse_expression();
        expect(TokKind::colon, D::LANG_SYNTAX_EXPECTED_ENTRY_VALUE);
        entries.push_back({ key, parse_expression() });
      }
      Span end = expect(TokKind::set_close, D::LANG_SYNTAX_EXPECTED_CLOSING_SET).span;
      auto where = parse_arena();
      auto dict = make<DictLit>(start.to(where != nullptr ? where->span : end));
      dict->entries = list(entries);
      dict->arena = where;
      return dict;
    }


    Expr* Parser::parse_arena()
    {
      // `'in' NAME`: a name and not an expression, or a hole in a macro.
      if (accept(TokKind::kw_in) == nullptr)
        return nullptr;
      if (quoting && check(TokKind::dollar))
        return parse_atom();
      const Token& written = expect(TokKind::ident);
      auto name = make<NameRef>(written.span);
      name->name = written.text;
      return name;
    }


    ArrayLit* Parser::parse_array()
    {
      Span start = expect(TokKind::array_open).span;
      std::vector<const Expr*> elements;
      if (! check(TokKind::array_close)) {
        elements.push_back(parse_spreadable());
        while (accept(TokKind::comma) != nullptr)
          elements.push_back(parse_spreadable());
      }
      Span end = expect(TokKind::array_close, D::LANG_SYNTAX_EXPECTED_CLOSING_ARRAY).span;
      auto array = make<ArrayLit>(start.to(end));
      array->elements = list(elements);
      return array;
    }


    ListLit* Parser::parse_list()
    {
      Span start = expect(TokKind::lbracket).span;
      std::vector<const Expr*> elements;
      if (! check(TokKind::rbracket)) {
        elements.push_back(parse_spreadable());
        while (accept(TokKind::comma) != nullptr)
          elements.push_back(parse_spreadable());
      }
      Span end = expect(TokKind::rbracket, D::LANG_SYNTAX_EXPECTED_CLOSING_LIST).span;
      auto found = make<ListLit>(start.to(end));
      found->elements = list(elements);
      return found;
    }


    Expr* Parser::parse_lift()
    {
      // `⌜TYPE⌝` or `⌜EXPRESSION⌝`: a type is tried first and kept where it
      // reaches the closing mark.
      std::size_t mark = pos;
      Span start = expect(TokKind::lift_open).span;
      std::size_t after = pos;
      const TypeExpr* written = nullptr;
      if (begins_a_type()) {
        written = parse_type_ref();
        if (! check(TokKind::lift_close)) {
          written = nullptr;
          pos = after;
        }
      }
      if (written == nullptr && quoting) {
        // In the body of something that runs while the compiler does, marks
        // holding no type hold a piece of the program.
        pos = mark;
        return parse_quote();
      }
      const Expr* value = written != nullptr ? nullptr : parse_expression();
      const Token& end = expect(TokKind::lift_close, D::LANG_SYNTAX_EXPECTED_CLOSING_LIFT);
      auto lifted = make<Lifted>(start.to(end.span));
      lifted->written = written;
      lifted->value = value;
      return lifted;
    }


    Expr* Parser::parse_atom()
    {
      const Token& token = current();
      switch (token.kind) {
      case TokKind::kw_yield: {
        // A value handed out of a coroutine where what it takes back is wanted.
        Span start = advance().span;
        bool delegates = accept(TokKind::iter) != nullptr;
        auto value = parse_expression();
        auto yield = make<YieldValue>(start.to(value->span));
        yield->value = value;
        yield->delegates = delegates;
        return yield;
      }
      case TokKind::dollar: {
        // A hole, written only inside a macro's pattern or template.
        advance();
        if (check(TokKind::lparen)) {
          advance();
          auto written = parse_expression();
          const Token& end = expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN);
          auto hole = make<Hole>(token.span.to(end.span));
          hole->value = written;
          return hole;
        }
        const Token& name = expect(TokKind::ident);
        auto value = make<NameRef>(name.span);
        value->name = name.text;
        auto hole = make<Hole>(token.span.to(name.span));
        hole->name = name.text;
        hole->value = value;
        return hole;
      }
      case TokKind::lambda:
        return parse_lambda();
      case TokKind::at_lbracket: {
        // What is said about a lambda is said before it.
        auto attrs = parse_attributes();
        return parse_lambda(attrs);
      }
      case TokKind::lparen: {
        advance();
        auto inner = parse_expression();
        expect(TokKind::rparen, D::LANG_SYNTAX_EXPECTED_CLOSING_PAREN);
        return inner;
      }
      case TokKind::bottom: {
        // `⊥` and `⊥ VALUE`, read the way `return` is.
        advance();
        auto failure = make<Failure>(token.span);
        if (begins_an_expression(current().kind)) {
          failure->value = parse_expression();
          failure->span = token.span.to(failure->value->span);
        }
        return failure;
      }
      case TokKind::lift_open:
        return parse_lift();
      case TokKind::int_lit: {
        advance();
        auto lit = make<IntLit>(token.span);
        lit->value = { token.int_value, token.int_too_large };
        if (! token.type_suffix.empty())
          lit->type_name = token.type_suffix;
        return lit;
      }
      case TokKind::float_lit: {
        advance();
        auto lit = make<FloatLit>(token.span);
        lit->value = token.float_value;
        if (! token.type_suffix.empty())
          lit->type_name = token.type_suffix;
        return lit;
      }
      case TokKind::string_lit: {
        advance();
        auto lit = make<StringLit>(token.span);
        lit->value = arena.text(token.str_value);
        return lit;
      }
      case TokKind::char_lit: {
        advance();
        auto lit = make<CharLit>(token.span);
        lit->value = { token.int_value, false };
        return lit;
      }
      case TokKind::kw_true:
      case TokKind::kw_false: {
        advance();
        auto lit = make<BoolLit>(token.span);
        lit->value = token.kind == TokKind::kw_true;
        return lit;
      }
      case TokKind::tuple_open:
        return parse_tuple();
      case TokKind::set_open:
        return parse_collection();
      case TokKind::lbracket:
        return parse_list();
      case TokKind::array_open:
        return parse_array();
      case TokKind::spread:
        // Said here, because what is wrong is where the glyph stands.
        diags.emit(D::LANG_SYNTAX_SPREAD_OUTSIDE_A_CALL, token.span);
        throw Bail{};
      case TokKind::kw_match:
        return parse_match();
      case TokKind::kw_if:
        return parse_if();
      case TokKind::kw_comptime:
        return parse_comptime();
      case TokKind::kw_while:
      case TokKind::kw_unless:
        return parse_while();
      case TokKind::kw_foreach: {
        Span start = advance().span;
        auto label = parse_label();
        return parse_iteration(start, "foreach", label);
      }
      case TokKind::ident: {
        advance();
        auto name = make<NameRef>(token.span);
        name->name = token.text;
        return name;
      }
      default:
        diags.emit(D::LANG_SYNTAX_UNEXPECTED_TOKEN, token.span,
                   { { "expected", "an expression" }, { "found", token.describe() } });
        throw Bail{};
      }
    }

  } // anonymous namespace


  const ast::SourceUnit* parse(ast::SyntaxArena& arena, std::vector<Token> tokens, std::string_view path,
                               DiagEngine& diags, bool defer, const SourceFile* source)
  {
    const auto& kept = arena.keep(std::move(tokens));
    Parser parser(arena, kept, path, diags, true, source);
    auto unit = parser.parse_unit();
    if (! defer)
      for (auto block : parser.deferred)
        (void) block->stmts();
    return unit;
  }

} // namespace pl4g
