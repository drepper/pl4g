// Running a function at compile time.
//
// A port of pypl4g's comptime/evaluate.py: one walk of the checked syntax tree
// with an environment, the way a small interpreter works.  What it cannot work
// out it refuses with a `CannotEvaluate` naming the construct, so that whoever
// asked decides which diagnostic that becomes.
//
// **The values.**  Python's evaluator computes with Python's own objects, and
// this one with a `Value` standing for each of them: nothing (`None`), the one
// `MISSING`, a truth value, a whole number, a float, text, a run (a Python list
// -- arrays, lists and tuples alike), a table, a record, a reference, and a
// constant of an enumeration the checker named.  The aggregates are held by
// `shared_ptr`, because Python's are objects that two names may hold and a
// change through one is seen through the other; the scalars are held by value.
// Equality is Python's `==`: numbers equal across `bool`, `int` and `float`,
// runs and records element for element, tables by their entries whatever the
// order, references by what their cells hold, and the rest by identity.
//
// **Whole numbers have no width here** in Python; here they are 128 bits, and
// a number that would not fit is refused as one too large to work out rather
// than carried on with.  No pl4g type holds more, so what is refused is only
// ever an intermediate value of a computation no program could store.
#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

#include "front/ast.hh"
#include "source/location.hh"


namespace pl4g::ir {
  struct Value;
}


namespace pl4g::comptime {

  // How many steps one evaluation may take before it is called an endless one.
  inline constexpr std::int64_t steps_allowed = 100'000;

  // And how deep a call may go.
  inline constexpr int depth_allowed = 128;


  struct Value;
  struct Cell;
  struct Record;
  struct Table;

  // pypl4g's `MISSING`: what a lookup that found nothing answers with.
  struct Missing {
    bool operator==(const Missing&) const = default;
  };

  // A run of values: what Python holds an array, a list or a tuple as.
  using Run = std::vector<Value>;

  // What `&x` and `&mut x` answer with: the cell, not the value in it.
  struct Reference {
    std::shared_ptr<Cell> cell;
    bool mutable_ = false;
  };


  // One value while an evaluation is running.
  struct Value {
    using Holds = std::variant<std::monostate, Missing, bool, __int128, double, std::string, std::shared_ptr<Run>,
                               std::shared_ptr<Table>, std::shared_ptr<Record>, Reference, const ir::Value*>;
    Holds held;

    Value() = default;
    Value(Missing m) : held(m) {}
    Value(bool b) : held(b) {}
    Value(__int128 n) : held(n) {}
    Value(std::int64_t n) : held(static_cast<__int128>(n)) {}
    Value(int n) : held(static_cast<__int128>(n)) {}
    Value(double d) : held(d) {}
    Value(std::string s) : held(std::move(s)) {}
    Value(std::string_view s) : held(std::string(s)) {}
    Value(const char* s) : held(std::string(s)) {}
    Value(std::shared_ptr<Run> r) : held(std::move(r)) {}
    Value(std::shared_ptr<Table> t) : held(std::move(t)) {}
    Value(std::shared_ptr<Record> r) : held(std::move(r)) {}
    Value(Reference r) : held(std::move(r)) {}
    // A constant of an enumeration, which the checker made.
    Value(const ir::Value* c) : held(c) {}

    // A new run holding `values`.
    static Value run(Run values = {}) { return Value(std::make_shared<Run>(std::move(values))); }

    bool is_none() const noexcept { return std::holds_alternative<std::monostate>(held); }
    bool is_missing() const noexcept { return std::holds_alternative<Missing>(held); }
    template<typename T>
    bool is() const noexcept { return std::holds_alternative<T>(held); }
    template<typename T>
    const T* get() const noexcept { return std::get_if<T>(&held); }
    template<typename T>
    T* get() noexcept { return std::get_if<T>(&held); }

    // Python's `isinstance(v, int) and not isinstance(v, bool)`.
    bool is_whole() const noexcept { return is<__int128>(); }
    // A number an operator computes with: a whole number or a float, not a truth value.
    bool is_number() const noexcept { return is<__int128>() || is<double>(); }
  };


  // Somewhere a value lives, so that a reference has something to point at.
  struct Cell {
    Value value;
  };


  // A value of a record type, while it is being worked out: the fields in the
  // order they were first given.
  struct Record {
    std::string type_name;
    std::vector<std::pair<std::string, Value>> fields;

    // The field called `name`, or nothing.
    const Value* field(std::string_view name) const;
    // Give the field called `name` a value, where it is or at the end.
    void set(std::string_view name, Value value);
  };


  // A set or a dictionary while the compiler works one out: the keys in the
  // order they were put in, which is the order a table in the image is laid
  // out in.  Which of the two it is, is said by what is done to it; nothing
  // where neither has been said, as for `⸨⸩` written empty.
  struct Table {
    std::optional<bool> is_set;
    // A dictionary that is not one being worked out: what `⎕environ` stands
    // for, which Python holds as a plain `dict` and refuses to change.
    bool plain = false;
    std::vector<std::pair<Value, Value>> entries;

    explicit Table(std::optional<bool> is_set = std::nullopt, bool plain = false) : is_set(is_set), plain(plain) {}

    // What is held under `key`, or nothing.
    const Value* find(const Value& key) const;
    bool contains(const Value& key) const { return find(key) != nullptr; }
    // Put `value` under `key`: where an equal key is, or at the end.
    void put(Value key, Value value);
    std::size_t size() const noexcept { return entries.size(); }

  private:
    std::unordered_multimap<std::size_t, std::size_t> index;
  };


  // Python's `a == b` over two values.
  bool equal(const Value& a, const Value& b);

  // Python's `bool(v)`.
  bool truthy(const Value& v);

  // Python's `str(v)` and `repr(v)`.
  std::string to_str(const Value& v);
  std::string to_repr(const Value& v);


  // What the evaluator asks of the checker about the program's types.  Each
  // answers nothing where it has nothing to say, which the evaluator then
  // refuses as something it cannot work out.
  class Reflect {
  public:
    virtual ~Reflect() = default;

    // What `⎕fields` of `written` answers: a run of pairs of name and value.
    virtual std::optional<Value> fields(const ast::Expr* written);
    // The value `TYPE.NAME` stands for, where it names one.
    virtual std::optional<Value> enum_value(const ast::Member* written);
  };


  // What the compiler could not work out, and where.
  class CannotEvaluate : public std::runtime_error {
  public:
    CannotEvaluate(Span span, std::string detail, bool ran_out = false)
      : std::runtime_error(detail), span(span), detail(std::move(detail)), ran_out(ran_out) {}

    Span span;
    std::string detail;
    // Whether what went wrong is that it did not finish.
    bool ran_out = false;
  };

  // A division with no answer: by nought, or nought raised to a negative power.
  class NoAnswer : public CannotEvaluate {
  public:
    using CannotEvaluate::CannotEvaluate;
  };

  // Python's ZeroDivisionError, out of `power`.
  struct DivisionByNought : std::domain_error {
    DivisionByNought() : std::domain_error("division by nought") {}
  };


  // A function the compiler provides while an evaluation is running, called
  // with the place the call is written and the arguments.
  struct Builtin {
    std::string name;
    std::function<Value(Span, std::span<const Value>)> run;
  };

  // The builtins of one module, by name.
  using ModuleBuiltins = std::unordered_map<std::string, Builtin>;
  // And of every module that has any, by what the import names as its source.
  using Builtins = std::unordered_map<std::string, ModuleBuiltins>;


  // One evaluation: the program it is over, and what it has worked out.  Built
  // for one run and thrown away.
  class Evaluator {
  public:
    Evaluator(std::span<const ast::SourceUnit* const> units, Builtins builtins = {},
              std::vector<std::pair<std::string, std::string>> environ = {}, Reflect* reflect = nullptr);
    Evaluator(const Evaluator&) = delete;
    Evaluator& operator=(const Evaluator&) = delete;

    // Run `func` over `arguments` and answer with what it answered.
    Value call(const ast::FuncDef& func, std::span<const Value> arguments);
    // The function the program defines under `name`, where it defines one.
    const ast::FuncDef* function(std::string_view name) const;
    // What `expr` comes to, with nothing bound but the program's own variables.
    Value evaluate(const ast::Expr* expr);

  private:
    using Scope = std::unordered_map<std::string_view, std::shared_ptr<Cell>>;
    struct Turns;

    Value call_at(const ast::FuncDef& func, std::span<const Value> arguments, Span span);
    Value block(const ast::Block* body);
    void put_off(const std::vector<const ast::Stmt*>& later);
    Value stmt(const ast::Stmt* stmt);
    void define(const ast::VarDef* stmt);
    void assign_name(const ast::AssignStmt* stmt);
    void assign_member(const ast::MemberAssign* stmt);
    void assign_deref(const ast::DerefAssign* stmt);
    void assign_element(const ast::ElementAssign* stmt);
    void assign_entry(const ast::EntryAssign* stmt);
    Value if_(const ast::If* expr);
    Value while_(const ast::While* expr);
    Value foreach(const ast::ForEach* expr);
    Turns iterable(const ast::Expr* expr);
    Value expr(const ast::Expr* expr);
    bool bound(std::string_view name) const;
    std::shared_ptr<Cell> cell_of(std::string_view name, Span span);
    Value binary(const ast::Binary* expr);
    Value raised(const ast::Raised* expr);
    Value unary(const ast::Unary* expr);
    Value call_expr(const ast::Call* expr);
    const Builtin& builtin(std::string_view module, std::string_view name, Span span) const;
    Value member(const ast::Member* expr);
    Value element(const ast::Element* expr);
    Value index(const ast::Index* expr);
    Value address_of(const ast::AddressOf* expr);
    Value deref(const ast::Deref* expr);
    Value value(const ast::Expr* expr, bool missing = false);
    std::shared_ptr<Record> record_of(const ast::Expr* expr);
    bool truth(const ast::Expr* expr);
    __int128 number(const ast::Expr* expr);
    std::size_t as_index(const ast::Expr* expr, std::size_t length);
    void step(Span span);
    CannotEvaluate refuse(Span span, const ast::Node* node) const;

    Reflect fallback;
    Reflect* reflect;
    std::unordered_map<std::string_view, const ast::FuncDef*> functions;
    std::unordered_map<std::string_view, const ast::VarDef*> globals;
    std::unordered_map<std::string_view, std::shared_ptr<Cell>> settled;
    std::unordered_map<std::string_view, std::string_view> modules;
    Builtins builtins;
    std::vector<std::pair<std::string, std::string>> environ;
    std::vector<Scope> scopes;
    std::int64_t steps = 0;
    int depth = 0;
  };


  // A whole number raised to a whole power, as the program raises it: a
  // negative exponent is one divided by the positive power, toward zero, and
  // throws `DivisionByNought` where what was raised is nought.  An answer
  // beyond 128 bits says so.
  ast::Integer power(__int128 base, __int128 exponent);
  // And a float raised to one.
  double power(double base, __int128 exponent);

} // namespace pl4g::comptime
