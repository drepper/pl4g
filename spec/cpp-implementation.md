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
CMakeLists.txt              the project: the library, the driver, the tests, the sanitizer and fuzzing builds
src/lib/                    the library, libpl4g.a
    source/                 source files, spans and positions
    diag/                   the diagnostic engine and the generated catalog
    front/                  tokens, lexer, syntax tree, parser, macro expander
    sema/                   the checker, which lowers to the IR
    ir/                     types, values, instructions, builder, verifier, printer
    interp/                 the interpreter of the IR
    rt/                     the runtime the language's `std` reaches: io_uring, the heap
    repl/                   the REPL
    ts/                     the tree-sitter service
    lsp/                    the LSP service
src/repl/main.cc            the driver: the stand-alone REPL program
src/fuzz/                   fuzzing entry points, a replay driver, and the seed corpora
src/tools/                  pl4g-gen-diag (the catalog from share/diagnostics.json), pl4g-lexdump
src/test/                   the checks ctest runs that compare with pypl4g, and the fuzzing run;
                            irjson.py writes the whole of what pypl4g compiled as JSON
bin/pl4g-interp-tests       runs tests/language through the interpreter
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

Phases and status
-----------------

Each phase is committed when its own checks pass; the status column is kept current.

| Phase | What | Checked by | Status |
|---|---|---|---|
| 1 | CMake, sources, diagnostics, tokens, lexer | every test program lexes; fuzzed | done: all 980 files and the edge corpus lex as `pypl4g` lexes them, plainly and lazily; AFL++ in ctest |
| 2 | Syntax tree and parser | every test program parses as `pypl4g` parses it | done: every file's tree, compared field by field (a dumper written by reflection), as a program's file and as a module's; no difference on 3527 inputs AFL++ found |
| 3 | IR: types, values, instructions, layout, printer, the JSON exchange | every program's IR, read from what pypl4g compiled, prints as pypl4g prints it and is laid out alike | done; the builder and the verifier go with the checker, which is what uses them |
| 4 | Interpreter of the IR, and the runtime: heap, io_uring | IR pypl4g compiled, read as JSON, runs and exits as the test says | done: 464 of 464, and under the address and undefined-behaviour sanitizers all but the one test that needs a data limit |
| 5 | Checker, part one: definitions, types, expressions, statements, calls | the first test programs run | |
| 6 | Checker, part two: everything else the language has | every test program runs or is refused as it should | |
| 7 | Macro expander | the macro tests | |
| 8 | REPL, tree-sitter service, LSP service | their own tests | |
| 9 | Sanitizer and fuzzing builds as part of testing | `ctest` | |
