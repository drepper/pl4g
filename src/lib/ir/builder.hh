// Building instructions at a place in a function.
//
// A port of pypl4g's ir/builder.py, call for call, so that what the checker and
// the passes make here is instruction for instruction what they make there --
// which is what lets the two be compared by the text of what they made.
#pragma once

#include <optional>
#include <span>
#include <vector>

#include "ir/ir.hh"
#include "source/location.hh"


namespace pl4g::ir {

  class Builder {
  public:
    // Build in `func`, at the end of its entry block until told otherwise.
    Builder(Module& module, Function& func) : module_(module), func(func), block_(func.entry()) {}

    Module& module() const noexcept { return module_; }
    Function& function() const noexcept { return func; }
    BasicBlock* block() const noexcept { return block_; }
    // Whether there is nowhere to add to: no block, or one already ended.
    bool is_terminated() const noexcept { return block_ == nullptr || block_->terminator() != nullptr; }

    BasicBlock* new_block(std::optional<std::string> label = std::nullopt) { return func.add_block(std::move(label)); }
    void position_at(BasicBlock* block) noexcept { block_ = block; }

    // An instruction made here and added where the builder is, unless the
    // block there has ended, in which case it is made and goes nowhere.
    Instruction* append(Instruction* inst);
    Instruction* make(InstKind kind, const Type* ty, std::initializer_list<Value*> operands, Span span = invalid_span);

    Value* int_const(const Type* ty, __int128 value) { return const_cast<IntConst*>(module_.int_const(ty, value)); }
    Value* float_const(const Type* ty, double value) { return const_cast<FloatConst*>(module_.float_const(ty, value)); }
    Value* bool_const(bool value) { return const_cast<BoolConst*>(module_.bool_const(value)); }

    Value* binary(BinOp op, Value* lhs, Value* rhs, Span span = invalid_span, const Type* ty = nullptr);
    Value* wrap(Value* value, Value* failed, const Type* result, Span span = invalid_span, Value* error = nullptr);
    Value* make_tuple(std::span<Value* const> values, const Type* ty, Span span = invalid_span);
    Value* extract(Value* value, std::int64_t index, const Type* ty, Span span = invalid_span);
    // A value's parts, each a value of its own, and a value made of parts.
    std::vector<Value*> leaves(Value* value, const Type* ty, Span span = invalid_span);
    Value* whole(std::span<Value* const> parts, const Type* ty, Span span = invalid_span);
    Value* unwrap(Value* value, const Type* ok, Span span = invalid_span);
    Value* failed(Value* value, Span span = invalid_span);
    Value* error(Value* value, const Type* err, Span span = invalid_span);
    Value* unary(UnOp op, Value* value, const Type* ty = nullptr, Span span = invalid_span);
    Value* select(Value* cond, Value* if_true, Value* if_false, Span span = invalid_span);
    Value* compare(CmpPred pred, Value* lhs, Value* rhs, Span span = invalid_span);
    Value* splat(Value* value, const Type* target, Span span = invalid_span);
    Value* any_lane(Value* value, Span span = invalid_span);
    // A call of `callee`, or, where it is nothing, of the first of `args`.
    Value* call(Function* callee, std::span<Value* const> args, const Type* result, Span span = invalid_span);
    Value* cast(CastKind kind, Value* value, const Type* target, Span span = invalid_span);
    // The memory token in force, starting the chain at the top of the
    // function the first time it is asked for.
    Value* memory();
    // The memory token in force, or nothing where the chain has not begun --
    // asked without beginning it.
    Value* memory_so_far() const noexcept { return memory_; }
    void set_memory(Value* token) noexcept { memory_ = token; }
    Value* frame(const Type* held, Span span = invalid_span);
    Value* size_of(const Type* held, Span span = invalid_span);
    Value* check(Value* condition, std::string what, int status, Span span = invalid_span, bool observing = false);
    Value* address(Value* var, Span span = invalid_span);
    Value* code_address(Function* callee, Span span = invalid_span);
    Value* load(Value* address, Span span = invalid_span, Ordering ordering = Ordering::plain);
    Value* store(Value* address, Value* value, Span span = invalid_span, Ordering ordering = Ordering::plain);
    // A value held in memory copied from one place to another, a word, a half,
    // a quarter or a byte at a time.
    Value* copy(Value* into, Value* from, const Type* ty, Span span = invalid_span);
    Value* syscall(Value* number, std::span<Value* const> args, const Type* result, Span span = invalid_span);
    Instruction* ret(Value* value = nullptr, Span span = invalid_span);
    Instruction* br(BasicBlock* block, std::span<Value* const> args = {}, Span span = invalid_span);
    Instruction* condbr(Value* cond, BasicBlock* if_true, BasicBlock* if_false, std::span<Value* const> true_args = {},
                        std::span<Value* const> false_args = {}, Span span = invalid_span);
    Instruction* switch_(Value* value, std::span<const std::pair<__int128, BasicBlock*>> cases, BasicBlock* otherwise,
                         Span span = invalid_span);
    Instruction* yield(BasicBlock* block, Span span = invalid_span, bool delegates = false);
    Instruction* unreachable(Span span = invalid_span);

  private:
    Value* piece(Value* place, const Type* part, std::uint64_t offset, Span span);

    Module& module_;
    Function& func;
    BasicBlock* block_;
    Value* memory_ = nullptr;
  };


  // -- rewriting --------------------------------------------------------------

  // Every use of `gone` in `func` made a use of `instead`.
  void stands_for(Function& func, Value* gone, Value* instead);

  // The same for every value `instead` maps.
  void all_stand_for(Function& func, const std::unordered_map<const Value*, Value*>& instead);

  // What branches into each block, and from where.
  std::unordered_map<const BasicBlock*, std::vector<std::pair<BasicBlock*, BlockTarget*>>>
  incoming_edges(Function& func);

  // The parameters a branch hands values to, beside the values, leaving out
  // the memory token.
  std::vector<std::pair<BlockParam*, Value*>> carried_values(const BlockTarget& target);

  // What the stub that finds a function the host defines is called, before the
  // host's symbol: the compiler's own prefix, so that a lookup never finds it.
  inline constexpr std::string_view host_prefix = "__pl4g_host.";

  // The name a function is known by in an image.
  std::string symbol_name(const Function& func);
  std::string mangle(std::string_view name, const FuncType& ty, std::string_view module);

} // namespace pl4g::ir
