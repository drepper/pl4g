// The auxiliary vector, and the table the entry point reads it into.
//
// Every builder call below is sequenced as pypl4g's are: where two operands of
// one instruction each make instructions of their own, the first is made into
// a local first.
#include "sema/auxv.hh"

#include <vector>

#include "sema/tables.hh"
#include "target/started.hh"


namespace pl4g::sema::auxv {

  namespace {

    using ir::BinOp;
    using ir::CastKind;
    using ir::CmpPred;
    using Args = std::vector<ir::Value*>;
    using Types = std::vector<const ir::Type*>;

    // A word of the given value.
    ir::Value* number(ir::Builder& builder, __int128 value)
    {
      return builder.int_const(ir::u64(), value);
    }

  } // namespace


  const ir::ArrayType* held_type()
  {
    return ir::array_type(ir::u64(), { std::uint64_t(slots) });
  }


  ir::GlobalVar* variable(ir::Module& module)
  {
    if (auto found = module.global(target::auxv_symbol); found != nullptr)
      return found;
    auto ty = held_type();
    return module.add_global(
      std::make_unique<ir::GlobalVar>(std::string(target::auxv_symbol), ty, ir::ptr_type(ty, true)),
      std::string(target::auxv_symbol));
  }


  ir::Value* words_of(ir::Builder& builder, ir::GlobalVar* table)
  {
    auto place = builder.address(table);
    return builder.cast(CastKind::bitcast, place, ir::ptr_type(ir::u64(), true));
  }


  ir::Function* make_function(ir::Module& module, ir::GlobalVar* table)
  {
    auto words = ir::ptr_type(ir::u64(), true);
    auto [func, fresh] = tables::generated(module, target::auxv_make, Types{ words }, ir::void_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    auto skip = func->add_block("skip");
    auto pairs = func->add_block("pairs");
    auto one = func->add_block("one");
    auto keep = func->add_block("keep");
    auto done = func->add_block("done");
    ir::Builder builder(module, *func);

    builder.position_at(entry);
    auto at = entry->add_param(words, "at");
    // Past the count, the arguments and the null after them: the count and two
    // more words.
    {
      auto count = builder.load(at);
      auto words_past = builder.binary(BinOp::wrap_mul, builder.binary(BinOp::wrap_add, count, number(builder, 2)),
                                       number(builder, word));
      auto past = builder.binary(BinOp::add, at, words_past);
      builder.br(skip, Args{ past, builder.memory() });
    }

    // The environment, to the null that ends it.
    builder.position_at(skip);
    auto walked = skip->add_param(words, "walked");
    builder.set_memory(skip->add_param(ir::mem_type(), "mem"));
    {
      auto after = builder.binary(BinOp::add, walked, number(builder, word));
      auto cond = builder.compare(CmpPred::eq, builder.load(walked), number(builder, 0));
      auto mem = builder.memory();
      builder.condbr(cond, pairs, skip, Args{ after, number(builder, 0), mem }, Args{ after, mem });
    }

    // A pair, or the `AT_NULL` that ends them.
    builder.position_at(pairs);
    auto pair_at = pairs->add_param(words, "pair");
    auto seen = pairs->add_param(ir::u64(), "seen");
    builder.set_memory(pairs->add_param(ir::mem_type(), "mem"));
    auto kind = builder.load(pair_at);
    {
      auto cond = builder.compare(CmpPred::eq, kind, number(builder, 0));
      auto mem = builder.memory();
      builder.condbr(cond, done, one, Args{ mem }, Args{ mem });
    }

    // One the table has a word for, or one it has not.
    builder.position_at(one);
    builder.set_memory(one->add_param(ir::mem_type(), "mem"));
    auto following = builder.binary(BinOp::add, pair_at, number(builder, pair));
    {
      auto cond = builder.compare(CmpPred::ult, kind, number(builder, slots));
      auto mem = builder.memory();
      builder.condbr(cond, keep, pairs, Args{ mem }, Args{ following, seen, mem });
    }

    builder.position_at(keep);
    builder.set_memory(keep->add_param(ir::mem_type(), "mem"));
    {
      auto first = words_of(builder, table);
      auto into = builder.binary(BinOp::add, first, builder.binary(BinOp::wrap_mul, kind, number(builder, word)));
      auto value = builder.load(builder.binary(BinOp::add, pair_at, number(builder, word)));
      builder.store(into, value);
      auto bit = builder.binary(BinOp::wrap_shl, number(builder, 1), kind);
      auto gathered = builder.binary(BinOp::or_, seen, bit);
      builder.br(pairs, Args{ following, gathered, builder.memory() });
    }

    // The bits of the types there were, in the word `AT_NULL` would have had.
    builder.position_at(done);
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    builder.store(words_of(builder, table), seen);
    builder.ret();
    return func;
  }

} // namespace pl4g::sema::auxv
