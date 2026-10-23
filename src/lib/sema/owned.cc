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
      case ir::TypeKind::func: return lambdas && ! static_cast<const ir::FuncType*>(ty)->system();
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


    // The parts of a record or a tuple, in the order they were declared, and
    // where each starts in a value of it held in memory.
    std::pair<Types, std::vector<std::uint64_t>> parts_and_offsets(const ir::Type* ty)
    {
      Types members;
      if (auto product = ir::as<ir::ProductType>(ty)) {
        for (const auto& [_, part] : product->fields)
          members.push_back(part);
        return { members, ir::offsets_of(product, layout) };
      }
      auto tuple = ir::as<ir::TupleType>(ty);
      contract_assert(tuple != nullptr);
      return { tuple->members, ir::member_offsets_of(tuple, layout) };
    }


    // The tag of the sum held at `place`.
    ir::Value* tag_at(ir::Builder& builder, ir::Value* place, const ir::SumType* ty)
    {
      auto where = builder.binary(BinOp::add, place, number(builder, std::int64_t(ir::tag_offset_of(ty, layout))));
      return builder.load(builder.cast(CastKind::bitcast, where, ir::ptr_type(ir::u8(), true)));
    }


    // `one(part)` for the one part of a sum its tag says it holds, where that
    // part points somewhere: a chain of comparisons over the tag, each way
    // carrying the memory, ending in a block named `joined`.
    void by_the_tag(ir::Builder& builder, ir::Function& func, ir::Value* tag, const ir::SumType* ty,
                    std::string_view joined, std::string_view arm_name,
                    const std::function<void(const ir::Type*)>& one)
    {
      auto done = func.add_block(std::string(joined));
      for (std::size_t at = 0; at < ty->variants.size(); ++at) {
        auto part = ty->variants[at].second;
        if (! points(part))
          continue;
        auto arm = func.add_block(std::string(arm_name));
        auto other = func.add_block("other");
        {
          auto cond = builder.compare(CmpPred::eq, tag, builder.int_const(ir::u8(), std::int64_t(at)));
          auto mem = builder.memory();
          builder.condbr(cond, arm, other, Args{ mem }, Args{ mem });
        }
        builder.position_at(arm);
        builder.set_memory(arm->add_param(ir::mem_type(), "mem"));
        one(part);
        builder.br(done, Args{ builder.memory() });
        builder.position_at(other);
        builder.set_memory(other->add_param(ir::mem_type(), "mem"));
      }
      builder.br(done, Args{ builder.memory() });
      builder.position_at(done);
      builder.set_memory(done->add_param(ir::mem_type(), "mem"));
    }


    // Where the part of type `part` at `offset` in `room` is.
    ir::Value* part_place(ir::Builder& builder, ir::Value* room, std::uint64_t offset, const ir::Type* part)
    {
      auto place = builder.cast(CastKind::bitcast, room, ir::ptr_type(part, true));
      if (offset != 0)
        place = builder.binary(BinOp::add, place, number(builder, std::int64_t(offset)));
      return place;
    }


    // The part at `offset` in `room` copied into `arena` and written back
    // where it was read.
    void own_where_it_stands(ir::Builder& builder, ir::Value* arena, ir::Value* room, std::uint64_t offset,
                             const ir::Type* part)
    {
      auto place = part_place(builder, room, offset, part);
      auto callee = own_function(builder.module(), part);
      auto read = builder.load(place);
      auto copied = builder.call(callee, Args{ arena, read }, part);
      builder.store(place, copied);
    }


    // A value held in memory -- a sum, or a record or a tuple holding one --
    // copied into room of this function's own, and then every part of the copy
    // that points somewhere copied into `arena` where it stands; of a sum, the
    // one part its tag names.  The answer is that room.
    void build_own_in_memory(ir::Builder& builder, ir::Function& func, ir::Value* arena, ir::Value* value,
                             const ir::Type* ty)
    {
      auto room = builder.frame(ty);
      builder.copy(room, builder.cast(CastKind::bitcast, value, ir::ptr_type(ty, true)), ty);
      if (auto sum = ir::as<ir::SumType>(ty)) {
        auto tag = tag_at(builder, room, sum);
        by_the_tag(builder, func, tag, sum, "owned", "own",
                   [&](const ir::Type* part) { own_where_it_stands(builder, arena, room, 0, part); });
      } else {
        auto [members, offsets] = parts_and_offsets(ty);
        for (std::size_t at = 0; at < members.size(); ++at)
          if (points(members[at]))
            own_where_it_stands(builder, arena, room, offsets[at], members[at]);
      }
      builder.ret(builder.cast(CastKind::bitcast, room, ty));
    }


    // Give back what the parts of a value travelling in registers own, a
    // record's and a tuple's taken field by field.
    void disown_leaves(ir::Builder& builder, std::span<ir::Value* const> leaves, const ir::Type* ty)
    {
      if ((ir::is<ir::ProductType>(ty) || ir::is<ir::TupleType>(ty)) && ! ir::held_in_memory(ty)) {
        Types members;
        if (auto product = ir::as<ir::ProductType>(ty))
          for (const auto& [_, part] : product->fields)
            members.push_back(part);
        else
          members = ir::as<ir::TupleType>(ty)->members;
        std::size_t at = 0;
        for (auto part : members) {
          auto count = ir::parts_of(part).size();
          if (points(part))
            disown_leaves(builder, leaves.subspan(at, count), part);
          at += count;
        }
        return;
      }
      auto pieces = ir::parts_of(ty);
      ir::Value* whole = pieces.size() == 1 && pieces[0] == ty ? leaves[0] : builder.make_tuple(leaves, ty);
      auto callee = disown_function(builder.module(), ty);
      builder.call(callee, Args{ whole }, ir::void_type());
    }


    // Give back what the parts of a value held in memory own, read where they
    // stand: every part of a record or a tuple that points somewhere, and of a
    // sum the one part its tag says it holds.
    void disown_in_memory(ir::Builder& builder, ir::Function& func, ir::Value* place, const ir::Type* ty)
    {
      auto one = [&](std::uint64_t offset, const ir::Type* part) {
        auto where = part_place(builder, place, offset, part);
        // A part held in memory is where it is, which is what a value of it is.
        auto read = ir::held_in_memory(part) ? builder.cast(CastKind::bitcast, where, part) : builder.load(where);
        auto callee = disown_function(builder.module(), part);
        builder.call(callee, Args{ read }, ir::void_type());
      };
      if (auto sum = ir::as<ir::SumType>(ty)) {
        auto tag = tag_at(builder, place, sum);
        by_the_tag(builder, func, tag, sum, "disowned", "disown", [&](const ir::Type* part) { one(0, part); });
        return;
      }
      auto [members, offsets] = parts_and_offsets(ty);
      for (std::size_t at = 0; at < members.size(); ++at)
        if (points(members[at]))
          one(offsets[at], members[at]);
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
      if (ir::held_in_memory(ty) && (ir::is<ir::ProductType>(ty) || ir::is<ir::TupleType>(ty) || ir::is<ir::SumType>(ty))) {
        build_own_in_memory(builder, func, arena, value, ty);
        return;
      }
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


  namespace {

    bool can_own(const ir::Type* ty, std::vector<const ir::Type*>& seen)
    {
      // A type that reaches itself through a list is asked about once: met again
      // on the way down, it is taken to be what the rest of the walk finds.
      if (std::ranges::find(seen, ty) != seen.end())
        return true;
      seen.push_back(ty);
      struct Pop {
        std::vector<const ir::Type*>& seen;
        ~Pop() { seen.pop_back(); }
      } pop{ seen };
      auto part_can = [&](const ir::Type* part) { return ! points(part) || can_own(part, seen); };
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
      case ir::TypeKind::sum:
        return std::ranges::all_of(static_cast<const ir::SumType*>(ty)->variants,
                                   [&](const auto& variant) { return part_can(variant.second); });
      case ir::TypeKind::array: {
        // A view of one dimension: its elements copied into a run of their own.
        auto array = static_cast<const ir::ArrayType*>(ty);
        return ! array->fixed() && array->rank() == 1 && part_can(array->element);
      }
      default: return false;
      }
    }

  } // namespace


  bool can_own(const ir::Type* ty)
  {
    std::vector<const ir::Type*> seen;
    return can_own(ty, seen);
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
    if (ir::is<ir::ProductType>(ty) || ir::is<ir::TupleType>(ty) || ir::is<ir::SumType>(ty)) {
      // A record, a tuple or a sum: what it owns is what its parts own, and it
      // has no storage of its own to give back.
      if (ir::held_in_memory(ty))
        disown_in_memory(builder, *func, builder.cast(CastKind::bitcast, value, ir::ptr_type(ty, true)), ty);
      else {
        auto leaves = builder.leaves(value, ty);
        disown_leaves(builder, leaves, ty);
      }
      builder.ret();
      return func;
    }
    auto [start, count] = parts(builder, value, ty);
    if (auto list = ir::as<ir::ListType>(ty); list != nullptr && points(list->element)) {
      auto element = list->element;
      auto inner = disown_function(module, element);
      each(builder, *func, count, "disown", [&, start = start](ir::Value* at) {
        auto place = element_at(builder, start, element, at);
        // An element held in memory is where it is, which is what a value of
        // it is: read, it would be copied into a frame to be given back.
        auto read = ir::held_in_memory(element) ? builder.cast(CastKind::bitcast, place, element) : builder.load(place);
        builder.call(inner, Args{ read }, ir::void_type());
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
