// Run a program of assembler-builder calls, read as JSON, through the machine
// code layer and the ELF writer with a small made-up target, and print what
// came of it: the debugging dump, what the register allocator decided, each
// function's frame, and the image.  src/test/mc_agrees.py makes the programs
// up and holds this to what pypl4g's layer makes of them with the same target.
//
// Usage: pl4g-mcdump PROGRAM.json
#include <argp.h>

#include <format>
#include <map>
#include <print>
#include <string>
#include <vector>

#include <simdjson.h>

#include "elf/writer.hh"
#include "mc/asmbuilder.hh"
#include "mc/dump.hh"
#include "source/manager.hh"


namespace {

  using namespace pl4g::mc;
  using Element = ::simdjson::dom::element;
  using pl4g::Span;

  // A target of six integer registers, three floating-point ones, a stack
  // pointer and a link register, whose instructions encode as their text:
  // what mc_agrees.py's `Fake` is.
  class Fake final : public InstructionSelector {
  public:
    Fake()
    {
      gpr = info.add_class("gpr", 64);
      fpr = info.add_class("fpr", 64);
      auto special = info.add_class("special", 64, false);
      for (int at = 0; at < 6; ++at) {
        auto unit = info.add_unit(gpr, at, "r" + std::to_string(at));
        info.add_view(unit, "r" + std::to_string(at), 64);
        info.add_view(unit, "r" + std::to_string(at) + "d", 32);
        info.add_view(unit, "r" + std::to_string(at) + "b", 8);
      }
      for (int at = 0; at < 3; ++at) {
        auto unit = info.add_unit(fpr, 16 + at, "f" + std::to_string(at));
        info.add_view(unit, "f" + std::to_string(at), 64);
        info.add_view(unit, "f" + std::to_string(at) + "s", 32);
      }
      sp = info.add_view(info.add_unit(special, 30, "sp"), "sp", 64);
      lr = info.add_view(info.add_unit(special, 31, "lr"), "lr", 64);
      using R = OperandRole;
      add("mov", InstFlags::move, { R::def, R::use });
      add("add", InstFlags::none, { R::def, R::use, R::use });
      add("sub", InstFlags::none, { R::def, R::use, R::use });
      add("mul", InstFlags::none, { R::def, R::use, R::use }, { info.reg_named("r2") }, { info.reg_named("r0") });
      add("and", InstFlags::none, { R::def, R::use, R::use });
      add("or", InstFlags::none, { R::def_use, R::use, R::use });
      add("xor", InstFlags::none, { R::def, R::use, R::use });
      add("cmp", InstFlags::none, { R::use, R::use });
      add("jmp", InstFlags::barrier | InstFlags::terminator, { R::use });
      add("call", InstFlags::call, { R::use });
      add("ret", InstFlags::return_ | InstFlags::barrier | InstFlags::terminator, {});
      add("st", InstFlags::may_store, { R::use, R::use });
      add("ld", InstFlags::may_load, { R::def, R::use });
      add("lea", InstFlags::none, { R::def, R::use });
      for (int c = 0; c <= int(Condition::uge); ++c)
        add("b." + std::string(name_of(Condition(c))), InstFlags::terminator, { R::use });
    }

    RegisterInfo info;
    const RegClass* gpr;
    const RegClass* fpr;
    const PhysReg* sp;
    const PhysReg* lr;

    InstRef inst(const std::string& name, std::vector<MCOperand> operands, Span span = pl4g::invalid_span)
    {
      return make_inst(&descs.at(name), std::move(operands), span);
    }

    Insts select_move(Reg dst, const MCOperand& src, Span span) override
    {
      return { inst("mov", { MCReg{ dst }, src }, span) };
    }

    Insts select_op(const Op* op, Reg dst, std::span<const MCOperand> sources, Span span) override
    {
      static const std::map<std::string, std::string> names{ { "plus", "add" }, { "minus", "sub" },
                                                              { "times", "mul" }, { "and", "and" },
                                                              { "or", "or" },     { "xor", "xor" },
                                                              { "compare", "cmp" } };
      std::vector<MCOperand> operands;
      if (dst)
        operands.push_back(MCReg{ dst });
      operands.insert(operands.end(), sources.begin(), sources.end());
      return { inst(names.at(op->name), std::move(operands), span) };
    }

    Insts select_store(const MCMem& address, const MCOperand& value, Span span) override
    {
      return { inst("st", { address, value }, span) };
    }

    Insts select_call(const MCOperand& target, Span span) override { return { inst("call", { target }, span) }; }
    Insts select_return(Span span) override { return { inst("ret", {}, span) }; }
    Insts select_jump(const MCSymRef& target, Span span) override { return { inst("jmp", { target }, span) }; }

    Insts select_branch(Condition cond, const MCOperand& lhs, const MCOperand& rhs, const MCSymRef& target,
                        Span span) override
    {
      return { inst("cmp", { lhs, rhs }, span), inst("b." + std::string(name_of(cond)), { target }, span) };
    }

    Insts select_address(Reg dst, const MCSymRef& symbol, Span span) override
    {
      return { inst("lea", { MCReg{ dst }, symbol }, span) };
    }

    Insts select_spill(std::int64_t slot, Reg source, Span span) override
    {
      MCMem place;
      place.base = Reg(sp);
      place.disp = slot;
      place.size_bits = source.bits();
      return { inst("st", { place, MCReg{ source } }, span) };
    }

    Insts select_reload(Reg destination, std::int64_t slot, Span span) override
    {
      MCMem place;
      place.base = Reg(sp);
      place.disp = slot;
      place.size_bits = destination.bits();
      return { inst("ld", { MCReg{ destination }, place }, span) };
    }

    Insts select_frame(std::int64_t size, Span span) override
    {
      return { inst("sub", { MCReg{ Reg(sp) }, MCReg{ Reg(sp) }, MCImm{ size, 32 } }, span) };
    }

    Insts select_unframe(std::int64_t size, Span span) override
    {
      return { inst("add", { MCReg{ Reg(sp) }, MCReg{ Reg(sp) }, MCImm{ size, 32 } }, span) };
    }

    std::int64_t link_slot_size() override { return 8; }

    std::pair<std::optional<std::int64_t>, std::int64_t> frame_walk(std::int64_t total, std::int64_t size,
                                                                    std::int64_t link) override
    {
      return { link != 0 ? std::optional(size) : std::nullopt, total };
    }

    Insts select_save_link(std::int64_t offset, Span span) override
    {
      MCMem place;
      place.base = Reg(sp);
      place.disp = offset;
      place.size_bits = 64;
      return { inst("st", { place, MCReg{ Reg(lr) } }, span) };
    }

    Insts select_restore_link(std::int64_t offset, Span span) override
    {
      MCMem place;
      place.base = Reg(sp);
      place.disp = offset;
      place.size_bits = 64;
      return { inst("ld", { MCReg{ Reg(lr) }, place }, span) };
    }

    // The text of the instruction, its length first, and four bytes for each
    // symbol it refers to, which a fixup fills in.
    std::pair<Bytes, std::vector<MCFixup>> encode(const MCInst& one) const
    {
      auto text = one.render();
      Bytes out{ std::uint8_t(text.size() & 0xFF) };
      out.insert(out.end(), text.begin(), text.end());
      std::vector<MCFixup> fixups;
      for (const auto& operand : one.operands)
        if (auto ref = std::get_if<MCSymRef>(&operand)) {
          fixups.push_back(MCFixup{ std::int64_t(out.size()), &pcrel32, ref->expr });
          out.insert(out.end(), 4, 0);
        }
      return { std::move(out), std::move(fixups) };
    }

  private:
    // A row of the table; no roles is Python's `None`.
    void add(const std::string& name, InstFlags flags, std::vector<OperandRole> roles,
             std::vector<const PhysReg*> defs = {}, std::vector<const PhysReg*> uses = {})
    {
      InstDesc desc;
      desc.mnemonic = name;
      desc.flags = flags;
      if (! roles.empty())
        desc.roles = std::move(roles);
      desc.implicit_defs = std::move(defs);
      desc.implicit_uses = std::move(uses);
      descs.emplace(name, std::move(desc));
    }

    std::map<std::string, InstDesc> descs;
  };


  std::string text_of(Element e)
  {
    std::string_view s;
    if (e.get(s) != ::simdjson::SUCCESS)
      return {};
    return std::string(s);
  }

  std::int64_t int_of(Element e)
  {
    std::int64_t v = 0;
    if (e.get(v) != ::simdjson::SUCCESS) {
      bool b = false;
      if (e.get(b) == ::simdjson::SUCCESS)
        return b ? 1 : 0;
    }
    return v;
  }

  bool bool_of(Element e)
  {
    bool b = false;
    if (e.get(b) != ::simdjson::SUCCESS)
      return false;
    return b;
  }


  // Run the program and print what came of it, as mc_agrees.py does.
  std::string run(Element program)
  {
    Fake fake;
    MCStreamer streamer([&](const MCInst& one) { return fake.encode(one); });
    auto units = [&](Element list) {
      std::vector<const RegUnit*> found;
      for (Element one : list.get_array())
        found.push_back(fake.info.unit_named(text_of(one)));
      return found;
    };
    AssemblerOptions options;
    options.registers = &fake.info;
    Element order = program["order"];
    if (order.is_object()) {
      pl4g::OrderedMap<std::string, std::vector<const RegUnit*>> classes;
      for (auto field : order.get_object())
        classes[std::string(field.key)] = units(field.value);
      options.allocation_order = AllocationOrder::per_class(std::move(classes));
    } else
      options.allocation_order = AllocationOrder::single(units(order));
    for (auto unit : units(program["callee_saved"]))
      options.callee_saved.insert(unit);
    Assembler asm_(fake, streamer, std::move(options));

    std::vector<const VirtReg*> virtuals;
    auto reg = [&](const std::string& name) -> Reg {
      if (name.starts_with("v"))
        return Reg(virtuals.at(std::stoul(name.substr(1))));
      return Reg(fake.info.reg_named(name));
    };
    auto operand = [&](Element given) -> MCOperand {
      if (Element r; given["reg"].get(r) == ::simdjson::SUCCESS) {
        MCReg made{ reg(text_of(r)) };
        if (Element b; given["bits"].get(b) == ::simdjson::SUCCESS && ! b.is_null())
          made.bits = int(int_of(b));
        return made;
      }
      if (Element v; given["imm"].get(v) == ::simdjson::SUCCESS) {
        MCImm made{ int_of(v) };
        if (Element b; given["bits"].get(b) == ::simdjson::SUCCESS)
          made.bits = int(int_of(b));
        if (Element s; given["signed"].get(s) == ::simdjson::SUCCESS)
          made.is_signed = bool_of(s);
        return made;
      }
      if (Element s; given["sym"].get(s) == ::simdjson::SUCCESS)
        return asm_.symref(text_of(s));
      Element mem = given["mem"];
      MCMem made;
      if (Element b; mem["base"].get(b) == ::simdjson::SUCCESS && ! b.is_null())
        made.base = reg(text_of(b));
      if (Element i; mem["index"].get(i) == ::simdjson::SUCCESS && ! i.is_null())
        made.index = reg(text_of(i));
      if (Element s; mem["scale"].get(s) == ::simdjson::SUCCESS)
        made.scale = int(int_of(s));
      if (Element d; mem["disp"].get(d) == ::simdjson::SUCCESS)
        made.disp = int_of(d);
      if (Element z; mem["size_bits"].get(z) == ::simdjson::SUCCESS && ! z.is_null())
        made.size_bits = int(int_of(z));
      return made;
    };
    auto regs = [&](Element list) {
      std::vector<Reg> found;
      for (Element one : list.get_array())
        found.push_back(reg(text_of(one)));
      return found;
    };

    std::map<std::string, MCSymbol*> labels;
    for (Element step : program["steps"].get_array()) {
      auto s = step.get_array();
      auto kind = text_of(s.at(0));
      if (kind == "section") {
        SectionOptions how;
        how.executable = bool_of(s.at(2));
        how.writable = bool_of(s.at(3));
        how.alignment = std::uint64_t(int_of(s.at(4)));
        if (s.size() > 5) {
          Element extra = s.at(5);
          if (Element v; extra["alloc"].get(v) == ::simdjson::SUCCESS)
            how.alloc = bool_of(v);
          if (Element v; extra["sh_type"].get(v) == ::simdjson::SUCCESS)
            how.sh_type = std::uint32_t(int_of(v));
          if (Element v; extra["sh_link_to"].get(v) == ::simdjson::SUCCESS)
            how.sh_link_to = text_of(v);
          if (Element v; extra["sh_entsize"].get(v) == ::simdjson::SUCCESS)
            how.sh_entsize = std::uint64_t(int_of(v));
        }
        asm_.section(text_of(s.at(1)), how);
      } else if (kind == "virtual") {
        const PhysReg* hint = s.at(3).is_null() ? nullptr : fake.info.reg_named(text_of(s.at(3)));
        auto cls = text_of(s.at(1)) == "gpr" ? fake.gpr : fake.fpr;
        virtuals.push_back(fake.info.new_virtual(cls, int(int_of(s.at(2))), hint, bool_of(s.at(4))));
      } else if (kind == "begin") {
        std::optional<AllocationOrder> override;
        if (! s.at(4).is_null())
          override = AllocationOrder::single(units(s.at(4)));
        asm_.begin_function(text_of(s.at(1)), bool_of(s.at(2)), int_of(s.at(3)), std::move(override));
      } else if (kind == "block")
        asm_.block(text_of(s.at(1)));
      else if (kind == "move")
        asm_.loadreg(reg(text_of(s.at(1))), operand(s.at(2)));
      else if (kind == "op") {
        static const std::map<std::string, const Op*> found{
          { "plus", ops::plus }, { "minus", ops::minus }, { "times", ops::times }, { "and", ops::and_ },
          { "or", ops::or_ },    { "xor", ops::xor_ },    { "compare", ops::compare },
        };
        std::vector<MCOperand> sources;
        for (Element one : s.at(3).get_array())
          sources.push_back(operand(one));
        asm_.op(found.at(text_of(s.at(1))), s.at(2).is_null() ? Reg() : reg(text_of(s.at(2))), std::move(sources));
      } else if (kind == "store")
        asm_.store(std::get<MCMem>(operand(s.at(1))), operand(s.at(2)));
      else if (kind == "branch") {
        Condition cond = Condition::eq;
        for (int c = 0; c <= int(Condition::uge); ++c)
          if (name_of(Condition(c)) == text_of(s.at(1)))
            cond = Condition(c);
        asm_.branch(cond, operand(s.at(2)), operand(s.at(3)), text_of(s.at(4)));
      } else if (kind == "jump")
        asm_.jump(text_of(s.at(1)));
      else if (kind == "falls")
        asm_.falls_through(text_of(s.at(1)));
      else if (kind == "call")
        asm_.call(text_of(s.at(1)), pl4g::invalid_span, regs(s.at(2)), regs(s.at(3)));
      else if (kind == "address")
        asm_.address(reg(text_of(s.at(1))), text_of(s.at(2)));
      else if (kind == "ret")
        asm_.ret(pl4g::invalid_span, regs(s.at(1)));
      else if (kind == "end")
        asm_.end_function();
      else if (kind == "label") {
        auto binding = text_of(s.at(2)) == "global" ? SymBinding::global
                       : text_of(s.at(2)) == "weak" ? SymBinding::weak
                                                     : SymBinding::local;
        auto what = text_of(s.at(3)) == "object" ? SymKind::object
                    : text_of(s.at(3)) == "func" ? SymKind::func
                                                  : SymKind::notype;
        labels[text_of(s.at(1))] = asm_.label(text_of(s.at(1)), binding, what);
      } else if (kind == "end_label")
        asm_.end_label(labels.at(text_of(s.at(1))));
      else if (kind == "align")
        asm_.align(std::uint64_t(int_of(s.at(1))));
      else if (kind == "bytes") {
        auto hex = text_of(s.at(1));
        Bytes data;
        for (std::size_t i = 0; i + 1 < hex.size(); i += 2)
          data.push_back(std::uint8_t(std::stoul(hex.substr(i, 2), nullptr, 16)));
        std::vector<MCFixup> fixups;
        for (Element one : s.at(2).get_array()) {
          auto f = one.get_array();
          fixups.push_back(MCFixup{ int_of(f.at(0)), text_of(f.at(1)) == "abs64" ? &abs64 : &pcrel32,
                                    sym_expr(streamer.symbol(text_of(f.at(2)))) });
        }
        asm_.bytes(std::move(data), std::move(fixups));
      }
    }

    auto sections = streamer.section_list();
    auto symbols = streamer.symbol_list();
    std::string elf;
    try {
      pl4g::elf::ImageSettings settings;
      settings.machine = 62;
      settings.base_vaddr = 0x400000;
      settings.page_size = 0x1000;
      settings.entry_symbol = "_start";
      settings.stack_size = 0x100000;
      auto [image, _] = pl4g::elf::write_image(settings, sections, symbols, { "t.pl4g" });
      for (auto byte : image)
        elf += std::format("{:02x}", byte);
    } catch (const pl4g::elf::ImageError& exc) {
      elf = std::string("error ImageError: ") + exc.what();
    }
    std::string out = dump_sections(sections, symbols) + "\n";
    for (std::size_t index = 0; index < asm_.assignments.size(); ++index) {
      const auto& a = asm_.assignments[index];
      out += std::format("assignment {} coalesced={} forwarded={} spilled=[", index, a.coalesced, a.forwarded);
      bool first = true;
      for (const auto& [ident, slot] : a.spilled) {
        out += std::format("{}{}:{}", first ? "" : ",", ident, slot);
        first = false;
      }
      out += "] units=[";
      first = true;
      for (const auto& [ident, unit] : a.units) {
        out += std::format("{}{}:{}", first ? "" : ",", ident, unit->canonical);
        first = false;
      }
      out += "]\n";
    }
    for (auto function : asm_.functions)
      out += std::format("function {} frame={} return_at={} caller_at={}\n", function->name, function->frame.size(),
                         function->return_at ? std::to_string(*function->return_at) : std::string("None"),
                         function->caller_at);
    out += "elf " + elf + "\n";
    return out;
  }

} // anonymous namespace


namespace {

  ::error_t parse_option(int key, char* arg, ::argp_state* state)
  {
    auto& file = *static_cast<std::string*>(state->input);
    switch (key) {
    case ARGP_KEY_ARG:
      if (! file.empty()) [[unlikely]]
        ::argp_usage(state);
      file = arg;
      return 0;
    case ARGP_KEY_END:
      if (file.empty()) [[unlikely]]
        ::argp_usage(state);
      return 0;
    default:
      return ARGP_ERR_UNKNOWN;
    }
  }

  constexpr ::argp parser_spec = { nullptr, parse_option, "PROGRAM.json",
                                   "Run a program of assembler-builder calls with a made-up target.", nullptr,
                                   nullptr, nullptr };

} // anonymous namespace


int main(int argc, char* argv[])
{
  std::string file;
  ::argp_parse(&parser_spec, argc, argv, 0, nullptr, &file);
  ::simdjson::dom::parser parser;
  ::simdjson::padded_string text;
  if (::simdjson::padded_string::load(file).get(text) != ::simdjson::SUCCESS) [[unlikely]] {
    std::print(stderr, "cannot read {}\n", file);
    return 2;
  }
  Element program;
  if (parser.parse(text).get(program) != ::simdjson::SUCCESS) [[unlikely]] {
    std::print(stderr, "{} is not JSON\n", file);
    return 2;
  }
  std::string out;
  try {
    out = run(program);
  } catch (const std::exception& exc) {
    out = std::string("error ") + exc.what() + "\n";
  }
  std::print("{}", out);
  return 0;
}
