// What the checker keeps beside itself: the records of pypl4g's check.py that
// are not the checker, its constants, and the helpers that need no checker.
//
// A port of the module level of pypl4g/sema/check.py.  The names drop the
// leading underscore Python marks a module's own with; everything here is the
// checker's own and nothing outside sema/ includes it.
#pragma once

#include <cstdint>
#include <filesystem>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <variant>
#include <vector>

#include "diag/engine.hh"
#include "front/ast.hh"
#include "ir/builder.hh"
#include "ir/ir.hh"
#include "ir/layout.hh"
#include "ir/types.hh"
#include "source/location.hh"
#include "source/manager.hh"
#include "sema/attributes.hh"
#include "sema/clauses.hh"
#include "sema/held.hh"
#include "sema/modules.hh"
#include "sema/notes.hh"
#include "front/doccomment.hh"
#include "comptime/evaluate.hh"
#include "util/ordered.hh"


namespace pl4g::sema {

  struct Bundle;
  struct Generator;
  struct Generic;
  struct Iterable;
  struct LoadedModule;
  struct Local;
  struct NamedType;
  class Checker;


  // -- what Python's dynamic values become ------------------------------------

  // An insertion-ordered dict, as Python's are.
  template<typename K, typename V>
  using Dict = OrderedMap<K, V>;

  // A set of identities or small numbers (`set[int]`, `frozenset[int]`).
  // Python's iterates in an order that is the ids' and so means nothing; where
  // the order of one shows, the port says so.
  using KeySet = std::set<std::int64_t>;

  // A set of names.  Python's string hashing is randomized, so no output of
  // pypl4g's can depend on the order of one; this one is sorted.
  using NameSet = std::set<std::string, std::less<>>;

  // The identity of a Python object, as `id(x)` is: the address.
  inline std::int64_t id_of(const void* object) noexcept
  {
    return static_cast<std::int64_t>(reinterpret_cast<std::intptr_t>(object));
  }

  // A tuple of types as a key, compared as Python compares tuples of types:
  // element for element, by `==`, which for types is `ir::same`.
  struct Types {
    std::vector<const ir::Type*> types;

    bool operator==(const Types& other) const
    {
      if (types.size() != other.types.size())
        return false;
      for (std::size_t i = 0; i < types.size(); ++i)
        if (! ir::same(types[i], other.types[i]))
          return false;
      return true;
    }
  };

  // One type as a key, compared by `==` as a Python dict keyed by types does.
  struct SameType {
    const ir::Type* ty = nullptr;
    bool operator==(const SameType& other) const { return ir::same(ty, other.ty); }
  };


} // namespace pl4g::sema

template<>
struct pl4g::Hash<pl4g::sema::Types> {
  std::size_t operator()(const pl4g::sema::Types& key) const noexcept
  {
    std::size_t seed = key.types.size();
    for (auto one : key.types)
      seed = hash_combine(seed, pl4g::ir::structural_hash(one));
    return seed;
  }
};

template<>
struct pl4g::Hash<pl4g::sema::SameType> {
  std::size_t operator()(const pl4g::sema::SameType& key) const noexcept
  {
    return pl4g::ir::structural_hash(key.ty);
  }
};


namespace pl4g::sema {

  // -- records ----------------------------------------------------------------

  // One thing a construct said about a diagnostic it may raise.
  struct Expected {
    std::int64_t number = 0;
    Span span;
    // Whether it asserted that it is raised, rather than merely allowed it.
    bool asserted = false;
  };

  // A top-level function definition and the attributes bound to it.
  struct Collected {
    const ast::FuncDef* node = nullptr;
    std::vector<BoundAttr> attrs;
    ir::Function* func = nullptr;
    Expectation* expectation = nullptr;
  };

  // A name bound inside a function body, and the value it stands for.
  struct Local {
    std::string name;
    ir::Value* value = nullptr;
    Span span;
    bool is_mutable = false;
    // Whether anything has read the value the name currently stands for.
    bool read = false;
    // Whether anything has written the place the name stands for.
    bool written = false;
    // Where that value was given, for reporting one that nothing reads.
    Span value_span;
    // A parameter arrives with a value the caller chose.
    bool is_parameter = false;
    // Whether the name is a parameter's, whatever it has been given since:
    // `is_parameter` is about the value, and this about the name, which a list
    // taken as the body's own keeps.
    bool named_parameter = false;
    // What the definition said it raises, in force while the variable exists.
    Expectation* expectation = nullptr;
    std::vector<Expected> expected_pairs;
    // Whether the name stands for a place rather than for a value, and what
    // that storage holds.
    bool placed = false;
    const ir::Type* held = nullptr;
    // How deep the scope it was bound in is.
    std::int64_t depth = 0;
    // The arenas what it holds was made in, by the identity of their names.
    KeySet arenas;
    // Where the arena it was made in was given back, and which.
    std::optional<Span> gone_at;
    std::string gone_with;
    // For a value of a type marked `@[closed_by(f)]`: `f`, and whether it
    // still holds one.
    std::string closer;
    bool owes = false;
    // Where its lifetime ended -- along some way here -- and how.
    std::optional<Span> ended_at;
    std::string ended_how;
    // For a container whose elements point somewhere, the allocator they are
    // kept in.
    std::optional<std::int64_t> holds_in;
    // Whether this is the arena the body hands out with its answer.
    bool handed_out = false;
    // What a loop would walk, where the name stands for that.
    Iterable* iterable = nullptr;
    // Whether the room a list it holds is in is its own -- made where it was
    // bound, copied for it, or handed over by a name whose room was -- and so
    // may be handed on once it is done with.
    bool owns_room = false;
  };

  // A reference nothing has bound yet, which lives to the end of the statement.
  inline constexpr std::int64_t until_the_statement_ends = -1;

  // One reference that is out, and what it keeps anything else from doing.
  struct Borrow {
    std::string name;
    bool is_mutable = false;
    Span span;
    std::int64_t depth = 0;
    // The fields, outermost first, of the part of the place it reaches.
    std::vector<std::string> path;
    // The binding of the name that keeps it, and that name.
    std::optional<std::pair<std::int64_t, std::int64_t>> keeper;
    std::string keeper_name;
  };

  // Whether two field paths share anything.
  bool overlap(std::span<const std::string> one, std::span<const std::string> other);

  // What a `match` arm binds: the name, where, where its value is, the value
  // and its type.
  struct ArmBinding {
    std::string name;
    Span span;
    Span value_span;
    ir::Value* value = nullptr;
    const ir::Type* ty = nullptr;
  };

  // One arm to run: its body, the block it runs in, and what it binds.
  struct ArmPlan {
    const ast::Block* body = nullptr;
    ir::BasicBlock* block = nullptr;
    std::optional<ArmBinding> binds;
    ir::Value* memory = nullptr;
    bool carried = false;
    bool payload = false;
    // Whether the place the part is read from is the place the program named
    // rather than a copy of the sum made for the match.
    bool in_place = false;
  };

  // A type a program defined, and what it turned out to be.
  struct NamedType {
    std::string name;
    // A TypeDef or an EnumDef.
    const ast::Node* node = nullptr;
    std::string origin;
    bool exported = false;
    bool abi = false;
    bool unique = false;
    bool device = false;
    std::string closed_by;
    const ir::Type* ty = nullptr;
    bool resolving = false;
    // How many indirections were open when its resolving began: only one
    // opened since then lets it reach itself.
    std::int64_t opened_at = 0;
    ir::Type* shell = nullptr;
    // For an instance: what each parameter was given, and its definition.
    std::optional<Dict<std::string, const ir::Type*>> bound;
    NamedType* template_ = nullptr;
    // The instances made so far, by the types they were given.
    OrderedMap<Types, NamedType*> instances;
    // The names the file that wrote it could see, and its bundles.
    Dict<std::string, Held>* scope = nullptr;
    Dict<std::string, Bundle*>* siblings = nullptr;
    bool refused = false;
    OrderedSet<Types> unmet;

    // Whether this is a definition with type parameters, not yet given any.
    bool takes_types() const;
  };

  // A variable defined at the top level, and what it says it raises.
  struct Global {
    ir::GlobalVar* var = nullptr;
    Span span;
    Expectation* expectation = nullptr;
    std::vector<Expected> expected_pairs;
  };

  // A function written with `⇉`: what a call to it makes is a walk.
  struct Generator {
    const ast::FuncDef* node = nullptr;
    // Nothing for a template, which is compiled per set of types.
    ir::Function* func = nullptr;
    std::vector<const ir::Type*> params;
    const ir::Type* element = nullptr;
    bool impure = false;
    ir::CoroutineFrame* frame = nullptr;
    std::string path;
    ir::Function* step = nullptr;
    bool exported = false;
    Dict<std::string, Held>* scope = nullptr;
    Dict<std::string, Bundle*>* siblings = nullptr;
    std::vector<std::string> parameters;
    std::vector<BoundAttr> attrs;
    OrderedMap<Types, Generator*> instances;
    Dict<std::string, const ir::Type*> bound;
    const ir::Type* given = nullptr;
    const ir::Type* finishes = nullptr;
  };

  // A function with type parameters: what was written, kept to be repeated.
  struct Generic {
    const ast::FuncDef* node = nullptr;
    std::string path;
    std::vector<BoundAttr> attrs;
    std::vector<std::string> parameters;
    bool exported = false;
    Dict<std::string, Held>* scope = nullptr;
    Dict<std::string, Bundle*>* siblings = nullptr;
    // And the units it could name and the ones it said may stand for others,
    // which are the file's too -- settled before any function is collected.
    std::optional<std::vector<std::tuple<const ir::Unit*, const ir::Unit*>>> stands;
    Dict<std::string, const ir::Unit*> units;
    OrderedMap<Types, ir::Function*> made;
    OrderedSet<Types> making;
    bool refused = false;
    std::optional<std::vector<const ast::Param*>> written_params;
  };

  // A name for a set of requirements, kept to be substituted into.
  struct Bundle {
    const ast::BundleDef* node = nullptr;
    std::string path;
    Dict<std::string, Held>* scope = nullptr;
    Dict<std::string, Bundle*>* siblings = nullptr;
    bool exported = false;
    bool opening = false;
  };

  // A generic function being checked where it is written: what each
  // operation the requirements name answers, by the operation, its name and
  // its operands' types.
  struct Abstract {
    std::string name;
    OrderedMap<std::tuple<std::string, std::string, Types>, const ir::Type*> shapes;
    std::vector<Span> deferred;
  };

  // What one function's body did that `@[impure]` or `@[io]` is written for.
  struct Marks {
    // A FuncDef or a Lambda.
    const ast::Node* node = nullptr;
    std::optional<Span> impure_at;
    std::optional<Span> io_at;
    bool holds_device = false;
    bool changes = false;
    bool does_io = false;
    // Functions, or a generator's definition.
    std::vector<Held> changes_through;
    std::vector<Held> io_through;
  };

  // A statement put off until its block is left, and where it was written.
  struct OwnedRoom;

  struct Deferred {
    const ast::Defer* node = nullptr;
    std::int64_t depth = 0;
    Span block;
    // Where it is no statement at all but the room of a variable given back,
    // which the compiler puts off and nothing in the program wrote.
    OwnedRoom* room = nullptr;
  };

  // One name of a room's variable on the way up to the statement it is in: the
  // node, and the type of what it reads where that is known.
  using RoomStep = std::pair<const ast::Node*, const ir::Type*>;
  // Where in a scope a variable may hand its room on: by the statement's
  // identity, each name of it there with its steps up, and whether it is handed
  // to a parameter the callee takes as its own.
  using RoomHands = Dict<std::int64_t, std::vector<std::pair<std::vector<RoomStep>, bool>>>;

  // A variable of the body's own whose room is its own, given back where its
  // scope ends -- unless it has handed the room on by then (`_Room`).  Whether
  // it still holds the room is the truth value `owns` stands for: a local the
  // compiler made beside the variable, which every `if`, `match` and loop
  // carries as it carries the variable itself.
  struct OwnedRoom {
    Local* local = nullptr;
    Local* owns = nullptr;
    // The statements of its scope that may hand the room on, by identity, and
    // for each the names of the variable in it that do, with the nodes from
    // each up to the statement; marked where it is handed to a parameter the
    // callee takes as its own, the call deciding.
    RoomHands hands;
    // What `owned::give_back` is told of the allocator.
    std::optional<std::string> known;
  };

  // One name's state at one point of a body: the arenas it was made in, and
  // where and by which arena it was killed, if it was.
  struct NameState {
    Local* local = nullptr;
    KeySet arenas;
    std::optional<Span> gone_at;
    std::string gone_with;
    bool owes = false;
    std::optional<Span> ended_at;
    std::string ended_how;
  };

  // Every name's state at one point of a body, by the name's identity.
  using Provenance = Dict<std::int64_t, NameState>;

  // The state a loop carries from turn to turn, and how a turn moves it on.
  using LoopState = std::vector<ir::Value*>;
  using Step = std::function<LoopState(ir::Builder&, std::span<ir::Value* const>)>;
  using Ask = std::function<ir::Value*(ir::Builder&, std::span<ir::Value* const>)>;

  // A loop being lowered that has a name, and the two places a jump goes.
  struct Loop {
    std::string label;
    Span span;
    ir::BasicBlock* header = nullptr;
    ir::BasicBlock* after = nullptr;
    std::vector<Local*> carried;
    LoopState state;
    Step step;
    bool answers = false;
    bool wraps = false;
    const ir::Type* handing = nullptr;
    std::int64_t frame = 0;
    std::vector<Provenance> left_with;
    std::optional<Span> handed_at;
    std::optional<Span> bare_at;
    bool named = false;
    std::int64_t scope_at = 0;
    std::optional<Provenance> turn_began;
  };

  // What a collection's entries came to, and what was wanted of them.
  struct Entries {
    std::vector<ir::Value*> keys;
    std::vector<ir::Value*> values;
    const ir::Type* wanted_key = nullptr;
    const ir::Type* wanted_value = nullptr;
  };

  // A mask: a value, or a function asking it a place at a time.
  using Mask = std::variant<std::monostate, ir::Value*, std::function<ir::Value*(ir::Builder&, ir::Value*)>>;

  // What a name bound to something a loop can walk stands for.
  struct Iterable {
    const ir::Type* element = nullptr;
    const ast::Expr* written = nullptr;
    ir::Value* base = nullptr;
    const ir::Type* ty = nullptr;
    Mask mask;
    std::vector<std::string> lent;
    Generator* generator = nullptr;
    std::vector<ir::Value*> args;
    ir::Value* arena = nullptr;
  };

  // A whole number an expression comes to while compiling.
  struct Folded {
    __int128 value = 0;
    const ir::IntType* ty = nullptr;
    bool failed = false;
    bool too_large = false;
    // Whether this is Python's `_REFUSED` itself, which `is` tells apart from
    // an equal `_Folded(0, None, failed=True)`.
    bool refused = false;
  };

  // What folding answers where the expression is a constant and something about
  // it is wrong: it has been reported.  Python's `_REFUSED` (the later of the
  // two definitions, which is the one every use sees); told apart by `refused`.
  struct Refusal {};

  // Python's `_REFUSED`, and `x is _REFUSED`.
  inline Folded refused_fold() { return Folded{ .value = 0, .ty = nullptr, .failed = true, .refused = true }; }
  inline bool is_refused(const std::optional<Folded>& found) { return found.has_value() && found->refused; }

  // What a loop needs of the thing it takes its values from.
  struct Iteration {
    const ir::Type* element = nullptr;
    LoopState start;
    Ask more;
    Ask take;
    Step step;
    Ask wanted;
    Step passed;
    std::function<void(ir::Builder&)> finish;
    Ask place;
    // For a turn taken apart into several names: each member and where it is.
    std::optional<std::vector<std::pair<Ask, Ask>>> members;
  };

  // How an array's elements are laid out: where the first is, how many there
  // are along each dimension, how far apart neighbours are along each, and
  // whether that is one run.
  struct Laid {
    ir::Value* first = nullptr;
    std::vector<ir::Value*> counts;
    std::vector<ir::Value*> strides;
    bool one_run = false;
  };


  // -- constants --------------------------------------------------------------

  // The type every startup function must return.
  inline constexpr std::string_view startup_return_type_name = "u6";
  inline constexpr std::string_view std_module_name = "std";
  inline constexpr std::string_view startup_argument_type_name = "Init";
  inline constexpr std::string_view build_type_name = "Build";
  // How many arguments the kernel takes after the number.
  inline constexpr std::int64_t syscall_arguments = 6;
  // The most bytes a view copied into an array is read as one value where it
  // turns out to be one run; past it the elements are copied in a loop.
  inline constexpr std::int64_t as_one_at_most = 256;
  extern const std::string lambda_prefix;
  extern const std::string shim_prefix;
  inline constexpr std::string_view generic_mark = "'";
  inline constexpr std::string_view each_name = "each";
  extern const std::string quote_name, fill_name, fill_number_name, fill_text_name, refuse_name, head_name,
    kind_name, piece_name, parts_name, part_name, apply_name, alike_pieces_name, caller_name_name;
  extern const std::string array_pair, table_pair, dummy_prefix;
  // Where `→ T in ⎕heap`, a handed-out arena and `⎕static` put an answer.
  inline constexpr std::int64_t heap_at = -1;
  inline constexpr std::int64_t hands_at = -2;
  inline constexpr std::int64_t static_at = -3;
  // The one singleton the compiler fills in: type, name, module.
  inline constexpr std::string_view filled_singleton_type = "State";
  inline constexpr std::string_view filled_singleton_name = "cwd";
  inline constexpr std::string_view filled_singleton_module = "fs";
  inline constexpr std::int64_t at_fdcwd = -100;
  inline constexpr std::string_view tolerance_symbol = "__pl4g_tolerance";
  inline constexpr std::string_view heap_symbol = "__pl4g_heap";
  inline constexpr std::string_view globals_symbol = "__pl4g_globals";
  // What the generated function that gives a `constinit` variable its value is
  // called, the variable's name after it (check.py:INITIALIZER_SYMBOL).
  inline constexpr std::string_view initializer_symbol = "__pl4g_constinit.";
  inline constexpr std::string_view text_symbol = "__pl4g_text.";
  inline constexpr std::string_view static_symbol = "__pl4g_static.";
  inline constexpr std::int64_t instances_deep = 8;
  // What a value made in `⎕heap` is made in, and what one that may be in the
  // image carries.
  inline constexpr std::int64_t heap_key = -1;
  extern const std::int64_t image_key;
  // What a name killed by replacing an element carries for the arena's name.
  inline constexpr std::string_view replaced = std::string_view("\0", 1);

  // The SI units and the two the compiler counts with, and `#`'s type.
  extern const std::vector<std::string> si_units;
  extern const std::vector<std::string> counting_units;
  const Dict<std::string, const ir::Unit*>& builtin_units();
  const ir::Unit* idx_unit();
  const ir::Unit* size_unit();
  const ir::IntType* size_type();
  // What a lambda carries what it brought in through: a stray `&mut u8`.
  const ir::PtrType* environment_type();
  // The record the entry point lays out, as what an address may point into.
  ir::GlobalVar* startup_record();
  // The layout the checker measures with.
  const ir::DataLayout& layout();
  // What the bytes of a value held in memory are copied in, largest first.
  const std::vector<std::pair<const ir::Type*, std::int64_t>>& chunks();
  // The conventions every target knows.
  const NameSet& every_target_knows();
  // What the purity rules report.
  const std::set<DiagId>& purity();
  // The kinds of node that bind a name, and the fields the names are in.
  const Dict<std::string, std::vector<std::string>>& binds();
  // What each question about a piece of the program takes, and what each
  // of the macro builtins answers.
  const Dict<std::string, std::vector<const ir::Type*>>& asks_about_a_piece();
  const Dict<std::string, const ir::Type*>& syntax_answers();

  // The operator tables: what each operator of the source means in the IR.
  const std::set<ast::BinaryOp>& derives();
  const std::map<ast::BinaryOp, std::pair<ir::CmpPred, ir::CmpPred>>& comparisons();
  const std::map<ast::BinaryOp, std::pair<ir::BinOp, bool>>& logic_ops();
  const std::map<ast::BinaryOp, bool>& short_circuit();
  const std::map<ast::BinaryOp, std::pair<ir::BinOp, ir::BinOp>>& shifts();
  const std::set<ast::BinaryOp>& orderings();
  const std::map<ast::BinaryOp, std::tuple<bool, bool, ir::CmpPred>>& approximate();
  const std::set<ast::BinaryOp>& exact_on_floats();
  const std::set<ast::BinaryOp>& all_at_once();
  const std::set<ast::UnaryOp>& all_at_once_unary();
  const std::map<ir::BinOp, ir::BinOp>& wrapped();
  const std::set<ast::BinaryOp>& saturates();
  const std::map<ast::BinaryOp, std::pair<ir::BinOp, ir::BinOp>>& extrema();
  const std::map<ast::BinaryOp, ir::BinOp>& binary_ops();
  const std::set<ast::BinaryOp>& on_flags();
  const std::set<ast::BinaryOp>& on_sets();
  const std::set<ast::BinaryOp>& on_floats();
  const std::map<ast::UnaryOp, ir::UnOp>& unary_ops();
  const std::set<ast::BinaryOp>& divides();
  const std::set<ast::UnaryOp>& divides_unary();
  const std::map<ast::UnaryOp, ir::UnOp>& roundings();
  // The kinds of report that are what the compiler said rather than chose.
  const NameSet& said();

  // Python's `_EACH_STEPS`, `_CALLING` and `_APPLYING`: kinds of node.
  bool is_each_step(const ast::Node* node);
  bool is_calling(const ast::Node* node);
  bool is_applying(const ast::Node* node);

  // A key of the provenance: what a container of `⎕heap`'s carries, and what
  // a value read out of one does.
  std::int64_t container_key(const void* local);
  bool is_container_key(std::int64_t key);
  std::int64_t element_key(std::int64_t key);
  bool is_element_key(std::int64_t key);


} // namespace pl4g::sema

#include "sema/check-helpers.hh"
