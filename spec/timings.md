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

| commit | exit0 | digit-separators | boolean-values |
|---|---|---|---|
| `eee64a3` | 0.90 | 1.46 | 1.25 |
| `af657ad` | 0.89 | 1.47 | 1.24 |
| `2f564cb` | 0.88 | 1.49 | 1.23 |
| `37dbb28` | 0.89 | 1.63 | 1.27 |
| `1c4ae78` | 1.06 | 1.64 | 1.39 |
| `f391839` | 1.05 | 1.60 | 1.45 |
| `04c20c9` | 1.07 | 1.62 | 1.38 |
| `3020cf6` | 0.93 | 1.51 | 1.40 |
| `9bcaf84` | 0.99 | 1.55 | 1.31 |
| `40c35bb` | 1.00 | 1.52 | 1.29 |
| `9437d8a` | 1.00 | 1.64 | 1.55 |
| `d0c8cf5` | 1.05 | 1.60 | 1.43 |

### Variables and memory

Reading and writing variables, including one of every width.  These move when the loads and stores of a
backend do.

| commit | global-variable | assign-widths |
|---|---|---|
| `eee64a3` | 1.05 | 1.50 |
| `af657ad` | 1.10 | 1.49 |
| `2f564cb` | 1.04 | 1.50 |
| `37dbb28` | 1.05 | 1.64 |
| `1c4ae78` | 1.22 | 1.63 |
| `f391839` | 1.22 | 1.63 |
| `04c20c9` | 1.20 | 1.66 |
| `3020cf6` | 1.15 | 1.55 |
| `9bcaf84` | 1.16 | 1.60 |
| `40c35bb` | 1.18 | 1.62 |
| `9437d8a` | 1.22 | 1.68 |
| `d0c8cf5` | 1.13 | 1.59 |

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

### Expressions

Operators, and the folding of them.  These move when the expression parser, the semantic analysis or the
optimizer does.

| commit | bitwise-operators | bitwise-precedence |
|---|---|---|
| `eee64a3` |  |  |
| `af657ad` |  |  |
| `2f564cb` | 1.47 | 1.22 |
| `37dbb28` | 1.55 | 1.22 |
| `1c4ae78` | 1.56 | 1.42 |
| `f391839` | 1.57 | 1.40 |
| `04c20c9` | 1.58 | 1.39 |
| `3020cf6` | 1.44 | 1.28 |
| `9bcaf84` | 1.46 | 1.28 |
| `40c35bb` | 1.51 | 1.31 |
| `9437d8a` | 1.54 | 1.45 |
| `d0c8cf5` | 1.55 | 1.36 |

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

What each program exercises:

- `exit0` -- the smallest conforming program
- `digit-separators` -- literals in every base
- `boolean-values` -- truth values in both sections
- `global-variable` -- one variable, read once
- `assign-widths` -- a store of every width
- `many-values-at-once` -- four values live at once
- `spill-to-the-frame` -- thirty-two at once, which no target can hold
- `unreached-function` -- a function and a variable that are dropped
- `export-visibility` -- several definitions, some exported
- `bitwise-operators` -- four operators over two variables
- `bitwise-precedence` -- an expression the folder collapses entirely
