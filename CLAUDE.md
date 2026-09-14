PL4G (Programming Language for Generators)
======================================

This projects creates a compiler for a programming language that is specified in [spec/spec.md](this) document.

Details of the specific implementation are in [spec/details.md](this file).

Separate generated files in subdirectories:

- `pypl4g` for the Python-based compiler implementation
- `bin` for the binaries/scripts implementing the compiler and other tools
- `tests/language` for language-conformance tests
- `tests/compiler` for compiler-specific tests
- `src` for the sources of the actual compiler.
- `spec` for the Markdown documentation of the language specification, the compiler documentations (Python and final), and the logs of the
  design and implementations
- `share` for data files such as the error message list etc which might have to be shared with other implementations as well


How to proceed
--------------

The process to develop the language is methodical. Individual features are designed, the specification is written (language and
runtime), then the implementation and testing happens. Test programs are added to the project to serve as regression tests.

All decisions about the language design, internals of the compiler, or implementation details of the runtime support are to be
confirmed with the user first. For this, provide comparisons with other implementations of languages like C, C++, D, Go, Rust,
Odin, Zig, APL, BQN, UIUA, LISP, Scheme, Python, Haskell, Wolfram. All proposals/possibilities, decisions, comparisons are recorded
in the specification document.

The different architectures can be tested because the QEmu infrastructure available on the host system allows executing binaries
compiled for all the target architectures as long as they use the Linux kernel interface.

`python -m pytest tests` runs the suite over every core, `pytest-xdist` being a dependency and `-n auto` the default; `-n0` runs
it in one process where a debugger or a test's own output wants that.

A change to the syntax of the language changes `tree-sitter-pl4g/grammar.js` in the same commit, and `tree-sitter generate` is run
so that the committed parser matches.  A test requires the grammar and the compiler to agree on every program in the test suite, so
a syntax added to one and not the other fails the suite.

Every change that lands is timed.  Run `bin/pl4g-timing` after committing it, which appends a column for that commit to
`spec/timings.md`, and report what it shows.  Add a sample to the list in the script whenever a feature lands that could plausibly
cost time; samples are never removed, so that an older column stays meaningful.
