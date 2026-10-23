// Placing the program's variables in the image.
#include "target/globals.hh"
#include "target/emit.hh"

#include <algorithm>

#include "ir/builder.hh"
#include "target/relro.hh"
#include "target/started.hh"


namespace pl4g::target {

  namespace {

    // The name in the image of what an address in a variable points at.
    std::string target_symbol(const ir::Value* target)
    {
      if (target->vkind == ir::ValueKind::global)
        return symbol_of(static_cast<const ir::GlobalVar*>(target));
      return ir::symbol_name(*static_cast<const ir::Function*>(target));
    }

    // An address `offset` bytes into a symbol.
    mc::ExprRef at_offset(mc::ExprRef where, std::int64_t offset)
    {
      return offset == 0 ? where : mc::bin_expr('+', std::move(where), mc::const_expr(offset));
    }

    void put(std::vector<std::uint8_t>& out, std::uint64_t at, const std::vector<std::uint8_t>& bits)
    {
      std::ranges::copy(bits, out.begin() + std::ptrdiff_t(at));
    }

    // The bytes a constant occupies, laid out as its type says; `at` is where in
    // the variable they go.
    std::vector<std::uint8_t> encoded(const ir::Value* initializer, const ir::Type* ty, const ir::DataLayout& layout,
                                      std::vector<HeldAddress>& addresses, std::uint64_t at)
    {
      auto zeroes = [&] { return std::vector<std::uint8_t>(ir::size_of(ty, layout), 0); };
      if (initializer == nullptr)
        return zeroes();
      switch (initializer->vkind) {
      case ir::ValueKind::address_const: {
        auto address = static_cast<const ir::AddressConst*>(initializer);
        if (address->target != nullptr)
          addresses.push_back({ at, address->target, address->offset });
        return std::vector<std::uint8_t>(layout.pointer_size, 0);
      }
      case ir::ValueKind::parts_const: {
        // Each part where it is whenever such a value is in memory: laid out
        // as a tuple of the parts would be.
        auto pieces = ir::parts_of(ty);
        auto out = zeroes();
        auto offsets = ir::member_offsets_of(ir::tuple_type(pieces), layout);
        const auto& parts = static_cast<const ir::PartsConst*>(initializer)->parts;
        for (std::size_t i = 0; i < std::min({ offsets.size(), pieces.size(), parts.size() }); ++i)
          put(out, offsets[i], encoded(parts[i], pieces[i], layout, addresses, at + offsets[i]));
        return out;
      }
      case ir::ValueKind::int_const:
        return ir::encode_scalar(static_cast<const ir::IntConst*>(initializer)->value, ty, layout);
      case ir::ValueKind::bool_const:
        return ir::encode_scalar(static_cast<const ir::BoolConst*>(initializer)->value ? 1 : 0, ty, layout);
      case ir::ValueKind::float_const:
        return ir::encode_float(static_cast<const ir::FloatConst*>(initializer)->value, ty, layout);
      case ir::ValueKind::char_const:
        if (auto c = ir::as<ir::CharType>(ty))
          return ir::encode_scalar(static_cast<const ir::CharConst*>(initializer)->value, c->holder(), layout);
        return zeroes();
      case ir::ValueKind::enum_const:
        if (auto e = ir::as<ir::EnumType>(ty))
          return ir::encode_scalar(static_cast<const ir::EnumConst*>(initializer)->number(), e->holder, layout);
        return zeroes();
      case ir::ValueKind::array_const:
        if (auto array = ir::as<ir::ArrayType>(ty)) {
          // Element after element, each where the stride puts it.
          auto step = ir::stride_of(array->element, layout);
          auto out = zeroes();
          const auto& elements = static_cast<const ir::ArrayConst*>(initializer)->elements;
          for (std::size_t i = 0; i < elements.size(); ++i)
            put(out, i * step, encoded(elements[i], array->element, layout, addresses, at + i * step));
          return out;
        }
        return zeroes();
      case ir::ValueKind::record_const:
        if (auto record = ir::as<ir::ProductType>(ty)) {
          // Each field where `offsets_of` puts it, the padding left as zeroes.
          auto out = zeroes();
          auto offsets = ir::offsets_of(record, layout);
          const auto& fields = static_cast<const ir::RecordConst*>(initializer)->fields;
          for (std::size_t i = 0; i < std::min({ offsets.size(), fields.size(), record->fields.size() }); ++i)
            put(out, offsets[i], encoded(fields[i], record->fields[i].second, layout, addresses, at + offsets[i]));
          return out;
        }
        return zeroes();
      case ir::ValueKind::result_const:
        if (auto result = ir::as<ir::ResultType>(ty)) {
          // The answer where an answer goes, the truth value where the layout
          // says, and the rest zeroes.
          auto out = zeroes();
          auto made = static_cast<const ir::ResultConst*>(initializer);
          put(out, 0, encoded(made->answer, result->ok, layout, addresses, at));
          out[ir::tag_offset_of(result, layout)] = made->failed ? 1 : 0;
          return out;
        }
        return zeroes();
      case ir::ValueKind::sum_const:
        if (auto sum = ir::as<ir::SumType>(ty)) {
          // What the part carries at the start, the tag where the layout puts it.
          auto out = zeroes();
          auto made = static_cast<const ir::SumConst*>(initializer);
          if (made->part != nullptr)
            put(out, 0, encoded(made->part, sum->variants[made->index].second, layout, addresses, at));
          out[ir::tag_offset_of(sum, layout)] = std::uint8_t(made->index);
          return out;
        }
        return zeroes();
      default:
        return zeroes();
      }
    }

    // Emit `variables` into the section `name`, created only where something
    // goes in it.
    void emit_group(mc::Assembler& asm_, const std::vector<const ir::GlobalVar*>& variables, std::string_view name,
                    bool writable, const ir::DataLayout& layout)
    {
      if (variables.empty())
        return;
      std::uint64_t alignment = 0;
      for (auto var : variables)
        alignment = std::max(alignment, ir::align_of(var->value_type, layout_for(var, layout)));
      asm_.section(std::string(name), placed(writable, alignment));
      for (auto var : variables) {
        auto mine = layout_for(var, layout);
        asm_.align(ir::align_of(var->value_type, mine));
        bool visible = var->linkage == ir::Linkage::visible;
        auto symbol = asm_.label(symbol_of(var), visible ? mc::SymBinding::global : mc::SymBinding::local,
                                 mc::SymKind::object,
                                 visible ? mc::SymVisibility::default_ : mc::SymVisibility::hidden);
        std::vector<HeldAddress> addresses;
        auto data = initial_bytes(var, mine, &addresses);
        // The image is loaded where the linker put it, so each address is
        // worked out now and written as the number it is.
        std::vector<mc::MCFixup> fixups;
        for (const auto& one : addresses) {
          mc::MCFixup fixup;
          fixup.offset = std::int64_t(one.at);
          fixup.kind = &mc::abs64;
          fixup.target = at_offset(mc::sym_expr(asm_.symbol_named(target_symbol(one.target))), one.inside);
          fixups.push_back(std::move(fixup));
        }
        asm_.bytes(std::move(data), std::move(fixups));
        asm_.end_label(symbol);
      }
    }

  } // anonymous namespace


  void emit_globals(mc::Assembler& asm_, ir::Module& module, const ir::DataLayout& layout)
  {
    if (! module.ring_addresses.empty()) {
      auto at = ring_at(module, layout);
      if (! at) [[unlikely]]
        throw InternalError("an address of the ring, and no record to hold one");
      for (auto one : module.ring_addresses)
        const_cast<ir::AddressConst*>(static_cast<const ir::AddressConst*>(one))->offset = std::int64_t(*at);
    }
    std::vector<const ir::GlobalVar*> constant, variable, sealed;
    for (const auto& [_, var] : module.globals)
      (! var->is_mutable() ? constant : var->relro ? sealed : variable).push_back(var.get());
    emit_group(asm_, constant, rodata_section, false, layout);
    emit_group(asm_, variable, data_section, true, layout);
    // What the constructors alone write: writable while they run, and in a
    // section of its own so that the pages made read-only after them are
    // nothing else's.
    emit_group(asm_, sealed, relro_section, true, layout);
  }


  ir::DataLayout layout_for(const ir::GlobalVar* var, const ir::DataLayout& layout)
  {
    if (! var->system_layout || layout.system)
      return layout;
    auto mine = layout;
    mine.system = true;
    return mine;
  }


  std::string symbol_of(const ir::GlobalVar* var)
  {
    if (! var->module.empty())
      return var->module + "." + var->name;
    return var->name;
  }


  std::vector<std::uint8_t> initial_bytes(const ir::GlobalVar* var, const ir::DataLayout& layout,
                                          std::vector<HeldAddress>* addresses)
  {
    std::vector<HeldAddress> unused;
    return encoded(var->initializer, var->value_type, layout, addresses != nullptr ? *addresses : unused, 0);
  }

} // namespace pl4g::target
