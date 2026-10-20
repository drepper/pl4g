// Values, instructions, blocks, functions and modules of the IR.
//
// SSA with block parameters, as pypl4g has it: a block's parameters take the
// place of phi nodes, a branch hands over the values they take, and memory
// effects are ordered by a token every load takes and every store makes.
//
// An instruction is one struct whatever its shape, the shape a field of it
// beside the operation within the shape -- which is what an interpreter
// dispatching on it wants and what keeps the passes from casting.
#pragma once

#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "ir/types.hh"
#include "source/location.hh"
#include "util/reflect.hh"


namespace pl4g::ir {

  struct BasicBlock;
  struct Function;
  struct GlobalVar;


  // -- values -----------------------------------------------------------------

  enum class ValueKind : std::uint8_t {
    int_const, char_const, float_const, bool_const, enum_const, array_const, record_const, result_const,
    sum_const, address_const, parts_const, undef, argument, block_param, instruction, global, function,
  };

  // Anything an instruction may use.  Compared by identity.
  struct Value {
    ValueKind vkind;
    const Type* ty;
    std::optional<std::string> name_hint;
    Span name_span;

    Value(ValueKind vkind, const Type* ty) noexcept : vkind(vkind), ty(ty) {}
    Value(const Value&) = delete;
    Value& operator=(const Value&) = delete;
    virtual ~Value() = default;

    bool is_const() const noexcept { return vkind <= ValueKind::undef; }
  };

  struct IntConst final : Value {
    __int128 value;
    IntConst(const Type* ty, __int128 value) : Value(ValueKind::int_const, ty), value(value) {}
  };

  struct CharConst final : Value {
    std::uint32_t value;
    CharConst(const Type* ty, std::uint32_t value) : Value(ValueKind::char_const, ty), value(value) {}
  };

  struct FloatConst final : Value {
    double value;
    FloatConst(const Type* ty, double value) : Value(ValueKind::float_const, ty), value(value) {}
  };

  struct BoolConst final : Value {
    bool value;
    BoolConst(const Type* ty, bool value) : Value(ValueKind::bool_const, ty), value(value) {}
  };

  struct EnumConst final : Value {
    std::size_t index;
    EnumConst(const Type* ty, std::size_t index) : Value(ValueKind::enum_const, ty), index(index) {}
    // The number the value is stored as.
    std::int64_t number() const { return static_cast<const EnumType*>(ty)->values[index]; }
  };

  struct ArrayConst final : Value {
    std::vector<const Value*> elements;
    ArrayConst(const Type* ty, std::vector<const Value*> elements)
      : Value(ValueKind::array_const, ty), elements(std::move(elements)) {}
  };

  struct RecordConst final : Value {
    std::vector<const Value*> fields;
    RecordConst(const Type* ty, std::vector<const Value*> fields)
      : Value(ValueKind::record_const, ty), fields(std::move(fields)) {}
  };

  struct ResultConst final : Value {
    const Value* answer;
    bool failed;
    ResultConst(const Type* ty, const Value* answer, bool failed)
      : Value(ValueKind::result_const, ty), answer(answer), failed(failed) {}
  };

  struct SumConst final : Value {
    std::size_t index;
    const Value* part;
    SumConst(const Type* ty, std::size_t index, const Value* part)
      : Value(ValueKind::sum_const, ty), index(index), part(part) {}
  };

  // Where something of the module is: a global or a function, and how far in.
  struct AddressConst final : Value {
    const Value* target;
    std::int64_t offset;
    AddressConst(const Type* ty, const Value* target, std::int64_t offset)
      : Value(ValueKind::address_const, ty), target(target), offset(offset) {}
  };

  // A value of several parts, each a constant.
  struct PartsConst final : Value {
    std::vector<const Value*> parts;
    PartsConst(const Type* ty, std::vector<const Value*> parts)
      : Value(ValueKind::parts_const, ty), parts(std::move(parts)) {}
  };

  struct UndefConst final : Value {
    explicit UndefConst(const Type* ty) : Value(ValueKind::undef, ty) {}
  };

  struct BlockParam final : Value {
    BasicBlock* block;
    std::size_t index;
    BlockParam(const Type* ty, BasicBlock* block, std::size_t index)
      : Value(ValueKind::block_param, ty), block(block), index(index) {}
  };


  // -- instructions -----------------------------------------------------------

  // The shapes an instruction has, named as pypl4g's classes are.
  enum class InstKind : std::uint8_t {
    BinaryInst, UnaryInst, CmpInst, SelectInst, SplatInst, AnyLaneInst, WrapInst, TupleInst, ExtractInst,
    UnwrapInst, ErrorInst, FailedInst, CastInst, CallInst, SyscallInst, AddressInst, CodeInst, FrameInst,
    SizeOfInst, AssertInst, MemStartInst, LoadInst, StoreInst, FieldInst, SumMakeInst, SumTagInst, SumGetInst,
    RetInst, BrInst, CondBrInst, SwitchInst, YieldInst, UnreachableInst,
  };

  enum class BinOp : std::uint8_t {
    add [[=Describe("add")]], sub [[=Describe("sub")]], mul [[=Describe("mul")]], sdiv [[=Describe("sdiv")]],
    udiv [[=Describe("udiv")]], srem [[=Describe("srem")]], urem [[=Describe("urem")]], and_ [[=Describe("and")]],
    or_ [[=Describe("or")]], xor_ [[=Describe("xor")]], shl [[=Describe("shl")]], ashr [[=Describe("ashr")]],
    lshr [[=Describe("lshr")]], rotl [[=Describe("rotl")]], rotr [[=Describe("rotr")]], fdiv [[=Describe("fdiv")]],
    sat_add [[=Describe("sat.add")]], sat_sub [[=Describe("sat.sub")]], sat_mul [[=Describe("sat.mul")]],
    wrap_add [[=Describe("wrap.add")]], wrap_sub [[=Describe("wrap.sub")]], wrap_mul [[=Describe("wrap.mul")]],
    smax [[=Describe("smax")]], umax [[=Describe("umax")]], smin [[=Describe("smin")]], umin [[=Describe("umin")]],
    wrap_shl [[=Describe("wrap.shl")]], wrap_ashr [[=Describe("wrap.ashr")]], wrap_lshr [[=Describe("wrap.lshr")]],
    wrap_rotl [[=Describe("wrap.rotl")]], wrap_rotr [[=Describe("wrap.rotr")]],
  };

  enum class UnOp : std::uint8_t {
    neg [[=Describe("neg")]], not_ [[=Describe("not")]], count_ones [[=Describe("ones")]],
    count_leading [[=Describe("lead")]], fabs [[=Describe("fabs")]], floor [[=Describe("floor")]],
    ceil [[=Describe("ceil")]], nearest [[=Describe("nearest")]], rounded [[=Describe("rounded")]],
  };

  enum class CmpPred : std::uint8_t { eq, ne, slt, sle, sgt, sge, ult, ule, ugt, uge };

  enum class CastKind : std::uint8_t { zext, sext, trunc, bitcast, fext };

  enum class Ordering : std::uint8_t { plain, acquire, release };


  // A branch destination and what it hands the block's parameters.
  struct BlockTarget {
    BasicBlock* block = nullptr;
    std::vector<Value*> args;
  };


  struct Instruction final : Value {
    InstKind kind;
    std::vector<Value*> operands;
    BasicBlock* parent = nullptr;
    Span span;

    // The operation within the shape, where the shape has several.
    BinOp bin{};
    UnOp un{};
    CmpPred pred{};
    CastKind cast{};
    Ordering ordering{};
    // The position an extract, a field or a sum's variant names.
    std::int64_t index = 0;
    // The function a direct call calls, or whose code `code` answers.  An
    // indirect call has none: what it calls is its first operand.
    Function* callee = nullptr;
    // What a frame holds room for, and what `sizeof` measures.
    const Type* held = nullptr;
    // An assertion's message, the status it stops with, and whether it goes on.
    std::string what;
    int status = 0;
    bool observing = false;
    // A terminator's destinations, in the order pypl4g's `successors` gives
    // them: a branch's one, a conditional branch's true and false, a switch's
    // cases and then its default, a yield's one.
    std::vector<BlockTarget> targets;
    std::vector<__int128> cases;
    bool delegates = false;

    Instruction(InstKind kind, const Type* ty) : Value(ValueKind::instruction, ty), kind(kind) {}

    // Whether it defines a value that can be named.
    bool has_result() const noexcept { return ! is<VoidType>(ty); }
    bool is_terminator() const noexcept { return kind >= InstKind::RetInst; }
    // The mnemonic of the textual form.
    std::string opcode() const;
    // What a call hands over: every operand but an indirect callee.
    std::span<Value* const> arguments() const noexcept
    {
      return callee == nullptr && kind == InstKind::CallInst ? std::span(operands).subspan(1) : std::span(operands);
    }
  };


  // -- blocks, functions, modules ---------------------------------------------

  struct BasicBlock {
    std::string label;
    Function* parent = nullptr;
    std::vector<std::unique_ptr<BlockParam>> params;
    std::vector<Instruction*> insts;

    BlockParam* add_param(const Type* ty);
    // A parameter with the name a reader of the text sees it by.
    BlockParam* add_param(const Type* ty, std::string name_hint)
    {
      auto param = add_param(ty);
      param->name_hint = std::move(name_hint);
      return param;
    }
    Instruction* terminator() const noexcept
    {
      return ! insts.empty() && insts.back()->is_terminator() ? insts.back() : nullptr;
    }
  };


  enum class Linkage : std::uint8_t { internal, visible, imported };

  enum class SpecialKind : std::uint8_t {
    startup, build, constructor, destructor, test_always, test_build, test_suite,
  };

  enum class InlineHint : std::uint8_t { default_, always, never };

  enum class ReturnStyle : std::uint8_t { two_in_registers, three_in_registers };

  struct FuncAttrs {
    std::optional<SpecialKind> special;
    std::optional<std::int64_t> priority;
    InlineHint inline_ = InlineHint::default_;
    std::optional<std::string> abi;
    std::optional<std::string> external;
    bool builtin = false;
    bool can_ignore = false;
    bool impure = false;
    bool io = false;
    bool opaque = false;
    bool answer_in_storage = false;
    bool chained = false;
    bool listable = false;
    // pypl4g's `extra`: what `@[cdecl]`/`@[abi]` say of variadic calls, and
    // `@[align]` and `@[section]`.
    bool variadic = false;
    std::optional<std::int64_t> align;
    std::optional<std::string> section;
  };

  inline constexpr std::string_view default_cconv = "pl4g";
  inline constexpr std::string_view system_cconv = "cdecl";

  struct Function final : Value {
    std::string name;
    const FuncType* fty;
    FuncAttrs attrs;
    Linkage linkage = Linkage::internal;
    std::string cconv{ default_cconv };
    std::vector<std::unique_ptr<BasicBlock>> blocks;
    Span span;
    std::string source_path;
    std::string module;
    bool exported = false;
    std::vector<std::string> param_names;
    std::optional<ReturnStyle> answering;
    Span name_span;
    // Where a string or a list it answers with is made: `heap`, `static`, or
    // `param` and which one -- what lets its answer travel without its
    // allocator.
    std::optional<std::pair<std::string, int>> answer_from;
    // The frame of a generator, which the pass splitting it measures.
    const CoroutineFrame* coroutine = nullptr;
    // What the checker writes down about a function for its callers (pypl4g's
    // `Function` fields of the same names): the documentation comment, which
    // parameters are arrays it writes and lists it takes as its own, what each
    // parameter defaults to, which parameters its answer (and each leaf of a
    // tuple answer) borrows from, which pairs of parameters keep each other,
    // its lifetime names, which arenas its answer and each parameter were
    // made in, and the arena of its own it hands out with its answer.
    std::string doc;
    std::vector<int> writes_arrays;
    std::vector<int> takes_owned;
    std::vector<const Value*> defaults;
    std::vector<int> borrows_from;
    std::vector<std::pair<int, std::vector<int>>> leaf_borrows;
    std::vector<std::pair<int, int>> keeps;
    std::string lifetime_key;
    std::vector<std::pair<std::string, std::vector<int>>> lifetimes;
    std::optional<std::vector<int>> made_in;
    std::vector<std::optional<int>> param_made_in;
    std::optional<std::string> hands_out;
    // Block parameters a pass replaced, kept while anything may still name them.
    std::vector<std::unique_ptr<BlockParam>> retired;
    // Blocks a pass took out, kept as their parameters are.
    std::vector<std::unique_ptr<BasicBlock>> retired_blocks;

    Function(std::string name, const FuncType* ty) : Value(ValueKind::function, ty), name(std::move(name)), fty(ty) {}

    bool is_declaration() const noexcept { return blocks.empty(); }
    BasicBlock* entry() const noexcept { return blocks.empty() ? nullptr : blocks.front().get(); }
    // The style it answers in: what it says, or what its convention does.
    ReturnStyle answer_style() const noexcept
    {
      if (answering)
        return *answering;
      return cconv == default_cconv ? ReturnStyle::three_in_registers : ReturnStyle::two_in_registers;
    }
    // A new block at the end, its label made unique.
    BasicBlock* add_block(std::optional<std::string> label = std::nullopt);
    // The label a new block would be given.
    std::string unique_label(std::optional<std::string> label) const;
    // A block not yet in the function, for a pass to put where it wants.
    std::unique_ptr<BasicBlock> detached_block(std::string label);
    // Give it another type, which a pass changing how it answers does.
    void set_type(const FuncType* t) noexcept
    {
      fty = t;
      ty = t;
    }
    // A new instruction, kept by the function, not yet in any block.
    Instruction* make(InstKind kind, const Type* ty);

  private:
    std::deque<std::unique_ptr<Instruction>> pool;
  };


  struct GlobalVar final : Value {
    std::string name;
    const Type* value_type;
    Linkage linkage = Linkage::internal;
    const Value* initializer = nullptr;
    Span span;
    std::string module;
    bool exported = false;
    bool system_layout = false;

    GlobalVar(std::string name, const Type* value_type, const Type* ptr)
      : Value(ValueKind::global, ptr), name(std::move(name)), value_type(value_type) {}

    bool is_mutable() const noexcept { return static_cast<const PtrType*>(ty)->is_mutable; }
  };


  // What the compiler chose about the program, written down beside what it
  // said, for a reader to see why: pypl4g's ir/reports.py.
  struct Report {
    std::string kind;
    std::string subject;
    std::string reason;
    Span span;
  };


  struct Module {
    std::string name;
    std::vector<Report> reports;
    // Where the files marked `@[system]` are: what the compiler chose about
    // something in one is not recorded, what it said is.
    std::vector<std::pair<Loc, Loc>> system_files;
    // Whether `span` begins in a system file.
    bool in_system(Span span) const noexcept
    {
      if (! span.is_valid())
        return false;
      for (auto [a, b] : system_files)
        if (a <= span.start && span.start <= b)
          return true;
      return false;
    }
    void record(std::string kind, std::string subject, std::string reason, Span span = invalid_span)
    {
      bool said = kind == "fatal" || kind == "error" || kind == "warning" || kind == "note";
      if (said || ! in_system(span))
        reports.push_back({ std::move(kind), std::move(subject), std::move(reason), span });
    }
    std::string triple = "x86_64-linux-none";
    // In the order they were added, under the key they were added with.
    std::vector<std::pair<std::string, std::unique_ptr<Function>>> functions;
    // Functions something calls that the table above does not hold: pypl4g
    // files two overloads of an operator under one key, the second replacing
    // the first, and the back end reaches the first through the call.
    std::vector<std::unique_ptr<Function>> unlisted;
    // Variables a pass dropped, kept while a constant may still name them.
    std::vector<std::unique_ptr<GlobalVar>> retired_globals;
    std::vector<std::pair<std::string, std::unique_ptr<GlobalVar>>> globals;
    // The texts of the program: globals kept apart from its variables.
    std::vector<GlobalVar*> texts;
    std::vector<std::unique_ptr<GlobalVar>> text_store;
    // Each of them by the text it holds: pypl4g's `texts` is a dict so keyed,
    // and `texts` above is its values in order.
    std::unordered_map<std::string, GlobalVar*> text_of;
    // The record the entry point lays out and hands the startup function,
    // where an address in the image points into it; no variable of the module.
    std::unique_ptr<GlobalVar> startup_record;
    Function* startup = nullptr;
    Function* build = nullptr;
    GlobalVar* environ = nullptr;
    std::vector<const Value*> ring_addresses;
    std::vector<Function*> ctors;
    std::vector<Function*> dtors;
    std::vector<Function*> tests;
    std::vector<Function*> test_plan;
    std::vector<std::string> source_paths;
    std::int64_t stack_size = 1 << 20;
    std::int64_t guard_size = 1 << 16;
    // How large each generator's frame came to, which the pass splitting it
    // measured.
    std::unordered_map<const CoroutineFrame*, std::uint64_t> frame_sizes;

    Function* add_function(std::unique_ptr<Function> func, std::optional<std::string> key = std::nullopt);
    GlobalVar* add_global(std::unique_ptr<GlobalVar> var, std::optional<std::string> key = std::nullopt);
    Function* function(std::string_view key) const;
    GlobalVar* global(std::string_view key) const;

    // Constants, made once each where pypl4g makes them once.
    const IntConst* int_const(const Type* ty, __int128 value);
    const BoolConst* bool_const(bool value);
    const FloatConst* float_const(const Type* ty, double value);
    const CharConst* char_const(std::uint32_t value);
    const UndefConst* undef(const Type* ty);
    // Interned by the type's identity and the parts', as pypl4g's are.
    const EnumConst* enum_const(const Type* ty, std::size_t index)
    {
      auto& found = enums[{ ty, index }];
      if (found == nullptr)
        found = constant<EnumConst>(ty, index);
      return found;
    }
    const ResultConst* result_const(const Type* ty, const Value* answer, bool failed = false)
    {
      auto& found = results[{ ty, answer, failed }];
      if (found == nullptr)
        found = constant<ResultConst>(ty, answer, failed);
      return found;
    }
    const ArrayConst* array_const(const Type* ty, std::vector<const Value*> elements)
    {
      auto& found = arrays[{ ty, elements }];
      if (found == nullptr)
        found = constant<ArrayConst>(ty, std::move(elements));
      return found;
    }
    const RecordConst* record_const(const Type* ty, std::vector<const Value*> fields)
    {
      auto& found = records[{ ty, fields }];
      if (found == nullptr)
        found = constant<RecordConst>(ty, std::move(fields));
      return found;
    }
    // Any other constant, kept here.
    template<typename T, typename... Args>
    const T* constant(Args&&... args)
    {
      auto made = std::make_unique<T>(std::forward<Args>(args)...);
      const T* raw = made.get();
      constants.push_back(std::move(made));
      return raw;
    }

  private:
    std::deque<std::unique_ptr<Value>> constants;
    std::map<std::pair<const Type*, __int128>, const IntConst*> ints;
    std::map<std::pair<const Type*, std::uint64_t>, const FloatConst*> floats;
    std::map<std::uint32_t, const CharConst*> chars;
    std::map<const Type*, const UndefConst*> undefs;
    std::map<std::pair<const Type*, std::size_t>, const EnumConst*> enums;
    std::map<std::tuple<const Type*, const Value*, bool>, const ResultConst*> results;
    std::map<std::pair<const Type*, std::vector<const Value*>>, const ArrayConst*> arrays;
    std::map<std::pair<const Type*, std::vector<const Value*>>, const RecordConst*> records;
    const BoolConst* bools[2] = { nullptr, nullptr };
  };

} // namespace pl4g::ir
