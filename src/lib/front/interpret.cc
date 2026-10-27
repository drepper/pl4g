// The machine that runs a macro: a port of pypl4g's front/interpret.py.
#include "front/interpret.hh"

#include <algorithm>
#include <bit>
#include <format>

#include "diag/engine.hh"
#include "util/reflect.hh"


namespace pl4g::macro {

  namespace {

    using ir::BinOp;
    using ir::InstKind;

    // The layout the machine uses: the compiler's own word, not the target's.
    const ir::DataLayout& machine_layout()
    {
      static const ir::DataLayout layout{};
      return layout;
    }

    // A mask of the low `bits` bits.
    unsigned __int128 mask_of(unsigned bits) noexcept
    {
      return bits >= 128 ? ~(unsigned __int128)0 : (((unsigned __int128)1 << bits) - 1);
    }

    // `number` as a value of `ty`, signed where the type is.
    __int128 in_range(__int128 number, const ir::Type* ty) noexcept
    {
      auto it = ir::as<ir::IntType>(ty);
      if (it == nullptr)
        return number;
      unsigned bits = it->held();
      unsigned __int128 raw = (unsigned __int128)number & mask_of(bits);
      if (it->is_signed && raw >= ((unsigned __int128)1 << (bits - 1)))
        return (__int128)raw - ((__int128)1 << bits);
      return (__int128)raw;
    }

    // `number` as the bits that hold it, which is what a shift reads.
    __int128 as_written(__int128 number, const ir::Type* ty) noexcept
    {
      auto it = ir::as<ir::IntType>(ty);
      if (it == nullptr)
        return number;
      return (__int128)((unsigned __int128)number & mask_of(it->held()));
    }

    // How many bits a non-negative number needs: Python's `int.bit_length`.
    unsigned bit_length(__int128 number) noexcept
    {
      auto raw = (unsigned __int128)(number < 0 ? -number : number);
      unsigned bits = 0;
      while (raw != 0) {
        ++bits;
        raw >>= 1;
      }
      return bits;
    }

    // `a << b` as Python works it out, or nothing where the answer is beyond
    // what 128 bits hold -- which no type of the language holds either.
    std::optional<__int128> shifted_left(__int128 a, __int128 b)
    {
      if (b < 0) [[unlikely]]
        throw InternalError("a macro shifted by a negative count");
      if (a == 0)
        return 0;
      if (b >= 126)
        return std::nullopt;
      __int128 answer;
      if (__builtin_mul_overflow(a, (__int128)1 << b, &answer))
        return std::nullopt;
      return answer;
    }

    // `a >> b` as Python works it out: arithmetic, for any count.
    __int128 shifted_right(__int128 a, __int128 b)
    {
      if (b < 0) [[unlikely]]
        throw InternalError("a macro shifted by a negative count");
      if (b >= 127)
        return a < 0 ? -1 : 0;
      return a >> b;
    }

    // What each binary operation does to two numbers already in range, or
    // nothing where the answer is beyond 128 bits; `known` is false for an
    // operation the table has no entry for.
    std::optional<__int128> arithmetic(BinOp op, __int128 a, __int128 b, bool& known)
    {
      known = true;
      __int128 answer = 0;
      switch (op) {
      case BinOp::add:
      case BinOp::wrap_add:
      case BinOp::sat_add:
        if (__builtin_add_overflow(a, b, &answer))
          return std::nullopt;
        return answer;
      case BinOp::sub:
      case BinOp::wrap_sub:
      case BinOp::sat_sub:
        if (__builtin_sub_overflow(a, b, &answer))
          return std::nullopt;
        return answer;
      case BinOp::mul:
      case BinOp::wrap_mul:
      case BinOp::sat_mul:
        if (__builtin_mul_overflow(a, b, &answer))
          return std::nullopt;
        return answer;
      case BinOp::and_:
        return a & b;
      case BinOp::or_:
        return a | b;
      case BinOp::xor_:
        return a ^ b;
      case BinOp::shl:
      case BinOp::wrap_shl:
        return shifted_left(a, b);
      case BinOp::ashr:
      case BinOp::lshr:
      case BinOp::wrap_ashr:
      case BinOp::wrap_lshr:
        return shifted_right(a, b);
      case BinOp::smax:
      case BinOp::umax:
        return std::max(a, b);
      case BinOp::smin:
      case BinOp::umin:
        return std::min(a, b);
      default:
        known = false;
        return std::nullopt;
      }
    }

    // What each comparison asks of two numbers, already signed or unsigned.
    bool compared(ir::CmpPred pred, __int128 a, __int128 b) noexcept
    {
      switch (pred) {
      case ir::CmpPred::eq: return a == b;
      case ir::CmpPred::ne: return a != b;
      case ir::CmpPred::slt: case ir::CmpPred::ult: return a < b;
      case ir::CmpPred::sle: case ir::CmpPred::ule: return a <= b;
      case ir::CmpPred::sgt: case ir::CmpPred::ugt: return a > b;
      case ir::CmpPred::sge: case ir::CmpPred::uge: return a >= b;
      }
      return false;
    }

    // The part `index` of what is several values, as an assertion of pypl4g's
    // asks it to be.
    const MValue& part(const MValue& held, std::int64_t index)
    {
      auto parts = held.parts();
      if (parts == nullptr || index < 0 || std::size_t(index) >= parts->size()) [[unlikely]]
        throw InternalError("a macro took apart what is not several values");
      return (*parts)[std::size_t(index)];
    }

  } // anonymous namespace


  bool MValue::truthy() const noexcept
  {
    if (auto n = number())
      return *n != 0;
    if (auto p = parts())
      return ! p->empty();
    return false;
  }


  bool MValue::operator==(const MValue& other) const
  {
    if (v.index() != other.v.index())
      return false;
    if (auto n = number())
      return *n == *other.number();
    if (auto p = parts())
      return *p == *other.parts();
    return true;
  }


  // What a call's values are, by the value that computed each.
  struct Machine::Frame {
    std::unordered_map<const ir::Value*, MValue> values;
  };


  MValue Machine::call(const ir::Function* func, std::span<const MValue> args)
  {
    const ir::BasicBlock* at = func->entry();
    if (at == nullptr) {
      auto found = builtins.find(func->name);
      if (found == builtins.end()) [[unlikely]]
        throw Refused(std::format("a call to '{}', which has no body", func->name));
      return found->second(args);
    }
    if (depth >= calls_allowed) [[unlikely]]
      throw Stopped("it called itself too deeply");
    ++depth;
    struct Leave {
      std::int64_t& depth;
      ~Leave() { --depth; }
    } leave{ depth };
    Frame frame;
    std::vector<MValue> incoming(args.begin(), args.end());
    MValue answer;
    while (true) {
      std::size_t n = std::min(at->params.size(), incoming.size());
      for (std::size_t i = 0; i < n; ++i)
        frame.values[at->params[i].get()] = incoming[i];
      if (block(at, frame, incoming, answer))
        return answer;
    }
  }


  bool Machine::block(const ir::BasicBlock*& at, Frame& frame, std::vector<MValue>& incoming, MValue& answer)
  {
    for (const ir::Instruction* inst : at->insts) {
      if (++steps > limit) [[unlikely]]
        throw Stopped("it ran for too long");
      switch (inst->kind) {
      case InstKind::RetInst:
        answer = inst->operands.empty() ? MValue{} : operand(inst->operands[0], frame);
        return true;
      case InstKind::BrInst:
        goes(inst->targets[0], frame, at, incoming);
        return false;
      case InstKind::CondBrInst:
        goes(operand(inst->operands[0], frame).truthy() ? inst->targets[0] : inst->targets[1], frame, at, incoming);
        return false;
      case InstKind::SwitchInst: {
        MValue asked = operand(inst->operands[0], frame);
        for (std::size_t i = 0; i < inst->cases.size(); ++i)
          if (asked == MValue(inst->cases[i])) {
            goes(inst->targets[i], frame, at, incoming);
            return false;
          }
        goes(inst->targets.back(), frame, at, incoming);
        return false;
      }
      case InstKind::UnreachableInst:
        throw Stopped("it reached somewhere it said it would not");
      default:
        break;
      }
      bool computes = true;
      MValue found = one(inst, frame, computes);
      if (computes)
        frame.values[inst] = std::move(found);
    }
    throw Refused("a block with no end, which the verifier should have caught");
  }


  void Machine::goes(const ir::BlockTarget& target, Frame& frame, const ir::BasicBlock*& at,
                     std::vector<MValue>& incoming)
  {
    std::vector<MValue> handed;
    handed.reserve(target.args.size());
    for (auto one : target.args)
      handed.push_back(operand(one, frame));
    incoming = std::move(handed);
    at = target.block;
  }


  MValue Machine::one(const ir::Instruction* inst, Frame& frame, bool& computes)
  {
    auto all = [&] {
      std::vector<MValue> parts;
      parts.reserve(inst->operands.size());
      for (auto one : inst->operands)
        parts.push_back(operand(one, frame));
      return MValue(std::move(parts));
    };
    switch (inst->kind) {
    case InstKind::MemStartInst:
      // Memory is the machine's and needs no token to order it.
      return __int128(0);
    case InstKind::BinaryInst:
      return binary(inst, frame);
    case InstKind::UnaryInst:
      return unary(inst, frame);
    case InstKind::CmpInst: {
      __int128 left = number(inst->operands[0], frame);
      __int128 right = number(inst->operands[1], frame);
      return compared(inst->pred, left, right);
    }
    case InstKind::CastInst:
      return cast(inst, frame);
    case InstKind::SelectInst:
      return operand(number(inst->operands[0], frame) != 0 ? inst->operands[1] : inst->operands[2], frame);
    case InstKind::TupleInst:
    case InstKind::WrapInst:
    case InstKind::SumMakeInst:
      return all();
    case InstKind::ExtractInst:
      return part(operand(inst->operands[0], frame), inst->index);
    case InstKind::FailedInst:
    case InstKind::SumGetInst:
      return part(operand(inst->operands[0], frame), 1);
    case InstKind::ErrorInst:
      return part(operand(inst->operands[0], frame), 2);
    case InstKind::SumTagInst:
      return part(operand(inst->operands[0], frame), 0);
    case InstKind::FrameInst:
      return room(inst->held);
    case InstKind::AddressInst:
      return at(inst->operands[0]);
    case InstKind::FieldInst:
      return number(inst->operands[0], frame) + __int128(offset(inst));
    case InstKind::LoadInst:
      return read(number(inst->operands[1], frame), inst->ty);
    case InstKind::StoreInst:
      write(number(inst->operands[1], frame), inst->operands[2]->ty, operand(inst->operands[2], frame));
      // A store answers the memory after it, which something reads.
      return __int128(0);
    case InstKind::AssertInst:
      if (! operand(inst->operands[0], frame).truthy())
        throw Stopped(inst->what);
      computes = false;
      return {};
    case InstKind::CallInst: {
      const ir::Function* callee = inst->callee;
      if (callee == nullptr) [[unlikely]]
        throw Refused("a call through a value", inst);
      std::vector<MValue> given;
      given.reserve(inst->operands.size());
      for (auto one : inst->operands)
        given.push_back(operand(one, frame));
      // A call may carry more operands than the callee takes: what a record
      // answered through storage is handed is one of them.
      if (given.size() > callee->fty->params.size())
        given.resize(callee->fty->params.size());
      return call(callee, given);
    }
    default:
      break;
    }
    throw Refused(std::format("the instruction '{}'", inst->opcode()), inst);
  }


  MValue Machine::binary(const ir::Instruction* inst, Frame& frame)
  {
    __int128 left = number(inst->operands[0], frame);
    __int128 right = number(inst->operands[1], frame);
    const ir::Type* ty = inst->ty;
    auto refused = [&] { return Refused(std::format("the operation {}", describe(inst->bin)), inst); };
    bool known = true;
    if (inst->bin == BinOp::wide_mul) {
      // The whole product, which 128 bits have room for, cut into the two
      // halves the tuple holds: the upper read as the operands' type reads it
      // and the lower as the unsigned bits it is.
      auto tuple = ir::as<ir::TupleType>(ty);
      auto upper = tuple != nullptr ? ir::as<ir::IntType>(tuple->members[0]) : nullptr;
      if (upper == nullptr) [[unlikely]]
        throw InternalError("a wide product answering what is no pair of numbers");
      // Multiplied without a sign, which is the same bits for two signed
      // factors and keeps the product of two large unsigned ones defined.
      auto whole = (unsigned __int128)left * (unsigned __int128)right;
      return MValue(std::vector<MValue>{ in_range(__int128(whole >> 64), upper),
                                         __int128(whole & mask_of(64)) });
    }
    if (inst->bin == BinOp::clmul) {
      // The same two halves of a product with no carries in it, both of them
      // unsigned.
      auto whole = ir::carryless_product(std::uint64_t(left), std::uint64_t(right));
      return MValue(std::vector<MValue>{ __int128(whole >> 64), __int128(whole & mask_of(64)) });
    }
    if (ty == ir::bool_type()) {
      auto found = arithmetic(inst->bin, left, right, known);
      if (! known) [[unlikely]]
        throw refused();
      return found.value_or(1) != 0;
    }
    if (ir::is<ir::PtrType>(ty)) {
      // An address is a whole number here, as it is to the instruction
      // selectors.
      auto found = arithmetic(inst->bin, left, right, known);
      if (! known) [[unlikely]]
        throw refused();
      if (! found) [[unlikely]]
        throw InternalError("a macro worked out an address beyond 128 bits");
      return __int128((unsigned __int128)*found & mask_of(64));
    }
    auto it = ir::as<ir::IntType>(ty);
    if (it == nullptr) [[unlikely]]
      throw Refused(std::format("arithmetic on {}", ty->render()), inst);
    switch (inst->bin) {
    case BinOp::sdiv:
    case BinOp::udiv:
    case BinOp::srem:
    case BinOp::urem: {
      if (right == 0) [[unlikely]]
        throw Stopped("a division with no answer");
      __int128 whole = (left < 0 ? -left : left) / (right < 0 ? -right : right);
      if ((left < 0) != (right < 0))
        whole = -whole;
      if (inst->bin == BinOp::sdiv || inst->bin == BinOp::udiv)
        return in_range(whole, ty);
      return in_range(left - whole * right, ty);
    }
    case BinOp::ashr:
    case BinOp::lshr:
      return in_range(shifted_right(left, right), ty);
    case BinOp::rotl:
    case BinOp::rotr:
    case BinOp::wrap_shl:
    case BinOp::wrap_lshr:
    case BinOp::wrap_ashr:
    case BinOp::wrap_rotl:
    case BinOp::wrap_rotr: {
      // Over the type's own bits, the distance read as the bits of its type
      // without a sign: inside a wrap modulo the width, every width, and
      // outside one a rotation as far as the width or further stops.
      unsigned bits = it->bits;
      auto mask = mask_of(bits);
      auto distance = (unsigned __int128)right & mask;
      bool wrapped = inst->bin != BinOp::rotl && inst->bin != BinOp::rotr;
      if (wrapped)
        distance %= bits;
      else if (distance >= bits) [[unlikely]]
        throw Stopped("a rotation as far as the width or further");
      auto raw = (unsigned __int128)left & mask;
      auto d = unsigned(distance);
      unsigned __int128 answer;
      switch (inst->bin) {
      case BinOp::wrap_shl: answer = raw << d; break;
      case BinOp::wrap_lshr: answer = raw >> d; break;
      case BinOp::wrap_ashr: answer = (unsigned __int128)(left >> d); break;
      case BinOp::rotl: case BinOp::wrap_rotl: answer = (raw << d) | (raw >> (bits - d)); break;
      default: answer = (raw >> d) | (raw << (bits - d)); break;
      }
      answer &= mask;
      if (it->is_signed && ((answer >> (bits - 1)) & 1))
        return __int128(answer) - (__int128(1) << bits);
      return __int128(answer);
    }
    default:
      break;
    }
    auto found = arithmetic(inst->bin, left, right, known);
    if (! known) [[unlikely]]
      throw refused();
    if (! found || ! it->holds(*found)) [[unlikely]]
      // The same thing the generated program does: what will not fit is not
      // wrapped quietly.
      throw Stopped("an answer that will not fit its type");
    return *found;
  }


  MValue Machine::unary(const ir::Instruction* inst, Frame& frame)
  {
    __int128 held = number(inst->operands[0], frame);
    auto ty = ir::as<ir::IntType>(inst->operands[0]->ty);
    if (ty == nullptr) [[unlikely]]
      throw InternalError("a macro counted the bits of what is no integer");
    // Counted over the type's own bits, which for a type narrower than what
    // holds it are fewer than the holder's.
    __int128 raw = held & ((__int128(1) << ty->bits) - 1);
    if (inst->un == ir::UnOp::count_ones)
      return __int128(std::popcount((unsigned __int128)raw >> 64) + std::popcount((std::uint64_t)raw));
    if (inst->un == ir::UnOp::count_leading)
      return __int128(ty->bits) - __int128(bit_length(raw));
    if (inst->un == ir::UnOp::count_trailing) {
      // The lowest set bit is the only one a value shares with its negation;
      // nought has none, and its count is the width.
      if (raw == 0)
        return __int128(ty->bits);
      return __int128(bit_length(raw & -raw)) - 1;
    }
    throw Refused(std::format("the operation {}", describe(inst->un)), inst);
  }


  MValue Machine::cast(const ir::Instruction* inst, Frame& frame)
  {
    MValue held = operand(inst->operands[0], frame);
    const ir::Type* source = inst->operands[0]->ty;
    if (inst->cast == ir::CastKind::bitcast)
      return held;
    auto n = held.number();
    if (n == nullptr) [[unlikely]]
      throw Refused(std::format("a cast of {}", source->render()), inst);
    switch (inst->cast) {
    case ir::CastKind::trunc: {
      const ir::IntType* target = ir::as<ir::IntType>(inst->ty);
      if (target == nullptr) {
        auto ch = ir::as<ir::CharType>(inst->ty);
        if (ch == nullptr) [[unlikely]]
          throw InternalError("a macro narrowed to what is no integer");
        target = ch->holder();
      }
      return in_range(__int128((unsigned __int128)as_written(*n, source) & mask_of(target->held())), target);
    }
    case ir::CastKind::sext:
      return held;
    case ir::CastKind::zext:
      return as_written(*n, source);
    default:
      break;
    }
    throw Refused(std::format("the cast {}", enum_name(inst->cast)), inst);
  }


  std::uint64_t Machine::offset(const ir::Instruction* inst) const
  {
    const ir::Type* held = inst->operands[0]->ty;
    if (auto ptr = ir::as<ir::PtrType>(held))
      held = ptr->pointee;
    auto record = ir::as<ir::ProductType>(held);
    if (record == nullptr) [[unlikely]]
      throw Refused(std::format("a field of {}", held->render()), inst);
    return ir::offsets_of(record, machine_layout()).at(std::size_t(inst->index));
  }


  // -- memory -----------------------------------------------------------------

  __int128 Machine::at(const ir::Value* var)
  {
    if (var->vkind != ir::ValueKind::global) [[unlikely]]
      throw Refused(std::format("the address of {}", var->ty->render()));
    auto global = static_cast<const ir::GlobalVar*>(var);
    if (auto found = placed.find(global->name); found != placed.end())
      return found->second;
    __int128 where = room(global->value_type);
    placed[global->name] = where;
    if (global->initializer != nullptr)
      put(where, global->initializer);
    return where;
  }


  void Machine::put(__int128 where, const ir::Value* held)
  {
    const auto& layout = machine_layout();
    switch (held->vkind) {
    case ir::ValueKind::array_const: {
      auto array = static_cast<const ir::ArrayConst*>(held);
      auto element = ir::as<ir::ArrayType>(held->ty)->element;
      auto stride = std::max<std::uint64_t>(1, ir::size_of(element, layout));
      for (std::size_t which = 0; which < array->elements.size(); ++which)
        put(where + __int128(which * stride), array->elements[which]);
      return;
    }
    case ir::ValueKind::record_const: {
      auto record = static_cast<const ir::RecordConst*>(held);
      auto offsets = ir::offsets_of(ir::as<ir::ProductType>(held->ty), layout);
      for (std::size_t which = 0; which < record->fields.size(); ++which)
        put(where + __int128(offsets[which]), record->fields[which]);
      return;
    }
    case ir::ValueKind::sum_const: {
      auto sum = static_cast<const ir::SumConst*>(held);
      if (sum->part != nullptr)
        put(where, sum->part);
      write(where + __int128(ir::tag_offset_of(held->ty, layout)), ir::u8(), __int128(sum->index));
      return;
    }
    case ir::ValueKind::address_const: {
      auto address = static_cast<const ir::AddressConst*>(held);
      write(where, ir::u64(), address->target == nullptr ? __int128(0) : at(address->target) + address->offset);
      return;
    }
    case ir::ValueKind::parts_const: {
      auto parts = static_cast<const ir::PartsConst*>(held);
      auto pieces = ir::parts_of(held->ty);
      auto offsets = ir::member_offsets_of(ir::tuple_type(pieces), layout);
      for (std::size_t i = 0; i < std::min(offsets.size(), parts->parts.size()); ++i)
        put(where + __int128(offsets[i]), parts->parts[i]);
      return;
    }
    case ir::ValueKind::undef:
      // Nothing was written, so nought is what is there.
      return;
    default:
      write(where, held->ty, operand(held, Frame{}));
    }
  }


  MValue Machine::text(std::string_view held)
  {
    __int128 where = allocate(std::max<std::size_t>(1, held.size()));
    std::ranges::copy(held, memory.begin() + std::ptrdiff_t(where));
    return MValue(std::vector<MValue>{ where, __int128(held.size()), __int128(0) });
  }


  std::string Machine::read_text(const MValue& held) const
  {
    auto parts = held.parts();
    if (parts == nullptr || parts->size() < 2 || (*parts)[0].number() == nullptr || (*parts)[1].number() == nullptr)
      [[unlikely]]
      throw InternalError("a macro's text is not text");
    auto where = std::size_t(*(*parts)[0].number());
    auto length = std::size_t(*(*parts)[1].number());
    where = std::min(where, memory.size());
    length = std::min(length, memory.size() - where);
    std::string found(memory.begin() + std::ptrdiff_t(where), memory.begin() + std::ptrdiff_t(where + length));
    // Python decodes with "replace"; here each byte that does not begin a
    // well-formed sequence becomes the replacement character.
    std::string answer;
    for (std::size_t i = 0; i < found.size();) {
      auto c = (unsigned char)found[i];
      std::size_t n = c < 0x80 ? 1 : (c & 0xe0) == 0xc0 ? 2 : (c & 0xf0) == 0xe0 ? 3 : (c & 0xf8) == 0xf0 ? 4 : 0;
      bool good = n != 0 && i + n <= found.size();
      for (std::size_t k = 1; good && k < n; ++k)
        good = ((unsigned char)found[i + k] & 0xc0) == 0x80;
      if (good) {
        answer.append(found, i, n);
        i += n;
      } else {
        answer += "\N{REPLACEMENT CHARACTER}";
        ++i;
      }
    }
    return answer;
  }


  __int128 Machine::allocate(__int128 size)
  {
    auto want = std::size_t(std::max<__int128>(1, size));
    std::size_t where = memory.size();
    where += (16 - where % 16) % 16;
    memory.resize(where + want);
    return __int128(where);
  }


  __int128 Machine::room(const ir::Type* held)
  {
    const auto& layout = machine_layout();
    auto want = std::max<std::uint64_t>(1, ir::size_of(held, layout));
    auto boundary = std::max<std::uint64_t>(1, ir::align_of(held, layout));
    std::size_t where = memory.size();
    where += (boundary - where % boundary) % boundary;
    memory.resize(where + want);
    return __int128(where);
  }


  MValue Machine::read(__int128 where, const ir::Type* ty) const
  {
    if (where < 0) [[unlikely]]
      throw InternalError("a macro read before its memory");
    auto width = std::max<std::uint64_t>(1, ir::size_of(ty, machine_layout()));
    // A read past the end finds nothing there, as Python's slice does; what
    // is beyond 128 bits is not kept.
    unsigned __int128 raw = 0;
    for (std::uint64_t i = 0; i < std::min<std::uint64_t>(width, 16); ++i) {
      auto at = std::size_t(where) + i;
      if (at < memory.size())
        raw |= (unsigned __int128)memory[at] << (8 * i);
    }
    if (ir::is<ir::IntType>(ty))
      return in_range(__int128(raw), ty);
    return __int128(raw);
  }


  void Machine::write(__int128 where, const ir::Type* ty, const MValue& held)
  {
    if (held.parts() != nullptr) [[unlikely]]
      throw Refused(std::format("a store of {}, which is several values at once", ty->render()));
    auto n = held.number();
    if (n == nullptr || where < 0) [[unlikely]]
      throw InternalError("a macro stored nothing, or before its memory");
    auto width = std::max<std::uint64_t>(1, ir::size_of(ty, machine_layout()));
    __int128 number = ir::is<ir::IntType>(ty) ? as_written(*n, ty) : *n;
    auto end = std::size_t(where) + width;
    if (end > memory.size())
      memory.resize(end);
    for (std::uint64_t i = 0; i < width; ++i)
      memory[std::size_t(where) + i] = i < 16 ? std::uint8_t((unsigned __int128)number >> (8 * i))
                                              : number < 0 ? 0xff : 0;
  }


  // -- operands ---------------------------------------------------------------

  MValue Machine::operand(const ir::Value* value, const Frame& frame) const
  {
    switch (value->vkind) {
    case ir::ValueKind::int_const:
      return static_cast<const ir::IntConst*>(value)->value;
    case ir::ValueKind::bool_const:
      return static_cast<const ir::BoolConst*>(value)->value;
    case ir::ValueKind::char_const:
      return __int128(static_cast<const ir::CharConst*>(value)->value);
    case ir::ValueKind::enum_const:
      return __int128(static_cast<const ir::EnumConst*>(value)->number());
    case ir::ValueKind::undef:
      // A macro reading what was never written is answered nought.
      return __int128(0);
    default:
      break;
    }
    auto found = frame.values.find(value);
    if ((found == frame.values.end() || found->second.is_none()) && value->vkind != ir::ValueKind::block_param)
      [[unlikely]]
      throw Refused(std::format("a value of {} that nothing computed", value->ty->render()));
    return found == frame.values.end() ? MValue{} : found->second;
  }


  __int128 Machine::number(const ir::Value* value, const Frame& frame) const
  {
    MValue found = operand(value, frame);
    auto n = found.number();
    if (n == nullptr) [[unlikely]]
      throw Refused(std::format("a number wanted and {} given", value->ty->render()));
    return *n;
  }


  std::string what_it_holds(const ir::Type* ty)
  {
    if (ir::is<ir::IntType>(ty) || ir::is<ir::BoolType>(ty) || ir::is<ir::CharType>(ty) || ir::is<ir::EnumType>(ty)
        || ir::is<ir::PtrType>(ty) || ir::is<ir::SyntaxType>(ty))
      return "a number";
    if (ir::is<ir::FloatType>(ty))
      return "a floating-point number, which the machine does not hold";
    return "several values at once";
  }

} // namespace pl4g::macro
