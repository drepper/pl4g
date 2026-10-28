// The exit statuses the runtime reserves, and one per kind of stop.
//
// A port of pypl4g's target/statuses.py, which says why each is what it is:
// 0-63 are the program's own, 64-78 `<sysexits.h>`'s, 79-127 the runtime's and
// 128-255 a signal the program really died of.
#pragma once


namespace pl4g::target::statuses {

  // What `<sysexits.h>` has taken, which is `EX__BASE` through `EX__MAX`.
  inline constexpr int SYSEXITS_FIRST = 64;
  inline constexpr int SYSEXITS_LAST = 78;

  // The lowest and highest of the reserved range.
  inline constexpr int FIRST = SYSEXITS_LAST + 1;
  inline constexpr int LAST = 127;

  // A stop the runtime has no more particular number for.
  inline constexpr int GENERAL = FIRST;
  // An answer that will not fit the type it was asked for.
  inline constexpr int OVERFLOW = FIRST + 1;
  // A floating-point operation that came to an infinity or to not-a-number.
  inline constexpr int NO_ANSWER = FIRST + 2;
  // An index or a slice outside what it names.
  inline constexpr int OUT_OF_RANGE = FIRST + 3;
  // A number turned into a code point that is not one.
  inline constexpr int NOT_A_CODE_POINT = FIRST + 4;
  // A walk used after it has ended, or moved off either end of what it walks.
  inline constexpr int WALK_ENDED = FIRST + 5;
  // The system would give no more memory.
  inline constexpr int OUT_OF_MEMORY = FIRST + 6;
  // The program ran off the bottom of its stack.
  inline constexpr int STACK_OVERFLOW = FIRST + 7;
  // The processor is not the one the program was built for.
  inline constexpr int WRONG_PROCESSOR = FIRST + 8;
  // A test the binary runs answered that it did not pass.
  inline constexpr int TESTS_FAILED = FIRST + 9;
  // A condition written in a signature that did not hold on the way in.
  inline constexpr int PRE_CONDITION = FIRST + 10;
  // And one that did not hold on the way out.
  inline constexpr int POST_CONDITION = FIRST + 11;
  // A range whose step was worked out while running and came to nought.
  inline constexpr int ZERO_STEP = FIRST + 12;
  // A function the host defines whose symbol the host does not.
  inline constexpr int HOST_SYMBOL_MISSING = FIRST + 13;
  // The host asked the interpreter to stop the program, from another thread.
  inline constexpr int STOPPED_BY_HOST = FIRST + 14;
  // A function the host defines answered text that is not well-formed UTF-8.
  inline constexpr int HOST_TEXT_NOT_UTF8 = FIRST + 15;

} // namespace pl4g::target::statuses
