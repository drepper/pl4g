// Text, written in the representation.
//
// Every builder call below is sequenced as pypl4g's are: where two operands of
// one instruction each make instructions of their own, the first is made into
// a local first.
#include "sema/strings.hh"

#include <array>
#include <format>
#include <string>
#include <tuple>
#include <vector>

#include "sema/tables.hh"


namespace pl4g::sema::strings {

  namespace {

    using ir::BinOp;
    using ir::CastKind;
    using ir::CmpPred;
    using Args = std::vector<ir::Value*>;
    using Types = std::vector<const ir::Type*>;

    // What the leading byte of a sequence says: the value the byte must be
    // below, how many bits of the code point it carries, and how many bytes
    // the sequence has.  First match wins.
    struct Leading {
      std::uint64_t limit;
      unsigned bits;
      unsigned count;
    };
    constexpr std::array<Leading, 4> leading_rows{ { { 0x80, 7, 1 }, { 0xE0, 5, 2 }, { 0xF0, 4, 3 }, { 0x100, 3, 4 } } };


    // A word of the program.
    ir::Value* number(ir::Builder& builder, __int128 value)
    {
      return builder.int_const(ir::u64(), value);
    }


    // A function of this module's own, and whether it has yet to be built:
    // impure where it takes room from an arena.
    std::pair<ir::Function*, bool> generated(ir::Module& module, std::string_view name, const Types& params,
                                             const ir::Type* result, bool impure)
    {
      if (auto found = module.function(name); found != nullptr)
        return { found, false };
      auto func = std::make_unique<ir::Function>(std::string(name), ir::func_type(params, result));
      func->attrs.abi = "pl4g.runtime";
      func->attrs.impure = impure;
      func->linkage = ir::Linkage::internal;
      return { module.add_function(std::move(func)), true };
    }


    const ir::PtrType* arena_pointer()
    {
      return ir::ptr_type(ir::arena_type(), true);
    }


    // The byte at an offset, widened into a word.
    ir::Value* byte_at(ir::Builder& builder, ir::Value* bytes, ir::Value* at)
    {
      auto place = builder.binary(BinOp::add, bytes, at);
      return builder.cast(CastKind::zext, builder.load(place), ir::u64());
    }


    // The byte at an offset, widened into a signed word.
    ir::Value* signed_byte_at(ir::Builder& builder, ir::Value* bytes, ir::Value* at)
    {
      auto place = builder.binary(BinOp::add, bytes, at);
      return builder.cast(CastKind::zext, builder.load(place), ir::i64());
    }


    // Which of two counts is the greater, as a negative, zero or positive
    // number, without a branch.
    ir::Value* which_way(ir::Builder& builder, ir::Value* left, ir::Value* right)
    {
      auto greater = builder.cast(CastKind::zext, builder.compare(CmpPred::ugt, left, right), ir::i64());
      auto less = builder.cast(CastKind::zext, builder.compare(CmpPred::ult, left, right), ir::i64());
      return builder.binary(BinOp::wrap_sub, greater, less);
    }


    // Build the one that encodes a code point as the bytes of a string.
    void build_char(ir::Module& module, ir::Function& func)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto arena = entry->add_param(arena_pointer(), "arena");
      auto code = entry->add_param(ir::char_type(), "code");
      auto wide = builder.cast(CastKind::zext, builder.cast(CastKind::bitcast, code, ir::u32()), ir::u64());
      auto into = builder.call(tables::allocator_function(module), Args{ arena, number(builder, 4) },
                               bytes_type(module));
      // The memory as the entry block leaves it, which each arm writes from.
      auto start = builder.memory();
      auto one = builder.new_block("one");
      auto two = builder.new_block("two");
      auto three = builder.new_block("three");
      auto four = builder.new_block("four");
      auto ask_two = builder.new_block("ask.two");
      auto ask_three = builder.new_block("ask.three");
      builder.condbr(builder.compare(CmpPred::ult, wide, number(builder, 0x80)), one, ask_two);
      builder.position_at(ask_two);
      builder.condbr(builder.compare(CmpPred::ult, wide, number(builder, 0x800)), two, ask_three);
      builder.position_at(ask_three);
      builder.condbr(builder.compare(CmpPred::ult, wide, number(builder, 0x10000)), three, four);
      struct Byte {
        unsigned at;
        unsigned mark;
        unsigned shift;
      };
      const std::vector<std::pair<ir::BasicBlock*, std::vector<Byte>>> arms{
        { one, { { 0, 0x00, 0 } } },
        { two, { { 0, 0xc0, 6 }, { 1, 0x80, 0 } } },
        { three, { { 0, 0xe0, 12 }, { 1, 0x80, 6 }, { 2, 0x80, 0 } } },
        { four, { { 0, 0xf0, 18 }, { 1, 0x80, 12 }, { 2, 0x80, 6 }, { 3, 0x80, 0 } } },
      };
      for (const auto& [block, lengths] : arms) {
        builder.position_at(block);
        builder.set_memory(start);
        for (auto [at, mark, shift] : lengths) {
          auto part = builder.binary(BinOp::lshr, wide, number(builder, shift));
          unsigned mask = mark == 0x00 ? 0x7f : mark == 0x80 ? 0x3f : mark == 0xc0 ? 0x1f : mark == 0xe0 ? 0x0f : 0x07;
          auto kept = builder.binary(BinOp::and_, part, number(builder, mask));
          auto byte = builder.binary(BinOp::or_, kept, number(builder, mark));
          auto place = builder.binary(BinOp::add, into, number(builder, at));
          builder.store(place, builder.cast(CastKind::trunc, byte, ir::u8()));
        }
        builder.ret(into);
      }
    }


    // Build the one that says which of two strings comes first, by their bytes.
    void build_compare(ir::Module& module, ir::Function& func)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto first = entry->add_param(bytes_type(module), "first");
      auto first_len = entry->add_param(ir::u64(), "first.length");
      auto second = entry->add_param(bytes_type(module), "second");
      auto second_len = entry->add_param(ir::u64(), "second.length");
      // As far as both go, which is where the bytes can be compared at all.
      auto limit = builder.binary(BinOp::umin, first_len, second_len);
      auto header = builder.new_block("comparing");
      auto body = builder.new_block("compare");
      auto differ = builder.new_block("differ");
      auto ended = builder.new_block("alike");
      builder.br(header, Args{ number(builder, 0) });
      builder.position_at(header);
      auto at = header->add_param(ir::u64(), "at");
      builder.condbr(builder.compare(CmpPred::ult, at, limit), body, ended);
      builder.position_at(body);
      auto here = signed_byte_at(builder, first, at);
      auto there = signed_byte_at(builder, second, at);
      auto again = builder.new_block("next");
      builder.condbr(builder.compare(CmpPred::eq, here, there), again, differ);
      builder.position_at(again);
      builder.br(header, Args{ builder.binary(BinOp::wrap_add, at, number(builder, 1)) });
      builder.position_at(differ);
      builder.ret(builder.binary(BinOp::wrap_sub, here, there));
      builder.position_at(ended);
      builder.ret(which_way(builder, first_len, second_len));
    }


    // Build the one that counts the characters of a string: every byte that
    // is not a continuation byte begins one.
    void build_length(ir::Module& module, ir::Function& func)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto bytes = entry->add_param(bytes_type(module), "bytes");
      auto length = entry->add_param(ir::u64(), "length");
      auto header = builder.new_block("counting");
      auto body = builder.new_block("count");
      auto done = builder.new_block("counted");
      builder.br(header, Args{ number(builder, 0), number(builder, 0) });
      builder.position_at(header);
      auto at = header->add_param(ir::u64(), "at");
      auto found = header->add_param(ir::u64(), "found");
      builder.condbr(builder.compare(CmpPred::ult, at, length), body, done);
      builder.position_at(body);
      auto leading = byte_at(builder, bytes, at);
      auto begins = builder.compare(CmpPred::ne, builder.binary(BinOp::and_, leading, number(builder, 0xC0)),
                                    number(builder, 0x80));
      auto next = builder.binary(BinOp::wrap_add, at, number(builder, 1));
      auto counted = builder.binary(BinOp::wrap_add, found, builder.cast(CastKind::zext, begins, ir::u64()));
      builder.br(header, Args{ next, counted });
      builder.position_at(done);
      builder.ret(found);
    }


    // Build the code point out of a sequence of a known length and hand it on.
    void fold(ir::Builder& builder, ir::Value* bytes, ir::Value* at, ir::Value* leading, unsigned bits, unsigned count,
              ir::BasicBlock* answered)
    {
      auto found = builder.binary(BinOp::and_, leading, number(builder, (__int128(1) << bits) - 1));
      for (unsigned step = 1; step < count; ++step) {
        auto following = byte_at(builder, bytes, builder.binary(BinOp::wrap_add, at, number(builder, step)));
        auto shifted = builder.binary(BinOp::wrap_shl, found, number(builder, 6));
        auto low = builder.binary(BinOp::and_, following, number(builder, 0x3F));
        found = builder.binary(BinOp::or_, shifted, low);
      }
      builder.br(answered, Args{ found, number(builder, count) });
    }


    // Build the one that takes the character at a place: the leading byte
    // decides how many bytes follow, each carrying six more bits.
    void build_next(ir::Module& module, ir::Function& func)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto bytes = entry->add_param(bytes_type(module), "bytes");
      auto at = entry->add_param(ir::u64(), "at");
      auto leading = byte_at(builder, bytes, at);
      auto answered = builder.new_block("taken");
      auto answer = answered->add_param(ir::u64(), "point");
      auto length = answered->add_param(ir::u64(), "length");
      for (const auto& row : leading_rows) {
        if (row.count == leading_rows.size()) {
          // The last row takes whatever is left, so there is nothing to ask.
          fold(builder, bytes, at, leading, row.bits, row.count, answered);
          break;
        }
        auto shorter = builder.new_block("shorter");
        auto longer = builder.new_block("longer");
        builder.condbr(builder.compare(CmpPred::ult, leading, number(builder, row.limit)), shorter, longer);
        builder.position_at(shorter);
        fold(builder, bytes, at, leading, row.bits, row.count, answered);
        builder.position_at(longer);
      }
      builder.position_at(answered);
      auto point = builder.cast(CastKind::bitcast, builder.cast(CastKind::trunc, answer, ir::u32()), ir::char_type());
      auto after = builder.binary(BinOp::wrap_add, at, length);
      builder.ret(builder.make_tuple(Args{ point, after }, taken_type(module)));
    }


    // Build the one that puts two strings' bytes end to end.
    void build_join(ir::Module& module, ir::Function& func, bool heap)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      ir::Value* arena = heap ? nullptr : entry->add_param(arena_pointer(), "arena");
      auto first = entry->add_param(bytes_type(module), "first");
      auto first_len = entry->add_param(ir::u64(), "first.length");
      auto second = entry->add_param(bytes_type(module), "second");
      auto second_len = entry->add_param(ir::u64(), "second.length");
      auto room = builder.binary(BinOp::wrap_add, first_len, second_len);
      ir::Value* into;
      if (arena == nullptr)
        into = builder.call(tables::heap_new_function(module), Args{ room }, bytes_type(module));
      else
        into = builder.call(tables::allocator_function(module), Args{ arena, room }, bytes_type(module));
      copy_bytes(builder, into, number(builder, 0), first, first_len);
      copy_bytes(builder, into, first_len, second, second_len);
      builder.ret(into);
    }

  } // namespace


  const ir::PtrType* bytes_type(ir::Module&)
  {
    return ir::ptr_type(ir::u8(), true);
  }


  const ir::ProductType* host_text_type(ir::Module& module)
  {
    // The length beside the bytes and not a nul after them, so that text
    // holding a nul crosses whole.  One record for every module, as the
    // arena's is: the types outlive the modules here.
    static const ir::ProductType* the = [&module] {
      auto made = ir::new_product();
      made->fields = { { "text", bytes_type(module) }, { "length", ir::u64() } };
      made->name = std::string(host_text_name);
      made->abi = true;
      return made;
    }();
    return the;
  }


  const ir::Type* taken_type(ir::Module&)
  {
    return ir::tuple_type({ ir::char_type(), ir::u64() });
  }


  ir::Function* next_function(ir::Module& module)
  {
    auto [func, fresh] = generated(module, next_symbol, Types{ bytes_type(module), ir::u64() }, taken_type(module), false);
    if (fresh)
      build_next(module, *func);
    return func;
  }


  ir::Function* join_function(ir::Module& module, bool heap)
  {
    auto bytes = bytes_type(module);
    Types params;
    if (! heap)
      params.push_back(arena_pointer());
    params.insert(params.end(), { bytes, ir::u64(), bytes, ir::u64() });
    auto [func, fresh] = generated(module, heap ? std::string(join_symbol) + ".heap" : std::string(join_symbol), params,
                                   bytes, true);
    if (fresh)
      build_join(module, *func, heap);
    return func;
  }


  ir::Value* joined(ir::Builder& builder, ir::Value* arena, std::span<ir::Value* const> pieces, const ir::Type* answer,
                    Span span)
  {
    if (tables::is_heap(arena))
      return builder.call(join_function(builder.module(), true), pieces, answer, span);
    Args args{ arena };
    args.insert(args.end(), pieces.begin(), pieces.end());
    return builder.call(join_function(builder.module()), args, answer, span);
  }


  ir::Function* c_string_function(ir::Module& module)
  {
    auto bytes = bytes_type(module);
    auto [func, fresh] = generated(module, c_string_symbol, Types{ bytes, ir::u64() }, bytes, true);
    if (! fresh)
      return func;
    // C means by text the address of bytes ending in a nul, which a `str` is
    // not: it holds how many bytes it has beside them, and has no nul.  So the
    // copy is one byte longer and the nul goes there.
    auto entry = func->add_block();
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto text = entry->add_param(bytes, "text");
    auto length = entry->add_param(ir::u64(), "text.length");
    auto into = builder.call(tables::heap_new_function(module),
                             Args{ builder.binary(BinOp::wrap_add, length, number(builder, 1)) }, bytes);
    copy_bytes(builder, into, number(builder, 0), text, length);
    auto end = builder.binary(BinOp::add, into, length);
    builder.store(end, builder.int_const(ir::u8(), 0));
    builder.ret(into);
    return func;
  }


  ir::Function* char_function(ir::Module& module)
  {
    auto [func, fresh] = generated(module, char_symbol, Types{ arena_pointer(), ir::char_type() }, bytes_type(module),
                                   true);
    if (fresh)
      build_char(module, *func);
    return func;
  }


  ir::Function* length_in_bytes(ir::Module& module)
  {
    auto [func, fresh] = generated(module, byte_count_symbol, Types{ ir::char_type() }, ir::u64(), false);
    if (fresh) {
      auto entry = func->add_block();
      ir::Builder builder(module, *func);
      builder.position_at(entry);
      auto code = entry->add_param(ir::char_type(), "code");
      auto wide = builder.cast(CastKind::zext, builder.cast(CastKind::bitcast, code, ir::u32()), ir::u64());
      std::vector<ir::BasicBlock*> answers;
      for (unsigned n = 1; n <= 4; ++n)
        answers.push_back(builder.new_block("bytes." + std::to_string(n)));
      std::vector<ir::BasicBlock*> asks;
      for (unsigned n = 2; n <= 3; ++n)
        asks.push_back(builder.new_block("ask." + std::to_string(n)));
      const std::array<std::pair<unsigned, ir::BasicBlock*>, 3> bounds{
        { { 0x80, answers[0] }, { 0x800, answers[1] }, { 0x10000, answers[2] } }
      };
      for (std::size_t at = 0; at < bounds.size(); ++at) {
        auto [bound, taken] = bounds[at];
        builder.condbr(builder.compare(CmpPred::ult, wide, number(builder, bound)), taken,
                       at < asks.size() ? asks[at] : answers[3]);
        if (at < asks.size())
          builder.position_at(asks[at]);
      }
      for (std::size_t n = 1; n <= answers.size(); ++n) {
        builder.position_at(answers[n - 1]);
        builder.ret(number(builder, n));
      }
    }
    return func;
  }


  ir::Function* length_function(ir::Module& module)
  {
    auto [func, fresh] = generated(module, length_symbol, Types{ bytes_type(module), ir::u64() }, ir::u64(), false);
    if (fresh)
      build_length(module, *func);
    return func;
  }


  ir::Function* compare_function(ir::Module& module)
  {
    auto bytes = bytes_type(module);
    auto [func, fresh] = generated(module, compare_symbol, Types{ bytes, ir::u64(), bytes, ir::u64() }, ir::i64(), false);
    if (fresh)
      build_compare(module, *func);
    return func;
  }


  void copy_bytes(ir::Builder& builder, ir::Value* into, ir::Value* offset, ir::Value* source, ir::Value* count)
  {
    auto header = builder.new_block("copy");
    auto body = builder.new_block("copying");
    auto done = builder.new_block("copied");
    builder.br(header, Args{ number(builder, 0), builder.memory() });
    builder.position_at(header);
    auto at = header->add_param(ir::u64(), "at");
    auto token = header->add_param(ir::mem_type(), "mem");
    builder.set_memory(token);
    builder.condbr(builder.compare(CmpPred::ult, at, count), body, done);
    builder.position_at(body);
    auto place = builder.binary(BinOp::add, into, builder.binary(BinOp::wrap_add, offset, at));
    builder.store(place, builder.load(builder.binary(BinOp::add, source, at)));
    builder.br(header, Args{ builder.binary(BinOp::wrap_add, at, number(builder, 1)), builder.memory() });
    builder.position_at(done);
    // What holds after the loop is what the loop carried.
    builder.set_memory(token);
  }


  namespace {

    // The top bit of every byte of a word: a word none of whose bytes has it
    // set is eight characters of ASCII.
    constexpr std::uint64_t high_bits = 0x8080'8080'8080'8080;

    // How many bytes a word holds, which is how far the fast paths go at a time.
    constexpr unsigned word_size = 8;


    /// Build the one that moves a run of bytes: a word at a time, read and
    /// written where it is whatever its alignment, and the rest a byte at a time.
    void build_move(ir::Module& module, ir::Function& func)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto into = entry->add_param(bytes_type(module), "into");
      auto source = entry->add_param(bytes_type(module), "source");
      auto count = entry->add_param(ir::u64(), "count");
      auto word_ptr = ir::ptr_type(ir::u64(), true);
      auto words = builder.new_block("words");
      auto word = builder.new_block("word");
      auto tail = builder.new_block("tail");
      auto byte = builder.new_block("byte");
      auto done = builder.new_block("moved");
      auto zero = number(builder, 0);
      builder.br(words, Args{ zero, builder.memory() });
      builder.position_at(words);
      auto at = words->add_param(ir::u64(), "at");
      auto token = words->add_param(ir::mem_type(), "mem");
      builder.set_memory(token);
      auto ahead = builder.binary(BinOp::wrap_add, at, number(builder, word_size));
      builder.condbr(builder.compare(CmpPred::ule, ahead, count), word, tail, {}, Args{ at, token });
      builder.position_at(word);
      auto to = builder.cast(CastKind::bitcast, builder.binary(BinOp::add, into, at), word_ptr);
      auto from = builder.cast(CastKind::bitcast, builder.binary(BinOp::add, source, at), word_ptr);
      builder.store(to, builder.load(from));
      builder.br(words, Args{ ahead, builder.memory() });
      builder.position_at(tail);
      auto left = tail->add_param(ir::u64(), "at");
      auto left_token = tail->add_param(ir::mem_type(), "mem");
      builder.set_memory(left_token);
      builder.condbr(builder.compare(CmpPred::ult, left, count), byte, done);
      builder.position_at(byte);
      auto place = builder.binary(BinOp::add, into, left);
      builder.store(place, builder.load(builder.binary(BinOp::add, source, left)));
      auto next = builder.binary(BinOp::wrap_add, left, number(builder, 1));
      builder.br(tail, Args{ next, builder.memory() });
      builder.position_at(done);
      builder.set_memory(left_token);
      builder.ret();
    }


    /// Where the range the byte after `first` has to be in begins, and how
    /// wide it is, for a sequence of `count` bytes: any continuation byte after
    /// a leading byte of two, and narrowed after `E0`, `ED`, `F0` and `F4`.
    std::pair<ir::Value*, ir::Value*> first_following(ir::Builder& builder, ir::Value* first, unsigned count)
    {
      if (count == 2)
        return { number(builder, 0x80), number(builder, 0x3F) };
      auto [lowest, highest] = count == 3 ? std::pair{ 0xE0, 0xED } : std::pair{ 0xF0, 0xF4 };
      auto is_lowest = builder.compare(CmpPred::eq, first, number(builder, lowest));
      auto low = builder.select(is_lowest, number(builder, count == 3 ? 0xA0 : 0x90), number(builder, 0x80));
      auto is_highest = builder.compare(CmpPred::eq, first, number(builder, highest));
      auto high = builder.select(is_highest, number(builder, count == 3 ? 0x9F : 0x8F), number(builder, 0xBF));
      return { low, builder.binary(BinOp::wrap_sub, high, low) };
    }


    /// Build the one that asks whether bytes are well-formed UTF-8: eight bytes
    /// at a time while they are ASCII, and a sequence at a time, up to the end
    /// of the word, from the first word that is not -- each sequence checked as
    /// Unicode's table of well-formed byte sequences has it.
    void build_valid(ir::Module& module, ir::Function& func)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto bytes = entry->add_param(bytes_type(module), "bytes");
      auto length = entry->add_param(ir::u64(), "length");
      auto word_ptr = ir::ptr_type(ir::u64(), true);
      auto words = builder.new_block("words");
      auto word = builder.new_block("word");
      auto last = builder.new_block("last");
      auto sequence = builder.new_block("sequence");
      auto lead = builder.new_block("lead");
      auto bad = builder.new_block("bad");
      auto good = builder.new_block("good");
      builder.br(words, Args{ number(builder, 0) });

      // The words: eight bytes at a time while there are eight, and the
      // sequences up to the end of a word that is not all ASCII.
      builder.position_at(words);
      auto at = words->add_param(ir::u64(), "at");
      auto ahead = builder.binary(BinOp::wrap_add, at, number(builder, word_size));
      builder.condbr(builder.compare(CmpPred::ule, ahead, length), word, last);
      builder.position_at(word);
      auto read = builder.load(builder.cast(CastKind::bitcast, builder.binary(BinOp::add, bytes, at), word_ptr));
      auto high = builder.binary(BinOp::and_, read, number(builder, high_bits));
      builder.condbr(builder.compare(CmpPred::eq, high, number(builder, 0)), words, sequence, Args{ ahead },
                     Args{ at, ahead });
      builder.position_at(last);
      builder.condbr(builder.compare(CmpPred::ult, at, length), sequence, good, Args{ at, length });

      // One sequence at a time, up to `until`: then the words again.
      builder.position_at(sequence);
      auto here = sequence->add_param(ir::u64(), "at");
      auto until = sequence->add_param(ir::u64(), "until");
      builder.condbr(builder.compare(CmpPred::ult, here, until), lead, words, {}, Args{ here });
      builder.position_at(lead);
      auto first = byte_at(builder, bytes, here);
      auto ascii = builder.new_block("ascii");
      auto above = builder.new_block("above");
      auto starts = builder.new_block("starts");
      auto over_two = builder.new_block("over.two");
      auto over_three = builder.new_block("over.three");
      std::array<std::pair<unsigned, ir::BasicBlock*>, 3> lengths{};
      for (unsigned count = 2; count <= 4; ++count)
        lengths[count - 2] = { count, builder.new_block("bytes." + std::to_string(count)) };
      builder.condbr(builder.compare(CmpPred::ult, first, number(builder, 0x80)), ascii, above);
      builder.position_at(ascii);
      builder.br(sequence, Args{ builder.binary(BinOp::wrap_add, here, number(builder, 1)), until });
      // Below `C2` is a continuation byte, or the start of an overlong form.
      builder.position_at(above);
      builder.condbr(builder.compare(CmpPred::ult, first, number(builder, 0xC2)), bad, starts);
      builder.position_at(starts);
      builder.condbr(builder.compare(CmpPred::ult, first, number(builder, 0xE0)), lengths[0].second, over_two);
      builder.position_at(over_two);
      builder.condbr(builder.compare(CmpPred::ult, first, number(builder, 0xF0)), lengths[1].second, over_three);
      builder.position_at(over_three);
      builder.condbr(builder.compare(CmpPred::ult, first, number(builder, 0xF5)), lengths[2].second, bad);
      for (auto [count, block] : lengths) {
        builder.position_at(block);
        // The whole sequence has to be there before any of it is read.
        auto whole = builder.new_block("whole." + std::to_string(count));
        auto end = builder.binary(BinOp::wrap_add, here, number(builder, count - 1));
        builder.condbr(builder.compare(CmpPred::ult, end, length), whole, bad);
        builder.position_at(whole);
        auto [low, width] = first_following(builder, first, count);
        for (unsigned step = 1; step < count; ++step) {
          auto following = byte_at(builder, bytes, builder.binary(BinOp::wrap_add, here, number(builder, step)));
          auto then = builder.new_block(std::format("following.{}.{}", count, step));
          auto off = builder.binary(BinOp::wrap_sub, following, step == 1 ? low : number(builder, 0x80));
          builder.condbr(builder.compare(CmpPred::ule, off, step == 1 ? width : number(builder, 0x3F)), then, bad);
          builder.position_at(then);
        }
        builder.br(sequence, Args{ builder.binary(BinOp::wrap_add, here, number(builder, count)), until });
      }
      builder.position_at(bad);
      builder.ret(here);
      builder.position_at(good);
      builder.ret(length);
    }

  } // namespace


  ir::Function* valid_function(ir::Module& module)
  {
    auto [func, fresh] = generated(module, valid_symbol, Types{ bytes_type(module), ir::u64() }, ir::u64(), false);
    if (fresh)
      build_valid(module, *func);
    return func;
  }


  ir::Function* move_function(ir::Module& module)
  {
    auto bytes = bytes_type(module);
    auto [func, fresh] = generated(module, move_symbol, Types{ bytes, bytes, ir::u64() }, ir::void_type(), true);
    if (fresh)
      build_move(module, *func);
    return func;
  }


  void moved(ir::Builder& builder, ir::Value* into, ir::Value* source, ir::Value* count, Span span)
  {
    builder.call(move_function(builder.module()), Args{ into, source, count }, ir::void_type(), span);
  }

} // namespace pl4g::sema::strings
