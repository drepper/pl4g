#include "interp/interp.hh"

#include <algorithm>
#include <array>
#include <bit>
#include <cerrno>
#include <cmath>
#include <csignal>
#include <cstring>
#include <deque>
#include <format>
#include <memory>
#include <random>
#include <unordered_map>
#include <unordered_set>

#include <dlfcn.h>
#include <ffi.h>
#include <sys/auxv.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "ir/layout.hh"
#include "diag/engine.hh"
#include "rt/runtime.hh"
#include "sema/auxv.hh"
#include "target/host.hh"
#include "target/statuses.hh"
#include "util/fdio.hh"


namespace pl4g::interp {

  namespace {

    using namespace ir;
    using Cell = std::uint64_t;

    // The statuses the runtime reserves, as pypl4g/target/statuses.py numbers
    // them.
    namespace status {
      constexpr int overflow = 80;
      constexpr int no_answer = 81;
      constexpr int out_of_memory = 85;
      constexpr int stack_overflow = 86;
      constexpr int tests_failed = 88;
    } // namespace status

    // The flag of a run nobody stops, so that asking it needs no test of
    // whether there is one.
    constinit const std::atomic<bool> never_stopped{ false };

    // How the program ended, unwound to the top of the run.
    struct Exit {
      int status;
    };

    // A trap the program would have died of.
    struct Signal {
      int number;
    };


    // -- what a value is ------------------------------------------------------

    // What an operation needs to know about a value of one part.
    struct Scalar {
      enum class Kind : std::uint8_t { integer, boolean, floating, address } kind = Kind::address;
      // The type's own width, and the width it is held at.
      unsigned bits = 64;
      unsigned held = 64;
      bool is_signed = false;

      bool is_float() const noexcept { return kind == Kind::floating; }
    };

    Scalar scalar_of(const Type* ty)
    {
      if (auto vec = as<VecType>(ty))
        return scalar_of(vec->element);
      if (auto i = as<IntType>(ty))
        return { Scalar::Kind::integer, i->bits, i->held(), i->is_signed };
      if (is<BoolType>(ty))
        return { Scalar::Kind::boolean, 1, 8, false };
      if (auto c = as<CharType>(ty))
        return scalar_of(c->holder());
      if (auto e = as<EnumType>(ty))
        return scalar_of(e->holder);
      if (auto f = as<FloatType>(ty))
        return { Scalar::Kind::floating, f->bits, f->bits, true };
      if (auto r = as<ResultType>(ty))
        return scalar_of(r->ok);
      if (auto p = as<ProductType>(ty)) {
        // A record of one value is that value.
        auto inner = parts_of(p);
        if (inner.size() == 1 && inner[0] != p && ! made_of_parts(inner[0]))
          return scalar_of(inner[0]);
      }
      return {};
    }

    // How many bytes a value of one part takes in memory: what the back end's
    // `_width_of` answers, in bytes.
    unsigned memory_bytes(const Type* ty)
    {
      if (auto i = as<IntType>(ty))
        return i->held() / 8;
      if (auto f = as<FloatType>(ty))
        return f->bits / 8;
      if (auto c = as<CharType>(ty))
        return c->holder()->bits / 8;
      if (auto e = as<EnumType>(ty))
        return e->holder->bits / 8;
      if (auto p = as<ProductType>(ty)) {
        auto inner = parts_of(p);
        if (inner.size() == 1 && inner[0] != p && ! made_of_parts(inner[0]))
          return memory_bytes(inner[0]);
      }
      return is<BoolType>(ty) ? 1 : 8;
    }

    // How many cells a value of `ty` takes: one per part, one per lane.
    std::uint32_t cells_of(const Type* ty)
    {
      if (is<VoidType>(ty))
        return 0;
      if (auto vec = as<VecType>(ty))
        return vec->lanes;
      return std::uint32_t(leaves_of(ty).size());
    }

    // How many cells a function's parameters take.
    std::size_t cells_in(const Function* func)
    {
      std::size_t n = 0;
      for (auto p : func->fty->params)
        n += cells_of(p);
      return n;
    }

    // `v`, its low `from` bits extended to the whole cell as `sign` says.
    Cell extend(Cell v, unsigned from, bool sign) noexcept
    {
      if (from >= 64)
        return v;
      Cell mask = (Cell(1) << from) - 1;
      v &= mask;
      if (sign && ((v >> (from - 1)) & 1))
        v |= ~mask;
      return v;
    }

    // The bits of a cell as a floating-point number, and back.
    double as_double(Cell v, unsigned bits) noexcept
    {
      return bits == 32 ? double(std::bit_cast<float>(std::uint32_t(v))) : std::bit_cast<double>(v);
    }

    Cell of_double(double d, unsigned bits) noexcept
    {
      return bits == 32 ? Cell(std::bit_cast<std::uint32_t>(float(d))) : std::bit_cast<Cell>(d);
    }


    // -- prepared code --------------------------------------------------------

    // Where an operand's cells are: the activation's own, or the code's
    // constants where `at` is negative.
    struct In {
      std::int32_t at = 0;
      std::uint32_t count = 0;
    };

    // A branch: where to, and which cells go to which parameter.
    struct Jump {
      std::uint32_t block = 0;
      // Whether it goes to a block at or before the one it leaves, which
      // every loop has one of: where the host's stop is asked.
      bool backward = false;
      std::vector<std::pair<In, std::uint32_t>> moves;
      std::uint32_t moved = 0;
    };

    struct Code;
    using Native = Cell (*)(class Machine&, const Cell* args);

    // How a binary operation is carried out, decided once.
    enum class Arith : std::uint8_t {
      trapping, saturating, wrapping, shift, division, extremum, plain, floating, address,
    };

    struct Op {
      InstKind kind{};
      std::uint8_t sub = 0;
      Arith arith = Arith::plain;
      const Instruction* inst = nullptr;
      std::uint32_t dst = 0;
      std::uint32_t count = 0;
      std::vector<In> in;
      // Of the result; of the operands for a comparison; of what is cast.
      Scalar scalar;
      Scalar from;
      unsigned lanes = 0;
      // Each part's place and width for a load or a store, and how it is read.
      std::vector<std::uint64_t> offsets;
      std::vector<unsigned> widths;
      std::vector<Scalar> parts;
      std::uint64_t value = 0;
      // A call's callee, or a native one.
      const Function* callee = nullptr;
      Native native = nullptr;
      // Whether a call goes to a function at or before its caller in the
      // module, or through a value: every cycle of calls has one of these,
      // as every loop has a jump back, and here the host's stop is asked.
      bool backward = false;
      std::vector<Jump> jumps;
      std::vector<Cell> cases;
      // What a fault here says.
      std::string message;
    };

    struct Code {
      const Function* func = nullptr;
      std::vector<std::vector<Op>> blocks;
      std::uint32_t cells = 0;
      std::vector<std::uint32_t> params;
      std::uint32_t arguments = 0;
      std::vector<Cell> constants;
      std::uint64_t frame_bytes = 0;
    };


    // -- what the host defines ------------------------------------------------

    // How one value crosses to the host and back: what libffi is told it is,
    // how many bytes of its cell C reads, and how what comes back is made a
    // cell again.
    struct Crossing {
      ::ffi_type* type = &::ffi_type_void;
      Scalar scalar;
      unsigned bytes = 0;
    };

    // What a value of `ty` is to C.  The checker lets only what C has cross,
    // and a `str` has been made an address by then, so every one of these is
    // a single cell, held as the x86-64 back end holds it in a register: an
    // integer at its held width, a truth value as a byte, a character as its
    // `u32`, a floating-point number as its bits, a reference as the address
    // of the place it names.
    Crossing crossing_of(const Type* ty)
    {
      if (is<VoidType>(ty))
        return {};
      if (auto c = as<CharType>(ty))
        return crossing_of(c->holder());
      if (auto e = as<EnumType>(ty))
        return crossing_of(e->holder);
      if (auto i = as<IntType>(ty)) {
        Scalar s{ Scalar::Kind::integer, i->bits, i->held(), i->is_signed };
        switch (i->held()) {
        case 8: return { i->is_signed ? &::ffi_type_sint8 : &::ffi_type_uint8, s, 1 };
        case 16: return { i->is_signed ? &::ffi_type_sint16 : &::ffi_type_uint16, s, 2 };
        case 32: return { i->is_signed ? &::ffi_type_sint32 : &::ffi_type_uint32, s, 4 };
        default: return { i->is_signed ? &::ffi_type_sint64 : &::ffi_type_uint64, s, 8 };
        }
      }
      if (is<BoolType>(ty))
        return { &::ffi_type_uint8, { Scalar::Kind::boolean, 1, 8, false }, 1 };
      if (auto f = as<FloatType>(ty)) {
        if (f->bits == 32)
          return { &::ffi_type_float, { Scalar::Kind::floating, 32, 32, true }, 4 };
        if (f->bits == 64)
          return { &::ffi_type_double, { Scalar::Kind::floating, 64, 64, true }, 8 };
      }
      if (is<PtrType>(ty))
        return { &::ffi_type_pointer, {}, 8 };
      throw InternalError(std::format("a value of type {} cannot cross to the host", ty->render()));
    }

    // A function the host defines, as it is called: the call's description,
    // prepared once, and where the function is, once it was found.
    struct HostCall {
      ::ffi_cif cif{};
      std::vector<::ffi_type*> types;
      std::vector<Crossing> params;
      Crossing answer;
      void* address = nullptr;
      // What a function the host handed over with a context is handed first,
      // and whether `cif` is ready: it says how many arguments there are,
      // which the context is one of, so it is made once the function is found.
      void* context = nullptr;
      bool prepared = false;
      // Where the answer is a record laid out for C: the `struct` libffi is
      // told it is, which it answers into a buffer copied into the room the
      // caller made, handed over last -- and the descriptions of the structs
      // inside it, which have to stay where they are.
      ::ffi_type* record = nullptr;
      std::deque<::ffi_type> structs;
      std::deque<std::vector<::ffi_type*>> elements;
    };

    // The `struct` libffi is told a record laid out for C is: its fields in
    // order, a record among them a `struct` of its own and an array a
    // `struct` of its elements, which is how libffi is told of one.
    ::ffi_type* ffi_type_of(const Type* ty, HostCall& host)
    {
      std::vector<::ffi_type*> members;
      if (auto record = as<ProductType>(ty))
        for (const auto& [_, field] : record->fields)
          members.push_back(ffi_type_of(field, host));
      else if (auto tuple = as<TupleType>(ty))
        for (auto member : tuple->members)
          members.push_back(ffi_type_of(member, host));
      else if (auto array = as<ArrayType>(ty); array != nullptr && array->fixed()) {
        auto element = ffi_type_of(array->element, host);
        members.assign(array->count().value_or(0), element);
      } else
        return crossing_of(ty).type;
      members.push_back(nullptr);
      auto& kept = host.elements.emplace_back(std::move(members));
      auto& made = host.structs.emplace_back();
      made.size = 0;
      made.alignment = 0;
      made.type = FFI_TYPE_STRUCT;
      made.elements = kept.data();
      return &made;
    }

    // Where in a cell are the `bytes` bytes a C value of that many bytes
    // takes: its low ones, wherever the machine keeps those.
    std::byte* low_bytes(Cell& cell, unsigned bytes) noexcept
    {
      auto at = reinterpret_cast<std::byte*>(&cell);
      return std::endian::native == std::endian::big ? at + (sizeof(Cell) - bytes) : at;
    }

    // Whether a value of `ty` is one C has, which `crossing_of` describes.
    bool crosses(const Type* ty)
    {
      if (auto c = as<CharType>(ty))
        return crosses(c->holder());
      if (auto f = as<FloatType>(ty))
        return f->bits == 32 || f->bits == 64;
      return is<VoidType>(ty) || is<IntType>(ty) || is<BoolType>(ty) || is<PtrType>(ty);
    }

    // Call the code at `address` as C calls it, with `given`, one cell an
    // argument, its description in `host` prepared.  Each argument is a cell
    // of its own, of which C reads the low bytes its type has; a context goes
    // first where there is one.  What comes back is at least a word: libffi
    // widens a narrow integer to one, as its type says -- or, where it is a
    // record laid out for C, the bytes of the `struct`, written into the room
    // that follows the arguments in `given`.
    Cell crossed_call(HostCall& host, void* address, const Cell* given)
    {
      std::vector<Cell> words(given, given + host.params.size());
      std::vector<void*> values;
      values.reserve(words.size() + 1);
      if (host.context != nullptr)
        values.push_back(&host.context);
      for (std::size_t i = 0; i < words.size(); ++i)
        values.push_back(low_bytes(words[i], host.params[i].bytes));
      if (host.record != nullptr) {
        // What libffi answers, at least a word or two of it whatever the
        // record's size, copied into the room handed over after the
        // arguments: the bytes are C's either way.
        std::vector<std::uint64_t> room(host.record->size / 8 + 2);
        ::ffi_call(&host.cif, FFI_FN(address), room.data(), values.data());
        std::memcpy(reinterpret_cast<void*>(given[host.params.size()]), room.data(), host.record->size);
        return 0;
      }
      union {
        ::ffi_arg word;
        float single;
        double twice;
      } answer{};
      ::ffi_call(&host.cif, FFI_FN(address), &answer, values.data());
      const Crossing& back = host.answer;
      if (back.type == &::ffi_type_void)
        return 0;
      if (back.type == &::ffi_type_float)
        return Cell(std::bit_cast<std::uint32_t>(answer.single));
      if (back.type == &::ffi_type_double)
        return std::bit_cast<Cell>(answer.twice);
      if (back.type == &::ffi_type_pointer)
        return Cell(answer.word);
      // What the register holds at the width the type is held at, read as a
      // load from memory reads it: whatever C left above that is not looked at.
      return extend(Cell(answer.word), back.scalar.held, back.scalar.is_signed);
    }

    class Machine;

    // A function of the program of the system's convention, as code something
    // compiled by anything else can call: a libffi closure, whose handler
    // makes cells of what C handed over and runs the function in the
    // interpreter.  What a value of `@[cdecl] fn(…)` holds where the
    // function's address is taken, so that the number `⎕address` answers is
    // one a host can call.
    struct Closure {
      ::ffi_closure* closure = nullptr;
      void* code = nullptr;
      HostCall call;
      Machine* machine = nullptr;
      const Function* func = nullptr;
    };


    // -- the machine ----------------------------------------------------------

    class Machine {
    public:
      Machine(const Module& module, const Options& options);
      Machine(const Machine&) = delete;
      Machine& operator=(const Machine&) = delete;
      ~Machine()
      {
        for (auto& [_, one] : closures)
          ::ffi_closure_free(one->closure);
        ::munmap(stack, stack_size + 4096);
      }
      Outcome start();
      // Where a variable was placed.
      std::byte* place_of(const GlobalVar* var) const
      {
        auto it = places.find(var);
        return it == places.end() ? nullptr : it->second;
      }

      // What a native callee needs.
      [[noreturn]] void fault(std::string_view message, int status);
      const Options& options;
      // Run `func` with `args`, answering its cells.
      std::vector<Cell> call(const Function* func, std::span<const Cell> args);

    private:
      friend Cell native_alloc(Machine&, const Cell*);

      struct Activation {
        const Code* code;
        std::size_t base;
        std::size_t frame_mark;
        std::uint32_t block = 0;
        std::uint32_t at = 0;
        // Where in the caller the answer goes.
        std::uint32_t answer = 0;
      };

      const Code& code_of(const Function* func);
      void compile(Code& code, const Function& func);
      In operand(Code& code, const Value* value, const std::unordered_map<const Value*, std::uint32_t>& cells);
      void expand(std::vector<Cell>& out, const Value* value);
      Cell address_of(const Value* value);
      std::string describe(std::string_view what, const Function& func, Span span) const
      {
        return describe(what, func.name, span);
      }
      std::string describe(std::string_view what, std::string_view name, Span span) const;
      void place_globals();
      void encode(std::byte* at, const Value* init, const Type* ty, const DataLayout& layout);

      void execute(std::size_t depth);
      void binary(const Op& op, Cell* dst, const Cell* a, const Cell* b);
      Cell binary_int(const Op& op, const Scalar& s, Cell a, Cell b);
      Cell native_call(const Function* callee, const Cell* args);
      Cell host_call(const Function* callee, const Cell* args);
      HostCall& host_call_of(const Function* callee);
      // What a value of a function of the program holds: the address of a
      // closure for one of the system's convention whose types C has, which a
      // host can call, and the function itself otherwise.
      Cell code_value(const Function* func);
      // Call code at an address nothing here made, as C calls it, for the
      // call through a value `call`.
      Cell foreign_call(const Instruction* call, void* address, const Cell* args);

      const Cell* read(const Activation& a, In in) const
      {
        return in.at >= 0 ? cells.data() + a.base + std::size_t(in.at) : a.code->constants.data() + ~in.at;
      }

      // End the run as the host has asked, from another thread: out of the
      // way of the loop that asks, which is one relaxed load and a branch not
      // taken -- nothing is ordered by it, a stop seen a turn later costing
      // nothing.
      [[noreturn, gnu::cold, gnu::noinline]] void stopped()
      {
        fault("pl4g: the host stopped the program\n", target::statuses::STOPPED_BY_HOST);
      }

      const Module& module;
      // The host's flag, or one nobody sets.
      const std::atomic<bool>* stopping;
      DataLayout layout;
      std::unordered_map<const Function*, std::unique_ptr<Code>> codes;
      std::unordered_set<const Function*> known;
      // Where each function stands in the module, which says which calls go
      // back.
      std::unordered_map<const Function*, std::size_t> order;
      std::unordered_map<const Value*, std::byte*> places;
      // Room for the image's objects, each aligned as its type wants.
      struct Freed {
        void operator()(std::byte* at) const noexcept { std::free(at); }
      };
      std::vector<std::unique_ptr<std::byte, Freed>> storage;
      std::byte* room(std::uint64_t size, std::uint64_t align);
      std::byte* init_record = nullptr;
      const ProductType* init_type = nullptr;
      std::uint64_t ring_offset = 0;
      std::uint64_t args_offset = 0;

      std::vector<Cell> cells;
      std::vector<Activation> activations;
      // Where what a branch hands over waits while the parameters are written.
      std::vector<Cell> moving;
      std::byte* stack = nullptr;
      std::size_t stack_size = 0;
      std::size_t stack_used = 0;
      std::size_t frame_top = 0;
      // What the kernel would have set the process up with.
      std::vector<std::string> strings;
      std::vector<Cell> initial_stack;
      std::array<unsigned char, 16> random{};
      // What the host defines: each function's call, prepared the first time
      // it is called, and where each symbol was found, asked once.
      std::unordered_map<const Function*, std::unique_ptr<HostCall>> host_calls;
      std::unordered_map<std::string, HostFunction> host_symbols;
      // The closures handed out for the program's functions, by function and
      // by the address of their code; and each call through a value that
      // reached code nothing here made, with its description.
      std::unordered_map<const Function*, std::unique_ptr<Closure>> closures;
      std::unordered_map<Cell, const Function*> closed;
      std::unordered_map<const Instruction*, std::unique_ptr<HostCall>> foreign_calls;
    };


    // What a closure does when C calls it: the arguments made cells as a load
    // from memory makes them, the function run, and its answer handed back as
    // C reads one -- an integer widened to a word, as libffi wants it.
    void closed_call(::ffi_cif*, void* ret, void** args, void* user)
    {
      auto& closure = *static_cast<Closure*>(user);
      std::vector<Cell> given;
      given.reserve(closure.call.params.size());
      for (std::size_t i = 0; i < closure.call.params.size(); ++i) {
        const Crossing& one = closure.call.params[i];
        Cell cell = 0;
        std::memcpy(low_bytes(cell, one.bytes), args[i], one.bytes);
        if (one.type != &::ffi_type_float && one.type != &::ffi_type_double && one.type != &::ffi_type_pointer)
          cell = extend(cell, one.bytes * 8, one.scalar.is_signed);
        given.push_back(cell);
      }
      // The buffer libffi answers a `struct` from is the room the function
      // writes its record into.
      if (closure.call.record != nullptr)
        given.push_back(reinterpret_cast<Cell>(ret));
      auto answer = closure.machine->call(closure.func, given);
      const Crossing& back = closure.call.answer;
      if (back.type == &::ffi_type_void || closure.call.record != nullptr)
        return;
      Cell first = answer.empty() ? 0 : answer[0];
      if (back.type == &::ffi_type_float)
        *static_cast<float*>(ret) = std::bit_cast<float>(std::uint32_t(first));
      else if (back.type == &::ffi_type_double)
        *static_cast<double*>(ret) = std::bit_cast<double>(first);
      else
        *static_cast<::ffi_arg*>(ret) = ::ffi_arg(first);
    }


    // Where in a record the field called `name` is, and what it is.
    std::pair<std::uint64_t, const Type*> where_in(const ProductType* record, std::string_view name,
                                                   const DataLayout& layout)
    {
      auto offsets = offsets_of(record, layout);
      for (std::size_t at = 0; at < record->fields.size(); ++at)
        if (record->fields[at].first == name)
          return { offsets[at], record->fields[at].second };
      throw InternalError(std::format("the record a program is started with has no '{}'", name));
    }


    Machine::Machine(const Module& module, const Options& options)
      : options(options), module(module), stopping(options.stop != nullptr ? options.stop : &never_stopped)
    {
      layout.frames = &module.frame_sizes;
      // Any order will do for which calls go back, so long as it is one.
      for (const auto& [_, f] : module.functions) {
        known.insert(f.get());
        order.emplace(f.get(), order.size());
      }
      for (const auto& f : module.unlisted) {
        known.insert(f.get());
        order.emplace(f.get(), order.size());
      }
      // The record the startup function is handed, where it takes one.
      if (module.startup != nullptr && ! module.startup->fty->params.empty())
        if (auto ptr = as<PtrType>(module.startup->fty->params[0]))
          init_type = as<ProductType>(ptr->pointee);
      if (init_type != nullptr) {
        auto size = size_of(init_type, layout);
        init_record = room(size + 16, 16);
        auto [state_at, state] = where_in(init_type, "fs", layout);
        ring_offset = state_at + where_in(as<ProductType>(state), "ring", layout).first;
        args_offset = where_in(init_type, "args", layout).first;
        auto [devices_at, devices_type] = where_in(init_type, "io", layout);
        auto devices = as<ProductType>(devices_type);
        auto device_offsets = offsets_of(devices, layout);
        int fds[3] = { options.devices.input, options.devices.output, options.devices.error };
        for (std::size_t i = 0; i < devices->fields.size() && i < 3; ++i) {
          auto one = as<ProductType>(devices->fields[i].second);
          auto [fd_at, fd_type] = where_in(one, "fd", layout);
          auto width = as<IntType>(fd_type)->bits / 8;
          std::int64_t fd = fds[i];
          std::memcpy(init_record + devices_at + device_offsets[i] + fd_at, &fd, width);
          Cell ring = reinterpret_cast<Cell>(init_record + ring_offset);
          std::memcpy(init_record + devices_at + device_offsets[i] + where_in(one, "ring", layout).first, &ring, 8);
        }
      }
      if (module.startup_record != nullptr)
        places[module.startup_record.get()] = init_record;
      place_globals();
      stack_size = module.stack_size > 0 ? std::size_t(module.stack_size) : std::size_t(8) << 20;
      void* mapped = ::mmap(nullptr, stack_size + 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
      if (mapped == MAP_FAILED) [[unlikely]]
        throw InternalError("no room for the interpreter's stack");
      stack = static_cast<std::byte*>(mapped);
    }


    // -- the image ------------------------------------------------------------

    std::byte* Machine::room(std::uint64_t size, std::uint64_t align)
    {
      auto rounded = (size + align - 1) / align * align;
      auto at = static_cast<std::byte*>(std::aligned_alloc(align, rounded));
      if (at == nullptr) [[unlikely]]
        throw InternalError("no room for the program's image");
      std::memset(at, 0, rounded);
      storage.emplace_back(at);
      return at;
    }

    void Machine::place_globals()
    {
      std::vector<const GlobalVar*> all;
      for (const auto& [_, var] : module.globals)
        all.push_back(var.get());
      for (auto var : module.texts)
        all.push_back(var);
      for (auto var : all) {
        DataLayout mine = layout;
        mine.system = var->system_layout;
        auto size = std::max<std::uint64_t>(size_of(var->value_type, mine), 1);
        auto align = std::max<std::uint64_t>(align_of(var->value_type, mine), 16);
        places[var] = room(size, align);
      }
      // The values after every place is known, since one may hold another's
      // address.
      for (auto var : all) {
        DataLayout mine = layout;
        mine.system = var->system_layout;
        if (var->initializer != nullptr)
          encode(places[var], var->initializer, var->value_type, mine);
      }
    }


    void Machine::encode(std::byte* at, const Value* init, const Type* ty, const DataLayout& lay)
    {
      // As pypl4g/target/globals.py lays a constant out: every part where its
      // type says, and nought between.
      auto scalar = [&](Cell v, std::uint64_t bytes) { std::memcpy(at, &v, std::min<std::uint64_t>(bytes, 8)); };
      switch (init->vkind) {
      case ValueKind::address_const:
        scalar(address_of(init), lay.pointer_size);
        return;
      case ValueKind::parts_const: {
        auto parts = static_cast<const PartsConst*>(init)->parts;
        auto pieces = parts_of(ty);
        auto offsets = member_offsets_of(tuple_type(pieces), lay);
        for (std::size_t i = 0; i < parts.size() && i < pieces.size(); ++i)
          encode(at + offsets[i], parts[i], pieces[i], lay);
        return;
      }
      case ValueKind::int_const:
        scalar(Cell(static_cast<const IntConst*>(init)->value), size_of(ty, lay));
        return;
      case ValueKind::bool_const:
        scalar(static_cast<const BoolConst*>(init)->value ? 1 : 0, size_of(ty, lay));
        return;
      case ValueKind::float_const:
        scalar(of_double(static_cast<const FloatConst*>(init)->value, unsigned(size_of(ty, lay) * 8)),
               size_of(ty, lay));
        return;
      case ValueKind::char_const:
        scalar(static_cast<const CharConst*>(init)->value, 4);
        return;
      case ValueKind::enum_const:
        scalar(Cell(static_cast<const EnumConst*>(init)->number()), size_of(ty, lay));
        return;
      case ValueKind::array_const: {
        auto array = as<ArrayType>(ty);
        if (array == nullptr)
          return;
        auto step = stride_of(array->element, lay);
        const auto& elements = static_cast<const ArrayConst*>(init)->elements;
        for (std::size_t i = 0; i < elements.size(); ++i)
          encode(at + i * step, elements[i], array->element, lay);
        return;
      }
      case ValueKind::record_const: {
        auto record = as<ProductType>(ty);
        if (record == nullptr)
          return;
        auto offsets = offsets_of(record, lay);
        const auto& fields = static_cast<const RecordConst*>(init)->fields;
        for (std::size_t i = 0; i < fields.size(); ++i)
          encode(at + offsets[i], fields[i], record->fields[i].second, lay);
        return;
      }
      case ValueKind::result_const: {
        auto result = as<ResultType>(ty);
        if (result == nullptr)
          return;
        auto r = static_cast<const ResultConst*>(init);
        encode(at, r->answer, result->ok, lay);
        at[tag_offset_of(result, lay)] = std::byte(r->failed ? 1 : 0);
        return;
      }
      case ValueKind::sum_const: {
        auto sum = as<SumType>(ty);
        if (sum == nullptr)
          return;
        auto s = static_cast<const SumConst*>(init);
        if (s->part != nullptr)
          encode(at, s->part, sum->variants[s->index].second, lay);
        at[tag_offset_of(sum, lay)] = std::byte(s->index);
        return;
      }
      default:
        return;
      }
    }


    Cell Machine::address_of(const Value* value)
    {
      if (value->vkind == ValueKind::address_const) {
        auto a = static_cast<const AddressConst*>(value);
        if (a->target == nullptr)
          return 0;
        // An address of the ring is one into the record the entry point lays
        // out, and how far in is what the layout says now.
        std::int64_t offset = a->offset;
        if (a->target == module.startup_record.get() && std::ranges::find(module.ring_addresses, value)
                                                          != module.ring_addresses.end())
          offset = std::int64_t(ring_offset);
        return address_of(a->target) + Cell(offset);
      }
      if (value->vkind == ValueKind::function)
        return reinterpret_cast<Cell>(static_cast<const Function*>(value));
      auto it = places.find(value);
      if (it == places.end()) [[unlikely]]
        throw InternalError(value->vkind == ValueKind::global
                              ? "the address of a variable with no place: " + static_cast<const GlobalVar*>(value)->name
                              : std::string("the address of something with no place"));
      return reinterpret_cast<Cell>(it->second);
    }


    // -- preparing code -------------------------------------------------------

    void Machine::expand(std::vector<Cell>& out, const Value* value)
    {
      switch (value->vkind) {
      case ValueKind::int_const:
        out.push_back(Cell(static_cast<const IntConst*>(value)->value));
        return;
      case ValueKind::char_const:
        out.push_back(static_cast<const CharConst*>(value)->value);
        return;
      case ValueKind::bool_const:
        out.push_back(static_cast<const BoolConst*>(value)->value ? 1 : 0);
        return;
      case ValueKind::float_const:
        out.push_back(of_double(static_cast<const FloatConst*>(value)->value, scalar_of(value->ty).bits));
        return;
      case ValueKind::enum_const:
        out.push_back(Cell(static_cast<const EnumConst*>(value)->number()));
        return;
      case ValueKind::undef:
        out.insert(out.end(), cells_of(value->ty), 0);
        return;
      case ValueKind::result_const: {
        auto r = static_cast<const ResultConst*>(value);
        auto result = as<ResultType>(r->ty);
        auto before = out.size();
        expand(out, r->answer);
        out.resize(before + leaves_of(result->ok).size(), 0);
        out.push_back(r->failed ? 1 : 0);
        if (result->err != nullptr)
          out.insert(out.end(), leaves_of(result->err).size(), 0);
        return;
      }
      case ValueKind::parts_const:
        for (auto part : static_cast<const PartsConst*>(value)->parts)
          expand(out, part);
        return;
      case ValueKind::address_const:
      case ValueKind::global:
      case ValueKind::function:
        out.push_back(address_of(value));
        return;
      case ValueKind::array_const:
      case ValueKind::record_const:
      case ValueKind::sum_const: {
        // A value held in memory is where its bytes are: a copy of its own.
        auto size = std::max<std::uint64_t>(size_of(value->ty, layout), 1);
        auto at = room(size, 16);
        encode(at, value, value->ty, layout);
        out.push_back(reinterpret_cast<Cell>(at));
        return;
      }
      default:
        throw InternalError("a constant of no kind there is");
      }
    }


    In Machine::operand(Code& code, const Value* value, const std::unordered_map<const Value*, std::uint32_t>& cells)
    {
      if (auto it = cells.find(value); it != cells.end())
        return { std::int32_t(it->second), cells_of(value->ty) };
      auto start = code.constants.size();
      expand(code.constants, value);
      return { ~std::int32_t(start), std::uint32_t(code.constants.size() - start) };
    }


    std::string Machine::describe(std::string_view what, std::string_view name, Span span) const
    {
      std::string where;
      if (options.sources != nullptr && span.is_valid())
        if (auto position = options.sources->position(span.start))
          where = std::format("{}:{}:{}: ", position->path, position->line, position->column);
      return std::format("{}pl4g: {} in '{}'\n", where, what, name);
    }


    // What each binary operation is called where a message says which went wrong.
    std::string_view operation_name(BinOp op)
    {
      switch (op) {
      case BinOp::add: case BinOp::sat_add: case BinOp::wrap_add: return "addition";
      case BinOp::sub: case BinOp::sat_sub: case BinOp::wrap_sub: return "subtraction";
      case BinOp::mul: case BinOp::sat_mul: case BinOp::wrap_mul: return "multiplication";
      case BinOp::shl: case BinOp::ashr: case BinOp::lshr: case BinOp::wrap_shl: case BinOp::wrap_ashr:
      case BinOp::wrap_lshr: return "shift";
      case BinOp::rotl: case BinOp::rotr: case BinOp::wrap_rotl: case BinOp::wrap_rotr: return "rotation";
      case BinOp::sdiv: case BinOp::udiv: case BinOp::fdiv: return "division";
      case BinOp::srem: case BinOp::urem: return "remainder";
      default: return "operation";
      }
    }


    void Machine::compile(Code& code, const Function& func)
    {
      code.func = &func;
      // A cell range for every value the function computes.
      std::unordered_map<const Value*, std::uint32_t> cells;
      std::uint32_t next = 0;
      std::unordered_map<const BasicBlock*, std::uint32_t> numbers;
      for (std::size_t b = 0; b < func.blocks.size(); ++b) {
        const auto& block = func.blocks[b];
        numbers[block.get()] = std::uint32_t(b);
        for (const auto& param : block->params) {
          cells[param.get()] = next;
          next += std::max<std::uint32_t>(cells_of(param->ty), 1);
        }
        for (auto inst : block->insts)
          if (inst->has_result()) {
            cells[inst] = next;
            next += std::max<std::uint32_t>(cells_of(inst->ty), 1);
          }
      }
      code.cells = next;
      for (const auto& param : func.blocks.front()->params) {
        code.params.push_back(cells[param.get()]);
        code.arguments += cells_of(param->ty);
      }

      code.blocks.resize(func.blocks.size());
      for (std::size_t b = 0; b < func.blocks.size(); ++b)
        for (auto inst : func.blocks[b]->insts) {
          Op op;
          op.kind = inst->kind;
          op.inst = inst;
          if (inst->has_result()) {
            op.dst = cells[inst];
            op.count = cells_of(inst->ty);
          }
          for (auto one : inst->operands)
            op.in.push_back(operand(code, one, cells));
          op.scalar = scalar_of(inst->ty);
          for (const auto& target : inst->targets) {
            Jump jump;
            jump.block = numbers.at(target.block);
            jump.backward = jump.block <= b;
            for (std::size_t i = 0; i < target.args.size(); ++i) {
              auto from = operand(code, target.args[i], cells);
              jump.moves.emplace_back(from, cells[target.block->params[i].get()]);
              jump.moved += from.count;
            }
            op.jumps.push_back(std::move(jump));
          }
          switch (inst->kind) {
          case InstKind::BinaryInst: {
            op.sub = std::uint8_t(inst->bin);
            const Type* ty = inst->ty;
            if (auto vec = as<VecType>(ty))
              op.lanes = vec->lanes;
            auto answer = as<ResultType>(ty) != nullptr ? as<ResultType>(ty)->ok : ty;
            switch (inst->bin) {
            case BinOp::add: case BinOp::sub: case BinOp::mul: op.arith = Arith::trapping; break;
            case BinOp::sat_add: case BinOp::sat_sub: case BinOp::sat_mul: op.arith = Arith::saturating; break;
            case BinOp::wrap_add: case BinOp::wrap_sub: case BinOp::wrap_mul: op.arith = Arith::wrapping; break;
            case BinOp::shl: case BinOp::ashr: case BinOp::lshr: case BinOp::rotl: case BinOp::rotr:
            case BinOp::wrap_shl: case BinOp::wrap_ashr: case BinOp::wrap_lshr: case BinOp::wrap_rotl:
            case BinOp::wrap_rotr: op.arith = Arith::shift; break;
            case BinOp::sdiv: case BinOp::udiv: case BinOp::srem: case BinOp::urem: op.arith = Arith::division; break;
            case BinOp::smax: case BinOp::umax: case BinOp::smin: case BinOp::umin: op.arith = Arith::extremum; break;
            default: op.arith = Arith::plain; break;
            }
            if (is<FloatType>(answer) || (op.lanes > 0 && scalar_of(ty).is_float()))
              op.arith = Arith::floating;
            else if (is<PtrType>(ty))
              op.arith = Arith::address;
            if (inst->bin == BinOp::wide_mul)
              // The halves are of the operands' kind, signed or not.
              op.from = scalar_of(inst->operands[0]->ty);
            if (op.arith == Arith::floating)
              op.message = describe(std::string(operation_name(inst->bin)) + " with no number for an answer", func,
                                    inst->span);
            else if (op.arith == Arith::shift)
              op.message = describe(std::string(operation_name(inst->bin)) + " by more than the width of the type",
                                    func, inst->span);
            else
              op.message = describe(std::string(operation_name(inst->bin)) + " that does not fit", func, inst->span);
            break;
          }
          case InstKind::UnaryInst:
            op.sub = std::uint8_t(inst->un);
            if (auto vec = as<VecType>(inst->ty))
              op.lanes = vec->lanes;
            else if (auto read = as<VecType>(inst->operands[0]->ty))
              // A mask reads a run and answers a number: how many lanes it
              // gathers.
              op.value = read->lanes;
            op.from = scalar_of(inst->operands[0]->ty);
            break;
          case InstKind::CmpInst:
            op.sub = std::uint8_t(inst->pred);
            op.scalar = scalar_of(inst->operands[0]->ty);
            if (auto vec = as<VecType>(inst->operands[0]->ty))
              op.lanes = vec->lanes;
            break;
          case InstKind::CastInst:
            op.sub = std::uint8_t(inst->cast);
            op.from = scalar_of(inst->operands[0]->ty);
            break;
          case InstKind::SplatInst:
            op.lanes = as<VecType>(inst->ty)->lanes;
            break;
          case InstKind::AnyLaneInst:
            op.lanes = as<VecType>(inst->operands[0]->ty)->lanes;
            break;
          case InstKind::LoadInst:
          case InstKind::StoreInst: {
            const Type* ty = inst->kind == InstKind::LoadInst ? inst->ty : inst->operands[2]->ty;
            if (auto vec = as<VecType>(ty)) {
              op.lanes = vec->lanes;
              auto step = stride_of(vec->element, layout);
              for (unsigned i = 0; i < vec->lanes; ++i) {
                op.offsets.push_back(i * step);
                op.widths.push_back(memory_bytes(vec->element));
                op.parts.push_back(scalar_of(vec->element));
              }
            } else if (made_of_parts(ty)) {
              auto leaves = leaves_of(ty);
              op.offsets = leaf_offsets_of(ty, layout);
              for (auto leaf : leaves) {
                op.widths.push_back(memory_bytes(leaf));
                op.parts.push_back(scalar_of(leaf));
              }
            } else {
              op.offsets.push_back(0);
              op.widths.push_back(memory_bytes(ty));
              op.parts.push_back(scalar_of(ty));
            }
            break;
          }
          case InstKind::FrameInst: {
            auto align = std::max<std::uint64_t>(align_of(inst->held, layout), 1);
            code.frame_bytes = (code.frame_bytes + align - 1) / align * align;
            op.value = code.frame_bytes;
            code.frame_bytes += size_of(inst->held, layout);
            break;
          }
          case InstKind::SizeOfInst:
            op.value = size_of(inst->held, layout);
            break;
          case InstKind::AddressInst:
            op.value = address_of(inst->operands[0]);
            break;
          case InstKind::CodeInst:
            op.value = code_value(inst->callee);
            break;
          case InstKind::CallInst:
            op.callee = inst->callee;
            op.backward = inst->callee == nullptr || ! order.contains(inst->callee)
                          || order.at(inst->callee) <= order.at(&func);
            break;
          case InstKind::AssertInst:
            op.message = describe(inst->what, func, inst->span);
            break;
          case InstKind::ExtractInst:
            op.from.bits = unsigned(leaves_of(inst->operands[0]->ty).size());
            if (auto result = as<ResultType>(inst->operands[0]->ty))
              op.value = inst->index == 0 ? 0 : leaves_of(result->ok).size() + std::uint64_t(inst->index) - 1;
            else
              op.value = std::uint64_t(inst->index);
            break;
          case InstKind::UnwrapInst:
          case InstKind::FailedInst:
          case InstKind::ErrorInst:
            op.value = leaves_of(as<ResultType>(inst->operands[0]->ty)->ok).size();
            break;
          case InstKind::SwitchInst:
            for (auto c : inst->cases)
              op.cases.push_back(Cell(c));
            break;
          default:
            break;
          }
          code.blocks[b].push_back(std::move(op));
        }
    }


    const Code& Machine::code_of(const Function* func)
    {
      auto& found = codes[func];
      if (found == nullptr) {
        found = std::make_unique<Code>();
        compile(*found, *func);
      }
      return *found;
    }


    // -- running --------------------------------------------------------------

    [[noreturn]] void Machine::fault(std::string_view message, int code)
    {
      write_all(options.devices.error, message);
      throw Exit{ code };
    }


    Cell Machine::binary_int(const Op& op, const Scalar& s, Cell a, Cell b)
    {
      auto bin = BinOp(op.sub);
      auto in_type = [&](Cell v) { return extend(v, s.bits, s.is_signed); };
      switch (op.arith) {
      case Arith::trapping:
      case Arith::saturating: {
        // Worked out exactly, and then held against the two ends of the type.
        __int128 x = s.is_signed ? __int128(std::int64_t(a)) : __int128(a);
        __int128 y = s.is_signed ? __int128(std::int64_t(b)) : __int128(b);
        __int128 r;
        bool past = false;
        switch (bin) {
        case BinOp::add: case BinOp::sat_add: r = x + y; break;
        case BinOp::sub: case BinOp::sat_sub: r = x - y; break;
        default:
          if (__builtin_mul_overflow(x, y, &r))
            past = true;
          break;
        }
        __int128 low = s.is_signed ? -(__int128(1) << (s.bits - 1)) : 0;
        __int128 high = s.is_signed ? (__int128(1) << (s.bits - 1)) - 1 : (__int128(1) << s.bits) - 1;
        if (past || r < low || r > high) {
          if (op.arith == Arith::trapping)
            fault(op.message, status::overflow);
          bool below = past ? ((x < 0) != (y < 0)) : r < low;
          r = below ? low : high;
        }
        return in_type(Cell(r));
      }
      case Arith::wrapping:
        switch (bin) {
        case BinOp::wrap_add: return in_type(a + b);
        case BinOp::wrap_sub: return in_type(a - b);
        default: return in_type(a * b);
        }
      case Arith::shift: {
        Cell distance = b;
        bool wrapped = bin >= BinOp::wrap_shl;
        // Modulo the width, the distance read as the bits of its type without
        // a sign: an `and` for a width that is a power of two, and a remainder
        // for one that is not.
        if (wrapped)
          distance = extend(distance, s.bits, false) % s.bits;
        else if (distance >= s.bits)
          fault(op.message, status::overflow);
        switch (bin) {
        case BinOp::shl: case BinOp::wrap_shl: return in_type(a << distance);
        case BinOp::lshr: case BinOp::wrap_lshr: return in_type(a >> distance);
        case BinOp::ashr: case BinOp::wrap_ashr: return in_type(Cell(std::int64_t(a) >> distance));
        default: {
          // Two shifts and an or, so that a narrow type turns within itself.
          Cell other = (s.bits - distance) % s.bits;
          bool left = bin == BinOp::rotl || bin == BinOp::wrap_rotl;
          Cell forwards = left ? distance : other;
          Cell backwards = left ? other : distance;
          Cell shifted_left = forwards >= 64 ? 0 : a << forwards;
          Cell shifted_right = backwards >= 64 ? 0 : a >> backwards;
          return in_type(shifted_left | shifted_right);
        }
        }
      }
      case Arith::extremum:
        switch (bin) {
        case BinOp::smax: return std::int64_t(a) < std::int64_t(b) ? b : a;
        case BinOp::umax: return a < b ? b : a;
        case BinOp::smin: return std::int64_t(a) > std::int64_t(b) ? b : a;
        default: return a > b ? b : a;
        }
      case Arith::address:
        switch (bin) {
        case BinOp::add: return a + b;
        case BinOp::sub: return a - b;
        case BinOp::mul: return a * b;
        case BinOp::and_: return a & b;
        case BinOp::or_: return a | b;
        default: return a ^ b;
        }
      default:
        switch (bin) {
        case BinOp::and_: return a & b;
        case BinOp::or_: return a | b;
        case BinOp::xor_: return a ^ b;
        case BinOp::add: return a + b;
        case BinOp::sub: return a - b;
        default: return a * b;
        }
      }
    }


    void Machine::binary(const Op& op, Cell* dst, const Cell* a, const Cell* b)
    {
      auto bin = BinOp(op.sub);
      if (bin == BinOp::wide_mul) {
        // The whole product in 128 bits, the upper half first as the tuple
        // has it and the lower half the unsigned bits below it.
        unsigned __int128 whole = op.from.is_signed
                                    ? (unsigned __int128)(__int128(std::int64_t(a[0])) * __int128(std::int64_t(b[0])))
                                    : (unsigned __int128)a[0] * (unsigned __int128)b[0];
        dst[0] = Cell(whole >> 64);
        dst[1] = Cell(whole);
        return;
      }
      if (bin == BinOp::clmul) {
        // And the product without carries, the same two halves of it.
        auto whole = ir::carryless_product(std::uint64_t(a[0]), std::uint64_t(b[0]));
        dst[0] = Cell(whole >> 64);
        dst[1] = Cell(whole);
        return;
      }
      if (op.arith == Arith::floating) {
        unsigned bits = op.scalar.bits;
        auto one = [&](Cell x, Cell y, Cell& out) {
          double l = as_double(x, bits), r = as_double(y, bits);
          double v;
          bool keeps = false;
          switch (bin) {
          case BinOp::add: v = bits == 32 ? double(float(l) + float(r)) : l + r; break;
          case BinOp::sub: v = bits == 32 ? double(float(l) - float(r)) : l - r; break;
          case BinOp::mul: v = bits == 32 ? double(float(l) * float(r)) : l * r; break;
          case BinOp::fdiv: v = bits == 32 ? double(float(l) / float(r)) : l / r; break;
          case BinOp::smax: v = l > r ? l : r; keeps = true; break;
          default: v = l < r ? l : r; keeps = true; break;
          }
          out = of_double(v, bits);
          return keeps || std::isfinite(v);
        };
        if (op.lanes > 0) {
          for (unsigned i = 0; i < op.lanes; ++i)
            if (! one(a[i], b[i], dst[i])) [[unlikely]]
              fault(op.message, status::no_answer);
          return;
        }
        if (is<ResultType>(op.inst->ty)) {
          // A division by nought has no answer, and that is what the result says.
          bool failed = as_double(b[0], bits) == 0.0;
          dst[1] = failed ? 1 : 0;
          if (failed) {
            dst[0] = of_double(as_double(a[0], bits) / as_double(b[0], bits), bits);
            return;
          }
        }
        if (! one(a[0], b[0], dst[0])) [[unlikely]]
          fault(op.message, status::no_answer);
        return;
      }
      if (op.arith == Arith::division) {
        const Scalar& s = op.scalar;
        bool failed = b[0] == 0;
        if (s.is_signed) {
          __int128 low = -(__int128(1) << (s.bits - 1));
          if (std::int64_t(a[0]) == std::int64_t(low) && std::int64_t(b[0]) == -1)
            failed = true;
        }
        Cell answer = 0;
        if (! failed) {
          bool remainder = bin == BinOp::srem || bin == BinOp::urem;
          if (s.is_signed)
            answer = Cell(remainder ? std::int64_t(a[0]) % std::int64_t(b[0]) : std::int64_t(a[0]) / std::int64_t(b[0]));
          else
            answer = remainder ? a[0] % b[0] : a[0] / b[0];
        }
        dst[0] = extend(answer, s.bits, s.is_signed);
        if (op.count > 1)
          dst[1] = failed ? 1 : 0;
        return;
      }
      if (op.lanes > 0) {
        for (unsigned i = 0; i < op.lanes; ++i)
          dst[i] = binary_int(op, op.scalar, a[i], b[i]);
        return;
      }
      dst[0] = binary_int(op, op.scalar, a[0], b[0]);
    }


    // Whether `pred` holds between two cells read as `s` says.
    bool compare(CmpPred pred, const Scalar& s, Cell a, Cell b)
    {
      if (s.is_float()) {
        double x = as_double(a, s.bits), y = as_double(b, s.bits);
        switch (pred) {
        case CmpPred::eq: return x == y;
        case CmpPred::ne: return x != y;
        case CmpPred::slt: case CmpPred::ult: return x < y;
        case CmpPred::sle: case CmpPred::ule: return x <= y;
        case CmpPred::sgt: case CmpPred::ugt: return x > y;
        default: return x >= y;
        }
      }
      switch (pred) {
      case CmpPred::eq: return a == b;
      case CmpPred::ne: return a != b;
      case CmpPred::slt: return std::int64_t(a) < std::int64_t(b);
      case CmpPred::sle: return std::int64_t(a) <= std::int64_t(b);
      case CmpPred::sgt: return std::int64_t(a) > std::int64_t(b);
      case CmpPred::sge: return std::int64_t(a) >= std::int64_t(b);
      case CmpPred::ult: return a < b;
      case CmpPred::ule: return a <= b;
      case CmpPred::ugt: return a > b;
      default: return a >= b;
      }
    }


    Cell unary(const Op& op, const Scalar& s, Cell a)
    {
      switch (UnOp(op.sub)) {
      case UnOp::not_:
        // The complement of a narrow unsigned value has the bits above it
        // put back to nought.
        return s.is_signed || s.kind == Scalar::Kind::boolean ? ~a : extend(~a, s.bits, false);
      case UnOp::neg:
        return extend(Cell(-std::int64_t(a)), s.bits, s.is_signed);
      // Every count is over the type's own bits, not its holder's.
      case UnOp::count_ones:
        return Cell(std::popcount(extend(a, s.bits, false)));
      case UnOp::count_leading: {
        Cell v = extend(a, s.bits, false);
        return Cell(std::countl_zero(v) - (64 - int(s.bits)));
      }
      case UnOp::count_trailing: {
        // Nought counts the width of the type, as the back ends do.
        Cell v = extend(a, s.bits, false);
        return v == 0 ? Cell(s.bits) : Cell(std::countr_zero(v));
      }
      case UnOp::fabs:
        return of_double(std::fabs(as_double(a, s.bits)), s.bits);
      case UnOp::floor:
        return of_double(std::floor(as_double(a, s.bits)), s.bits);
      case UnOp::ceil:
        return of_double(std::ceil(as_double(a, s.bits)), s.bits);
      default:
        return of_double(std::nearbyint(as_double(a, s.bits)), s.bits);
      }
    }


    Cell cast(const Op& op, Cell a)
    {
      const Scalar& to = op.scalar;
      switch (CastKind(op.sub)) {
      case CastKind::zext:
        return extend(a, op.from.held, false);
      case CastKind::sext:
        return extend(a, op.from.held, true);
      case CastKind::trunc:
        return extend(a, to.held, to.is_signed);
      case CastKind::fext:
        return of_double(as_double(a, op.from.bits), to.bits);
      case CastKind::itof: {
        // Rounded once into either format, which the host's own conversion
        // of a 64-bit number does.
        Cell v = extend(a, op.from.held, op.from.is_signed);
        if (to.bits == 32)
          return of_double(op.from.is_signed ? double(float(std::int64_t(v))) : double(float(v)), 32);
        return of_double(op.from.is_signed ? double(std::int64_t(v)) : double(v), 64);
      }
      case CastKind::ftoi: {
        // Read only where the checker found room; anything else answers
        // nought, a conversion of it being undefined.
        double whole = std::trunc(as_double(a, op.from.bits));
        double top = std::ldexp(1.0, int(to.is_signed ? to.bits - 1 : to.bits));
        double bottom = to.is_signed ? -top : 0.0;
        if (! (whole >= bottom && whole < top))
          return 0;
        if (to.is_signed)
          return extend(Cell(std::int64_t(whole)), to.held, true);
        return extend(Cell(std::uint64_t(whole)), to.held, false);
      }
      case CastKind::fbits:
        // The same bits, read as the other kind.
        if (to.kind == Scalar::Kind::integer)
          return extend(a, to.held, false);
        return to.bits == 32 ? extend(a, 32, false) : a;
      default:
        // The same bits, read as the type now says.
        if (to.kind == Scalar::Kind::integer || to.kind == Scalar::Kind::boolean)
          return extend(a, to.held, to.is_signed);
        return a;
      }
    }


    std::vector<Cell> Machine::call(const Function* func, std::span<const Cell> args)
    {
      if (func->is_declaration())
        return { native_call(func, args.data()) };
      const Code& code = code_of(func);
      auto depth = activations.size();
      auto base = cells.size();
      cells.resize(base + code.cells + 1, 0);
      std::size_t taken = 0;
      for (std::size_t i = 0; i < code.params.size(); ++i) {
        auto n = cells_of(func->blocks.front()->params[i]->ty);
        std::copy_n(args.begin() + std::ptrdiff_t(taken), n, cells.begin() + std::ptrdiff_t(base + code.params[i]));
        taken += n;
      }
      auto mark = (frame_top + 15) & ~std::size_t(15);
      frame_top = mark + code.frame_bytes;
      activations.push_back({ &code, base, mark });
      // The answer is put after the last cell of this one, for the caller.
      execute(depth);
      std::vector<Cell> answer(cells.begin() + std::ptrdiff_t(base), cells.end());
      cells.resize(base);
      return answer;
    }


    void Machine::execute(std::size_t depth)
    {
      // Where the host's stop is, read once: the loop asks what it holds.
      const std::atomic<bool>& asked = *stopping;
      while (activations.size() > depth) {
        Activation& a = activations.back();
        const Op& op = a.code->blocks[a.block][a.at++];
        Cell* frame = cells.data() + a.base;
        auto in = [&](std::size_t i) { return read(a, op.in[i]); };
        switch (op.kind) {
        case InstKind::BinaryInst:
          binary(op, frame + op.dst, in(0), in(1));
          break;
        case InstKind::UnaryInst: {
          auto src = in(0);
          if (UnOp(op.sub) == UnOp::mask) {
            // A truth value per lane, the first the lowest bit.
            Cell bits = 0;
            for (std::uint64_t i = 0; i < op.value; ++i)
              if (src[i] != 0)
                bits |= Cell(1) << i;
            frame[op.dst] = bits;
            break;
          }
          if (op.lanes > 0)
            for (unsigned i = 0; i < op.lanes; ++i)
              frame[op.dst + i] = unary(op, op.from, src[i]);
          else
            frame[op.dst] = unary(op, op.from, src[0]);
          break;
        }
        case InstKind::CmpInst: {
          auto x = in(0), y = in(1);
          if (op.lanes > 0)
            for (unsigned i = 0; i < op.lanes; ++i)
              frame[op.dst + i] = compare(CmpPred(op.sub), op.scalar, x[i], y[i]) ? 1 : 0;
          else
            frame[op.dst] = compare(CmpPred(op.sub), op.scalar, x[0], y[0]) ? 1 : 0;
          break;
        }
        case InstKind::SelectInst: {
          auto chosen = in(0)[0] != 0 ? in(1) : in(2);
          std::copy_n(chosen, op.count, frame + op.dst);
          break;
        }
        case InstKind::SplatInst:
          std::fill_n(frame + op.dst, op.lanes, in(0)[0]);
          break;
        case InstKind::AnyLaneInst: {
          auto lanes = in(0);
          frame[op.dst] = std::any_of(lanes, lanes + op.lanes, [](Cell c) { return c != 0; }) ? 1 : 0;
          break;
        }
        case InstKind::WrapInst:
        case InstKind::TupleInst: {
          // Each operand's cells after the one before's, an answer that
          // carries nothing being noughts.
          std::uint32_t at = op.dst;
          for (std::size_t i = 0; i < op.in.size(); ++i) {
            std::copy_n(in(i), op.in[i].count, frame + at);
            at += op.in[i].count;
          }
          std::fill(frame + at, frame + op.dst + op.count, 0);
          break;
        }
        case InstKind::ExtractInst:
          std::copy_n(in(0) + op.value, op.count, frame + op.dst);
          break;
        case InstKind::UnwrapInst:
          std::copy_n(in(0), op.count, frame + op.dst);
          break;
        case InstKind::FailedInst:
          frame[op.dst] = in(0)[op.value];
          break;
        case InstKind::ErrorInst:
          std::copy_n(in(0) + op.value + 1, op.count, frame + op.dst);
          break;
        case InstKind::CastInst:
          frame[op.dst] = cast(op, in(0)[0]);
          break;
        case InstKind::MemStartInst:
          break;
        case InstKind::AddressInst:
        case InstKind::CodeInst:
        case InstKind::SizeOfInst:
          frame[op.dst] = op.value;
          break;
        case InstKind::FrameInst:
          frame[op.dst] = reinterpret_cast<Cell>(stack + a.frame_mark + op.value);
          break;
        case InstKind::LoadInst: {
          auto at = reinterpret_cast<const std::byte*>(in(1)[0]);
          for (std::size_t i = 0; i < op.offsets.size(); ++i) {
            Cell v = 0;
            std::memcpy(&v, at + op.offsets[i], op.widths[i]);
            const Scalar& s = op.parts[i];
            frame[op.dst + i] = s.is_float() ? v : extend(v, op.widths[i] * 8, s.is_signed);
          }
          break;
        }
        case InstKind::StoreInst: {
          auto at = reinterpret_cast<std::byte*>(in(1)[0]);
          auto value = in(2);
          for (std::size_t i = 0; i < op.offsets.size(); ++i)
            std::memcpy(at + op.offsets[i], value + i, op.widths[i]);
          break;
        }
        case InstKind::AssertInst:
          if (in(0)[0] == 0) {
            if (op.inst->observing)
              write_all(options.devices.error, op.message);
            else
              fault(op.message, op.inst->status);
          }
          break;
        case InstKind::SyscallInst: {
          Cell number = in(0)[0];
          Cell args[6] = {};
          for (std::size_t i = 1; i < op.in.size() && i <= 6; ++i)
            args[i - 1] = in(i)[0];
          if (number == SYS_exit || number == SYS_exit_group)
            throw Exit{ int(args[0] & 0xff) };
          long answer = ::syscall(long(number), args[0], args[1], args[2], args[3], args[4], args[5]);
          frame[op.dst] = Cell(answer == -1 ? -errno : answer);
          break;
        }
        case InstKind::CallInst: {
          const Function* callee = op.callee;
          std::size_t first = 0;
          bool system = op.inst->cconv != default_cconv;
          if (callee == nullptr) {
            // Through a value: the code is where its first part says -- a
            // function of the program, or for one of the system's
            // convention a closure of one, or code nothing here made, which
            // is called as C calls it.
            Cell target = in(0)[0];
            callee = reinterpret_cast<const Function*>(target);
            first = 1;
            if (system)
              if (auto found = closed.find(target); found != closed.end())
                callee = found->second;
            if (! known.contains(callee)) [[unlikely]] {
              if (! system || target == 0)
                throw Signal{ SIGSEGV };
              std::vector<Cell> args;
              for (std::size_t i = 1; i < op.in.size(); ++i)
                args.insert(args.end(), in(i), in(i) + op.in[i].count);
              Cell answer = foreign_call(op.inst, reinterpret_cast<void*>(target), args.data());
              // What it called may have called back into the program, and
              // the cells may have moved.
              if (op.count > 0)
                cells[activations.back().base + op.dst] = answer;
              break;
            }
          }
          // A call through a value of the language's convention hands the
          // environment first, in the chain register; a callee that has none
          // never reads it, and its own parameters are the arguments after
          // it.  One of the system's has none to hand.
          if (first == 1 && ! system && ! callee->attrs.chained && op.in.size() > 1)
            first = 2;
          std::vector<Cell> args;
          for (std::size_t i = first; i < op.in.size(); ++i)
            args.insert(args.end(), in(i), in(i) + op.in[i].count);
          if (callee->is_declaration()) {
            Cell answer = native_call(callee, args.data());
            // A host function may have called back into the program through
            // a closure, and the cells may have moved.
            frame = cells.data() + activations.back().base;
            if (options.trace >= 0) [[unlikely]] {
              std::string said = std::format("{:{}}{}(", "", activations.size(), callee->name);
              for (std::size_t i = 0; i < args.size(); ++i)
                said += std::format("{}{:#x}", i > 0 ? ", " : "", args[i]);
              write_all(options.trace, said + std::format(") = {:#x}\n", answer));
            }
            if (op.count > 0)
              frame[op.dst] = answer;
            break;
          }
          const Code& code = code_of(callee);
          if (args.size() != code.arguments) [[unlikely]]
            throw InternalError(std::format("'{}' handed {} words where '{}' takes {}", a.code->func->name,
                                            args.size(), callee->name, code.arguments));
          // What the call takes of the stack, which is what running out of it
          // is measured in.
          // The host's stop is asked with it at a call that goes back, which
          // a recursion with no loop in it makes over and over: a program
          // whose every call went forward would make finitely many.
          std::size_t cost = code.frame_bytes + 32 + 4 * std::size_t(code.cells);
          if (stack_used + cost > stack_size || frame_top + code.frame_bytes + 16 > stack_size
              || (op.backward && asked.load(std::memory_order_relaxed))) [[unlikely]] {
            if (asked.load(std::memory_order_relaxed))
              stopped();
            write_all(options.devices.error, "pl4g: the stack ran out\n");
            throw Exit{ status::stack_overflow };
          }
          stack_used += cost;
          if (options.trace >= 0) [[unlikely]]
            write_all(options.trace, std::format("{:{}}{}\n", "", activations.size(), callee->name));
          auto base = cells.size();
          cells.resize(base + code.cells + 1, 0);
          std::size_t taken = 0;
          for (std::size_t i = 0; i < code.params.size(); ++i) {
            auto n = cells_of(callee->blocks.front()->params[i]->ty);
            std::copy_n(args.begin() + std::ptrdiff_t(taken), n, cells.begin() + std::ptrdiff_t(base + code.params[i]));
            taken += n;
          }
          auto mark = (frame_top + 15) & ~std::size_t(15);
          frame_top = mark + code.frame_bytes;
          activations.push_back({ &code, base, mark, 0, 0, op.dst });
          break;
        }
        case InstKind::RetInst: {
          Activation done = a;
          std::size_t cost = done.code->frame_bytes + 32 + 4 * std::size_t(done.code->cells);
          std::vector<Cell> answer;
          if (! op.in.empty())
            answer.assign(read(done, op.in[0]), read(done, op.in[0]) + op.in[0].count);
          activations.pop_back();
          frame_top = done.frame_mark;
          if (activations.size() > depth) {
            stack_used -= cost;
            Cell* caller = cells.data() + activations.back().base;
            std::copy(answer.begin(), answer.end(), caller + done.answer);
            cells.resize(done.base);
          } else {
            // The outermost: what it answers is left where `call` reads it.
            cells.resize(done.base);
            cells.insert(cells.end(), answer.begin(), answer.end());
          }
          break;
        }
        case InstKind::BrInst:
        case InstKind::CondBrInst:
        case InstKind::SwitchInst: {
          const Jump* jump;
          if (op.kind == InstKind::BrInst)
            jump = &op.jumps[0];
          else if (op.kind == InstKind::CondBrInst)
            jump = &op.jumps[in(0)[0] != 0 ? 0 : 1];
          else {
            Cell value = in(0)[0];
            jump = &op.jumps.back();
            for (std::size_t i = 0; i < op.cases.size(); ++i)
              if (op.cases[i] == value) {
                jump = &op.jumps[i];
                break;
              }
          }
          // What a branch hands over is read before any of it is written.
          if (jump->moved > 0) {
            moving.resize(jump->moved);
            std::size_t at = 0;
            for (const auto& [from, to] : jump->moves) {
              std::copy_n(read(a, from), from.count, moving.data() + at);
              at += from.count;
            }
            at = 0;
            for (const auto& [from, to] : jump->moves) {
              std::copy_n(moving.data() + at, from.count, frame + to);
              at += from.count;
            }
          }
          a.block = jump->block;
          a.at = 0;
          if (jump->backward && asked.load(std::memory_order_relaxed)) [[unlikely]]
            stopped();
          break;
        }
        case InstKind::UnreachableInst:
          throw Signal{ SIGILL };
        default:
          throw InternalError(std::format("the interpreter cannot run '{}'", op.inst->opcode()));
        }
      }
    }


    // -- the natives ----------------------------------------------------------

    Cell native_alloc(Machine& m, const Cell* args)
    {
      void* made = rt::arena_alloc(reinterpret_cast<rt::Arena*>(args[0]), args[1]);
      // Nothing asked of an arena that has no chunk yet is where its bump
      // pointer is, which is nought: that is no failure, nothing being wanted.
      if (made == nullptr && args[1] != 0) [[unlikely]]
        m.fault("pl4g: out of memory\n", status::out_of_memory);
      return reinterpret_cast<Cell>(made);
    }


    HostCall& Machine::host_call_of(const Function* callee)
    {
      auto& found = host_calls[callee];
      if (found != nullptr)
        return *found;
      auto made = std::make_unique<HostCall>();
      // A record laid out for C is answered into room the caller made, which
      // `largeanswers` handed over last: what libffi is told is the rest, and
      // a function answering the record.
      std::span<const Type* const> given = callee->fty->params;
      if (callee->attrs.answer_in_storage) {
        given = given.first(given.size() - 1);
        made->record = ffi_type_of(as<PtrType>(callee->fty->params.back())->pointee, *made);
      }
      for (auto ty : given) {
        made->params.push_back(crossing_of(ty));
        made->types.push_back(made->params.back().type);
      }
      made->answer = crossing_of(callee->fty->ret);
      if (made->record != nullptr)
        made->answer.type = made->record;
      found = std::move(made);
      return *found;
    }


    Cell Machine::host_call(const Function* callee, const Cell* given)
    {
      HostCall& host = host_call_of(callee);
      if (host.address == nullptr) {
        // Asked of what the host handed over by name first, and then of what
        // the program loaded globally, as the compiled stub asks: once a
        // symbol, however many declarations name it.
        const std::string& symbol = *callee->attrs.host;
        auto [at, fresh] = host_symbols.try_emplace(symbol);
        if (fresh) {
          if (options.defined != nullptr)
            if (auto given = options.defined->find(symbol); given != options.defined->end())
              at->second = given->second;
          if (at->second.function == nullptr)
            at->second = { ::dlsym(RTLD_DEFAULT, symbol.c_str()), nullptr };
        }
        if (at->second.function == nullptr) [[unlikely]] {
          // The program stops, as a shared object's stub stops it, and the
          // message is about the function the program wrote rather than the
          // one the checker made of it to hand C its text.
          std::string_view name = callee->name;
          if (name.ends_with(target::host::crossing_suffix))
            name.remove_suffix(target::host::crossing_suffix.size());
          fault(describe(std::format("the host defines no '{}'", symbol), name, callee->span),
                target::statuses::HOST_SYMBOL_MISSING);
        }
        host.address = at->second.function;
        host.context = at->second.context;
      }
      if (! host.prepared) {
        // A context goes first, as a pointer, before what the declaration
        // says the function takes.
        if (host.context != nullptr)
          host.types.insert(host.types.begin(), &::ffi_type_pointer);
        if (::ffi_prep_cif(&host.cif, FFI_DEFAULT_ABI, unsigned(host.types.size()), host.answer.type,
                           host.types.data())
            != FFI_OK) [[unlikely]]
          throw InternalError(std::format("libffi cannot describe a call of '{}'", callee->name));
        host.prepared = true;
      }
      Cell answer = crossed_call(host, host.address, given);
      // A program stopped while the host's function ran -- which nothing
      // interrupts -- ends as it returns, the first moment it is the
      // program's again.
      if (stopping->load(std::memory_order_relaxed)) [[unlikely]]
        stopped();
      return answer;
    }


    Cell Machine::code_value(const Function* func)
    {
      // One answering a record laid out for C into room handed over last, as
      // in a program the interpreter runs, which a host calling it as C does
      // knows nothing of: the closure answers the `struct` libffi hands it a
      // buffer for, and that buffer is the room.  One answering anything else
      // through storage is nothing C can call.  Room handed over first is
      // C's own shape, which the closure's description already is.
      std::span<const Type* const> given = func->fty->params;
      const ProductType* record = nullptr;
      if (func->attrs.answer_in_storage && ! func->attrs.answer_first && ! given.empty()) {
        record = as<ProductType>(as<PtrType>(given.back())->pointee);
        if (record == nullptr || ! record->abi)
          return reinterpret_cast<Cell>(func);
        given = given.first(given.size() - 1);
      }
      if (func->cconv != system_cconv || ! crosses(func->fty->ret) || ! std::ranges::all_of(given, crosses))
        return reinterpret_cast<Cell>(func);
      auto& found = closures[func];
      if (found == nullptr) {
        auto made = std::make_unique<Closure>();
        made->machine = this;
        made->func = func;
        for (auto ty : given) {
          made->call.params.push_back(crossing_of(ty));
          made->call.types.push_back(made->call.params.back().type);
        }
        made->call.answer = crossing_of(func->fty->ret);
        if (record != nullptr)
          made->call.answer.type = made->call.record = ffi_type_of(record, made->call);
        made->closure = static_cast<::ffi_closure*>(::ffi_closure_alloc(sizeof(::ffi_closure), &made->code));
        if (made->closure == nullptr
            || ::ffi_prep_cif(&made->call.cif, FFI_DEFAULT_ABI, unsigned(made->call.types.size()),
                              made->call.answer.type, made->call.types.data())
                 != FFI_OK
            || ::ffi_prep_closure_loc(made->closure, &made->call.cif, closed_call, made.get(), made->code)
                 != FFI_OK) [[unlikely]] {
          if (made->closure != nullptr)
            ::ffi_closure_free(made->closure);
          throw InternalError(std::format("libffi cannot make code of '{}' a host can call", func->name));
        }
        closed[reinterpret_cast<Cell>(made->code)] = func;
        found = std::move(made);
      }
      return reinterpret_cast<Cell>(found->code);
    }


    Cell Machine::foreign_call(const Instruction* call, void* address, const Cell* given)
    {
      auto& found = foreign_calls[call];
      if (found == nullptr) {
        auto made = std::make_unique<HostCall>();
        // A record laid out for C is answered into room the caller made,
        // which `large_answers` handed over last, one argument more than the
        // value's type takes: what libffi is told is the rest, and code
        // answering the `struct`, as for a host function.
        std::span<Value* const> given_args = call->arguments();
        auto fty = as<FuncType>(call->operands[0]->ty);
        if (auto record = fty == nullptr ? nullptr : as<ProductType>(fty->ret);
            record != nullptr && record->abi && given_args.size() == fty->params.size() + 1) {
          given_args = given_args.first(given_args.size() - 1);
          made->record = ffi_type_of(record, *made);
        }
        for (auto one : given_args) {
          made->params.push_back(crossing_of(one->ty));
          made->types.push_back(made->params.back().type);
        }
        made->answer = crossing_of(call->ty);
        if (made->record != nullptr)
          made->answer.type = made->record;
        if (::ffi_prep_cif(&made->cif, FFI_DEFAULT_ABI, unsigned(made->types.size()), made->answer.type,
                           made->types.data())
            != FFI_OK) [[unlikely]]
          throw InternalError("libffi cannot describe a call through a value");
        made->prepared = true;
        found = std::move(made);
      }
      return crossed_call(*found, address, given);
    }


    Cell Machine::native_call(const Function* callee, const Cell* given)
    {
      if (callee->attrs.host) [[unlikely]]
        return host_call(callee, given);
      // A declaration may say less than the runtime takes -- a program may
      // declare `pl4g_io_submit` with five parameters -- and what it does not
      // hand over is nought here rather than whatever a register held.
      Cell args[8] = {};
      std::copy_n(given, std::min<std::size_t>(cells_in(callee), 8), args);
      std::string_view name = callee->attrs.external ? std::string_view(*callee->attrs.external) : callee->name;
      auto ptr = [&](int i) { return reinterpret_cast<void*>(args[i]); };
      if (name == "__pl4g_alloc")
        return native_alloc(*this, args);
      if (name == "__pl4g_release") {
        rt::arena_release(reinterpret_cast<rt::Arena*>(args[0]));
        return 0;
      }
      if (name == "pl4g_heap_alloc")
        return reinterpret_cast<Cell>(::pl4g_heap_alloc(args[0]));
      if (name == "pl4g_heap_free") {
        ::pl4g_heap_free(ptr(0), args[1]);
        return 0;
      }
      if (name == "pl4g_hash")
        return ::pl4g_hash(static_cast<const unsigned char*>(ptr(0)), args[1], args[2]);
      if (name == "pl4g_hash_word")
        return ::pl4g_hash_word(args[0], args[1]);
      if (name == "pl4g_hash_seed")
        return ::pl4g_hash_seed();
      if (name == "pl4g_io_submit")
        return Cell(::pl4g_io_submit(ptr(0), std::int32_t(args[1]), std::int32_t(args[2]), args[3], args[4], args[5]));
      if (name == "pl4g_io_read_kept")
        return Cell(::pl4g_io_read_kept(ptr(0), std::int32_t(args[1]), args[2]));
      if (name == "pl4g_io_taken")
        return ::pl4g_io_taken(ptr(0), std::int64_t(args[1]));
      if (name == "pl4g_io_wait")
        return Cell(::pl4g_io_wait(ptr(0), std::int64_t(args[1])));
      if (name == "pl4g_io_ready")
        return Cell(::pl4g_io_ready(ptr(0), std::int64_t(args[1])));
      if (name == "pl4g_io_step") {
        ::pl4g_io_step(ptr(0));
        return 0;
      }
      if (name == "pl4g_io_open")
        return Cell(::pl4g_io_open(std::int32_t(args[0]), std::int32_t(args[1]), args[2], args[3]));
      if (name == "pl4g_io_close")
        return Cell(::pl4g_io_close(ptr(0), std::int32_t(args[1])));
      if (name == "pl4g_io_drain") {
        ::pl4g_io_drain(ptr(0));
        return 0;
      }
      throw InternalError(std::format("a call of '{}', which nothing here provides", name));
    }


    // -- the entry point ------------------------------------------------------

    Outcome Machine::start()
    {
      // What the kernel sets a process up with: the count, the arguments, a
      // null, the environment, a null, and the auxiliary vector, of which the
      // random bytes are what the runtime reads.
      strings = options.args;
      for (const auto& one : options.environment)
        strings.push_back(one);
      std::random_device device;
      for (auto& byte : random)
        byte = static_cast<unsigned char>(device());
      initial_stack.push_back(options.args.size());
      for (std::size_t i = 0; i < options.args.size(); ++i)
        initial_stack.push_back(reinterpret_cast<Cell>(strings[i].c_str()));
      initial_stack.push_back(0);
      for (std::size_t i = options.args.size(); i < strings.size(); ++i)
        initial_stack.push_back(reinterpret_cast<Cell>(strings[i].c_str()));
      initial_stack.push_back(0);
      initial_stack.push_back(25); // AT_RANDOM
      initial_stack.push_back(reinterpret_cast<Cell>(random.data()));
      initial_stack.push_back(0); // AT_NULL
      initial_stack.push_back(0);

      try {
        // The auxiliary vector, where the program asks it: the table the
        // generated reader would have filled, filled from the C library's
        // `getauxval` instead -- the vector of the process the interpreter runs
        // in, which is the one the program runs in.  It says that it has
        // nothing only through `errno`, nought being an answer of its own.
        if (module.auxv != nullptr) {
          std::array<std::uint64_t, sema::auxv::slots> table{};
          for (std::int64_t type = 1; type < sema::auxv::slots; ++type) {
            errno = 0;
            auto value = ::getauxval(static_cast<unsigned long>(type));
            if (value != 0 || errno != ENOENT) {
              table[type] = value;
              table[0] |= std::uint64_t(1) << type;
            }
          }
          std::memcpy(places.at(module.auxv), table.data(), sizeof table);
        }
        if (module.environ != nullptr) {
          auto made = module.function("__pl4g_environ_make");
          if (made == nullptr) [[unlikely]]
            throw InternalError("the environment is named and nothing makes it");
          auto run = ::pl4g_env(initial_stack.data());
          std::vector<Cell> args{ reinterpret_cast<Cell>(run.at), run.len };
          args.resize(cells_of(made->fty->params[0]), 1);
          auto answer = call(made, args);
          std::memcpy(places.at(module.environ), answer.data(), 8);
        }
        // The `constinit` variables first, in the order they read one another,
        // as the entry point of an image calls them; then the constructors.
        for (const auto& one : module.initializers)
          call(one.func, {});
        for (auto ctor : module.ctors)
          call(ctor, {});
        // The tests this binary runs: the ones a program always runs, or what
        // a binary of tests was built to run.
        std::vector<const Function*> tests;
        if (! module.test_plan.empty())
          tests.assign(module.test_plan.begin(), module.test_plan.end());
        else
          for (auto one : module.tests)
            if (one->attrs.special == SpecialKind::test_always)
              tests.push_back(one);
        unsigned failed = 0;
        for (auto one : tests)
          if (call(one, {}).at(0) == 0) {
            write_all(options.devices.error, std::format("test {} failed\n", one->name));
            ++failed;
          }
        if (failed > 0)
          throw Exit{ status::tests_failed };
        int code = 0;
        if (module.test_plan.empty() && module.startup != nullptr) {
          std::vector<Cell> args;
          if (init_record != nullptr) {
            ::pl4g_args(initial_stack.data(), init_record + args_offset);
            args.push_back(reinterpret_cast<Cell>(init_record));
          }
          auto answer = call(module.startup, args);
          code = answer.empty() ? 0 : int(answer[0] & 0xff);
        }
        for (auto dtor : module.dtors)
          call(dtor, {});
        if (init_record != nullptr)
          ::pl4g_io_drain(init_record + ring_offset);
        return { code, 0 };
      } catch (const Exit& exit) {
        return { exit.status & 0xff, 0 };
      } catch (const Signal& signal) {
        return { 128 + signal.number, signal.number };
      }
    }

  } // anonymous namespace


  Outcome run(const ir::Module& module, const Options& options)
  {
    Machine machine(module, options);
    return machine.start();
  }


  struct Program::Placed {
    Options options;
    Machine machine;

    Placed(const ir::Module& module, Options given) : options(std::move(given)), machine(module, options) {}
  };


  Program::Program(const ir::Module& module, Options options)
    : placed(std::make_unique<Placed>(module, std::move(options)))
  {
  }


  Program::~Program() = default;


  std::byte* Program::place_of(const ir::GlobalVar* var) const
  {
    return placed->machine.place_of(var);
  }


  Outcome Program::start()
  {
    return placed->machine.start();
  }

} // namespace pl4g::interp
