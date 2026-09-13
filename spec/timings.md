Compiler Timings
================

How long the compiler takes on each sample program, in milliseconds, the
best of five runs.  One row per commit and one column per program: the
commits go on for ever and the programs do not, so the tables grow down
rather than across, and the programs are split into groups by what would
move them.  A blank means the sample did not exist yet at that commit.

Measured on one machine, so the numbers are worth comparing with each
other and with nobody else's.  A hash ending in `+` was measured with
changes not yet committed.  Written by `bin/pl4g-timing`.

Work
----

The sum of the compiler's own stages, which is what a change to the
compiler moves.

### The smallest programs

What a compilation costs when there is next to nothing to compile.  These move when the driver, the lexer or
the parser does, and they are the floor everything else is measured against.

| commit | exit0 | semicolon-separates-statements | digit-separators | boolean-values |
|---|---|---|---|---|
| `eee64a3` | 0.90 |  | 1.46 | 1.25 |
| `af657ad` | 0.89 |  | 1.47 | 1.24 |
| `2f564cb` | 0.88 |  | 1.49 | 1.23 |
| `37dbb28` | 0.89 |  | 1.63 | 1.27 |
| `1c4ae78` | 1.06 |  | 1.64 | 1.39 |
| `f391839` | 1.05 |  | 1.60 | 1.45 |
| `04c20c9` | 1.07 |  | 1.62 | 1.38 |
| `3020cf6` | 0.93 |  | 1.51 | 1.40 |
| `9bcaf84` | 0.99 |  | 1.55 | 1.31 |
| `40c35bb` | 1.00 |  | 1.52 | 1.29 |
| `9437d8a` | 1.00 |  | 1.64 | 1.55 |
| `d0c8cf5` | 1.05 |  | 1.60 | 1.43 |
| `73ce857` | 1.01 |  | 1.69 | 1.44 |
| `ed1c028` | 1.06 |  | 1.64 | 1.45 |
| `1269bd5` | 1.13 |  | 1.65 | 1.41 |
| `db0b436` | 1.13 |  | 1.65 | 1.40 |
| `25bb4e0` | 1.10 |  | 1.70 | 1.45 |
| `baf1f3c` | 1.08 |  | 1.72 | 1.47 |
| `74ac227` | 1.06 |  | 1.66 | 1.42 |
| `c0f29f2` | 1.05 |  | 1.65 | 1.37 |
| `5c3aec4` | 1.15 | 1.87 | 1.86 | 1.55 |
| `f7ba2fc` | 1.05 | 1.68 | 1.73 | 1.48 |
| `3e9eb27` | 1.20 | 1.70 | 1.56 | 1.35 |
| `0990c74` | 1.05 | 1.69 | 1.56 | 1.37 |
| `05d8da1` | 0.94 | 1.58 | 1.45 | 1.23 |
| `03887ff` | 1.15 | 1.91 | 1.76 | 1.57 |
| `87c066b` | 1.25 | 2.04 | 1.81 | 1.51 |
| `ca395a6` | 0.96 | 2.07 | 1.53 | 1.29 |
| `230b8a7` | 1.05 | 1.87 | 1.71 | 1.42 |
| `5706c5f` | 1.05 | 1.95 | 1.78 | 1.46 |
| `41f13c3` | 1.19 | 2.04 | 1.87 | 1.61 |
| `e7fb804` | 1.23 | 2.04 | 1.89 | 1.59 |
| `d0caa00` | 1.14 | 1.90 | 1.98 | 1.46 |
| `df0bfc5` | 1.14 | 1.88 | 1.89 | 1.47 |

### Variables and memory

Reading and writing variables, including one of every width.  These move when the loads and stores of a
backend do.

| commit | global-variable | assign-widths | boolean-in-memory |
|---|---|---|---|
| `eee64a3` | 1.05 | 1.50 |  |
| `af657ad` | 1.10 | 1.49 |  |
| `2f564cb` | 1.04 | 1.50 |  |
| `37dbb28` | 1.05 | 1.64 |  |
| `1c4ae78` | 1.22 | 1.63 |  |
| `f391839` | 1.22 | 1.63 |  |
| `04c20c9` | 1.20 | 1.66 |  |
| `3020cf6` | 1.15 | 1.55 |  |
| `9bcaf84` | 1.16 | 1.60 |  |
| `40c35bb` | 1.18 | 1.62 |  |
| `9437d8a` | 1.22 | 1.68 |  |
| `d0c8cf5` | 1.13 | 1.59 |  |
| `73ce857` | 1.19 | 1.65 |  |
| `ed1c028` | 1.22 | 1.73 | 1.43 |
| `1269bd5` | 1.25 | 1.67 | 1.53 |
| `db0b436` | 1.28 | 1.74 | 1.54 |
| `25bb4e0` | 1.26 | 1.68 | 1.47 |
| `baf1f3c` | 1.22 | 1.67 | 1.54 |
| `74ac227` | 1.23 | 1.66 | 1.43 |
| `c0f29f2` | 1.21 | 1.69 | 1.46 |
| `5c3aec4` | 1.35 | 1.83 | 1.61 |
| `f7ba2fc` | 1.23 | 1.71 | 1.44 |
| `3e9eb27` | 1.18 | 1.58 | 1.37 |
| `0990c74` | 1.20 | 1.59 | 1.39 |
| `05d8da1` | 1.05 | 1.46 | 1.29 |
| `03887ff` | 1.36 | 1.81 | 1.60 |
| `87c066b` | 1.54 | 2.02 | 1.59 |
| `ca395a6` | 1.14 | 1.55 | 1.67 |
| `230b8a7` | 1.27 | 1.74 | 1.49 |
| `5706c5f` | 1.24 | 1.76 | 1.52 |
| `41f13c3` | 1.36 | 1.92 | 1.67 |
| `e7fb804` | 1.37 | 1.87 | 1.73 |
| `d0caa00` | 1.34 | 1.82 | 1.53 |
| `df0bfc5` | 1.27 | 1.76 | 1.58 |

### Register pressure

More values wanted at once than a backend can hold in registers.  These move when the register allocator does,
and they are the only ones that reach the frame.

| commit | many-values-at-once | spill-to-the-frame |
|---|---|---|
| `eee64a3` | 1.73 |  |
| `af657ad` | 1.74 |  |
| `2f564cb` | 1.87 |  |
| `37dbb28` | 1.90 |  |
| `1c4ae78` | 1.90 |  |
| `f391839` | 1.88 |  |
| `04c20c9` | 1.87 |  |
| `3020cf6` | 1.81 |  |
| `9bcaf84` | 1.78 |  |
| `40c35bb` | 1.83 | 6.26 |
| `9437d8a` | 1.88 | 6.51 |
| `d0c8cf5` | 1.87 | 6.33 |
| `73ce857` | 1.89 | 6.48 |
| `ed1c028` | 1.94 | 6.32 |
| `1269bd5` | 1.94 | 6.65 |
| `db0b436` | 1.97 | 6.68 |
| `25bb4e0` | 2.02 | 6.75 |
| `baf1f3c` | 2.02 | 6.75 |
| `74ac227` | 1.94 | 6.73 |
| `c0f29f2` | 1.89 | 6.71 |
| `5c3aec4` | 2.06 | 6.97 |
| `f7ba2fc` | 1.94 | 6.70 |
| `3e9eb27` | 1.76 | 5.99 |
| `0990c74` | 1.81 | 5.97 |
| `05d8da1` | 1.70 | 5.88 |
| `03887ff` | 2.04 | 7.24 |
| `87c066b` | 2.10 | 7.18 |
| `ca395a6` | 1.76 | 6.76 |
| `230b8a7` | 2.02 | 7.22 |
| `5706c5f` | 2.17 | 7.30 |
| `41f13c3` | 2.10 | 7.32 |
| `e7fb804` | 2.21 | 7.38 |
| `d0caa00` | 2.04 | 7.19 |
| `df0bfc5` | 2.03 | 7.19 |

### What is left out

Definitions the compiler drops and definitions it keeps.  These move when the reachability pass or the
decision log does.

| commit | unreached-function | export-visibility |
|---|---|---|
| `eee64a3` | 1.33 | 1.28 |
| `af657ad` | 1.31 | 1.27 |
| `2f564cb` | 1.33 | 1.30 |
| `37dbb28` | 1.44 | 1.47 |
| `1c4ae78` | 1.46 | 1.42 |
| `f391839` | 1.44 | 1.45 |
| `04c20c9` | 1.44 | 1.44 |
| `3020cf6` | 1.36 | 1.34 |
| `9bcaf84` | 1.38 | 1.39 |
| `40c35bb` | 1.41 | 1.37 |
| `9437d8a` | 1.54 | 1.47 |
| `d0c8cf5` | 1.41 | 1.46 |
| `73ce857` | 1.50 | 1.57 |
| `ed1c028` | 1.49 | 1.59 |
| `1269bd5` | 1.52 | 1.58 |
| `db0b436` | 1.56 | 1.73 |
| `25bb4e0` | 1.53 | 1.58 |
| `baf1f3c` | 1.52 | 1.58 |
| `74ac227` | 1.48 | 1.50 |
| `c0f29f2` | 1.52 | 1.56 |
| `5c3aec4` | 1.63 | 1.75 |
| `f7ba2fc` | 1.53 | 1.56 |
| `3e9eb27` | 1.40 | 1.46 |
| `0990c74` | 1.44 | 1.48 |
| `05d8da1` | 1.31 | 1.49 |
| `03887ff` | 1.54 | 1.62 |
| `87c066b` | 1.72 | 1.68 |
| `ca395a6` | 1.36 | 1.45 |
| `230b8a7` | 1.56 | 1.74 |
| `5706c5f` | 1.58 | 1.74 |
| `41f13c3` | 1.69 | 1.79 |
| `e7fb804` | 1.70 | 1.82 |
| `d0caa00` | 1.58 | 1.63 |
| `df0bfc5` | 1.54 | 1.68 |

### Expressions

Operators, and the folding of them.  These move when the expression parser, the semantic analysis or the
optimizer does.

| commit | bitwise-operators | bitwise-precedence | comparison-operators | logic-operators | logic-short-circuit | saturating-precedence | arithmetic-operators | call-nested | arithmetic-division | shift-operators | float-arithmetic | float-approximate | result-type | result-as-an-argument | match-a-result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `eee64a3` |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `af657ad` |  |  |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `2f564cb` | 1.47 | 1.22 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `37dbb28` | 1.55 | 1.22 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `1c4ae78` | 1.56 | 1.42 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `f391839` | 1.57 | 1.40 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `04c20c9` | 1.58 | 1.39 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `3020cf6` | 1.44 | 1.28 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `9bcaf84` | 1.46 | 1.28 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `40c35bb` | 1.51 | 1.31 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `9437d8a` | 1.54 | 1.45 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `d0c8cf5` | 1.55 | 1.36 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `73ce857` | 1.58 | 1.39 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `ed1c028` | 1.65 | 1.45 |  |  |  |  |  |  |  |  |  |  |  |  |  |
| `1269bd5` | 1.58 | 1.43 | 2.40 |  |  |  |  |  |  |  |  |  |  |  |  |
| `db0b436` | 1.66 | 1.42 | 2.36 |  |  |  |  |  |  |  |  |  |  |  |  |
| `25bb4e0` | 1.67 | 1.39 | 2.29 | 2.39 | 2.33 |  |  |  |  |  |  |  |  |  |  |
| `baf1f3c` | 1.69 | 1.48 | 2.37 | 2.55 | 2.35 |  |  |  |  |  |  |  |  |  |  |
| `74ac227` | 1.71 | 1.37 | 2.29 | 2.41 | 2.27 | 1.57 |  |  |  |  |  |  |  |  |  |
| `c0f29f2` | 1.60 | 1.41 | 2.43 | 2.48 | 2.43 | 1.73 | 1.83 |  |  |  |  |  |  |  |  |
| `5c3aec4` | 1.83 | 1.55 | 2.67 | 2.79 | 2.56 | 1.76 | 1.95 |  |  |  |  |  |  |  |  |
| `f7ba2fc` | 1.65 | 1.44 | 2.61 | 2.65 | 2.43 | 1.67 | 1.75 |  |  |  |  |  |  |  |  |
| `3e9eb27` | 1.54 | 1.34 | 2.16 | 2.27 | 2.09 | 1.54 | 1.81 | 2.23 |  |  |  |  |  |  |  |
| `0990c74` | 1.56 | 1.36 | 2.17 | 2.31 | 2.09 | 1.52 | 1.79 | 2.25 | 2.09 |  |  |  |  |  |  |
| `05d8da1` | 1.45 | 1.22 | 2.06 | 2.13 | 2.01 | 1.42 | 1.70 | 2.16 | 1.86 | 2.00 |  |  |  |  |  |
| `03887ff` | 1.78 | 1.57 | 2.49 | 2.59 | 2.41 | 1.67 | 2.01 | 2.55 | 2.17 | 2.34 | 5.54 |  |  |  |  |
| `87c066b` | 1.78 | 1.56 | 2.63 | 2.76 | 2.46 | 1.80 | 2.08 | 2.59 | 2.21 | 2.29 | 6.22 |  |  |  |  |
| `ca395a6` | 1.53 | 1.66 | 2.60 | 2.72 | 2.45 | 1.85 | 2.12 | 2.68 | 2.28 | 2.41 | 5.91 | 9.15 |  |  |  |
| `230b8a7` | 1.70 | 1.44 | 2.54 | 2.61 | 2.43 | 1.65 | 1.94 | 2.68 | 2.40 | 2.27 | 6.34 | 9.70 | 3.80 |  |  |
| `5706c5f` | 1.77 | 1.47 | 2.59 | 2.71 | 2.57 | 1.71 | 2.10 | 2.77 | 2.45 | 2.43 | 6.43 | 9.88 | 3.88 |  |  |
| `41f13c3` | 1.98 | 1.61 | 2.58 | 2.75 | 2.45 | 1.98 | 2.17 | 2.77 | 2.43 | 2.41 | 6.40 | 9.62 | 4.10 | 10.07 |  |
| `e7fb804` | 1.90 | 1.61 | 2.66 | 2.81 | 2.58 | 1.87 | 2.15 | 2.75 | 2.50 | 2.47 | 6.45 | 9.96 | 3.96 | 10.11 | 4.29 |
| `d0caa00` | 1.87 | 1.51 | 2.45 | 2.62 | 2.44 | 1.83 | 2.04 | 2.60 | 2.31 | 2.35 | 6.36 | 9.62 | 3.79 | 10.51 | 4.23 |
| `df0bfc5` | 1.85 | 1.47 | 2.58 | 2.55 | 2.40 | 1.88 | 2.00 | 2.62 | 2.31 | 2.35 | 6.41 | 9.60 | 3.70 | 10.00 | 4.16 |

### Types

Definitions the program writes.  These move when the parser, the name resolution or the layout does.

| commit | type-definitions | enum-values | enum-flag |
|---|---|---|---|
| `eee64a3` |  |  |  |
| `af657ad` |  |  |  |
| `2f564cb` |  |  |  |
| `37dbb28` |  |  |  |
| `1c4ae78` |  |  |  |
| `f391839` |  |  |  |
| `04c20c9` |  |  |  |
| `3020cf6` |  |  |  |
| `9bcaf84` |  |  |  |
| `40c35bb` |  |  |  |
| `9437d8a` |  |  |  |
| `d0c8cf5` |  |  |  |
| `73ce857` |  |  |  |
| `ed1c028` |  |  |  |
| `1269bd5` |  |  |  |
| `db0b436` |  |  |  |
| `25bb4e0` |  |  |  |
| `baf1f3c` |  |  |  |
| `74ac227` |  |  |  |
| `c0f29f2` |  |  |  |
| `5c3aec4` |  |  |  |
| `f7ba2fc` |  |  |  |
| `3e9eb27` |  |  |  |
| `0990c74` |  |  |  |
| `05d8da1` |  |  |  |
| `03887ff` |  |  |  |
| `87c066b` |  |  |  |
| `ca395a6` |  |  |  |
| `230b8a7` |  |  |  |
| `5706c5f` | 1.42 |  |  |
| `41f13c3` | 1.53 |  |  |
| `e7fb804` | 1.54 |  |  |
| `d0caa00` | 1.60 | 3.76 |  |
| `df0bfc5` | 1.38 | 3.72 | 3.75 |

Process
-------

The whole run, which is what someone waiting for the compiler waits
for.  For the bootstrap compiler this is mostly starting the
interpreter and importing the package, so what one sample costs and
what the next costs is noise; only the range across all of them is
worth showing here, and every figure is in the JSON beside this file.

| commit | fastest | slowest |
|---|---|---|
| `eee64a3` | 68 | 71 |
| `af657ad` | 65 | 70 |
| `2f564cb` | 67 | 72 |
| `37dbb28` | 66 | 69 |
| `1c4ae78` | 67 | 71 |
| `f391839` | 67 | 71 |
| `04c20c9` | 67 | 70 |
| `3020cf6` | 70 | 75 |
| `9bcaf84` | 70 | 74 |
| `40c35bb` | 70 | 78 |
| `9437d8a` | 73 | 82 |
| `d0c8cf5` | 73 | 81 |
| `73ce857` | 73 | 81 |
| `ed1c028` | 74 | 80 |
| `1269bd5` | 75 | 85 |
| `db0b436` | 76 | 82 |
| `25bb4e0` | 74 | 83 |
| `baf1f3c` | 75 | 82 |
| `74ac227` | 73 | 85 |
| `c0f29f2` | 72 | 81 |
| `5c3aec4` | 79 | 86 |
| `f7ba2fc` | 74 | 84 |
| `3e9eb27` | 64 | 75 |
| `0990c74` | 65 | 71 |
| `05d8da1` | 65 | 73 |
| `03887ff` | 75 | 90 |
| `87c066b` | 77 | 90 |
| `ca395a6` | 66 | 86 |
| `230b8a7` | 75 | 84 |
| `5706c5f` | 80 | 93 |
| `41f13c3` | 77 | 89 |
| `e7fb804` | 80 | 91 |
| `d0caa00` | 80 | 96 |
| `df0bfc5` | 81 | 90 |

What each row is:

- `eee64a3` -- ✨ A register allocator, by linear scan, which does not spill
- `af657ad` -- ✨ Conditional branches, selected with their comparison and turned round
- `2f564cb` -- ✨ Expressions, by precedence climbing, with the bitwise operators
- `37dbb28` -- 🐛 A diagnostic number no block covers is refused, not mis-grouped
- `1c4ae78` -- ✨ What the compiler leaves out is recorded in the decision log
- `f391839` -- ✨ A local the sweep removes is logged too, by the name it was given
- `04c20c9` -- ✨ A grammar for editors, and a program that shows a log against its source
- `3020cf6` -- ✨ Spilling to a stack frame, by rewriting and starting again
- `9bcaf84` -- ⚡ A spilled value is read from the frame once for as many instructions as read it
- `40c35bb` -- 📝 Timing tables grow down, not across, and are grouped
- `9437d8a` -- ✨ Modules: read while compiling, read once, named by the shortest route
- `d0c8cf5` -- ✨ `export` and `visible` are two attributes, because they were two questions
- `73ce857` -- ✨ One meaning, one spelling: attributes are written one way
- `ed1c028` -- ✨ A truth value in a register, on all three architectures
- `1269bd5` -- ✨ The comparisons: `=` `≠` `<` `>` `≤` `≥`
- `db0b436` -- ✨ A statement's value must be used, and ≠ keeps its glyph
- `25bb4e0` -- ✨ The logical operators, and block parameters to carry `and` and `or`
- `baf1f3c` -- ✨ Constants of any width, and the header flags they travel under
- `74ac227` -- ✨ Saturating arithmetic: ⊞ ⊟ ⊠
- `c0f29f2` -- ✨ Arithmetic that checks, and the path a fault leaves through
- `5c3aec4` -- ✨ No arrow means nothing answered with; a semicolon separates and never ends
- `f7ba2fc` -- ✅ A call that answers with nothing has nothing to use
- `3e9eb27` -- ✨ Function calls, with positional arguments
- `0990c74` -- ✨ Division and what is left over: `÷` and `%`
- `05d8da1` -- ✨ Moving bits sideways: « » ↺ ↻, and an overflow seen while compiling
- `03887ff` -- 📝 Time the floating-point sample too
- `87c066b` -- ✨ A floating-point answer that is not a number stops the program
- `ca395a6` -- 📝 Time the approximate comparisons too
- `230b8a7` -- 📝 Time the result type too
- `5706c5f` -- 📝 Time the type definitions too
- `41f13c3` -- 📝 Time a result passed to a function too
- `e7fb804` -- 📝 Time a match too
- `d0caa00` -- 📝 Time the enumerations too
- `df0bfc5` -- 📝 Time a flag enumeration too

What each program exercises:

- `exit0` -- the smallest conforming program
- `semicolon-separates-statements` -- two statements on one line
- `digit-separators` -- literals in every base
- `boolean-values` -- truth values in both sections
- `global-variable` -- one variable, read once
- `assign-widths` -- a store of every width
- `boolean-in-memory` -- a truth value, which is one byte
- `many-values-at-once` -- four values live at once
- `spill-to-the-frame` -- thirty-two at once, which no target can hold
- `unreached-function` -- a function and a variable that are dropped
- `export-visibility` -- several definitions, some exported
- `bitwise-operators` -- four operators over two variables
- `bitwise-precedence` -- an expression the folder collapses entirely
- `comparison-operators` -- all six comparisons, none of them folded
- `logic-operators` -- all six logical operators, none of them folded
- `logic-short-circuit` -- three short circuits, which is six blocks
- `saturating-precedence` -- a sum and a product that saturate
- `arithmetic-operators` -- a sum and a product that check and can fault
- `call-nested` -- four calls, two of them nested
- `arithmetic-division` -- a division and a remainder, each checked twice
- `shift-operators` -- two shifts, each with its distance checked
- `float-arithmetic` -- ten floating-point answers, each asserted
- `float-approximate` -- every approximate comparison, in both widths
- `result-type` -- a result made, propagated with ? and read with ??
- `result-as-an-argument` -- results passed and answered with, both kinds
- `match-a-result` -- three matches, one of them carrying a name past
- `type-definitions` -- eight definitions in all three notations
- `enum-values` -- two enumerations, and three matches over them
- `enum-flag` -- a flag enumeration, its operators and three matches
