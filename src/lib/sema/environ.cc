// The environment, and the table the compiler builds it into.
//
// Every builder call below is sequenced as pypl4g's are: where two operands of
// one instruction each make instructions of their own, the first is made into
// a local first.
#include "sema/environ.hh"

#include <vector>

#include "ir/builder.hh"
#include "target/started.hh"


namespace pl4g::sema::environ {

  namespace {

    using ir::BinOp;
    using ir::CastKind;
    using ir::CmpPred;
    using Args = std::vector<ir::Value*>;
    using Types = std::vector<const ir::Type*>;

    ir::Value* number(ir::Builder& builder, __int128 value)
    {
      return builder.int_const(ir::u64(), value);
    }

  } // namespace


  tables::Shape shape()
  {
    return { ir::str_type(), ir::str_type() };
  }


  const ir::DictType* held_type(ir::Module&)
  {
    return ir::dict_type(ir::str_type(), ir::str_type());
  }


  ir::GlobalVar* variable(ir::Module& module)
  {
    if (auto found = module.global(target::environ_symbol); found != nullptr)
      return found;
    auto ty = held_type(module);
    return module.add_global(
      std::make_unique<ir::GlobalVar>(std::string(target::environ_symbol), ty, ir::ptr_type(ty, true)),
      std::string(target::environ_symbol));
  }


  ir::Function* make_function(ir::Module& module, ir::GlobalVar* heap)
  {
    auto held = shape();
    tables::ensure_runtime(module, held);
    auto table_ptr = tables::table_type(module);
    auto words = ir::ptr_type(ir::u64(), true);
    auto [func, fresh] = tables::generated(module, target::environ_make, Types{ words, ir::u64() }, table_ptr);
    if (! fresh)
      return func;
    auto put = module.function(tables::named(tables::put_symbol, held));
    auto text = ir::ptr_type(ir::str_type(), true);
    auto entry = func->add_block();
    auto turn = func->add_block("turn");
    auto one = func->add_block("one");
    auto done = func->add_block("done");
    ir::Builder builder(module, *func);

    builder.position_at(entry);
    auto at = entry->add_param(words, "at");
    auto many = entry->add_param(ir::u64(), "many");
    auto table = builder.call(module.function(tables::new_symbol),
                              Args{ builder.address(heap), number(builder, held.stride()) }, table_ptr);
    builder.br(turn, Args{ number(builder, 0), builder.memory() });

    // Two strings are one variable, so what says there is another one is that
    // both of them are there.
    builder.position_at(turn);
    auto which = turn->add_param(ir::u64(), "which");
    builder.set_memory(turn->add_param(ir::mem_type(), "mem"));
    {
      auto cond = builder.compare(CmpPred::ult, builder.binary(BinOp::wrap_add, which, number(builder, 1)), many);
      auto mem = builder.memory();
      builder.condbr(cond, one, done, Args{ mem }, Args{ mem });
    }

    builder.position_at(one);
    builder.set_memory(one->add_param(ir::mem_type(), "mem"));
    auto where = builder.binary(BinOp::add, at, builder.binary(BinOp::wrap_mul, which, number(builder, value_at)));
    auto name = builder.load(builder.cast(CastKind::bitcast, where, text));
    auto value = builder.load(
      builder.cast(CastKind::bitcast, builder.binary(BinOp::add, where, number(builder, value_at)), text));
    auto place = builder.call(put, Args{ table, name }, table_ptr);
    builder.store(
      builder.cast(CastKind::bitcast, builder.binary(BinOp::add, place, number(builder, held.value_at())), text),
      value);
    builder.br(turn, Args{ builder.binary(BinOp::wrap_add, which, number(builder, 2)), builder.memory() });

    builder.position_at(done);
    builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    builder.ret(table);
    return func;
  }

} // namespace pl4g::sema::environ
