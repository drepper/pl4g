The C++ Implementation
======================

A second implementation of the language, in C++26, that starts as an interpreter and is laid out to become the compiler.  It is
written against the same specification and the same tests as `pypl4g`, and is held to the same answers: a program that runs exits
as its test says, and one that must be refused is refused with the diagnostics its test names.

Decisions
---------

Made with the user on 2026-10-20, recorded in [decisions.md](decisions.md):

- **A front end and the IR, and an interpreter of the IR.**  Lexer, parser, macro expander and checker lower a program to the same
  SSA form with block parameters `pypl4g` uses; the interpreter executes that.  The compiler later adds the optimizer's passes and
  the back ends to the same IR, so everything up to it is shared.
- **The same exits and the same diagnostics.**  The checker implements the language's rules in full; its diagnostics are the
  catalog in `share/diagnostics.json`, from which the C++ identifiers are generated.  Tests that are about a target -- ELF, QEMU,
  golden assembly -- do not apply to an interpreter.
- **Real io_uring.**  The runtime's functions -- `pl4g_io_submit`, `pl4g_io_wait`, the heap, ... -- are implemented natively against a
  ring on the host, set up with the raw system calls (liburing is not installed), so requests are in flight as they are for a
  compiled program.  `⎕syscall` is a real system call.
- **simdjson** reads what an LSP client sends, and a writer of our own answers.

Requirements the user gave
--------------------------

- Mainly a library (an archive), holding the REPL, the tree-sitter service and the LSP service as well.  A small driver linked with
  the library makes a stand-alone REPL program.
- C++26, with what GCC 16 provides: contracts (`-fcontracts`), reflection (`-freflection`), `std::print`, coroutines where they
  replace building large intermediate results.  The rules of the user's `CLAUDE.md` for C++ hold: `.cc`/`.hh`, anonymous namespaces
  rather than `static`, `::` before third-party names, doxygen comments with `//`, error branches `[[unlikely]]`, `nullptr` compared
  with, member initializers in the definition, `\N{…}` for characters outside ASCII, argp for command lines.
- Sanitizers and a fuzzer are checked as part of the final testing, with targets for each in CMake.
- **Descriptors are the caller's.**  The library takes the descriptors a program's devices are on from whoever embeds it, rather than
  assuming 0, 1 and 2.
- Understandable first, fast second.

Toolchain
---------

Building: `cmake -S . -B build/cpp -G Ninja && cmake --build build/cpp && (cd build/cpp && ctest)`.  `ctest -L fuzz` runs only the
fuzzing, for `PL4G_FUZZ_SECONDS` (30 by default) per entry point; `ctest -LE fuzz` everything else.  The characters' classes are
libunistring's (1.1, Unicode 15; Python has 16, which differs only in the letters of the scripts 16 added).

GCC 16.2 is the compiler: it has contracts, reflection and the sanitizers.  Clang 22 has neither contracts nor reflection, so its
libFuzzer is not used; fuzzing is AFL++ with its GCC plugin (`afl-g++-fast`), over entry points written as libFuzzer's
`LLVMFuzzerTestOneInput`, so either can drive them.  tree-sitter 0.26 is installed as a library, and the grammar's generated parser is
compiled into ours.

Layout
------

```
CMakeLists.txt              the project: the library, the drivers, the tests, the sanitizer and fuzzing builds
src/lib/                    the library, libpl4g.a
    source/                 source files, spans and positions
    diag/                   the diagnostic engine, the generated catalog, the text renderer and its colours
    front/                  tokens, lexer, syntax tree, parser, documentation comments, macro expander and
                            the machine that runs a macro
    sema/                   the checker, which lowers to the IR, and its helpers: tables, strings, lists,
                            liveness, last uses, attributes, modules, notes
    comptime/               what runs while compiling: the evaluator and the build plan
    ir/                     types, values, instructions, builder, verifier, printer, layout, the JSON exchange
    opt/                    the passes every build runs
    target/                 what the checker asks of the targets: statuses, system calls, conventions
    interp/                 the interpreter of the IR
    rt/                     the runtime the language's `std` reaches: io_uring, the heap
    driver/                 the stages up to the IR: read, parse, expand, check
    repl/                   running a program from its source, and the interactive session
    ts/                     the tree-sitter service: highlighting and folds from the grammar
    lsp/                    the LSP service
src/repl/main.cc            pl4g-repl: the stand-alone interpreter and session
src/lsp/main.cc             pl4g-lsp: the stand-alone language server
src/fuzz/                   fuzzing entry points (lexer, parser, checker), a replay driver, the seed corpora
src/tools/                  pl4g-gen-diag (the catalog from share/diagnostics.json), and the dumpers the comparisons
                            run: pl4g-lexdump, -astdump, -irdump, -runjson, -checkdump
src/test/                   the checks ctest runs that compare with pypl4g, the session and the fuzzing runs;
                            irjson.py and checkout.py write what pypl4g made
bin/pl4g-interp-tests       runs tests/language through the interpreter, from source
```
The interpreter
---------------

What it runs is the module as the back ends receive it, after the passes that are not optimizations: generators split into
frames, large answers through the caller's storage.  Every instruction is then one a machine runs, and what the interpreter does
for each is what the x86-64 back end emits -- the same faults with the same statuses, the same layout of every value in memory,
which is the host's own.  Each function is prepared once into a list of operations over numbered cells, one per part of a value;
calls are activations on a stack of its own, so a program's recursion is measured against the stack size it was built with and
not the interpreter's.  `runtime/io.c` is compiled into the library and called as it is, so a program's input and output go
through the same ring; the arena allocator the back ends emit as machine code is the same few lines of C++.  The record the
startup function is handed carries the descriptors the embedder chose (`interp::Devices`).

The checker
-----------

`sema/` is pypl4g's `sema/check.py` ported method for method, in the order check.py has them, so that what it makes of a program is
instruction for instruction what pypl4g makes and what it says is word for word what pypl4g says.  `check.hh` declares the class:
its state, as `__init__` has it, and then every method, the declarations of each part of check.py in a header of their own
(`check-entry.hh` … `check-builtins.hh`) defined in the `.cc` of the same name; every declaration names the line of check.py it
comes from.  `check-types.hh` holds the records beside the checker (`Local`, `Borrow`, `NamedType`, `Generator`, …),
`check-helpers.hh` the module-level functions, and `held.hh` what a Python variable annotated `object` holds -- a variant of the
checker's things, compared by identity.

What the port keeps of Python's semantics, beside the types (above): a dict iterates in the order things were put in
(`util/ordered.hh`); `id(x)` is the address; a set of names or of identities iterates in an order nothing can depend on, since
Python's string hashes are randomized and its identities are addresses; the order Python evaluates the arguments of a call in is
kept wherever two of them make IR or a diagnostic, C++ leaving it unspecified.

The interactive session
-----------------------

`pl4g-repl` with no file is the session the user chose (decisions.md, 2026-10-20): a definition is kept, a later one of a name taking
the place of the earlier; statements run once in a function made for them; a name they bind at their top stays a variable of the
session holding what it held; the value of the last one, where it is one, is shown with its type; `@help`, `@type`, `@load`,
`@defs`, `@vars`, `@reset` and `@quit` are the session's own commands.

Each input is checked as one file, since a file's names are its own: the function the input runs in first -- what was typed
indented beneath its head, the file shown so that a diagnostic names the lines and columns that were typed -- then `std`, the
session's variables and the definitions kept.  Statements that bind something or come to a value are checked twice: the first
check is the one reported and says what the types are; the second, quiet, declares a variable of the session for each to be put
in.  Three things the checker does differently for the session, and only these (`sema/session.hh`): a variable of the session is
given no value where it is declared, a name the input binds at its top is the session's to keep and so neither unread nor
needlessly `mut`, and the last statement's value is kept rather than refused.  Before a program starts its variables of the session
are given the bytes they held when the last input ended, and after it the bytes are taken back; the programs that ran are kept,
since what a variable holds may point into the room one of them placed or into the heap, which nothing gives back.  A value is
shown as the language writes a literal of it (`repl/show.cc`).

The language server and the tree-sitter service
-----------------------------------------------

`lsp/` is pypl4g's `lsp/` ported, on descriptors the caller hands it, simdjson reading and a writer of its own; it answers every
conversation of `tests/compiler/test_lsp.py` as pypl4g's does, except saving, where pypl4g also generates code and the C++ side has
no back end yet.

`ts/` builds the grammar's generated parser and its queries (by `#embed`) into the library: what each piece of a text is and where
it folds.  The text renderer colours a snippet by it where colour is asked for, as pypl4g's does through the Python binding; every
test program's diagnostics agree with pypl4g's in colour as well.  The queries' one `#match?` is read by a matcher of literal
prefixes and suffixes, which is all the queries ask.

Which type is which
-------------------

pypl4g compares types both ways: `==`, by structure, and `is`, by identity, and the two differ where it made equal objects in two
places -- its TypeContext holds one object per structure, and `parts_of` answers with pointers of a cache of its own, equal to the
context's and not them.  Some answers depend on which question was asked (whether `leanvalues` folds an extract, whether the
checker inserts a cast), so the C++ types keep the same distinction: the factories intern by structure as the context does,
`element_pointer` is the other cache, `same` is `==` and pointer equality is `is`.  The JSON says for every type which of them
made it, so that a module read back has the same objects pypl4g's had.

Phases and status
-----------------

Each phase is committed when its own checks pass; the status column is kept current.

| Phase | What | Checked by | Status |
|---|---|---|---|
| 1 | CMake, sources, diagnostics, tokens, lexer | every test program lexes; fuzzed | done: all 980 files and the edge corpus lex as `pypl4g` lexes them, plainly and lazily; AFL++ in ctest |
| 2 | Syntax tree and parser | every test program parses as `pypl4g` parses it | done: every file's tree, compared field by field (a dumper written by reflection), as a program's file and as a module's; no difference on 3527 inputs AFL++ found |
| 3 | IR: types, values, instructions, layout, printer, the JSON exchange | every program's IR, read from what pypl4g compiled, prints as pypl4g prints it and is laid out alike | done; the builder and the verifier go with the checker, which is what uses them |
| 4 | Interpreter of the IR, and the runtime: heap, io_uring | IR pypl4g compiled, read as JSON, runs and exits as the test says | done: 464 of 464, and under the address and undefined-behaviour sanitizers all but the one test that needs a data limit |
| 5 | Checker, part one: definitions, types, expressions, statements, calls | the first test programs run | done with phase 6 |
| 6 | Checker, part two: everything else the language has | every test program runs or is refused as it should | done: the same diagnostics, word for word and in colour, and the same IR before any pass, on 947 of the 948 test programs; the one pypl4g fails (TODO-pypl4g.md) is held to its test; every one of the 464 that run runs from source as its test says; all of it under the sanitizers too |
| 7 | Macro expander | the macro tests | done, within phase 6's numbers |
| 8 | REPL, tree-sitter service, LSP service | their own tests | done: typed sessions, the colours of every diagnostic, and the LSP conversations of test_lsp.py |
| 9 | Sanitizer and fuzzing builds as part of testing | `ctest` | done: the comparisons, the source runs and the sessions run sanitized as well; the lexer, the parser and the checker are fuzzed |
