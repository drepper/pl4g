// The hash table a set and a dictionary are, written in the representation.
//
// Every builder call below is sequenced as pypl4g's are: where two operands of
// one instruction each make instructions of their own, the first is made into
// a local first, C++ leaving the order of a call's arguments open and the
// text of the IR showing it.
#include "sema/tables.hh"

#include <algorithm>
#include <vector>

#include "ir/layout.hh"
#include "sema/strings.hh"
#include "target/statuses.hh"


namespace pl4g::sema::tables {

  namespace {

    using ir::BinOp;
    using ir::CastKind;
    using ir::CmpPred;
    using Args = std::vector<ir::Value*>;
    using Types = std::vector<const ir::Type*>;

    // What a word is here: a table is words whatever the target, and every
    // target is sixty-four bit.
    const ir::DataLayout layout{};


    // How many words a value of `ty` takes in an entry, rounded up.
    std::int64_t words(const ir::Type* ty)
    {
      return std::max<std::int64_t>(1, (static_cast<std::int64_t>(ir::size_of(ty, layout)) + word - 1) / word);
    }


    // A mutable pointer to `pointee`, as the context makes it.
    const ir::PtrType* mut_ptr(const ir::Type* pointee)
    {
      return ir::ptr_type(pointee, true);
    }


    // A word of the program, which is how every number here is written.
    ir::Value* number(ir::Builder& builder, __int128 value)
    {
      return builder.int_const(ir::u64(), value);
    }


    // The declaration of a function the backend supplies, made once, in the
    // system's convention.
    ir::Function* declared(ir::Module& module, std::string_view name, const Types& params, const ir::Type* result,
                           bool impure = true)
    {
      if (auto found = module.function(name); found != nullptr)
        return found;
      auto func = std::make_unique<ir::Function>(std::string(name), ir::func_type(params, result));
      func->attrs.abi = std::string(ir::system_cconv);
      func->attrs.impure = impure;
      func->cconv = std::string(ir::system_cconv);
      func->linkage = ir::Linkage::visible;
      return module.add_function(std::move(func));
    }


    // Whether two addresses are the same, asked of the numbers they are.
    ir::Value* same_address(ir::Builder& builder, ir::Value* one, ir::Value* other)
    {
      auto left = builder.cast(CastKind::bitcast, one, ir::u64());
      auto right = builder.cast(CastKind::bitcast, other, ir::u64());
      return builder.compare(CmpPred::eq, left, right);
    }


    // The address of one field of a table block.
    ir::Value* field(ir::Builder& builder, ir::Value* table, std::int64_t offset)
    {
      if (offset == 0)
        return table;
      return builder.binary(BinOp::add, table, number(builder, offset));
    }


    // One field of a table block.
    ir::Value* read(ir::Builder& builder, ir::Value* table, std::int64_t offset)
    {
      return builder.load(field(builder, table, offset));
    }


    // Put something in one field of a table block.
    void write(ir::Builder& builder, ir::Value* table, std::int64_t offset, ir::Value* value)
    {
      builder.store(field(builder, table, offset), value);
    }


    // The same address, read as pointing at something else.
    ir::Value* as(ir::Builder& builder, ir::Value* address, const ir::Type* pointee)
    {
      return builder.cast(CastKind::bitcast, address, mut_ptr(pointee));
    }


    // One field of a table block, which holds an address.
    ir::Value* read_address(ir::Builder& builder, ir::Value* table, std::int64_t offset, const ir::Type* pointee)
    {
      return builder.load(as(builder, field(builder, table, offset), mut_ptr(pointee)));
    }


    // Put an address in one field of a table block.
    void write_address(ir::Builder& builder, ir::Value* table, std::int64_t offset, ir::Value* value)
    {
      builder.store(as(builder, field(builder, table, offset), value->ty), value);
    }


    // The key of the entry at `place`.
    ir::Value* load_key(ir::Builder& builder, const Shape& shape, ir::Value* place)
    {
      auto where = field(builder, place, key_at);
      auto held = key_ir_type(shape);
      if (held == ir::u64())
        return builder.load(where);
      return builder.load(as(builder, where, held));
    }


    // Put a key into the entry at `place`.
    void store_key(ir::Builder& builder, const Shape& shape, ir::Value* place, ir::Value* key)
    {
      auto where = field(builder, place, key_at);
      auto held = key_ir_type(shape);
      if (held == ir::u64())
        builder.store(where, key);
      else
        builder.store(as(builder, where, held), key);
    }


    // The value of the entry at `place`.
    ir::Value* load_value(ir::Builder& builder, const Shape& shape, ir::Value* place)
      pre(shape.value != nullptr)
    {
      return builder.load(as(builder, field(builder, place, shape.value_at()), shape.value));
    }


    // Put a value into the entry at `place`.
    void store_value(ir::Builder& builder, const Shape& shape, ir::Value* place, ir::Value* value)
      pre(shape.value != nullptr)
    {
      builder.store(as(builder, field(builder, place, shape.value_at()), shape.value), value);
    }


    // A string as the two things it is: where its bytes are, and how many.
    std::pair<ir::Value*, ir::Value*> taken_apart(ir::Builder& builder, ir::Value* text)
    {
      auto bytes = builder.extract(text, 0, ir::parts_of(ir::str_type())[0]);
      auto count = builder.extract(text, 1, ir::u64());
      return { bytes, count };
    }


    // Where a probe for `key` starts: its hash with the table's seed, masked.
    ir::Value* hash_of(ir::Builder& builder, const Shape& shape, ir::Value* key, ir::Value* mask, ir::Value* seed)
    {
      auto& module = builder.module();
      ir::Value* hashed;
      if (key_ir_type(shape) == ir::u64())
        hashed = builder.call(hash_word_function(module), Args{ key, seed }, ir::u64());
      else {
        auto callee = hash_function(module);
        auto [bytes, count] = taken_apart(builder, key);
        hashed = builder.call(callee, Args{ bytes, count, seed }, ir::u64());
      }
      return builder.binary(BinOp::and_, hashed, mask);
    }


    // Whether the key in an entry is the key being looked for: `=` for a
    // word, and the language's own comparison of text for text.
    ir::Value* same_key(ir::Builder& builder, const Shape& shape, ir::Value* held, ir::Value* key)
    {
      if (key_ir_type(shape) == ir::u64())
        return builder.compare(CmpPred::eq, held, key);
      auto callee = strings::compare_function(builder.module());
      auto [held_bytes, held_count] = taken_apart(builder, held);
      auto [key_bytes, key_count] = taken_apart(builder, key);
      auto order = builder.call(callee, Args{ held_bytes, held_count, key_bytes, key_count }, ir::i64());
      return builder.compare(CmpPred::eq, order, builder.int_const(ir::i64(), 0));
    }


    // The address of the entry at `index`.
    ir::Value* entry_of(ir::Builder& builder, ir::Value* entries, ir::Value* index, ir::Value* stride)
    {
      return builder.binary(BinOp::add, entries, builder.binary(BinOp::wrap_mul, index, stride));
    }


    // The next place a probe looks, which is the one after and round again.
    ir::Value* stepped(ir::Builder& builder, ir::Value* index, ir::Value* mask)
    {
      return builder.binary(BinOp::and_, builder.binary(BinOp::wrap_add, index, number(builder, 1)), mask);
    }


    // A conditional branch handing the memory token to both sides, which is
    // what most of those below are.
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


    // `__pl4g_table_new(arena, stride)`: a table with room for a few entries.
    ir::Function* build_new(ir::Module& module)
    {
      auto table_ptr = table_type(module);
      auto arena_ptr = mut_ptr(ir::arena_type());
      auto [func, fresh] = generated(module, new_symbol, Types{ arena_ptr, ir::u64() }, table_ptr);
      if (! fresh)
        return func;
      auto alloc = allocator_function(module);
      auto entry = func->add_block();
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto arena = entry->add_param(arena_ptr, "arena");
      auto stride = entry->add_param(ir::u64(), "stride");
      auto block = as(builder, builder.call(alloc, Args{ arena, number(builder, block_size) }, mut_ptr(ir::u8())),
                      ir::u64());
      auto room = builder.binary(BinOp::wrap_mul, stride, number(builder, first_capacity));
      auto entries = builder.call(alloc, Args{ arena, room }, mut_ptr(ir::u8()));
      write_address(builder, block, arena_field, arena);
      write(builder, block, mask_field, number(builder, first_capacity - 1));
      write(builder, block, count_field, number(builder, 0));
      write(builder, block, used_field, number(builder, 0));
      write(builder, block, stride_field, stride);
      write_address(builder, block, entries_field, entries);
      write(builder, block, seed_field, builder.call(seed_function(module), {}, ir::u64()));
      builder.ret(block);
      return func;
    }


    // `__pl4g_table_slot(table, key)`: the entry the key is in, or would go in.
    ir::Function* build_slot(ir::Module& module, const Shape& shape)
    {
      auto table_ptr = table_type(module);
      auto held = key_ir_type(shape);
      auto [func, fresh] = generated(module, named(slot_symbol, shape), Types{ table_ptr, held }, table_ptr);
      if (! fresh)
        return func;
      auto entry = func->add_block();
      auto loop = func->add_block("probe");
      auto free = func->add_block("free");
      auto here = func->add_block("here");
      auto reused = func->add_block("reused");
      auto occupied = func->add_block("occupied");
      auto same = func->add_block("same");
      auto other = func->add_block("other");
      auto given_up = func->add_block("given_up");
      auto first = func->add_block("first");
      auto onward = func->add_block("onward");
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto table = entry->add_param(table_ptr, "table");
      auto key = entry->add_param(held, "key");
      auto mask = read(builder, table, mask_field);
      auto stride = read(builder, table, stride_field);
      auto entries = read_address(builder, table, entries_field, ir::u64());
      // One past the last index: "nothing given up yet".
      auto none = builder.binary(BinOp::wrap_add, mask, number(builder, 1));
      auto seed = read(builder, table, seed_field);
      auto start = hash_of(builder, shape, key, mask, seed);
      builder.br(loop, Args{ start, none });

      builder.position_at(loop);
      auto index = loop->add_param(ir::u64(), "at");
      auto kept = loop->add_param(ir::u64(), "kept");
      auto place = entry_of(builder, entries, index, stride);
      auto state = builder.load(place);
      builder.condbr(builder.compare(CmpPred::eq, state, number(builder, empty)), free, occupied);

      builder.position_at(free);
      builder.condbr(builder.compare(CmpPred::eq, kept, none), here, reused);

      builder.position_at(here);
      builder.ret(place);

      builder.position_at(reused);
      builder.ret(entry_of(builder, entries, kept, stride));

      builder.position_at(occupied);
      builder.condbr(same_key(builder, shape, load_key(builder, shape, place), key), same, other);

      builder.position_at(same);
      builder.ret(place);

      builder.position_at(other);
      builder.condbr(builder.compare(CmpPred::eq, state, number(builder, gone)), given_up, onward);

      builder.position_at(given_up);
      builder.condbr(builder.compare(CmpPred::eq, kept, none), first, onward);

      builder.position_at(onward);
      builder.br(loop, Args{ stepped(builder, index, mask), kept });

      builder.position_at(first);
      builder.br(loop, Args{ stepped(builder, index, mask), index });
      return func;
    }


    // `__pl4g_table_put(table, key)`: the entry for the key, made if it is new.
    ir::Function* build_put(ir::Module& module, const Shape& shape)
    {
      auto table_ptr = table_type(module);
      auto held = key_ir_type(shape);
      auto [func, made_now] = generated(module, named(put_symbol, shape), Types{ table_ptr, held }, table_ptr);
      if (! made_now)
        return func;
      auto slot_of = build_slot(module, shape);
      auto alloc = allocator_function(module);
      auto entry = func->add_block();
      auto seeding = func->add_block("seeding");
      auto seeded = func->add_block("seeded");
      auto grow = func->add_block("grow");
      auto bigger = func->add_block("bigger");
      auto afresh = func->add_block("afresh");
      auto sized = func->add_block("sized");
      auto rehash = func->add_block("rehash");
      auto one = func->add_block("one");
      auto move = func->add_block("move");
      auto step = func->add_block("stepped");
      auto grown = func->add_block("grown");
      auto ready = func->add_block("ready");
      auto there = func->add_block("there");
      auto added = func->add_block("added");
      auto fresh = func->add_block("fresh");
      auto taken = func->add_block("taken");
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto table = entry->add_param(table_ptr, "table");
      auto key = entry->add_param(held, "key");
      // A table laid out in the image empty has no seed of its own yet.
      {
        auto unseeded = builder.compare(CmpPred::eq, read(builder, table, seed_field), number(builder, 0));
        auto mem = builder.memory();
        builder.condbr(unseeded, seeding, seeded, {}, Args{ mem });
      }

      builder.position_at(seeding);
      write(builder, table, seed_field, builder.call(seed_function(module), {}, ir::u64()));
      builder.br(seeded, Args{ builder.memory() });

      builder.position_at(seeded);
      take_memory(builder, seeded);
      auto wanted = builder.binary(BinOp::wrap_add, read(builder, table, used_field), number(builder, 1));
      auto capacity = builder.binary(BinOp::wrap_add, read(builder, table, mask_field), number(builder, 1));
      auto wanted_load = builder.binary(BinOp::wrap_mul, wanted, number(builder, load_denominator));
      auto capacity_load = builder.binary(BinOp::wrap_mul, capacity, number(builder, load_numerator));
      auto crowded = builder.compare(CmpPred::ugt, wanted_load, capacity_load);
      {
        auto mem = builder.memory();
        builder.condbr(crowded, grow, ready, {}, Args{ mem });
      }

      // -- room for it, and everything moved into that --
      builder.position_at(grow);
      auto stride = read(builder, table, stride_field);
      auto old_mask = read(builder, table, mask_field);
      auto old_entries = read_address(builder, table, entries_field, ir::u64());
      auto keys = builder.binary(BinOp::wrap_add, read(builder, table, count_field), number(builder, 1));
      {
        auto keys_load = builder.binary(BinOp::wrap_mul, keys, number(builder, load_denominator));
        auto room_load = builder.binary(BinOp::wrap_mul, capacity, number(builder, load_numerator));
        builder.condbr(builder.compare(CmpPred::ugt, keys_load, room_load), bigger, afresh);
      }

      builder.position_at(bigger);
      builder.br(sized, Args{ builder.binary(BinOp::wrap_mul, capacity, number(builder, 2)) });

      builder.position_at(afresh);
      builder.br(sized, Args{ capacity });

      builder.position_at(sized);
      auto room = sized->add_param(ir::u64(), "room");
      {
        auto from = read_address(builder, table, arena_field, ir::arena_type());
        auto size = builder.binary(BinOp::wrap_mul, room, stride);
        auto roomier = builder.call(alloc, Args{ from, size }, mut_ptr(ir::u8()));
        write_address(builder, table, entries_field, roomier);
      }
      write(builder, table, mask_field, builder.binary(BinOp::wrap_sub, room, number(builder, 1)));
      write(builder, table, count_field, number(builder, 0));
      write(builder, table, used_field, number(builder, 0));
      write(builder, table, seed_field, builder.call(seed_function(module), {}, ir::u64()));
      builder.br(rehash, Args{ number(builder, 0), builder.memory() });

      // Every entry of the old array, put where the new array's own probe says.
      builder.position_at(rehash);
      auto at = rehash->add_param(ir::u64(), "at");
      take_memory(builder, rehash);
      branch_with_memory(builder, builder.compare(CmpPred::ugt, at, old_mask), grown, one);

      builder.position_at(one);
      take_memory(builder, one);
      auto old_place = entry_of(builder, old_entries, at, stride);
      branch_with_memory(builder, builder.compare(CmpPred::eq, builder.load(old_place), number(builder, live)), move,
                         step);

      builder.position_at(move);
      take_memory(builder, move);
      auto moved_key = load_key(builder, shape, old_place);
      auto into = builder.call(slot_of, Args{ table, moved_key }, table_ptr);
      write(builder, into, state_at, number(builder, live));
      store_key(builder, shape, into, moved_key);
      write(builder, table, count_field,
            builder.binary(BinOp::wrap_add, read(builder, table, count_field), number(builder, 1)));
      write(builder, table, used_field,
            builder.binary(BinOp::wrap_add, read(builder, table, used_field), number(builder, 1)));
      if (shape.value != nullptr)
        store_value(builder, shape, into, load_value(builder, shape, old_place));
      builder.br(step, Args{ builder.memory() });

      builder.position_at(step);
      take_memory(builder, step);
      {
        auto next = builder.binary(BinOp::wrap_add, at, number(builder, 1));
        builder.br(rehash, Args{ next, builder.memory() });
      }

      builder.position_at(grown);
      take_memory(builder, grown);
      builder.br(ready, Args{ builder.memory() });

      // -- and now the entry itself --
      builder.position_at(ready);
      take_memory(builder, ready);
      auto place = builder.call(slot_of, Args{ table, key }, table_ptr);
      branch_with_memory(builder, builder.compare(CmpPred::eq, builder.load(place), number(builder, live)), there,
                         added);

      builder.position_at(there);
      there->add_param(ir::mem_type(), "mem");
      builder.ret(place);

      builder.position_at(added);
      take_memory(builder, added);
      // A given-up entry taken back is one more key and not one more of those.
      auto was = builder.load(place);
      write(builder, place, state_at, number(builder, live));
      store_key(builder, shape, place, key);
      write(builder, table, count_field,
            builder.binary(BinOp::wrap_add, read(builder, table, count_field), number(builder, 1)));
      branch_with_memory(builder, builder.compare(CmpPred::eq, was, number(builder, gone)), taken, fresh);

      builder.position_at(fresh);
      take_memory(builder, fresh);
      write(builder, table, used_field,
            builder.binary(BinOp::wrap_add, read(builder, table, used_field), number(builder, 1)));
      builder.br(taken, Args{ builder.memory() });

      builder.position_at(taken);
      take_memory(builder, taken);
      builder.ret(place);
      return func;
    }


    // `__pl4g_table_select(out, walked, other, want)`: every live key of
    // `walked` looked up in `other` and put into `out` where what was found
    // is what `want` asked for.
    ir::Function* build_select(ir::Module& module, const Shape& shape)
    {
      auto table_ptr = table_type(module);
      auto [func, fresh] = generated(module, named(select_symbol, shape),
                                     Types{ table_ptr, table_ptr, table_ptr, ir::u64() }, ir::void_type());
      if (! fresh)
        return func;
      auto slot_of = build_slot(module, shape);
      auto put = build_put(module, shape);
      auto entry = func->add_block();
      auto walk = func->add_block("walk");
      auto look = func->add_block("look");
      auto live_block = func->add_block("live");
      auto ask = func->add_block("ask");
      auto when_present = func->add_block("present");
      auto when_absent = func->add_block("absent");
      auto take = func->add_block("take");
      auto onward = func->add_block("onward");
      auto done = func->add_block("done");
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto out = entry->add_param(table_ptr, "out");
      auto walked = entry->add_param(table_ptr, "walked");
      auto other = entry->add_param(table_ptr, "other");
      auto want = entry->add_param(ir::u64(), "want");
      auto mask = read(builder, walked, mask_field);
      auto stride = read(builder, walked, stride_field);
      auto entries = read_address(builder, walked, entries_field, ir::u64());
      builder.br(walk, Args{ number(builder, 0), builder.memory() });

      builder.position_at(walk);
      auto at = walk->add_param(ir::u64(), "at");
      take_memory(builder, walk);
      branch_with_memory(builder, builder.compare(CmpPred::ugt, at, mask), done, look);

      builder.position_at(look);
      take_memory(builder, look);
      auto place = entry_of(builder, entries, at, stride);
      branch_with_memory(builder, builder.compare(CmpPred::eq, builder.load(place), number(builder, live)),
                         live_block, onward);

      builder.position_at(live_block);
      take_memory(builder, live_block);
      auto key = load_key(builder, shape, place);
      auto found = builder.compare(CmpPred::eq, builder.load(builder.call(slot_of, Args{ other, key }, table_ptr)),
                                   number(builder, live));
      branch_with_memory(builder, builder.compare(CmpPred::eq, want, number(builder, want_either)), take, ask);

      builder.position_at(ask);
      take_memory(builder, ask);
      branch_with_memory(builder, found, when_present, when_absent);

      builder.position_at(when_present);
      take_memory(builder, when_present);
      branch_with_memory(builder, builder.compare(CmpPred::eq, want, number(builder, want_present)), take, onward);

      builder.position_at(when_absent);
      take_memory(builder, when_absent);
      branch_with_memory(builder, builder.compare(CmpPred::eq, want, number(builder, want_absent)), take, onward);

      builder.position_at(take);
      take_memory(builder, take);
      builder.call(put, Args{ out, key }, table_ptr);
      builder.br(onward, Args{ builder.memory() });

      builder.position_at(onward);
      take_memory(builder, onward);
      {
        auto next = builder.binary(BinOp::wrap_add, at, number(builder, 1));
        builder.br(walk, Args{ next, builder.memory() });
      }

      builder.position_at(done);
      done->add_param(ir::mem_type(), "mem");
      builder.ret();
      return func;
    }


    // `__pl4g_table_give_up(table, place)`: the entry holds no key any more.
    ir::Function* build_give_up(ir::Module& module)
    {
      auto table_ptr = table_type(module);
      auto [func, fresh] = generated(module, give_up_symbol, Types{ table_ptr, table_ptr }, ir::void_type());
      if (! fresh)
        return func;
      auto entry = func->add_block();
      auto held = func->add_block("held");
      auto done = func->add_block("done");
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto table = entry->add_param(table_ptr, "table");
      auto place = entry->add_param(table_ptr, "place");
      branch_with_memory(builder, builder.compare(CmpPred::eq, builder.load(place), number(builder, live)), held, done);

      builder.position_at(held);
      take_memory(builder, held);
      write(builder, place, state_at, number(builder, gone));
      write(builder, table, count_field,
            builder.binary(BinOp::wrap_sub, read(builder, table, count_field), number(builder, 1)));
      builder.br(done, Args{ builder.memory() });

      builder.position_at(done);
      take_memory(builder, done);
      builder.ret();
      return func;
    }


    // `__pl4g_table_next(table, from)`: where the next entry holding a key is,
    // or one past the last place where there is none.
    ir::Function* build_next(ir::Module& module)
    {
      auto table_ptr = table_type(module);
      auto [func, fresh] = generated(module, next_symbol, Types{ table_ptr, ir::u64() }, ir::u64());
      if (! fresh)
        return func;
      auto entry = func->add_block();
      auto walk = func->add_block("walk");
      auto look = func->add_block("look");
      auto onward = func->add_block("onward");
      auto done = func->add_block("done");
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto table = entry->add_param(table_ptr, "table");
      auto start = entry->add_param(ir::u64(), "from");
      auto capacity = builder.binary(BinOp::wrap_add, read(builder, table, mask_field), number(builder, 1));
      auto stride = read(builder, table, stride_field);
      auto entries = read_address(builder, table, entries_field, ir::u64());
      builder.br(walk, Args{ start });

      builder.position_at(walk);
      auto at = walk->add_param(ir::u64(), "at");
      builder.condbr(builder.compare(CmpPred::uge, at, capacity), done, look);

      builder.position_at(look);
      builder.condbr(
        builder.compare(CmpPred::eq, builder.load(entry_of(builder, entries, at, stride)), number(builder, live)), done,
        onward);

      builder.position_at(onward);
      builder.br(walk, Args{ builder.binary(BinOp::wrap_add, at, number(builder, 1)) });

      builder.position_at(done);
      builder.ret(at);
      return func;
    }

  } // namespace


  std::int64_t Shape::key_words() const
  {
    return words(key);
  }


  std::int64_t Shape::stride() const
  {
    return value_at() + (value == nullptr ? 0 : words(value) * word);
  }


  std::string Shape::tag() const
  {
    return value == nullptr ? key->render() : key->render() + ":" + value->render();
  }


  const ir::Type* key_ir_type(const Shape& shape)
  {
    return words(shape.key) == 1 ? ir::u64() : shape.key;
  }


  ir::GlobalVar* heap_global(ir::Module& module)
  {
    if (auto found = module.global(heap_key); found != nullptr)
      return found;
    return module.add_global(
      std::make_unique<ir::GlobalVar>(std::string(heap_symbol), ir::arena_type(), mut_ptr(ir::arena_type())),
      std::string(heap_key));
  }


  bool is_heap(const ir::Value* value)
  {
    if (value == nullptr || value->vkind != ir::ValueKind::instruction)
      return false;
    auto inst = static_cast<const ir::Instruction*>(value);
    if (inst->kind != ir::InstKind::AddressInst || inst->operands.empty()
        || inst->operands[0]->vkind != ir::ValueKind::global)
      return false;
    return static_cast<const ir::GlobalVar*>(inst->operands[0])->name == heap_symbol;
  }


  ir::Function* heap_new_function(ir::Module& module)
  {
    auto bytes = mut_ptr(ir::u8());
    auto [func, fresh] = generated(module, heap_new_symbol, Types{ ir::u64() }, bytes);
    if (! fresh)
      return func;
    auto alloc = declared(module, heap_alloc_symbol, Types{ ir::u64() }, bytes);
    auto entry = func->add_block();
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto size = entry->add_param(ir::u64(), "size");
    auto made = builder.call(alloc, Args{ size }, bytes);
    builder.check(builder.compare(CmpPred::ne, builder.cast(CastKind::bitcast, made, ir::u64()), number(builder, 0)),
                  "out of memory", target::statuses::OUT_OF_MEMORY);
    builder.ret(made);
    return func;
  }


  ir::Function* heap_free_function(ir::Module& module)
  {
    return declared(module, heap_free_symbol, Types{ mut_ptr(ir::u8()), ir::u64() }, ir::void_type());
  }


  ir::Function* allocator_function(ir::Module& module)
  {
    auto arena_ptr = mut_ptr(ir::arena_type());
    auto bytes = mut_ptr(ir::u8());
    auto [func, fresh] = generated(module, allocate_symbol, Types{ arena_ptr, ir::u64() }, bytes);
    if (! fresh)
      return func;
    auto bump = declared(module, alloc_symbol, Types{ arena_ptr, ir::u64() }, bytes);
    auto entry = func->add_block();
    auto heap = func->add_block("heap");
    auto pool = func->add_block("pool");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto arena = entry->add_param(arena_ptr, "allocator");
    auto size = entry->add_param(ir::u64(), "size");
    auto heap_address = builder.address(heap_global(module));
    builder.condbr(same_address(builder, arena, heap_address), heap, pool);
    builder.position_at(heap);
    builder.ret(builder.call(heap_new_function(module), Args{ size }, bytes));
    builder.position_at(pool);
    builder.ret(builder.call(bump, Args{ arena, size }, bytes));
    return func;
  }


  ir::Function* dispose_function(ir::Module& module)
  {
    auto arena_ptr = mut_ptr(ir::arena_type());
    auto bytes = mut_ptr(ir::u8());
    auto [func, fresh] = generated(module, dispose_symbol, Types{ arena_ptr, bytes, ir::u64() }, ir::void_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto heap = func->add_block("heap");
    auto done = func->add_block("done");
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto arena = entry->add_param(arena_ptr, "allocator");
    auto where = entry->add_param(bytes, "where");
    auto size = entry->add_param(ir::u64(), "size");
    auto heap_address = builder.address(heap_global(module));
    branch_with_memory(builder, same_address(builder, arena, heap_address), heap, done);
    builder.position_at(heap);
    take_memory(builder, heap);
    builder.call(heap_free_function(module), Args{ where, size }, ir::void_type());
    builder.ret();
    builder.position_at(done);
    take_memory(builder, done);
    builder.ret();
    return func;
  }


  ir::Value* no_allocator(ir::Builder& builder)
  {
    return builder.cast(CastKind::bitcast, number(builder, 0), mut_ptr(ir::arena_type()));
  }


  ir::Value* allocate(ir::Builder& builder, ir::Value* arena, ir::Value* size)
  {
    auto& module = builder.module();
    auto bytes = mut_ptr(ir::u8());
    if (is_heap(arena))
      return builder.call(heap_new_function(module), Args{ size }, bytes);
    return builder.call(allocator_function(module), Args{ arena, size }, bytes);
  }


  ir::Function* release_function(ir::Module& module)
  {
    return declared(module, release_symbol, Types{ mut_ptr(ir::arena_type()) }, ir::void_type());
  }


  ir::Function* hash_function(ir::Module& module)
  {
    return declared(module, hash_symbol, Types{ mut_ptr(ir::u8()), ir::u64(), ir::u64() }, ir::u64(), false);
  }


  ir::Function* hash_word_function(ir::Module& module)
  {
    return declared(module, hash_word_symbol, Types{ ir::u64(), ir::u64() }, ir::u64(), false);
  }


  ir::Function* seed_function(ir::Module& module)
  {
    return declared(module, hash_seed_symbol, Types{}, ir::u64());
  }


  std::pair<ir::Function*, bool> generated(ir::Module& module, std::string_view name,
                                           std::span<const ir::Type* const> params, const ir::Type* result)
  {
    if (auto found = module.function(name); found != nullptr)
      return { found, false };
    auto func = std::make_unique<ir::Function>(std::string(name),
                                               ir::func_type(Types(params.begin(), params.end()), result));
    func->attrs.abi = "pl4g.runtime";
    func->attrs.impure = true;
    func->linkage = ir::Linkage::internal;
    return { module.add_function(std::move(func)), true };
  }


  ir::Value* arena_of(ir::Builder& builder, ir::Value* table)
  {
    auto& module = builder.module();
    auto arena_ptr = mut_ptr(ir::arena_type());
    auto [func, fresh] = generated(module, arena_of_symbol, Types{ table_type(module) }, arena_ptr);
    if (fresh) {
      auto entry = func->add_block();
      auto none = func->add_block("none");
      auto some = func->add_block("some");
      ir::Builder inner(module, *func);
      inner.position_at(entry);
      auto held = read_address(inner, entry->add_param(table_type(module), "table"), arena_field, ir::arena_type());
      inner.condbr(inner.compare(CmpPred::eq, inner.cast(CastKind::bitcast, held, ir::u64()), number(inner, 0)), none,
                   some);
      inner.position_at(none);
      inner.ret(inner.address(heap_global(module)));
      inner.position_at(some);
      inner.ret(held);
    }
    return builder.call(func, Args{ builder.cast(CastKind::bitcast, table, table_type(module)) }, arena_ptr);
  }


  ir::Value* count_of(ir::Builder& builder, ir::Value* table)
  {
    return read(builder, table, count_field);
  }


  const ir::PtrType* table_type(ir::Module&)
  {
    return mut_ptr(ir::u64());
  }


  void ensure_allocator(ir::Module& module)
  {
    allocator_function(module);
  }


  ir::Function* ensure_operators(ir::Module& module, const Shape& shape)
  {
    ensure_runtime(module, shape);
    return build_select(module, shape);
  }


  void ensure_runtime(ir::Module& module, const Shape& shape)
  {
    build_new(module);
    build_next(module);
    build_slot(module, shape);
    build_put(module, shape);
  }


  ir::Function* ensure_take(ir::Module& module, const Shape& shape)
  {
    ensure_runtime(module, shape);
    return build_give_up(module);
  }


  std::string named(std::string_view symbol, const Shape& shape)
  {
    return std::string(symbol) + "." + shape.tag();
  }


  ir::Value* entry_at(ir::Builder& builder, ir::Value* table, ir::Value* at)
  {
    auto entries = read_address(builder, table, entries_field, ir::u64());
    return entry_of(builder, entries, at, read(builder, table, stride_field));
  }

} // namespace pl4g::sema::tables
