// The syntax tree.
//
// One struct per kind of node, named and laid out as pypl4g's dataclasses are,
// so that a tree here and a tree there can be compared field by field (the
// dumper in front/astdump does that by reflection).  A member whose name is a
// C++ keyword carries a trailing underscore, which the dumper drops.
//
// Every node lives in a `SyntaxArena` and is never freed on its own; children
// are pointers, sequences are spans into the arena.  A node is built by the
// parser, filled in field by field, and read-only once the parser hands it on.
#pragma once

#include <cstdint>
#include <deque>
#include <memory>
#include <memory_resource>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "front/token.hh"
#include "source/location.hh"
#include "util/reflect.hh"


namespace pl4g::ast {

  // A member the dumper leaves out: bookkeeping of this implementation that
  // pypl4g's tree has no field for.
  struct Hidden {};
  inline constexpr Hidden hidden{};

  // An integer as the source wrote it: pypl4g's are unbounded, and one beyond
  // 127 bits says so rather than being cut short.
  struct Integer {
    __int128 value = 0;
    bool too_large = false;
  };

  // A name and where it was written.
  struct NameAt {
    std::string_view name;
    Span span;
  };

  template<typename T>
  using List = std::span<const T>;

  using Text = std::string_view;
  using MaybeText = std::optional<std::string_view>;


  // Which kind a node is, one enumerator per struct below, named as it is.
  enum class NodeKind : std::uint8_t {
    AttrInt, AttrString, AttrBool, AttrName, AttrArg, Attribute,
    TypeRef, TupleTypeRef, CollectionTypeRef, ListTypeRef, ArrayTypeRef, RefTypeRef, UnitFactor, UnitRef,
    UnitTypeRef, IterTypeRef, RunningTypeRef, LiftedTypeRef, FuncTypeRef,
    IntLit, BoolLit, StringLit, CharLit, NameRef, FloatLit, Spread, Call, Member, Hole, Quote, Allocated, Scoped,
    Invoke, Rule, MacroDef, Fresh, Binary, Try, Named, Failure, Lifted, Raised, TupleLit, SetLit, DictLit,
    ArrayLit, ListLit, Element, Index, Take, TakeAt, TakeThrough, Unary, AddressOf, Each, Deref,
    ReturnStmt, Yield, YieldValue, ExprStmt, EmptyStmt, VarDef, Pattern, MatchArm, IfArm, If, Match,
    AssignStmt, JoinAssign, DerefAssign, ElementAssign, HoleAssign, MemberAssign, EntryAssign, UnitDef,
    ModuleImport, Range, ForEach, While, Label, Break, Defer, Continue, Block, Param, Capture, Lambda,
    Clause, BundleDef, FuncDef, Field, TypeDef, EnumMember, EnumDef, SourceUnit,
  };


  enum class BlockStyle : std::uint8_t { layout, explicit_, inline_ };

  enum class BinaryOp : std::uint8_t {
    bit_and [[=Describe("&")]],
    bit_or [[=Describe("|")]],
    bit_xor [[=Describe("^")]],
    equal [[=Describe("=")]],
    not_equal [[=Describe("\N{NOT EQUAL TO}")]],
    less [[=Describe("<")]],
    greater [[=Describe(">")]],
    less_equal [[=Describe("\N{LESS-THAN OR EQUAL TO}")]],
    greater_equal [[=Describe("\N{GREATER-THAN OR EQUAL TO}")]],
    or_else [[=Describe("??")]],
    alike [[=Describe("\N{APPROXIMATELY EQUAL TO}")]],
    unalike [[=Describe("\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}")]],
    below_or_alike [[=Describe("\N{LESS-THAN OR APPROXIMATE}")]],
    above_or_alike [[=Describe("\N{GREATER-THAN OR APPROXIMATE}")]],
    below_not_alike [[=Describe("\N{LESS-THAN AND NOT APPROXIMATE}")]],
    above_not_alike [[=Describe("\N{GREATER-THAN AND NOT APPROXIMATE}")]],
    logic_and [[=Describe("\N{LOGICAL AND}")]],
    logic_or [[=Describe("\N{LOGICAL OR}")]],
    logic_xor [[=Describe("\N{CIRCLED PLUS}")]],
    logic_nand [[=Describe("\N{NAND}")]],
    logic_nor [[=Describe("\N{NOR}")]],
    short_and [[=Describe("and")]],
    short_or [[=Describe("or")]],
    add [[=Describe("+")]],
    subtract [[=Describe("-")]],
    multiply [[=Describe("\N{MULTIPLICATION SIGN}")]],
    divide [[=Describe("\N{DIVISION SIGN}")]],
    remainder [[=Describe("%")]],
    shift_left [[=Describe("\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}")]],
    shift_right [[=Describe("\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}")]],
    rotate_left [[=Describe("\N{ANTICLOCKWISE OPEN CIRCLE ARROW}")]],
    rotate_right [[=Describe("\N{CLOCKWISE OPEN CIRCLE ARROW}")]],
    sat_add [[=Describe("\N{SQUARED PLUS}")]],
    sat_sub [[=Describe("\N{SQUARED MINUS}")]],
    sat_mul [[=Describe("\N{SQUARED TIMES}")]],
    concat [[=Describe("\N{DOUBLE PLUS}")]],
    shape [[=Describe("\N{APL FUNCTIONAL SYMBOL RHO}")]],
    max [[=Describe("\N{LEFT CEILING}")]],
    min [[=Describe("\N{LEFT FLOOR}")]],
    power [[=Describe("\N{SUPERSCRIPT LATIN SMALL LETTER N}")]],
    divides [[=Describe("\N{DIVIDES}")]],
    not_divides [[=Describe("\N{DOES NOT DIVIDE}")]],
  };

  enum class UnaryOp : std::uint8_t {
    bit_not [[=Describe("~")]],
    logic_not [[=Describe("\N{NOT SIGN}")]],
    length [[=Describe("#")]],
    shape [[=Describe("\N{APL FUNCTIONAL SYMBOL RHO}")]],
    next [[=Describe("\N{UPWARDS WHITE ARROW}")]],
    prev [[=Describe("\N{DOWNWARDS WHITE ARROW}")]],
    max [[=Describe("\N{LEFT CEILING}")]],
    min [[=Describe("\N{LEFT FLOOR}")]],
    divides [[=Describe("\N{DIVIDES}")]],
    not_divides [[=Describe("\N{DOES NOT DIVIDE}")]],
    floor [[=Describe("\N{DOWNWARDS ARROW}")]],
    ceiling [[=Describe("\N{UPWARDS ARROW}")]],
    nearest [[=Describe("\N{UP DOWN ARROW}")]],
    rounded [[=Describe("\N{UP DOWN DOUBLE ARROW}")]],
  };

  enum class CaptureAll : std::uint8_t { by_value [[=Describe("=")]], by_reference [[=Describe("&")]] };
  enum class ClauseKind : std::uint8_t { pre, post };
  enum class TypeKind : std::uint8_t { product [[=Describe(";")]], sum [[=Describe("|")]] };


  // What every node has.
  struct Node {
    NodeKind tag{};
    Span span;
  };

  // The kinds of node that stand in one kind of place.
  struct AttrValue : Node {};
  struct TypeExpr : Node {};
  struct Expr : Node {};

  struct Attribute;
  struct Block;
  struct UnitRef;
  struct Quote;
  struct Label;

  struct Stmt : Node {
    List<const Attribute*> attrs;
  };

  // A pair of expressions: a dictionary's entry.
  struct ExprPair {
    const Expr* key = nullptr;
    const Expr* value = nullptr;
  };

  // The numbers a unit is scaled by, above and below the line.
  struct Scale {
    Integer over;
    Integer under;
  };

  // A unit that stands for another.
  struct UnitPair {
    const UnitRef* what = nullptr;
    const UnitRef* where = nullptr;
  };


  // -- attributes -------------------------------------------------------------

  struct AttrInt : AttrValue {
    static constexpr NodeKind kind_of = NodeKind::AttrInt;
    Integer value;
  };

  struct AttrString : AttrValue {
    static constexpr NodeKind kind_of = NodeKind::AttrString;
    Text value;
  };

  struct AttrBool : AttrValue {
    static constexpr NodeKind kind_of = NodeKind::AttrBool;
    bool value = false;
  };

  struct AttrName : AttrValue {
    static constexpr NodeKind kind_of = NodeKind::AttrName;
    Text value;
  };

  struct AttrArg : Node {
    static constexpr NodeKind kind_of = NodeKind::AttrArg;
    const AttrValue* value = nullptr;
    MaybeText name;
  };

  struct Attribute : Node {
    static constexpr NodeKind kind_of = NodeKind::Attribute;
    Text name;
    List<const AttrArg*> args;
    Span name_span;
  };


  // -- types ------------------------------------------------------------------

  struct TypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::TypeRef;
    Text name;
    MaybeText module;
    bool result = false;
    MaybeText error;
    const UnitRef* unit = nullptr;
    List<const TypeExpr*> args;
  };

  struct TupleTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::TupleTypeRef;
    List<const TypeExpr*> members;
  };

  struct CollectionTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::CollectionTypeRef;
    const TypeExpr* element = nullptr;
    const TypeExpr* value = nullptr;
    bool mutable_ = false;
  };

  struct ListTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::ListTypeRef;
    const TypeExpr* element = nullptr;
    bool result = false;
    MaybeText error;
  };

  struct ArrayTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::ArrayTypeRef;
    // Nothing where a definition leaves what it holds to its value.
    const TypeExpr* element = nullptr;
    // One per dimension; nothing for a dimension of no stated size.
    List<const Expr*> shape;
    bool result = false;
    MaybeText error;
  };

  struct RefTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::RefTypeRef;
    const TypeExpr* pointee = nullptr;
    bool mutable_ = false;
    bool lasting = false;
    MaybeText lifetime;
  };

  struct UnitFactor : Node {
    static constexpr NodeKind kind_of = NodeKind::UnitFactor;
    Text name;
    bool quoted = false;
    Integer exponent{ 1 };
  };

  struct UnitRef : Node {
    static constexpr NodeKind kind_of = NodeKind::UnitRef;
    List<const UnitFactor*> factors;
  };

  struct UnitTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::UnitTypeRef;
    const TypeExpr* base = nullptr;
    const UnitRef* unit = nullptr;
  };

  struct IterTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::IterTypeRef;
    const TypeExpr* element = nullptr;
  };

  struct RunningTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::RunningTypeRef;
    const TypeExpr* element = nullptr;
    const TypeExpr* given = nullptr;
    std::optional<NameAt> kept_in;
  };

  struct LiftedTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::LiftedTypeRef;
    const TypeExpr* inner = nullptr;
  };

  struct FuncTypeRef : TypeExpr {
    static constexpr NodeKind kind_of = NodeKind::FuncTypeRef;
    List<const TypeExpr*> params;
    const TypeExpr* ret = nullptr;
    List<const Attribute*> attrs;
    std::optional<NameAt> made_in;
    std::optional<NameAt> kept_in;
  };


  // -- expressions ------------------------------------------------------------

  struct IntLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::IntLit;
    Integer value;
    MaybeText type_name;
  };

  struct BoolLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::BoolLit;
    bool value = false;
  };

  struct StringLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::StringLit;
    Text value;
  };

  struct CharLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::CharLit;
    Integer value;
  };

  struct NameRef : Expr {
    static constexpr NodeKind kind_of = NodeKind::NameRef;
    Text name;
  };

  struct FloatLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::FloatLit;
    double value = 0.0;
    MaybeText type_name;
  };

  struct Spread : Expr {
    static constexpr NodeKind kind_of = NodeKind::Spread;
    const Expr* operand = nullptr;
  };

  struct Call : Expr {
    static constexpr NodeKind kind_of = NodeKind::Call;
    const Expr* callee = nullptr;
    List<const Expr*> args;
  };

  struct Member : Expr {
    static constexpr NodeKind kind_of = NodeKind::Member;
    const Expr* base = nullptr;
    Text name;
    Span name_span;
  };

  struct Hole : Expr {
    static constexpr NodeKind kind_of = NodeKind::Hole;
    Text name;
    const Expr* value = nullptr;
  };

  // Pieces of the program between the lifting marks; it stands where an
  // expression does inside a body that runs while the compiler does.
  struct Quote : Expr {
    static constexpr NodeKind kind_of = NodeKind::Quote;
    List<const Expr*> pieces;
    const Block* body = nullptr;
  };

  struct Allocated : Expr {
    static constexpr NodeKind kind_of = NodeKind::Allocated;
    const Expr* value = nullptr;
    const Expr* arena = nullptr;
  };

  struct Scoped : Expr {
    static constexpr NodeKind kind_of = NodeKind::Scoped;
    const Block* body = nullptr;
  };

  struct Invoke : Expr {
    static constexpr NodeKind kind_of = NodeKind::Invoke;
    Text name;
    Span name_span;
    const Quote* arguments = nullptr;
    MaybeText through;
  };

  struct Rule : Node {
    static constexpr NodeKind kind_of = NodeKind::Rule;
    const Quote* pattern = nullptr;
    const Quote* template_ = nullptr;
  };

  struct MacroDef : Node {
    static constexpr NodeKind kind_of = NodeKind::MacroDef;
    Text name;
    Span name_span;
    List<const Rule*> rules;
    List<const Attribute*> attrs;
    MaybeText doc;
    List<Span> doc_lines;
  };

  struct Fresh : Expr {
    static constexpr NodeKind kind_of = NodeKind::Fresh;
    Text glyph;
    List<const Expr*> operands;
  };

  struct Binary : Expr {
    static constexpr NodeKind kind_of = NodeKind::Binary;
    BinaryOp op{};
    const Expr* left = nullptr;
    const Expr* right = nullptr;
  };

  struct Try : Expr {
    static constexpr NodeKind kind_of = NodeKind::Try;
    const Expr* operand = nullptr;
  };

  struct Named : Expr {
    static constexpr NodeKind kind_of = NodeKind::Named;
    Text name;
    Span name_span;
    const Expr* value = nullptr;
  };

  struct Failure : Expr {
    static constexpr NodeKind kind_of = NodeKind::Failure;
    const Expr* value = nullptr;
  };

  struct Lifted : Expr {
    static constexpr NodeKind kind_of = NodeKind::Lifted;
    const TypeExpr* written = nullptr;
    const Expr* value = nullptr;
  };

  struct Raised : Expr {
    static constexpr NodeKind kind_of = NodeKind::Raised;
    const Expr* base = nullptr;
    Integer exponent;
  };

  struct TupleLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::TupleLit;
    List<const Expr*> members;
  };

  struct SetLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::SetLit;
    List<const Expr*> elements;
    const Expr* arena = nullptr;
    bool spread = false;
  };

  struct DictLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::DictLit;
    List<ExprPair> entries;
    const Expr* arena = nullptr;
  };

  struct ArrayLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::ArrayLit;
    List<const Expr*> elements;
  };

  struct ListLit : Expr {
    static constexpr NodeKind kind_of = NodeKind::ListLit;
    List<const Expr*> elements;
  };

  struct Element : Expr {
    static constexpr NodeKind kind_of = NodeKind::Element;
    const Expr* base = nullptr;
    List<const Expr*> indices;
  };

  struct Index : Expr {
    static constexpr NodeKind kind_of = NodeKind::Index;
    const Expr* base = nullptr;
    const Expr* key = nullptr;
  };

  struct Take : Expr {
    static constexpr NodeKind kind_of = NodeKind::Take;
    const Expr* base = nullptr;
    const Expr* key = nullptr;
  };

  struct TakeAt : Expr {
    static constexpr NodeKind kind_of = NodeKind::TakeAt;
    const Expr* base = nullptr;
    const Expr* index = nullptr;
  };

  struct TakeThrough : Expr {
    static constexpr NodeKind kind_of = NodeKind::TakeThrough;
    const Expr* operand = nullptr;
  };

  struct Unary : Expr {
    static constexpr NodeKind kind_of = NodeKind::Unary;
    UnaryOp op{};
    const Expr* operand = nullptr;
  };

  struct AddressOf : Expr {
    static constexpr NodeKind kind_of = NodeKind::AddressOf;
    const Expr* operand = nullptr;
    bool mutable_ = false;
  };

  struct Each : Expr {
    static constexpr NodeKind kind_of = NodeKind::Each;
    const Expr* operand = nullptr;
  };

  struct Deref : Expr {
    static constexpr NodeKind kind_of = NodeKind::Deref;
    const Expr* operand = nullptr;
  };


  // -- statements -------------------------------------------------------------

  struct ReturnStmt : Stmt {
    static constexpr NodeKind kind_of = NodeKind::ReturnStmt;
    const Expr* value = nullptr;
    bool explicit_ = false;
  };

  struct Yield : Stmt {
    static constexpr NodeKind kind_of = NodeKind::Yield;
    const Expr* value = nullptr;
    bool delegates = false;
  };

  struct YieldValue : Expr {
    static constexpr NodeKind kind_of = NodeKind::YieldValue;
    const Expr* value = nullptr;
    bool delegates = false;
  };

  struct ExprStmt : Stmt {
    static constexpr NodeKind kind_of = NodeKind::ExprStmt;
    const Expr* value = nullptr;
  };

  struct EmptyStmt : Stmt {
    static constexpr NodeKind kind_of = NodeKind::EmptyStmt;
  };

  struct VarDef : Stmt {
    static constexpr NodeKind kind_of = NodeKind::VarDef;
    Text name;
    Span name_span;
    const TypeExpr* type = nullptr;
    const Expr* value = nullptr;
    bool mutable_ = false;
    MaybeText doc;
    List<Span> doc_lines;
    std::optional<NameAt> made_in;
    List<NameAt> more;
  };

  struct Pattern : Node {
    static constexpr NodeKind kind_of = NodeKind::Pattern;
    const TypeExpr* type = nullptr;
    MaybeText name;
    Span name_span;
    bool wildcard = false;
  };

  struct MatchArm : Node {
    static constexpr NodeKind kind_of = NodeKind::MatchArm;
    const Pattern* pattern = nullptr;
    const Block* body = nullptr;
  };

  struct IfArm : Node {
    static constexpr NodeKind kind_of = NodeKind::IfArm;
    // Nothing for the `else` arm.
    const Expr* condition = nullptr;
    const Block* body = nullptr;
    bool comptime = false;
  };

  struct If : Expr {
    static constexpr NodeKind kind_of = NodeKind::If;
    List<const IfArm*> arms;
  };

  struct Match : Expr {
    static constexpr NodeKind kind_of = NodeKind::Match;
    const Expr* subject = nullptr;
    List<const MatchArm*> arms;
  };

  struct AssignStmt : Stmt {
    static constexpr NodeKind kind_of = NodeKind::AssignStmt;
    Text name;
    Span name_span;
    const Expr* value = nullptr;
    List<NameAt> more;
  };

  struct JoinAssign : Stmt {
    static constexpr NodeKind kind_of = NodeKind::JoinAssign;
    const Expr* target = nullptr;
    const Expr* value = nullptr;
  };

  struct DerefAssign : Stmt {
    static constexpr NodeKind kind_of = NodeKind::DerefAssign;
    const Expr* target = nullptr;
    const Expr* value = nullptr;
  };

  struct ElementAssign : Stmt {
    static constexpr NodeKind kind_of = NodeKind::ElementAssign;
    const Expr* base = nullptr;
    List<const Expr*> indices;
    const Expr* value = nullptr;
  };

  struct HoleAssign : Stmt {
    static constexpr NodeKind kind_of = NodeKind::HoleAssign;
    const Expr* target = nullptr;
    const Expr* value = nullptr;
  };

  struct MemberAssign : Stmt {
    static constexpr NodeKind kind_of = NodeKind::MemberAssign;
    const Expr* base = nullptr;
    Text name;
    Span name_span;
    const Expr* value = nullptr;
  };

  struct EntryAssign : Stmt {
    static constexpr NodeKind kind_of = NodeKind::EntryAssign;
    const Expr* base = nullptr;
    const Expr* key = nullptr;
    const Expr* value = nullptr;
  };

  struct UnitDef : Stmt {
    static constexpr NodeKind kind_of = NodeKind::UnitDef;
    Text name;
    Span name_span;
    bool quoted = false;
    const UnitRef* measured = nullptr;
    std::optional<Scale> scale;
    std::optional<UnitPair> stands;
    MaybeText doc;
    List<Span> doc_lines;
  };

  struct ModuleImport : Node {
    static constexpr NodeKind kind_of = NodeKind::ModuleImport;
    Text name;
    Span name_span;
    Text source;
    Span source_span;
    MaybeText doc;
    List<Span> doc_lines;
    List<const Attribute*> attrs;
  };

  struct Range : Expr {
    static constexpr NodeKind kind_of = NodeKind::Range;
    const Expr* start = nullptr;
    const Expr* stop = nullptr;
    const Expr* step = nullptr;
  };

  struct ForEach : Expr {
    static constexpr NodeKind kind_of = NodeKind::ForEach;
    Text name;
    Span name_span;
    const TypeExpr* type = nullptr;
    const Expr* iterable = nullptr;
    const Block* body = nullptr;
    List<NameAt> more;
    Text keyword = "foreach";
    const Label* label = nullptr;
    const Block* alternative = nullptr;
    bool comptime = false;
    List<Text> marks;
  };

  struct While : Expr {
    static constexpr NodeKind kind_of = NodeKind::While;
    const Expr* condition = nullptr;
    const Block* body = nullptr;
    const Label* label = nullptr;
    const Block* alternative = nullptr;
    bool until = false;
  };

  struct Label : Node {
    static constexpr NodeKind kind_of = NodeKind::Label;
    Text name;
  };

  struct Break : Stmt {
    static constexpr NodeKind kind_of = NodeKind::Break;
    const Label* label = nullptr;
    const Expr* value = nullptr;
  };

  struct Defer : Stmt {
    static constexpr NodeKind kind_of = NodeKind::Defer;
    const Stmt* stmt = nullptr;
  };

  struct Continue : Stmt {
    static constexpr NodeKind kind_of = NodeKind::Continue;
    const Label* label = nullptr;
  };


  // How a body set aside is parsed when something first reads it.
  struct BodyParse {
    // The statements, or nothing where the body could not be read.
    virtual std::optional<List<const Stmt*>> parse() = 0;
    // Report in the compilation's engine what the parse said.
    virtual void replay() = 0;

  protected:
    ~BodyParse() = default;
  };


  struct Block : Node {
    static constexpr NodeKind kind_of = NodeKind::Block;
    BlockStyle style{};

    // The statements.  A body the parser set aside is parsed the first time
    // they are asked for, and what that said is reported then.
    List<const Stmt*> stmts() const;

    // Whether the statements are in hand, and whether reading them failed.
    bool parsed() const noexcept { return job == nullptr || done; }
    bool failed() const { (void) stmts(); return did_fail; }

    // Settle a body set aside with what parsing it came to.
    void settle(std::optional<List<const Stmt*>> found) const;

    [[=hidden]] mutable List<const Stmt*> statements;
    [[=hidden]] BodyParse* job = nullptr;
    [[=hidden]] mutable bool done = false;
    [[=hidden]] mutable bool did_fail = false;
  };


  // -- definitions ------------------------------------------------------------

  struct Param : Node {
    static constexpr NodeKind kind_of = NodeKind::Param;
    Text name;
    const TypeExpr* type = nullptr;
    bool mutable_ = false;
    const Expr* default_ = nullptr;
    bool several = false;
    std::optional<NameAt> made_in;
  };

  struct Capture : Node {
    static constexpr NodeKind kind_of = NodeKind::Capture;
    Text name;
    bool by_reference = false;
  };

  struct Lambda : Expr {
    static constexpr NodeKind kind_of = NodeKind::Lambda;
    List<const Param*> params;
    const Block* body = nullptr;
    List<const Capture*> captures;
    const TypeExpr* ret_type = nullptr;
    std::optional<CaptureAll> brings_in;
    List<const Attribute*> attrs;
    std::optional<NameAt> made_in;
    std::optional<NameAt> kept_in;
  };

  struct Clause : Node {
    static constexpr NodeKind kind_of = NodeKind::Clause;
    ClauseKind kind{};
    const Expr* expr = nullptr;
    const TypeExpr* answers = nullptr;
  };

  struct BundleDef : Node {
    static constexpr NodeKind kind_of = NodeKind::BundleDef;
    Text name;
    Span name_span;
    List<Text> params;
    List<Span> param_spans;
    List<const Clause*> clauses;
    List<const Attribute*> attrs;
    MaybeText doc;
    List<Span> doc_lines;
  };

  struct FuncDef : Node {
    static constexpr NodeKind kind_of = NodeKind::FuncDef;
    Text name;
    Span name_span;
    List<const Param*> params;
    const TypeExpr* ret_type = nullptr;
    // Nothing for a declaration of a function defined elsewhere.
    const Block* body = nullptr;
    List<const Clause*> clauses;
    List<NameAt> made_in;
    bool at_compile_time = false;
    bool is_macro = false;
    bool generates = false;
    const TypeExpr* takes = nullptr;
    const TypeExpr* finishes = nullptr;
    MaybeText held;
    std::optional<Span> held_span;
    List<const Attribute*> attrs;
    MaybeText doc;
    List<Span> doc_lines;
  };

  struct Field : Node {
    static constexpr NodeKind kind_of = NodeKind::Field;
    Text name;
    Span name_span;
    const TypeExpr* type = nullptr;
    bool mutable_ = false;
    bool singleton = false;
    const Expr* value = nullptr;
  };

  struct TypeDef : Node {
    static constexpr NodeKind kind_of = NodeKind::TypeDef;
    Text name;
    Span name_span;
    TypeKind kind{};
    List<const Field*> fields;
    List<const Field*> singletons;
    List<const Attribute*> attrs;
    List<Text> params;
    List<Span> param_spans;
    List<const Clause*> clauses;
    MaybeText doc;
    List<Span> doc_lines;
  };

  struct EnumMember : Node {
    static constexpr NodeKind kind_of = NodeKind::EnumMember;
    Text name;
    Span name_span;
    // An `IntLit`, a `NameRef`, or nothing.
    const Expr* value = nullptr;
  };

  struct EnumDef : Node {
    static constexpr NodeKind kind_of = NodeKind::EnumDef;
    Text name;
    Span name_span;
    List<const EnumMember*> members;
    const TypeExpr* holder = nullptr;
    List<const Attribute*> attrs;
    MaybeText doc;
    List<Span> doc_lines;
  };

  struct SourceUnit : Node {
    static constexpr NodeKind kind_of = NodeKind::SourceUnit;
    Text path;
    // Each a FuncDef, VarDef, ModuleImport, TypeDef, EnumDef, UnitDef,
    // BundleDef or MacroDef.
    List<const Node*> items;
    List<const Attribute*> attrs;
  };


  // Whether `node` is of the kind T is, where T is a kind of node.
  template<typename T>
  bool is(const Node* node) noexcept
  {
    return node != nullptr && node->tag == T::kind_of;
  }

  // `node` as a T, or nothing where it is not one.
  template<typename T>
  const T* as(const Node* node) noexcept
  {
    return is<T>(node) ? static_cast<const T*>(node) : nullptr;
  }


  // Call `f` with `node` as the struct it is.
  template<typename F>
  decltype(auto) visit(const Node& node, F&& f)
  {
    constexpr auto ctx = std::meta::access_context::unchecked();
    template for (constexpr auto t : std::define_static_array(std::meta::members_of(^^pl4g::ast, ctx)))
      if constexpr (std::meta::is_type(t) && ! std::meta::is_type_alias(t) && std::meta::is_class_type(t)
                    && std::meta::is_complete_type(t))
        if constexpr (requires { [:t:]::kind_of; })
          if (node.tag == [:t:]::kind_of)
            return std::forward<F>(f)(static_cast<const [:t:]&>(node));
    __builtin_unreachable();
  }


  // Where the nodes of a compilation live, with the tokens their names point
  // into and the bodies set aside to be parsed later.
  class SyntaxArena {
  public:
    SyntaxArena() = default;
    SyntaxArena(const SyntaxArena&) = delete;
    SyntaxArena& operator=(const SyntaxArena&) = delete;

    // A new node of kind T, every field at its default.
    template<typename T>
      requires std::is_base_of_v<Node, T>
    T* make(Span span)
    {
      static_assert(std::is_trivially_destructible_v<T>, "nodes are never destroyed one by one");
      T* node = new (memory.allocate(sizeof(T), alignof(T))) T{};
      node->tag = T::kind_of;
      node->span = span;
      return node;
    }

    // A copy, kept here, of the elements of `items`.
    template<typename T>
    List<T> list(std::span<const T> items)
    {
      if (items.empty())
        return {};
      static_assert(std::is_trivially_destructible_v<T>);
      T* copy = static_cast<T*>(memory.allocate(sizeof(T) * items.size(), alignof(T)));
      std::uninitialized_copy(items.begin(), items.end(), copy);
      return { copy, items.size() };
    }

    template<typename T>
    List<T> list(const std::vector<T>& items) { return list(std::span<const T>(items)); }

    // A copy, kept here, of `text`.
    Text text(std::string_view text)
    {
      if (text.empty())
        return {};
      char* copy = static_cast<char*>(memory.allocate(text.size(), 1));
      std::ranges::copy(text, copy);
      return { copy, text.size() };
    }

    // Keep `tokens` for as long as the nodes are, and say where they are.
    const std::vector<Token>& keep(std::vector<Token> tokens)
    {
      return token_lists.emplace_back(std::move(tokens));
    }

    // Keep an object -- a body's parse job -- for as long as the nodes are.
    template<typename T, typename... Args>
    T& hold(Args&&... args)
    {
      auto owner = std::make_shared<T>(std::forward<Args>(args)...);
      held.push_back(owner);
      return *owner;
    }

  private:
    std::pmr::monotonic_buffer_resource memory{ 1 << 16 };
    std::deque<std::vector<Token>> token_lists;
    std::vector<std::shared_ptr<void>> held;
  };

} // namespace pl4g::ast
