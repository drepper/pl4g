// What a container owns, copied into its allocator and given back from it.
//
// Every builder call below is sequenced as pypl4g's are: where two operands of
// one instruction each make instructions of their own, the first is made into
// a local first.
#include "sema/owned.hh"

#include <algorithm>
#include <functional>
#include <vector>

#include "ir/layout.hh"
#include "sema/lists.hh"
#include "sema/strings.hh"
#include "sema/tables.hh"


namespace pl4g::sema::owned {

  namespace {

    using ir::BinOp;
    using ir::CastKind;
    using ir::CmpPred;
    using Args = std::vector<ir::Value*>;
    using Types = std::vector<const ir::Type*>;

    const ir::DataLayout layout{};


    const ir::PtrType* bytes_type()
    {
      return ir::ptr_type(ir::u8(), true);
    }


    ir::Value* number(ir::Builder& builder, __int128 value)
    {
      return builder.int_const(ir::u64(), value);
    }


    // How far apart elements of `ty` are.
    std::int64_t stride(const ir::Type* ty)
    {
      return static_cast<std::int64_t>(ir::stride_of(ty, layout));
    }


    bool points_somewhere(const ir::Type* ty, std::vector<const ir::Type*>& seen, bool lambdas, bool references)
    {
      if (std::ranges::find(seen, ty) != seen.end())
        return false;
      seen.push_back(ty);
      struct Pop {
        std::vector<const ir::Type*>& seen;
        ~Pop() { seen.pop_back(); }
      } pop{ seen };
      auto deeper = [&](const ir::Type* one) { return points_somewhere(one, seen, lambdas, references); };
      switch (ty->kind) {
      case ir::TypeKind::ptr: return references;
      case ir::TypeKind::str:
      case ir::TypeKind::list:
      case ir::TypeKind::set:
      case ir::TypeKind::dict:
      case ir::TypeKind::cursor: return true;
      case ir::TypeKind::func: return lambdas;
      case ir::TypeKind::array: {
        auto array = static_cast<const ir::ArrayType*>(ty);
        return ! array->fixed() || deeper(array->element);
      }
      case ir::TypeKind::tuple: return std::ranges::any_of(static_cast<const ir::TupleType*>(ty)->members, deeper);
      case ir::TypeKind::result: {
        auto result = static_cast<const ir::ResultType*>(ty);
        return deeper(result->ok) || (result->err != nullptr && deeper(result->err));
      }
      case ir::TypeKind::product:
        return std::ranges::any_of(static_cast<const ir::ProductType*>(ty)->fields,
                                   [&](const auto& field) { return deeper(field.second); });
      case ir::TypeKind::sum:
        return std::ranges::any_of(static_cast<const ir::SumType*>(ty)->variants,
                                   [&](const auto& variant) { return deeper(variant.second); });
      default: return false;
      }
    }


    // Where a string's or a list's elements are, and how many there are.
    std::pair<ir::Value*, ir::Value*> parts(ir::Builder& builder, ir::Value* value, const ir::Type* ty)
    {
      auto start = builder.extract(value, 0, ir::parts_of(ty)[0]);
      auto count = builder.extract(value, 1, ir::u64());
      return { start, count };
    }


    // How many bytes the elements of a string or a list take.
    ir::Value* size_of_elements(ir::Builder& builder, const ir::Type* ty, ir::Value* count)
    {
      if (ir::is<ir::StrType>(ty))
        return count;
      auto list = ir::as<ir::ListType>(ty);
      contract_assert(list != nullptr);
      return builder.binary(BinOp::wrap_mul, count, number(builder, stride(list->element)));
    }


    // Where the element at `index` is.
    ir::Value* element_at(ir::Builder& builder, ir::Value* start, const ir::Type* element, ir::Value* index)
    {
      return builder.binary(BinOp::add, start, builder.binary(BinOp::wrap_mul, index, number(builder, stride(element))));
    }


    // Run `turn(at)` for every index below `count`, carrying the memory through.
    void each(ir::Builder& builder, ir::Function& func, ir::Value* count, std::string_view name,
              const std::function<void(ir::Value*)>& turn)
    {
      auto loop = func.add_block(std::string(name) + ".loop");
      auto body = func.add_block(std::string(name) + ".body");
      auto done = func.add_block(std::string(name) + ".done");
      builder.br(loop, Args{ number(builder, 0), builder.memory() });
      builder.position_at(loop);
      auto at = loop->add_param(ir::u64(), "at");
      builder.set_memory(loop->add_param(ir::mem_type(), "mem"));
      {
        auto cond = builder.compare(CmpPred::ult, at, count);
        auto mem = builder.memory();
        builder.condbr(cond, body, done, Args{ mem }, Args{ mem });
      }
      builder.position_at(body);
      builder.set_memory(body->add_param(ir::mem_type(), "mem"));
      turn(at);
      builder.br(loop, Args{ builder.binary(BinOp::wrap_add, at, number(builder, 1)), builder.memory() });
      builder.position_at(done);
      builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    }


    // The parts a value of `ty` travels in, with every string and list among
    // them copied into `arena`.
    std::vector<ir::Value*> owned_leaves(ir::Builder& builder, ir::Value* arena, std::span<ir::Value* const> leaves,
                                         const ir::Type* ty)
    {
      auto& module = builder.module();
      if (ir::is<ir::StrType>(ty) || ir::is<ir::ListType>(ty)) {
        auto callee = own_function(module, ty);
        auto whole = builder.make_tuple(leaves, ty);
        auto copied = builder.call(callee, Args{ arena, whole }, ty);
        std::vector<ir::Value*> found;
        auto pieces = ir::parts_of(ty);
        for (std::size_t at = 0; at < pieces.size(); ++at)
          found.push_back(builder.extract(copied, std::int64_t(at), pieces[at]));
        return found;
      }
      Types members;
      if (auto product = ir::as<ir::ProductType>(ty))
        for (const auto& [_, part] : product->fields)
          members.push_back(part);
      else if (auto tuple = ir::as<ir::TupleType>(ty))
        members = tuple->members;
      else
        return { leaves.begin(), leaves.end() };
      std::vector<ir::Value*> found;
      std::size_t at = 0;
      for (auto part : members) {
        auto count = ir::parts_of(part).size();
        auto piece = leaves.subspan(at, count);
        if (points(part))
          std::ranges::copy(owned_leaves(builder, arena, piece, part), std::back_inserter(found));
        else
          found.insert(found.end(), piece.begin(), piece.end());
        at += count;
      }
      return found;
    }


    // A view's elements copied into a run of their own in `arena`, and the
    // view of that run.
    void build_own_view(ir::Builder& builder, ir::Function& func, ir::Value* arena, ir::Value* value,
                        const ir::ArrayType* ty)
    {
      auto pointer = ir::parts_of(ty)[0];
      auto start = builder.extract(value, 0, pointer);
      auto count = builder.extract(value, 1, ir::u64());
      auto apart = builder.extract(value, 2, ir::u64());
      auto size = stride(ty->element);
      auto room = builder.cast(
        CastKind::bitcast,
        tables::allocate(builder, arena, builder.binary(BinOp::wrap_mul, count, number(builder, size))), pointer);
      auto element = ty->element;
      auto inner = points(element) ? own_function(builder.module(), element) : nullptr;
      each(builder, func, count, "own", [&](ir::Value* at) {
        ir::Value* read = builder.load(element_at(builder, start, element, builder.binary(BinOp::wrap_mul, at, apart)));
        if (inner != nullptr)
          read = builder.call(inner, Args{ arena, read }, element);
        builder.store(element_at(builder, room, element, at), read);
      });
      builder.ret(builder.make_tuple(Args{ room, count, number(builder, 1) }, ty));
    }


    // The copy: the bytes first, then every element that points somewhere --
    // or, of a record or a tuple, every part that does.
    void build_own(ir::Module& module, ir::Function& func, const ir::Type* ty)
    {
      auto entry = func.add_block();
      ir::Builder builder(module, func);
      builder.position_at(entry);
      auto arena = entry->add_param(ir::ptr_type(ir::arena_type(), true), "arena");
      auto value = entry->add_param(ty, "v");
      if (ir::is<ir::ProductType>(ty) || ir::is<ir::TupleType>(ty)) {
        auto leaves = builder.leaves(value, ty);
        builder.ret(builder.make_tuple(owned_leaves(builder, arena, leaves, ty), ty));
        return;
      }
      if (auto array = ir::as<ir::ArrayType>(ty)) {
        build_own_view(builder, func, arena, value, array);
        return;
      }
      auto [start, count] = parts(builder, value, ty);
      auto size = size_of_elements(builder, ty, count);
      auto raw = builder.cast(CastKind::bitcast, start, bytes_type());
      // A join of the bytes with nothing: the one routine that takes room in an
      // arena and copies into it.
      auto list = ir::as<ir::ListType>(ty);
      ir::Value* joined;
      if (list != nullptr) {
        auto callee = lists::join_function(module);
        joined = builder.call(callee, Args{ arena, raw, count, raw, number(builder, 0), number(builder, stride(list->element)) },
                              bytes_type());
      } else {
        auto callee = strings::join_function(module);
        joined = builder.call(callee, Args{ arena, raw, size, raw, number(builder, 0) }, bytes_type());
      }
      auto copied = builder.cast(CastKind::bitcast, joined, ir::parts_of(ty)[0]);
      if (list != nullptr && points(list->element)) {
        auto element = list->element;
        auto inner = own_function(module, element);
        each(builder, func, count, "own", [&](ir::Value* at) {
          auto place = element_at(builder, copied, element, at);
          builder.store(place, builder.call(inner, Args{ arena, builder.load(place) }, element));
        });
      }
      builder.ret(builder.make_tuple(Args{ copied, count, arena }, ty));
    }


    // A string's bytes or a list's room given back to `allocator`.
    void dispose(ir::Builder& builder, ir::Value* allocator, ir::Value* start, ir::Value* count, const ir::Type* ty)
    {
      auto& module = builder.module();
      auto where = builder.cast(CastKind::bitcast, start, bytes_type());
      if (auto list = ir::as<ir::ListType>(ty)) {
        builder.call(lists::free_function(module), Args{ allocator, where, number(builder, stride(list->element)) },
                     ir::void_type());
        return;
      }
      auto callee = tables::dispose_function(module);
      builder.call(callee, Args{ allocator, where, size_of_elements(builder, ty, count) }, ir::void_type());
    }


    // The bytes of a string given back to the heap straight away.
    void free_text_on_heap(ir::Builder& builder, ir::Value* value, const ir::Type* ty)
    {
      auto [start, count] = parts(builder, value, ty);
      auto callee = tables::heap_free_function(builder.module());
      auto where = builder.cast(CastKind::bitcast, start, bytes_type());
      builder.call(callee, Args{ where, size_of_elements(builder, ty, count) }, ir::void_type());
    }


    // The element type of what a list or a view holds, where it holds one.
    const ir::Type* element_of(const ir::Type* ty)
    {
      if (auto list = ir::as<ir::ListType>(ty))
        return list->element;
      if (auto array = ir::as<ir::ArrayType>(ty))
        return array->element;
      return nullptr;
    }

  } // namespace


  bool points_somewhere(const ir::Type* ty, bool lambdas, bool references)
  {
    std::vector<const ir::Type*> seen;
    return points_somewhere(ty, seen, lambdas, references);
  }


  bool can_own(const ir::Type* ty)
  {
    auto part_can = [](const ir::Type* part) { return ! points(part) || can_own(part); };
    switch (ty->kind) {
    case ir::TypeKind::str: return true;
    case ir::TypeKind::list: return part_can(static_cast<const ir::ListType*>(ty)->element);
    case ir::TypeKind::product: {
      auto product = static_cast<const ir::ProductType*>(ty);
      if (product->unique || ! product->closed_by.empty() || product->device)
        return false;
      return std::ranges::all_of(product->fields, [&](const auto& field) { return part_can(field.second); });
    }
    case ir::TypeKind::tuple: return std::ranges::all_of(static_cast<const ir::TupleType*>(ty)->members, part_can);
    case ir::TypeKind::array: {
      // A view of one dimension: its elements copied into a run of their own.
      auto array = static_cast<const ir::ArrayType*>(ty);
      return ! array->fixed() && array->rank() == 1 && part_can(array->element);
    }
    default: return false;
    }
  }


  bool points(const ir::Type* ty)
  {
    switch (ty->kind) {
    case ir::TypeKind::str:
    case ir::TypeKind::list: return true;
    case ir::TypeKind::ptr:
    case ir::TypeKind::set:
    case ir::TypeKind::dict:
    case ir::TypeKind::func:
    case ir::TypeKind::iter:
    case ir::TypeKind::running: return false;
    default: return points_somewhere(ty, false, false);
    }
  }


  ir::Function* own_function(ir::Module& module, const ir::Type* ty)
  {
    auto arena = ir::ptr_type(ir::arena_type(), true);
    auto [func, fresh] = tables::generated(module, std::string(own_symbol) + "." + ty->mangled(), Types{ arena, ty }, ty);
    if (fresh)
      build_own(module, *func, ty);
    return func;
  }


  ir::Function* disown_function(ir::Module& module, const ir::Type* ty)
  {
    auto [func, fresh] = tables::generated(module, std::string(disown_symbol) + "." + ty->mangled(), Types{ ty },
                                           ir::void_type());
    if (! fresh)
      return func;
    auto entry = func->add_block();
    ir::Builder builder(module, *func);
    builder.position_at(entry);
    auto value = entry->add_param(ty, "v");
    auto [start, count] = parts(builder, value, ty);
    if (auto list = ir::as<ir::ListType>(ty); list != nullptr && points(list->element)) {
      auto element = list->element;
      auto inner = disown_function(module, element);
      each(builder, *func, count, "disown", [&, start = start](ir::Value* at) {
        builder.call(inner, Args{ builder.load(element_at(builder, start, element, at)) }, ir::void_type());
      });
    }
    dispose(builder, builder.extract(value, 2, ir::parts_of(ty)[2]), start, count, ty);
    builder.ret();
    return func;
  }


  void give_back(ir::Builder& builder, ir::Value* value, const ir::Type* ty, std::optional<std::string_view> known)
  {
    if (known == "pool" || ! points(ty))
      return;
    if (known == "heap" && ir::is<ir::StrType>(ty)) {
      free_text_on_heap(builder, value, ty);
      return;
    }
    // pypl4g reads `ty.element` here, which only a list or a view has.
    if (auto element = element_of(ty); known == "heap" && element != nullptr && ! points(element)) {
      auto [start, count] = parts(builder, value, ty);
      dispose(builder, builder.extract(value, 2, ir::parts_of(ty)[2]), start, count, ty);
      return;
    }
    builder.call(disown_function(builder.module(), ty), Args{ value }, ir::void_type());
  }


  void give_back_storage(ir::Builder& builder, ir::Value* value, const ir::Type* ty,
                         std::optional<std::string_view> known)
  {
    if (known == "pool")
      return;
    if (known == "heap" && ir::is<ir::StrType>(ty)) {
      free_text_on_heap(builder, value, ty);
      return;
    }
    auto [start, count] = parts(builder, value, ty);
    dispose(builder, builder.extract(value, 2, ir::parts_of(ty)[2]), start, count, ty);
  }

} // namespace pl4g::sema::owned
