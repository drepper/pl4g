#include "ir/json.hh"

#include <charconv>
#include <limits>
#include <unordered_map>

#include <simdjson.h>


namespace pl4g::ir {

  namespace {

    using Object = ::simdjson::dom::object;
    using Element = ::simdjson::dom::element;
    using Array = ::simdjson::dom::array;

    // What went wrong, thrown to the top of the reader.
    struct Malformed {
      std::string what;
    };

    [[noreturn]] void malformed(std::string what) { throw Malformed{ std::move(what) }; }

    std::string_view string_of(Element e)
    {
      std::string_view s;
      if (e.get(s) != ::simdjson::SUCCESS) [[unlikely]]
        malformed("expected a string");
      return s;
    }

    std::int64_t int_of(Element e)
    {
      std::int64_t v;
      if (e.get(v) == ::simdjson::SUCCESS)
        return v;
      std::uint64_t u;
      if (e.get(u) == ::simdjson::SUCCESS)
        return std::int64_t(u);
      malformed("expected an integer");
    }

    bool bool_of(Element e)
    {
      bool v;
      if (e.get(v) != ::simdjson::SUCCESS) [[unlikely]]
        malformed("expected a truth value");
      return v;
    }

    bool is_null(Element e) { return e.is_null(); }

    // A big integer written as a decimal string.
    __int128 big_of(Element e)
    {
      auto s = string_of(e);
      bool negative = ! s.empty() && s[0] == '-';
      if (negative)
        s.remove_prefix(1);
      __int128 v = 0;
      for (char c : s)
        v = v * 10 + (c - '0');
      return negative ? -v : v;
    }


    // What `table` holds under `name`, which it must.
    template<typename T>
    T* named(const std::unordered_map<std::string, T*>& table, std::string_view name, std::string_view what)
    {
      auto it = table.find(std::string(name));
      if (it == table.end()) [[unlikely]]
        malformed("no " + std::string(what) + " called '" + std::string(name) + "'");
      return it->second;
    }


    // What the record the entry point lays out is called, which an address
    // may name without the module holding it.
    constexpr std::string_view startup_record_name = "__pl4g_init";


    class Reader {
    public:
      explicit Reader(SourceManager* sources) : sources(sources) {}
      std::unique_ptr<Module> read(Object doc);
      // Which type each number stands for, once read.
      const std::vector<const Type*>& types_by_number() const noexcept { return made; }

    private:
      const Type* type(Element id);
      const Type* type_at(std::size_t id);
      void nominal_shells();
      void fill_nominal();
      const Unit* unit(Element u);
      const Value* constant(Element c);
      Value* operand(Element o, const std::vector<Value*>& numbered);
      GlobalVar* global(std::string_view name);
      void fill(Function& func, Object f);

      Module* module = nullptr;
      Array types;
      std::vector<const Type*> made;
      std::vector<bool> making;
      std::unordered_map<std::string, GlobalVar*> globals;
      std::unordered_map<std::string, Function*> functions;
      // The constants made of their own, by the number the file gives each.
      std::unordered_map<std::int64_t, const Value*> fresh_ones;

      // The files pypl4g read, added to `sources`, and a span of pypl4g's as
      // one of theirs.
      void files(Object doc);
      Span place(Array span) const;
      SourceManager* sources;
      struct Read {
        Loc theirs = 0;
        std::size_t characters = 0;
        Loc ours = 0;
        // The byte each character begins at.
        std::vector<std::uint32_t> at;
      };
      std::vector<Read> read_files;
    };


    void Reader::files(Object doc)
    {
      Array all;
      if (sources == nullptr || doc["files"].get(all) != ::simdjson::SUCCESS)
        return;
      Loc theirs = 0;
      for (Element one : all) {
        std::string_view path = string_of(one["path"]);
        std::string_view text = string_of(one["text"]);
        const SourceFile& added = sources->add(std::string(path), std::string(text));
        Read made{ theirs, 0, added.base, {} };
        for (std::size_t at = 0; at < text.size(); ++at)
          if ((static_cast<unsigned char>(text[at]) & 0xc0) != 0x80)
            made.at.push_back(std::uint32_t(at));
        made.at.push_back(std::uint32_t(text.size()));
        made.characters = made.at.size() - 1;
        // One past the end, as both managers leave between two files.
        theirs += Loc(made.characters) + 1;
        read_files.push_back(std::move(made));
      }
    }


    Span Reader::place(Array span) const
    {
      Span given{ int_of(span.at(0)), int_of(span.at(1)) };
      if (read_files.empty() || ! given.is_valid())
        return given;
      auto convert = [&](Loc loc) -> Loc {
        for (const auto& one : read_files)
          if (loc >= one.theirs && loc <= one.theirs + Loc(one.characters))
            return one.ours + Loc(one.at[std::size_t(loc - one.theirs)]);
        return loc;
      };
      return { convert(given.start), convert(given.end) };
    }


    const Unit* Reader::unit(Element u)
    {
      std::vector<std::pair<std::string, std::int64_t>> powers;
      for (Element p : u["powers"].get_array())
        powers.emplace_back(std::string(string_of(p.at(0))), int_of(p.at(1)));
      Array scale = u["scale"].get_array();
      return Unit::make(std::move(powers), Rational::of(int_of(scale.at(0)), int_of(scale.at(1))));
    }


    GlobalVar* Reader::global(std::string_view name)
    {
      if (name == startup_record_name && ! globals.contains(std::string(name))) {
        module->startup_record = std::make_unique<GlobalVar>(std::string(name), u8(), ptr_type(u8(), true));
        globals[std::string(name)] = module->startup_record.get();
      }
      return named(globals, name, "global");
    }


    const Type* Reader::type(Element id)
    {
      if (is_null(id))
        return nullptr;
      return type_at(std::size_t(int_of(id)));
    }


    void Reader::nominal_shells()
    {
      // A nominal type exists before what it holds is known, so that a cycle
      // -- a record reaching itself through a reference -- finds it.
      for (std::size_t id = 0; id < made.size(); ++id) {
        Object t = types.at(id).get_object();
        auto k = string_of(t["k"]);
        if (k == "product") {
          if (string_of(t["name"]) == "arena" && string_of(t["origin"]).empty())
            // The record the language provides is one type wherever it is read.
            made[id] = arena_type();
          else
            made[id] = new_product();
        } else if (k == "sum")
          made[id] = new_sum();
        else if (k == "frame") {
          auto f = new_frame(std::string(string_of(t["name"])));
          if (! is_null(t["size"]))
            module->frame_sizes[f] = std::uint64_t(int_of(t["size"]));
          made[id] = f;
        } else if (k == "opaque")
          made[id] = new_opaque(std::string(string_of(t["name"])));
      }
    }


    void Reader::fill_nominal()
    {
      for (std::size_t id = 0; id < made.size(); ++id) {
        Object t = types.at(id).get_object();
        auto k = string_of(t["k"]);
        if (k == "product" && made[id] != arena_type()) {
          auto p = const_cast<ProductType*>(static_cast<const ProductType*>(made[id]));
          p->name = string_of(t["name"]);
          p->origin = string_of(t["origin"]);
          p->abi = bool_of(t["abi"]);
          p->unique = bool_of(t["unique"]);
          p->device = bool_of(t["device"]);
          p->closed_by = string_of(t["closed_by"]);
          for (Element m : t["mutable_fields"].get_array())
            p->mutable_fields.emplace_back(string_of(m));
          for (Element f : t["fields"].get_array())
            p->fields.emplace_back(std::string(string_of(f.at(0))), type(f.at(1)));
          for (Element a : t["args"].get_array())
            p->args.push_back(type(a));
        } else if (k == "sum") {
          auto s = const_cast<SumType*>(static_cast<const SumType*>(made[id]));
          s->name = string_of(t["name"]);
          s->origin = string_of(t["origin"]);
          for (Element v : t["variants"].get_array())
            s->variants.emplace_back(std::string(string_of(v.at(0))), type(v.at(1)));
          for (Element a : t["args"].get_array())
            s->args.push_back(type(a));
        }
      }
    }


    const Type* Reader::type_at(std::size_t id)
    {
      if (id >= made.size()) [[unlikely]]
        malformed("a type number out of range");
      if (made[id] != nullptr)
        return made[id];
      Object t = types.at(id).get_object();
      auto k = string_of(t["k"]);
      // A nominal type exists before what it holds is known; everything else
      // is made of types already made, and a cycle runs through a nominal one.
      if (making[id]) [[unlikely]]
        malformed("a structural type reaching itself");
      making[id] = true;
      const Type* found = nullptr;
      if (k == "void")
        found = void_type();
      else if (k == "bool")
        found = bool_type();
      else if (k == "char")
        found = char_type();
      else if (k == "syntax")
        found = syntax_type();
      else if (k == "mem")
        found = mem_type();
      else if (k == "error")
        found = error_type();
      else if (k == "str")
        found = str_type();
      else if (k == "int")
        found = int_type(unsigned(int_of(t["bits"])), bool_of(t["signed"]), unit(t["unit"]));
      else if (k == "float")
        found = float_type(unsigned(int_of(t["bits"])), unit(t["unit"]));
      else if (k == "ptr")
        found = ptr_type(type(t["to"]), bool_of(t["mut"]), bool_of(t["lasting"]));
      else if (k == "running")
        found = running_type(type(t["element"]), type(t["given"]), bool_of(t["lasting"]));
      else if (k == "iter")
        found = iter_type(type(t["element"]));
      else if (k == "func") {
        std::vector<const Type*> params;
        for (Element p : t["params"].get_array())
          params.push_back(type(p));
        FuncType shape;
        shape.listable = bool_of(t["listable"]);
        shape.hands_out = bool_of(t["hands_out"]);
        shape.lasting = bool_of(t["lasting"]);
        shape.impure = bool_of(t["impure"]);
        shape.can_ignore = bool_of(t["can_ignore"]);
        for (Element b : t["borrows"].get_array()) {
          Borrowed borrowed;
          Object o = b.at(1).get_object();
          for (Element p : o["params"].get_array())
            borrowed.params.push_back(int(int_of(p)));
          for (Element l : o["leaves"].get_array()) {
            if (is_null(l))
              borrowed.leaves.emplace_back(std::nullopt);
            else {
              std::vector<int> leaves;
              for (Element one : l.get_array())
                leaves.push_back(int(int_of(one)));
              borrowed.leaves.emplace_back(std::move(leaves));
            }
          }
          for (Element o2 : o["outer"].get_array())
            borrowed.outer.emplace_back(std::string(string_of(o2.at(0))), std::string(string_of(o2.at(1))));
          shape.borrows.emplace_back(int(int_of(b.at(0))), std::move(borrowed));
        }
        found = func_type(std::move(params), type(t["ret"]), shape);
      } else if (k == "result")
        found = result_type(type(t["ok"]), type(t["err"]));
      else if (k == "tuple") {
        std::vector<const Type*> members;
        for (Element m : t["members"].get_array())
          members.push_back(type(m));
        found = tuple_type(std::move(members));
      } else if (k == "array") {
        std::vector<std::optional<std::uint64_t>> shape;
        for (Element d : t["shape"].get_array())
          shape.push_back(is_null(d) ? std::nullopt : std::optional(std::uint64_t(int_of(d))));
        found = array_type(type(t["element"]), std::move(shape));
      } else if (k == "vec")
        found = vec_type(type(t["element"]), unsigned(int_of(t["lanes"])));
      else if (k == "list")
        found = list_type(type(t["element"]));
      else if (k == "cursor")
        found = cursor_type(type(t["element"]));
      else if (k == "set")
        found = set_type(type(t["element"]), bool_of(t["mut"]));
      else if (k == "dict")
        found = dict_type(type(t["key"]), type(t["value"]), bool_of(t["mut"]));
      else if (k == "enum") {
        std::vector<std::string> members;
        for (Element m : t["members"].get_array())
          members.emplace_back(string_of(m));
        std::vector<std::int64_t> values;
        for (Element v : t["values"].get_array())
          values.push_back(int_of(v));
        found = enum_type(std::move(members), std::move(values), as<IntType>(type(t["holder"])), bool_of(t["flag"]),
                          std::string(string_of(t["name"])), std::string(string_of(t["origin"])));
      } else [[unlikely]]
        malformed("an unknown kind of type: " + std::string(k));
      // Where pypl4g made it: its context's one of the structure, the cache of
      // pointers `parts_of` answers with, or neither -- equal objects that an
      // `is` tells apart, as it does here.
      std::string_view where;
      if (t["made"].get(where) == ::simdjson::SUCCESS) {
        if (where == "element")
          found = element_pointer(static_cast<const PtrType*>(found)->pointee);
        else if (where == "stray")
          found = stray_copy(found);
      }
      return made[id] = found;
    }


    const Value* Reader::constant(Element e)
    {
      if (is_null(e))
        return nullptr;
      Object c = e.get_object();
      auto kind = string_of(c["c"]);
      const Type* ty = type(c["t"]);
      // A constant pypl4g made of its own rather than asked the module for --
      // every undef is one -- is a value of its own, told apart from an equal
      // one by identity as pypl4g tells them; the file numbers each.
      std::int64_t fresh = -1;
      if (c["fresh"].get(fresh) == ::simdjson::SUCCESS) {
        if (auto it = fresh_ones.find(fresh); it != fresh_ones.end())
          return it->second;
        const Value* made = nullptr;
        if (kind == "int")
          made = module->constant<IntConst>(ty, big_of(c["v"]));
        else if (kind == "char")
          made = module->constant<CharConst>(ty, std::uint32_t(int_of(c["v"])));
        else if (kind == "float") {
          auto bits = string_of(c["bits"]);
          std::uint64_t raw = 0;
          std::from_chars(bits.data(), bits.data() + bits.size(), raw, 16);
          made = module->constant<FloatConst>(ty, std::bit_cast<double>(raw));
        } else if (kind == "bool")
          made = module->constant<BoolConst>(ty, bool_of(c["v"]));
        else if (kind == "enum")
          made = module->constant<EnumConst>(ty, std::size_t(int_of(c["index"])));
        else if (kind == "undef")
          made = module->constant<UndefConst>(ty);
        if (made != nullptr)
          return fresh_ones[fresh] = made;
      }
      if (kind == "int")
        return module->int_const(ty, big_of(c["v"]));
      if (kind == "char")
        return module->char_const(std::uint32_t(int_of(c["v"])));
      if (kind == "float") {
        auto bits = string_of(c["bits"]);
        std::uint64_t raw = 0;
        std::from_chars(bits.data(), bits.data() + bits.size(), raw, 16);
        return module->float_const(ty, std::bit_cast<double>(raw));
      }
      if (kind == "bool")
        return module->bool_const(bool_of(c["v"]));
      if (kind == "enum")
        return module->enum_const(ty, std::size_t(int_of(c["index"])));
      if (kind == "array" || kind == "record" || kind == "parts") {
        std::vector<const Value*> items;
        for (Element one : c[kind == "array" ? "elements" : kind == "record" ? "fields" : "parts"].get_array())
          items.push_back(constant(one));
        if (kind == "array")
          return module->constant<ArrayConst>(ty, std::move(items));
        if (kind == "record")
          return module->constant<RecordConst>(ty, std::move(items));
        return module->constant<PartsConst>(ty, std::move(items));
      }
      if (kind == "result")
        return module->constant<ResultConst>(ty, constant(c["answer"]), bool_of(c["failed"]));
      if (kind == "sum")
        return module->constant<SumConst>(ty, std::size_t(int_of(c["index"])), constant(c["part"]));
      if (kind == "address") {
        const Value* target = nullptr;
        if (! is_null(c["target"])) {
          Object t = c["target"].get_object();
          std::string_view name;
          if (t["g"].get(name) == ::simdjson::SUCCESS)
            target = global(name);
          else
            target = named(functions, string_of(t["f"]), "function");
        }
        auto made = module->constant<AddressConst>(ty, target, int_of(c["offset"]));
        bool ring = false;
        if (c["ring"].get(ring) == ::simdjson::SUCCESS && ring)
          module->ring_addresses.push_back(made);
        return made;
      }
      if (kind == "undef")
        return module->undef(ty);
      malformed("an unknown kind of constant: " + std::string(kind));
    }


    Value* Reader::operand(Element e, const std::vector<Value*>& numbered)
    {
      Object o = e.get_object();
      std::string_view constant_kind;
      if (o["c"].get(constant_kind) == ::simdjson::SUCCESS)
        // A constant: the IR does not change one, so the cast only lets it
        // stand where any value may.
        return const_cast<Value*>(constant(e));
      std::int64_t number;
      if (o["v"].get(number) == ::simdjson::SUCCESS) {
        if (number < 0 || std::size_t(number) >= numbered.size() || numbered[std::size_t(number)] == nullptr)
          [[unlikely]]
          malformed("a value number nothing has");
        return numbered[std::size_t(number)];
      }
      std::string_view name;
      if (o["g"].get(name) == ::simdjson::SUCCESS)
        return global(name);
      if (o["f"].get(name) == ::simdjson::SUCCESS)
        return named(functions, name, "function");
      malformed("an operand that is none of the kinds there are");
    }


    InstKind inst_kind(std::string_view op)
    {
      template for (constexpr auto m : std::define_static_array(std::meta::enumerators_of(^^InstKind)))
        if (op == std::meta::identifier_of(m))
          return [:m:];
      malformed("an unknown instruction: " + std::string(op));
    }

    template<typename E>
    E by_description(std::string_view text)
    {
      template for (constexpr auto m : std::define_static_array(std::meta::enumerators_of(^^E)))
        if (text == describe([:m:]))
          return [:m:];
      malformed("an unknown operation: " + std::string(text));
    }


    void Reader::fill(Function& func, Object f)
    {
      // Every block and every value first, then what each instruction uses:
      // a value may be used above where it is defined, in a later block.
      std::vector<Value*> numbered;
      auto number = [&](std::int64_t id, Value* v) {
        if (std::size_t(id) >= numbered.size())
          numbered.resize(std::size_t(id) + 1);
        numbered[std::size_t(id)] = v;
      };
      std::vector<std::pair<Instruction*, Object>> pending;
      for (Element b : f["blocks"].get_array()) {
        Object block = b.get_object();
        auto made_block = func.add_block(std::string(string_of(block["label"])));
        for (Element p : block["params"].get_array()) {
          auto param = made_block->add_param(type(p["t"]));
          std::string_view name;
          if (p["name"].get(name) == ::simdjson::SUCCESS)
            param->name_hint = std::string(name);
          number(int_of(p["id"]), param);
        }
        for (Element i : block["insts"].get_array()) {
          Object inst = i.get_object();
          auto made_inst = func.make(inst_kind(string_of(inst["op"])), type(inst["t"]));
          made_inst->parent = made_block;
          made_block->insts.push_back(made_inst);
          std::int64_t id;
          if (inst["id"].get(id) == ::simdjson::SUCCESS)
            number(id, made_inst);
          pending.emplace_back(made_inst, inst);
        }
      }
      for (auto [inst, o] : pending) {
        for (Element one : o["operands"].get_array())
          inst->operands.push_back(operand(one, numbered));
        std::string_view text;
        if (o["name"].get(text) == ::simdjson::SUCCESS)
          inst->name_hint = std::string(text);
        Array span;
        if (o["span"].get(span) == ::simdjson::SUCCESS)
          inst->span = place(span);
        if (o["kind"].get(text) == ::simdjson::SUCCESS) {
          switch (inst->kind) {
          case InstKind::BinaryInst: inst->bin = by_description<BinOp>(text); break;
          case InstKind::UnaryInst: inst->un = by_description<UnOp>(text); break;
          case InstKind::CmpInst: inst->pred = by_description<CmpPred>(text); break;
          case InstKind::CastInst: inst->cast = by_description<CastKind>(text); break;
          default: break;
          }
        }
        std::int64_t index;
        if (o["index"].get(index) == ::simdjson::SUCCESS)
          inst->index = index;
        Element callee;
        if (o["callee"].get(callee) == ::simdjson::SUCCESS && ! is_null(callee)) {
          auto it = functions.find(std::string(string_of(callee)));
          if (it == functions.end()) [[unlikely]]
            malformed("a call of a function the module does not have: " + std::string(string_of(callee)));
          inst->callee = it->second;
        }
        Element held;
        if (o["held"].get(held) == ::simdjson::SUCCESS)
          inst->held = type(held);
        if (o["what"].get(text) == ::simdjson::SUCCESS) {
          inst->what = text;
          inst->status = int(int_of(o["status"]));
          inst->observing = bool_of(o["observing"]);
        }
        if (o["ordering"].get(text) == ::simdjson::SUCCESS)
          inst->ordering = text == "acquire" ? Ordering::acquire : text == "release" ? Ordering::release : Ordering::plain;
        Array targets;
        if (o["targets"].get(targets) == ::simdjson::SUCCESS)
          for (Element t : targets) {
            BlockTarget target;
            target.block = func.blocks.at(std::size_t(int_of(t["block"]))).get();
            for (Element a : t["args"].get_array())
              target.args.push_back(operand(a, numbered));
            inst->targets.push_back(std::move(target));
          }
        Array cases;
        if (o["cases"].get(cases) == ::simdjson::SUCCESS)
          for (Element c : cases)
            inst->cases.push_back(big_of(c));
        bool delegates;
        if (o["delegates"].get(delegates) == ::simdjson::SUCCESS)
          inst->delegates = delegates;
      }
    }


    std::optional<SpecialKind> special_of(Element e)
    {
      if (is_null(e))
        return std::nullopt;
      auto text = string_of(e);
      template for (constexpr auto m : std::define_static_array(std::meta::enumerators_of(^^SpecialKind)))
        if (text == std::meta::identifier_of(m))
          return [:m:];
      malformed("an unknown special function: " + std::string(text));
    }

    Linkage linkage_of(Element e)
    {
      auto text = string_of(e);
      return text == "visible" ? Linkage::visible : text == "imported" ? Linkage::imported : Linkage::internal;
    }

    std::optional<std::string> maybe_string(Element e)
    {
      if (is_null(e))
        return std::nullopt;
      return std::string(string_of(e));
    }


    std::unique_ptr<Module> Reader::read(Object doc)
    {
      auto out = std::make_unique<Module>();
      module = out.get();
      files(doc);
      out->name = string_of(doc["name"]);
      out->triple = string_of(doc["triple"]);
      out->stack_size = int_of(doc["stack_size"]);
      out->guard_size = int_of(doc["guard_size"]);
      types = doc["types"].get_array();
      made.assign(types.size(), nullptr);
      making.assign(types.size(), false);
      nominal_shells();

      // The globals and the functions, so that every reference finds them.
      Array keys = doc["global_keys"].get_array();
      std::size_t at = 0;
      for (Element g : doc["globals"].get_array()) {
        auto var = std::make_unique<GlobalVar>(std::string(string_of(g["name"])), type(g["t"]), type(g["pt"]));
        var->linkage = linkage_of(g["linkage"]);
        var->module = string_of(g["module"]);
        var->exported = bool_of(g["exported"]);
        var->system_layout = bool_of(g["system_layout"]);
        globals[var->name] = var.get();
        out->add_global(std::move(var), std::string(string_of(keys.at(at++))));
      }
      // The texts are globals the module also keeps by what they say; one
      // already among the globals is that one.
      std::vector<bool> fresh_text;
      for (Element g : doc["texts"].get_array()) {
        std::string name(string_of(g["name"]));
        if (auto it = globals.find(name); it != globals.end()) {
          out->texts.push_back(it->second);
          fresh_text.push_back(false);
          continue;
        }
        auto var = std::make_unique<GlobalVar>(name, type(g["t"]), type(g["pt"]));
        var->linkage = linkage_of(g["linkage"]);
        var->module = string_of(g["module"]);
        globals[var->name] = var.get();
        out->texts.push_back(var.get());
        out->text_store.push_back(std::move(var));
        fresh_text.push_back(true);
      }
      keys = doc["function_keys"].get_array();
      std::int64_t listed = std::numeric_limits<std::int64_t>::max();
      // Older files say nothing of it, and then every function is listed.
      if (doc["listed"].get(listed) != ::simdjson::SUCCESS)
        listed = std::numeric_limits<std::int64_t>::max();
      at = 0;
      std::vector<Function*> all;
      for (Element f : doc["functions"].get_array()) {
        auto func = std::make_unique<Function>(std::string(string_of(f["name"])), as<FuncType>(type(f["t"])));
        Object attrs = f["attrs"].get_object();
        func->attrs.special = special_of(attrs["special"]);
        if (! is_null(attrs["priority"]))
          func->attrs.priority = int_of(attrs["priority"]);
        auto inline_hint = string_of(attrs["inline"]);
        func->attrs.inline_ = inline_hint == "always" ? InlineHint::always
                              : inline_hint == "never" ? InlineHint::never : InlineHint::default_;
        func->attrs.abi = maybe_string(attrs["abi"]);
        func->attrs.external = maybe_string(attrs["external"]);
        func->attrs.host = maybe_string(attrs["host"]);
        func->attrs.builtin = bool_of(attrs["builtin"]);
        func->attrs.can_ignore = bool_of(attrs["can_ignore"]);
        func->attrs.impure = bool_of(attrs["impure"]);
        func->attrs.io = bool_of(attrs["io"]);
        func->attrs.opaque = bool_of(attrs["opaque"]);
        func->attrs.answer_in_storage = bool_of(attrs["answer_in_storage"]);
        func->attrs.chained = bool_of(attrs["chained"]);
        func->attrs.listable = bool_of(attrs["listable"]);
        func->linkage = linkage_of(f["linkage"]);
        func->cconv = string_of(f["cconv"]);
        auto answering = maybe_string(f["answering"]);
        if (answering)
          func->answering = *answering == "two-in-registers" ? ReturnStyle::two_in_registers
                                                             : ReturnStyle::three_in_registers;
        func->source_path = string_of(f["source_path"]);
        func->module = string_of(f["module"]);
        func->exported = bool_of(f["exported"]);
        for (Element n : f["param_names"].get_array())
          func->param_names.emplace_back(string_of(n));
        Array span;
        if (f["span"].get(span) == ::simdjson::SUCCESS)
          func->span = place(span);
        if (f["name_span"].get(span) == ::simdjson::SUCCESS)
          func->name_span = place(span);
        Array from;
        if (f["answer_from"].get(from) == ::simdjson::SUCCESS)
          func->answer_from = std::pair(std::string(string_of(from.at(0))), int(int_of(from.at(1))));
        Element frame;
        if (f["coroutine"].get(frame) == ::simdjson::SUCCESS && ! is_null(frame))
          func->coroutine = as<CoroutineFrame>(type(frame));
        // Filed, and referred to, under the module's key: a name may be
        // overloaded and a key is not.
        std::string key(string_of(keys.at(at)));
        functions[key] = func.get();
        all.push_back(func.get());
        if (std::int64_t(at++) < listed)
          out->add_function(std::move(func), key);
        else
          out->unlisted.push_back(std::move(func));
      }

      // What the globals start with, and what the functions do.
      at = 0;
      for (Element g : doc["globals"].get_array())
        out->globals[at++].second->initializer = constant(g["init"]);
      at = 0;
      for (Element g : doc["texts"].get_array()) {
        if (fresh_text[at])
          out->texts[at]->initializer = constant(g["init"]);
        ++at;
      }
      at = 0;
      for (Element f : doc["functions"].get_array())
        fill(*all[at++], f.get_object());

      auto function_named = [&](Element e) -> Function* {
        if (is_null(e))
          return nullptr;
        return named(functions, string_of(e), "function");
      };
      out->startup = function_named(doc["startup"]);
      out->build = function_named(doc["build"]);
      if (! is_null(doc["environ"]))
        out->environ = named(globals, string_of(doc["environ"]), "global");
      for (Element f : doc["ctors"].get_array())
        out->ctors.push_back(function_named(f));
      for (Element f : doc["dtors"].get_array())
        out->dtors.push_back(function_named(f));
      // A test the passes dropped is still among the module's tests, there being
      // nothing of it left to run: only the ones still here are kept.
      for (Element f : doc["tests"].get_array())
        if (functions.contains(std::string(string_of(f))))
          out->tests.push_back(function_named(f));
      for (Element f : doc["test_plan"].get_array())
        if (functions.contains(std::string(string_of(f))))
          out->test_plan.push_back(function_named(f));
      for (Element p : doc["source_paths"].get_array())
        out->source_paths.emplace_back(string_of(p));
      // Every type is made, the ones only a type refers to as well: a record a
      // pointer names is laid out like any other.
      for (std::size_t id = 0; id < made.size(); ++id)
        type_at(id);
      fill_nominal();
      return out;
    }

  } // anonymous namespace


  std::expected<std::unique_ptr<Module>, std::string> read_json(std::string_view text,
                                                                std::vector<const Type*>* types,
                                                                SourceManager* sources)
  {
    ::simdjson::dom::parser parser;
    ::simdjson::padded_string padded(text);
    Element doc;
    if (auto error = parser.parse(padded).get(doc)) [[unlikely]]
      return std::unexpected(std::string(::simdjson::error_message(error)));
    try {
      Object root;
      if (doc.get(root) != ::simdjson::SUCCESS) [[unlikely]]
        return std::unexpected(std::string("not an object"));
      Reader reader(sources);
      auto module = reader.read(root);
      if (types != nullptr)
        *types = reader.types_by_number();
      return module;
    } catch (const Malformed& m) {
      return std::unexpected(m.what);
    } catch (const ::simdjson::simdjson_error& e) {
      return std::unexpected(std::string(e.what()));
    } catch (const std::out_of_range&) {
      return std::unexpected(std::string("a name nothing in the module has"));
    }
  }

} // namespace pl4g::ir
