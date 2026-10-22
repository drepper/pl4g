// An example of a C++ program handing PL4G the member functions of its
// objects.
//
// A function handed to the interpreter by name may come with a context, a
// `void*` it is handed first, before what the program's declaration says it
// takes (see defined.cc).  That is the way to an object's member function: the
// context is the object, and what is handed over is a static member function
// of its class, which turns the context back into the object and calls the
// member on it.  Two accounts are the program's `savings_…` and `checking_…`
// functions through the same two static members, each account its own
// context; a journal the program writes into is handed over the same way.
//
// The program moves money between the accounts and asks for more than one
// holds, which the account refuses.  The exit status is nought where the
// balances, the refusal and the journal are what they should be, and says what
// was not otherwise.
//
// Run as `pl4g-embed-members [LIBPL4G-EMBED.SO]`.
#include <cstdint>
#include <exception>
#include <print>
#include <string>
#include <string_view>
#include <vector>

#include "embed/pl4g-embed.hh"


namespace {

  // An account of whole units that refuses to go below nought.
  class Account {
  public:
    explicit Account(std::string name) : name(std::move(name)) {}

    // Put `amount` in, and answer the balance.
    std::int64_t deposit(std::int64_t amount)
    {
      ++deposits;
      held += amount;
      return held;
    }

    // Take `amount` out and answer the balance, or answer -1 and leave the
    // account as it was where it holds less.
    std::int64_t withdraw(std::int64_t amount)
    {
      if (amount > held) [[unlikely]] {
        ++refused;
        return -1;
      }
      held -= amount;
      return held;
    }

    std::int64_t balance() const noexcept { return held; }
    int deposits_made() const noexcept { return deposits; }
    int refusals() const noexcept { return refused; }

    // What the interpreter calls: `self` is the account it was handed over
    // with, and the rest is what the program's declaration says.  Static, so
    // that each is a plain function pointer, which is what the table holds.
    static std::int64_t deposit_to(void* self, std::int64_t amount)
    {
      return static_cast<Account*>(self)->deposit(amount);
    }

    static std::int64_t withdraw_from(void* self, std::int64_t amount)
    {
      return static_cast<Account*>(self)->withdraw(amount);
    }

    // Hand this account to `interpreter` as `NAME_deposit` and
    // `NAME_withdraw`, NAME being its name.
    bool hand_to(pl4g::embed::LoadedInterpreter& interpreter)
    {
      return interpreter.define(name + "_deposit", &Account::deposit_to, this)
             && interpreter.define(name + "_withdraw", &Account::withdraw_from, this);
    }

  private:
    std::string name;
    std::int64_t held = 0;
    int deposits = 0;
    int refused = 0;
  };


  // What the program says it did, a line at a time.
  class Journal {
  public:
    void note(std::string_view line) { lines.emplace_back(line); }

    const std::vector<std::string>& written() const noexcept { return lines; }

    // What the interpreter calls for the program's `note`: its `str` arrives
    // as C's text.
    static void note_in(void* self, const char* line)
    {
      static_cast<Journal*>(self)->note(line);
    }

  private:
    std::vector<std::string> lines;
  };


  // The program, in PL4G.  It sees four functions of accounts and one of a
  // journal, and nothing of objects: which account a call reaches is decided
  // by what was handed over under its name.
  constexpr std::string_view program =
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n"
    "\n"
    "@[host(\"savings_deposit\")]\n"
    "fn savings_deposit(amount: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"savings_withdraw\")]\n"
    "fn savings_withdraw(amount: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"checking_deposit\")]\n"
    "fn checking_deposit(amount: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"checking_withdraw\")]\n"
    "fn checking_withdraw(amount: i64) \N{RIGHTWARDS ARROW} i64\n"
    "\n"
    "@[host(\"journal_note\")]\n"
    "fn note(line: str)\n"
    "\n"
    "@[startup, impure]\n"
    "fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:\n"
    "    let opened: i64 = savings_deposit(100i64)\n"
    "    let started: i64 = checking_deposit(40i64)\n"
    "    note(\"opened both\")\n"
    "    \N{REFERENCE MARK} thirty moved from one to the other\n"
    "    let left: i64 = savings_withdraw(30i64)\n"
    "    let moved: i64 = checking_deposit(30i64)\n"
    "    note(\"moved thirty\")\n"
    "    \N{REFERENCE MARK} more than the account holds, which it refuses\n"
    "    if checking_withdraw(500i64) < 0i64:\n"
    "        note(\"refused five hundred\")\n"
    "    if opened = 100i64 \N{LOGICAL AND} started = 40i64 \N{LOGICAL AND} left = 70i64 "
    "\N{LOGICAL AND} moved = 70i64: 0u6 else: 1u6\n";

} // anonymous namespace


int main(int argc, char* argv[])
{
  const char* library = argc > 1 ? argv[1] : pl4g::embed::LoadedInterpreter::default_library;
  Account savings("savings"), checking("checking");
  Journal journal;
  try {
    pl4g::embed::LoadedInterpreter interpreter(library);
    if (! savings.hand_to(interpreter) || ! checking.hand_to(interpreter)
        || ! interpreter.define("journal_note", &Journal::note_in, &journal)) [[unlikely]] {
      std::print(stderr, "the interpreter did not keep what it was handed\n");
      return 3;
    }
    if (int status = interpreter.run(program, "members.pl4g"); status != 0) [[unlikely]] {
      std::print(stderr, "the program ended with status {}\n", status);
      return 4;
    }
  } catch (const std::exception& error) {
    std::print(stderr, "{}\n", error.what());
    return 2;
  }
  if (savings.balance() != 70 || checking.balance() != 70) [[unlikely]] {
    std::print(stderr, "savings holds {} and checking {}; 70 each were due\n", savings.balance(), checking.balance());
    return 5;
  }
  if (savings.deposits_made() != 1 || checking.deposits_made() != 2 || checking.refusals() != 1
      || savings.refusals() != 0) [[unlikely]] {
    std::print(stderr, "the calls reached the wrong account: {} and {} deposits, {} and {} refusals\n",
               savings.deposits_made(), checking.deposits_made(), savings.refusals(), checking.refusals());
    return 6;
  }
  if (journal.written() != std::vector<std::string>{ "opened both", "moved thirty", "refused five hundred" })
      [[unlikely]] {
    std::print(stderr, "the journal holds {} lines, not the three due\n", journal.written().size());
    return 7;
  }
  std::print("savings {} and checking {} through one pair of static members; {} lines in the journal\n",
             savings.balance(), checking.balance(), journal.written().size());
  return 0;
}
