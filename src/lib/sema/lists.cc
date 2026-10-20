// What a list needs beyond what it is.
//
// Every builder call below is sequenced as pypl4g's are: where two operands of
// one instruction each make instructions of their own, the first is made into
// a local first.
#include "sema/lists.hh"

#include <vector>

#include "sema/strings.hh"
#include "sema/tables.hh"


namespace pl4g::sema::lists {

  namespace {

    using ir::BinOp;
    using ir::CastKind;
    using ir::CmpPred;
    using Args = std::vector<ir::Value*>;
    using Types = std::vector<const ir::Type*>;

    const ir::PtrType* bytes_type()
    {
      return ir::ptr_type(ir::u8(), true);
    }


    const ir::PtrType* allocator_type()
    {
      return ir::ptr_type(ir::arena_type(), true);
    }


    ir::Value* number(ir::Builder& builder, __int128 value)
    {
      return builder.int_const(ir::u64(), value);
    }


    // Whether `address` points nowhere.
    ir::Value* nowhere(ir::Builder& builder, ir::Value* address)
    {
      return builder.compare(CmpPred::eq, builder.cast(CastKind::bitcast, address, ir::u64()), number(builder, 0));
    }


    // Whether either of two addresses points nowhere.
    ir::Value* either_nowhere(ir::Builder& builder, ir::Value* one, ir::Value* other)
    {
      auto first = nowhere(builder, one);
      return builder.binary(BinOp::or_, first, nowhere(builder, other));
    }


    // Where the header of the elements at `where` is, as the word it holds.
    ir::Value* header_of(ir::Builder& builder, ir::Value* where)
    {
      return builder.cast(CastKind::bitcast, builder.binary(BinOp::sub, where, number(builder, header)),
                          ir::ptr_type(ir::u64(), true));
    }


    // A conditional branch handing the memory token to both sides.
    void branch_with_memory(ir::Builder& builder, ir::Value* cond, ir::BasicBlock* if_true, ir::BasicBlock* if_false)
    {
      auto mem = builder.memory();
      builder.condbr(cond, if_true, if_false, Args{ mem }, Args{ mem });
    }


    // A block's memory token, taken as its parameter.
    void take_memory(ir::Builder& builder, ir::BasicBlock* block)
    {
      builder.set_memory(block->add_param(ir::mem_type(), "mem"));
    }

  } // namespace


  ir::Function* erase_function(ir::Module& module)
  {
    auto bytes = bytes_type();
    auto [func, fresh] = tables::generated(module, erase_symbol, Types{ bytes, ir::u64(), ir::u64(), ir::u64() },
                                           ir::void_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto walk = func->add_block("walk");
    auto move = func->add_block("move");
    auto done = func->add_block("done");
    ir::Builder builder(module, *func);

    builder.position_at(entry);
    auto at = entry->add_param(bytes, "at");
    auto stride = entry->add_param(ir::u64(), "stride");
    auto count = entry->add_param(ir::u64(), "count");
    auto which = entry->add_param(ir::u64(), "which");
    // Where the bytes that move begin, and how many of them there are.
    auto start = builder.binary(BinOp::wrap_mul, which, stride);
    auto moving = builder.binary(
      BinOp::wrap_mul, stride,
      builder.binary(BinOp::wrap_sub, builder.binary(BinOp::wrap_sub, count, number(builder, 1)), which));
    builder.br(walk, Args{ number(builder, 0), builder.memory() });

    builder.position_at(walk);
    auto step = walk->add_param(ir::u64(), "step");
    auto token = walk->add_param(ir::mem_type(), "mem");
    builder.set_memory(token);
    builder.condbr(builder.compare(CmpPred::ult, step, moving), move, done);

    // One byte down by one element's worth, from the front.
    builder.position_at(move);
    auto where = builder.binary(BinOp::wrap_add, start, step);
    auto place = builder.binary(BinOp::add, at, where);
    builder.store(place, builder.load(builder.binary(BinOp::add, at, builder.binary(BinOp::wrap_add, where, stride))));
    builder.br(walk, Args{ builder.binary(BinOp::wrap_add, step, number(builder, 1)), builder.memory() });

    builder.position_at(done);
    // What holds after the loop is what the loop carried.
    builder.set_memory(token);
    builder.ret();
    return func;
  }


  ir::Function* room_function(ir::Module& module)
  {
    auto [func, fresh] = tables::generated(module, room_symbol, Types{ allocator_type(), ir::u64(), ir::u64() },
                                           bytes_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto none = func->add_block("none");
    auto some = func->add_block("some");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto allocator = entry->add_param(allocator_type(), "allocator");
    auto capacity = entry->add_param(ir::u64(), "capacity");
    auto stride = entry->add_param(ir::u64(), "stride");
    branch_with_memory(builder, builder.compare(CmpPred::eq, capacity, number(builder, 0)), none, some);
    builder.position_at(none);
    take_memory(builder, none);
    builder.ret(builder.cast(CastKind::bitcast, number(builder, 0), bytes_type()));
    builder.position_at(some);
    take_memory(builder, some);
    auto size = builder.binary(BinOp::wrap_add, number(builder, header),
                               builder.binary(BinOp::wrap_mul, capacity, stride));
    auto base = builder.call(tables::allocator_function(module), Args{ allocator, size }, bytes_type());
    builder.store(builder.cast(CastKind::bitcast, base, ir::ptr_type(ir::u64(), true)), capacity);
    builder.ret(builder.binary(BinOp::add, base, number(builder, header)));
    return func;
  }


  ir::Function* capacity_function(ir::Module& module)
  {
    auto [func, fresh] = tables::generated(module, capacity_symbol, Types{ bytes_type(), allocator_type() }, ir::u64());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto none = func->add_block("none");
    auto some = func->add_block("some");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto where = entry->add_param(bytes_type(), "where");
    auto allocator = entry->add_param(allocator_type(), "allocator");
    branch_with_memory(builder, either_nowhere(builder, where, allocator), none, some);
    builder.position_at(none);
    take_memory(builder, none);
    builder.ret(number(builder, 0));
    builder.position_at(some);
    take_memory(builder, some);
    builder.ret(builder.load(header_of(builder, where)));
    return func;
  }


  ir::Function* free_function(ir::Module& module)
  {
    auto [func, fresh] = tables::generated(module, free_symbol, Types{ allocator_type(), bytes_type(), ir::u64() },
                                           ir::void_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto none = func->add_block("none");
    auto some = func->add_block("some");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto allocator = entry->add_param(allocator_type(), "allocator");
    auto where = entry->add_param(bytes_type(), "where");
    auto stride = entry->add_param(ir::u64(), "stride");
    branch_with_memory(builder, either_nowhere(builder, where, allocator), none, some);
    builder.position_at(none);
    take_memory(builder, none);
    builder.ret();
    builder.position_at(some);
    take_memory(builder, some);
    auto head = header_of(builder, where);
    auto capacity = builder.load(head);
    auto dispose = tables::dispose_function(module);
    auto raw = builder.cast(CastKind::bitcast, head, bytes_type());
    auto size = builder.binary(BinOp::wrap_add, number(builder, header),
                               builder.binary(BinOp::wrap_mul, capacity, stride));
    builder.call(dispose, Args{ allocator, raw, size }, ir::void_type());
    builder.ret();
    return func;
  }


  ir::Function* grow_function(ir::Module& module)
  {
    auto bytes = bytes_type();
    auto [func, fresh] = tables::generated(
      module, grow_symbol, Types{ bytes, ir::u64(), allocator_type(), allocator_type(), ir::u64(), ir::u64() }, bytes);
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto enough = func->add_block("enough");
    auto more_room = func->add_block("grow");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto where = entry->add_param(bytes, "where");
    auto count = entry->add_param(ir::u64(), "count");
    auto allocator = entry->add_param(allocator_type(), "allocator");
    auto into = entry->add_param(allocator_type(), "into");
    auto more = entry->add_param(ir::u64(), "more");
    auto stride = entry->add_param(ir::u64(), "stride");
    auto wanted = builder.binary(BinOp::wrap_add, count, more);
    auto capacity = builder.call(capacity_function(module), Args{ where, allocator }, ir::u64());
    branch_with_memory(builder, builder.compare(CmpPred::ule, wanted, capacity), enough, more_room);
    builder.position_at(enough);
    take_memory(builder, enough);
    builder.ret(where);
    builder.position_at(more_room);
    take_memory(builder, more_room);
    auto doubled = builder.binary(BinOp::wrap_mul, capacity, number(builder, 2));
    auto room = builder.binary(BinOp::umax, builder.binary(BinOp::umax, wanted, doubled), number(builder, smallest));
    auto made = builder.call(room_function(module), Args{ into, room, stride }, bytes);
    strings::copy_bytes(builder, made, number(builder, 0), where, builder.binary(BinOp::wrap_mul, count, stride));
    builder.ret(made);
    return func;
  }


  ir::Function* release_function(ir::Module& module)
  {
    auto bytes = bytes_type();
    auto [func, fresh] = tables::generated(module, release_symbol, Types{ allocator_type(), bytes, bytes, ir::u64() },
                                           ir::void_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto same = func->add_block("same");
    auto moved = func->add_block("moved");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto allocator = entry->add_param(allocator_type(), "allocator");
    auto old = entry->add_param(bytes, "old");
    auto made = entry->add_param(bytes, "new");
    auto stride = entry->add_param(ir::u64(), "stride");
    {
      auto left = builder.cast(CastKind::bitcast, old, ir::u64());
      auto right = builder.cast(CastKind::bitcast, made, ir::u64());
      branch_with_memory(builder, builder.compare(CmpPred::eq, left, right), same, moved);
    }
    builder.position_at(same);
    take_memory(builder, same);
    builder.ret();
    builder.position_at(moved);
    take_memory(builder, moved);
    builder.call(free_function(module), Args{ allocator, old, stride }, ir::void_type());
    builder.ret();
    return func;
  }


  ir::Function* join_function(ir::Module& module)
  {
    auto bytes = bytes_type();
    auto [func, fresh] = tables::generated(
      module, join_symbol, Types{ allocator_type(), bytes, ir::u64(), bytes, ir::u64(), ir::u64() }, bytes);
    if (! fresh)
      return func;
    auto entry = func->add_block();
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto allocator = entry->add_param(allocator_type(), "allocator");
    auto first = entry->add_param(bytes, "first");
    auto first_count = entry->add_param(ir::u64(), "first.count");
    auto second = entry->add_param(bytes, "second");
    auto second_count = entry->add_param(ir::u64(), "second.count");
    auto stride = entry->add_param(ir::u64(), "stride");
    auto room = room_function(module);
    auto made = builder.call(room, Args{ allocator, builder.binary(BinOp::wrap_add, first_count, second_count), stride },
                             bytes);
    auto first_size = builder.binary(BinOp::wrap_mul, first_count, stride);
    strings::copy_bytes(builder, made, number(builder, 0), first, first_size);
    strings::copy_bytes(builder, made, first_size, second, builder.binary(BinOp::wrap_mul, second_count, stride));
    builder.ret(made);
    return func;
  }


  ir::Function* allocator_function(ir::Module& module)
  {
    auto [func, fresh] = tables::generated(module, allocator_symbol, Types{ allocator_type(), allocator_type() },
                                           allocator_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto own = func->add_block("own");
    auto other = func->add_block("other");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto allocator = entry->add_param(allocator_type(), "allocator");
    auto otherwise = entry->add_param(allocator_type(), "otherwise");
    branch_with_memory(builder, nowhere(builder, allocator), other, own);
    builder.position_at(own);
    take_memory(builder, own);
    builder.ret(allocator);
    builder.position_at(other);
    take_memory(builder, other);
    builder.ret(otherwise);
    return func;
  }

} // namespace pl4g::sema::lists
