// Walking the stack, so that a fault says who called.
#include "target/backtrace.hh"
#include "target/emit.hh"

#include "target/frames.hh"


namespace pl4g::target {

  namespace {

    // Where each of a row's fields is.
    constexpr std::int64_t row_start = 0;
    constexpr std::int64_t row_return = 8;
    constexpr std::int64_t row_caller = 12;
    constexpr std::int64_t row_line = 16;
    constexpr std::int64_t row_length = 20;

    mc::MCMem at(mc::Reg base, std::int64_t disp, int bits)
    {
      return memory(base, disp, bits);
    }

    mc::MCImm number(std::int64_t value)
    {
      return mc::MCImm{ value, 32, false };
    }

    // Read one of a row's four-byte fields into the whole of `dst`, without a
    // sign, which makes the same bytes the same number on all three.
    void four(mc::Assembler& asm_, mc::Reg dst, mc::Reg row, std::int64_t offset)
    {
      asm_.widen(dst, at(row, offset, 32), 32, false);
    }

  } // anonymous namespace


  void emit_backtrace(mc::Assembler& asm_, const WalkABI& abi, std::span<const mc::Reg> arguments)
  {
    auto where = abi.kept[0], stack = abi.kept[1], row = abi.kept[2], count = abi.kept[3];
    auto first = abi.scratch[0], second = abi.scratch[1];
    asm_.begin_function(std::string(backtrace_symbol), false);
    asm_.loadreg(where, asm_.reg(arguments[0]));
    asm_.loadreg(stack, asm_.reg(arguments[1]));
    asm_.op(mc::ops::xor_, count, { asm_.reg(count), asm_.reg(count) });

    auto frame = asm_.reserve_label("frame");
    auto search = asm_.reserve_label("search");
    auto found = asm_.reserve_label("found");
    auto done = asm_.reserve_label("done");
    auto written = asm_.reserve_label("written");

    // -- one frame ------------------------------------------------------------
    asm_.falls_through(frame);
    asm_.block(frame);
    asm_.address(row, std::string(frames_symbol));
    asm_.falls_through(search);
    asm_.block(search);
    // The sentinel is the row with no line, and it is the last.
    four(asm_, first, row, row_length);
    asm_.branch(mc::Condition::eq, asm_.reg(first), number(0), done);
    // Before the first function: not an address this program emitted.
    asm_.loadreg(second, at(row, row_start, 64));
    asm_.branch(mc::Condition::ult, asm_.reg(where), asm_.reg(second), done);
    // And in this row's function, if the next row begins above it.
    asm_.loadreg(second, at(row, std::int64_t(row_bytes) + row_start, 64));
    asm_.branch(mc::Condition::ult, asm_.reg(where), asm_.reg(second), found);
    asm_.op(mc::ops::plus, row, { asm_.reg(row), number(std::int64_t(row_bytes)) });
    asm_.jump(search);

    // -- what it says, and where it goes next ---------------------------------
    asm_.block(found);
    // The innermost frame is where the fault was, which the message said.
    asm_.branch(mc::Condition::eq, asm_.reg(count), number(0), written);
    auto third = abi.arguments[2];
    asm_.loadreg(third, asm_.reg(first));
    asm_.address(abi.arguments[1], std::string(frames_symbol));
    four(asm_, first, row, row_line);
    asm_.op(mc::ops::plus, abi.arguments[1], { asm_.reg(abi.arguments[1]), asm_.reg(first) });
    asm_.loadreg(abi.arguments[0], asm_.imm(standard_error, 32, false));
    asm_.loadreg(abi.number, asm_.imm(abi.write, 32, false));
    abi.enter(asm_);
    asm_.falls_through(written);
    asm_.block(written);
    // Where the caller is, and where its stack is.
    four(asm_, first, row, row_return);
    asm_.branch(mc::Condition::eq, asm_.reg(first), number(no_return_address), done);
    asm_.op(mc::ops::plus, first, { asm_.reg(first), asm_.reg(stack) });
    four(asm_, second, row, row_caller);
    asm_.op(mc::ops::plus, stack, { asm_.reg(stack), asm_.reg(second) });
    asm_.loadreg(where, at(first, 0, 64));
    asm_.op(mc::ops::plus, count, { asm_.reg(count), number(1) });
    asm_.branch(mc::Condition::ult, asm_.reg(count), number(walk_limit), frame);
    asm_.falls_through(done);
    asm_.block(done);
    asm_.ret();
    asm_.end_function();
  }

} // namespace pl4g::target
