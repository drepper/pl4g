// What a program is started with, and where the entry point puts it.
#include "target/started.hh"
#include "target/emit.hh"

#include <format>


namespace pl4g::target {

  const ir::ProductType* wanted_by(const ir::Module& module)
  {
    auto startup = module.startup;
    if (startup == nullptr || startup->fty->params.empty())
      return nullptr;
    auto held = ir::as<ir::PtrType>(startup->fty->params[0]);
    auto found = held != nullptr ? ir::as<ir::ProductType>(held->pointee) : nullptr;
    if (found == nullptr) [[unlikely]]
      throw InternalError("the startup function takes something other than a reference to a record");
    return found;
  }


  std::pair<std::uint64_t, const ir::Type*> where_in(const ir::ProductType* found, std::string_view name,
                                                     const ir::DataLayout& layout)
  {
    for (std::size_t at = 0; at < found->fields.size(); ++at)
      if (found->fields[at].first == name)
        return { ir::offsets_of(found, layout)[at], found->fields[at].second };
    throw InternalError(std::format("the record a program is started with has no '{}'; {} is filled in by name and "
                                    "not by position",
                                    name, init_symbol));
  }


  std::optional<std::uint64_t> arguments_at(const ir::Module& module, const ir::DataLayout& layout)
  {
    auto found = wanted_by(module);
    if (found == nullptr)
      return std::nullopt;
    return where_in(found, arguments_field, layout).first;
  }


  std::optional<std::uint64_t> ring_at(const ir::Module& module, const ir::DataLayout& layout)
  {
    auto found = wanted_by(module);
    if (found == nullptr)
      return std::nullopt;
    auto [start, state] = where_in(found, state_field, layout);
    auto record = ir::as<ir::ProductType>(state);
    if (record == nullptr) [[unlikely]]
      throw InternalError("the state a program is started with is not a record");
    return start + where_in(record, ring_field, layout).first;
  }


  bool holds_stack(const ir::Module& module, const ir::DataLayout& layout)
  {
    return arguments_at(module, layout) || module.environ != nullptr || module.auxv != nullptr;
  }


  bool entry_wants_runtime(const ir::Module& module)
  {
    return wanted_by(module) != nullptr || module.environ != nullptr;
  }


  void emit_started(mc::Assembler& asm_, const ir::Module& module, const ir::DataLayout& layout)
  {
    auto found = wanted_by(module);
    if (found == nullptr)
      return;
    mc::Bytes out(ir::size_of(found, layout), 0);
    auto ring = *ring_at(module, layout);
    auto [start, held_devices] = where_in(found, devices_field, layout);
    auto devices = ir::as<ir::ProductType>(held_devices);
    if (devices == nullptr || devices->fields.size() != inherited.size()) [[unlikely]]
      throw InternalError("the devices a program is started with are not the three it inherits");
    auto offsets = ir::offsets_of(devices, layout);
    std::vector<mc::MCFixup> fixups;
    for (std::size_t i = 0; i < inherited.size(); ++i) {
      auto one = ir::as<ir::ProductType>(devices->fields[i].second);
      if (one == nullptr) [[unlikely]]
        throw InternalError("a device a program is started with is not a record");
      auto [at, held] = where_in(one, descriptor_field, layout);
      auto integer = ir::as<ir::IntType>(held);
      if (integer == nullptr) [[unlikely]]
        throw InternalError("a device's descriptor is not an integer");
      auto width = integer->bits / 8;
      auto number = static_cast<std::uint64_t>(static_cast<std::int64_t>(inherited[i]));
      for (unsigned b = 0; b < width; ++b)
        out[start + offsets[i] + at + b] = std::uint8_t(number >> (8 * b));
      mc::MCFixup fixup;
      fixup.offset = std::int64_t(start + offsets[i] + where_in(one, ring_field, layout).first);
      fixup.kind = &mc::abs64;
      fixup.target = mc::bin_expr('+', mc::sym_expr(asm_.symbol_named(std::string(init_symbol))),
                                  mc::const_expr(std::int64_t(ring)));
      fixups.push_back(std::move(fixup));
    }
    auto alignment = ir::align_of(found, layout);
    asm_.section(std::string(init_section), placed(true, alignment));
    asm_.align(alignment);
    auto symbol = asm_.label(std::string(init_symbol), mc::SymBinding::local, mc::SymKind::object,
                             mc::SymVisibility::hidden);
    asm_.bytes(std::move(out), std::move(fixups));
    asm_.end_label(symbol);
  }

} // namespace pl4g::target
