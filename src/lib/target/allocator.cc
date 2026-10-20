// The allocator, and the arenas it hands storage out of.
#include "target/allocator.hh"
#include "target/emit.hh"

#include "ir/builder.hh"
#include "target/statuses.hh"


namespace pl4g::target {

  namespace {

    // Where the three things the slow path keeps across the system call go.
    constexpr std::int64_t kept_arena = 0;
    constexpr std::int64_t kept_size = word;
    constexpr std::int64_t kept_bytes = 2 * word;
    constexpr std::int64_t kept_room = 4 * word;

    mc::MCMem at(mc::Reg base, std::int64_t disp)
    {
      return memory(base, disp, 64);
    }

    mc::MCImm number(std::int64_t value)
    {
      return mc::MCImm{ value, 32, false };
    }

    // Bring `value` up to the next multiple of `alignment`.
    void round_up(mc::Assembler& asm_, mc::Reg value, std::int64_t alignment, mc::Reg scratch)
    {
      asm_.loadreg(scratch, number(alignment - 1));
      asm_.op(mc::ops::plus, value, { asm_.reg(value), asm_.reg(scratch) });
      asm_.op(mc::ops::not_, scratch, { asm_.reg(scratch) });
      asm_.op(mc::ops::and_, value, { asm_.reg(value), asm_.reg(scratch) });
    }

    // The allocation itself, which is an addition and a comparison.
    void emit_alloc(mc::Assembler& asm_, const AllocatorRegs& regs)
    {
      auto place = regs.scratch[0], limit = regs.scratch[1], room = regs.scratch[2];
      asm_.begin_function(std::string(alloc_symbol), false);
      round_up(asm_, regs.size, grain, room);
      asm_.loadreg(place, at(regs.arena, arena_next));
      asm_.loadreg(limit, at(regs.arena, arena_limit));
      asm_.op(mc::ops::plus, room, { asm_.reg(place), asm_.reg(regs.size) });
      // Past the end of this chunk: the grower answers in this one's place.
      auto short_ = asm_.reserve_label("no.room");
      asm_.branch(mc::Condition::ugt, asm_.reg(room), asm_.reg(limit), short_);
      asm_.store(at(regs.arena, arena_next), asm_.reg(room));
      asm_.loadreg(regs.answer, asm_.reg(place));
      asm_.ret();
      asm_.block(short_);
      asm_.tail_jump(std::string(grow_symbol));
      asm_.end_function();
    }

    // The path that asks the system for another chunk and allocates from it.
    void emit_grow(mc::Assembler& asm_, const SyscallABI& abi, const AllocatorRegs& regs,
                   std::string_view abort_symbol, std::string_view message_symbol)
    {
      auto asked = regs.scratch[0], other = regs.scratch[1], third = regs.scratch[2];
      asm_.begin_function(std::string(grow_symbol), false);
      asm_.frame(kept_room);
      asm_.put_aside(kept_arena, regs.arena);
      asm_.put_aside(kept_size, regs.size);
      // The allocation and a chunk's header, brought up to the smallest chunk.
      asm_.loadreg(asked, asm_.reg(regs.size));
      asm_.op(mc::ops::plus, asked, { asm_.reg(asked), number(chunk_body) });
      asm_.loadreg(other, number(chunk_minimum));
      auto enough = asm_.reserve_label("enough");
      asm_.branch(mc::Condition::uge, asm_.reg(asked), asm_.reg(other), enough);
      asm_.loadreg(asked, asm_.reg(other));
      asm_.block(enough);
      asm_.put_aside(kept_bytes, asked);
      // mmap(0, bytes, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0),
      // the descriptor all ones made from nought.
      auto first = abi.arguments[0], second = abi.arguments[1], third_arg = abi.arguments[2];
      auto fourth = abi.arguments[3], fifth = abi.arguments[4], sixth = abi.arguments[5];
      asm_.loadreg(second, asm_.reg(asked));
      asm_.loadreg(first, number(0));
      asm_.loadreg(third_arg, number(prot_read_write));
      asm_.loadreg(fourth, number(map_private_anonymous));
      asm_.loadreg(fifth, number(0));
      asm_.op(mc::ops::not_, fifth, { asm_.reg(fifth) });
      asm_.loadreg(sixth, number(0));
      asm_.loadreg(abi.number, number(abi.mmap));
      abi.enter(asm_);
      // A call that failed answers with one of the largest values there are.
      auto base = regs.scratch[0];
      asm_.loadreg(base, asm_.reg(abi.answer));
      asm_.loadreg(other, number(error_window));
      asm_.op(mc::ops::not_, other, { asm_.reg(other) });
      auto got_it = asm_.reserve_label("mapped");
      asm_.branch(mc::Condition::ule, asm_.reg(base), asm_.reg(other), got_it);
      // Nothing comes back from here, so the room is given up first.
      asm_.unframe(kept_room);
      asm_.address(abi.arguments[0], std::string(message_symbol));
      asm_.loadreg(abi.arguments[1], number(std::int64_t(out_of_memory.size())));
      asm_.loadreg(abi.arguments[2], number(statuses::OUT_OF_MEMORY));
      asm_.tail_jump(std::string(abort_symbol));
      asm_.block(got_it);
      asm_.take_back(regs.arena, kept_arena);
      asm_.take_back(regs.size, kept_size);
      asm_.take_back(other, kept_bytes);
      // Link the chunk on, so that giving the arena back gives this back too.
      asm_.loadreg(third, at(regs.arena, arena_chunk));
      asm_.store(at(base, chunk_next), asm_.reg(third));
      asm_.store(at(base, chunk_bytes), asm_.reg(other));
      asm_.store(at(regs.arena, arena_chunk), asm_.reg(base));
      // What is left of the chunk after this allocation.
      asm_.op(mc::ops::plus, third, { asm_.reg(base), asm_.reg(other) });
      asm_.store(at(regs.arena, arena_limit), asm_.reg(third));
      asm_.op(mc::ops::plus, base, { asm_.reg(base), number(chunk_body) });
      asm_.op(mc::ops::plus, third, { asm_.reg(base), asm_.reg(regs.size) });
      asm_.store(at(regs.arena, arena_next), asm_.reg(third));
      // Last, because where the answer goes is where the arena arrived.
      asm_.loadreg(regs.answer, asm_.reg(base));
      asm_.unframe(kept_room);
      asm_.ret();
      asm_.end_function();
    }

    // The giving back of a whole arena.
    void emit_release(mc::Assembler& asm_, const SyscallABI& abi, const AllocatorRegs& regs)
    {
      auto chunk = regs.scratch[0], other = regs.scratch[1], third = regs.scratch[2];
      asm_.begin_function(std::string(release_symbol), false);
      asm_.frame(kept_room);
      // Empty it before anything is unmapped.
      asm_.loadreg(chunk, at(regs.arena, arena_chunk));
      asm_.loadreg(other, number(0));
      asm_.store(at(regs.arena, arena_next), asm_.reg(other));
      asm_.store(at(regs.arena, arena_limit), asm_.reg(other));
      asm_.store(at(regs.arena, arena_chunk), asm_.reg(other));
      auto again = asm_.reserve_label("next.chunk");
      auto done = asm_.reserve_label("all.gone");
      asm_.jump(again);
      asm_.block(again);
      asm_.loadreg(other, number(0));
      asm_.branch(mc::Condition::eq, asm_.reg(chunk), asm_.reg(other), done);
      // What comes after it, read before it goes and kept across the call.
      asm_.loadreg(third, at(chunk, chunk_next));
      asm_.put_aside(kept_size, third);
      asm_.loadreg(other, at(chunk, chunk_bytes));
      asm_.loadreg(abi.arguments[0], asm_.reg(chunk));
      asm_.loadreg(abi.arguments[1], asm_.reg(other));
      asm_.loadreg(abi.number, number(abi.munmap));
      abi.enter(asm_);
      asm_.take_back(chunk, kept_size);
      asm_.jump(again);
      asm_.block(done);
      asm_.unframe(kept_room);
      asm_.ret();
      asm_.end_function();
    }

  } // anonymous namespace


  bool allocator_wanted_by(const ir::Module& module)
  {
    for (const auto& [_, func] : module.functions)
      if (func->is_declaration()) {
        auto name = ir::symbol_name(*func);
        if (name == alloc_symbol || name == release_symbol)
          return true;
      }
    return false;
  }


  void emit_allocator(mc::Assembler& asm_, const SyscallABI& abi, const AllocatorRegs& regs,
                      std::string_view abort_symbol, std::string_view message_symbol)
  {
    emit_alloc(asm_, regs);
    emit_grow(asm_, abi, regs, abort_symbol, message_symbol);
    emit_release(asm_, abi, regs);
  }

} // namespace pl4g::target
