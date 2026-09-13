Compiler Timings
================

How long the compiler takes on each sample program, in milliseconds, the
best of five runs.  One column per commit, added by `bin/pl4g-timing`; a
blank means the sample did not exist yet at that commit.  Measured on one
machine, so the numbers are worth comparing with each other and with
nobody else's.

A hash ending in `+` was measured with changes not yet committed.

Work
----

The sum of the compiler's own stages, which is what a change to the
compiler moves.

| program | eee64a3 | af657ad | 2f564cb | 37dbb28 | 1c4ae78 | f391839 |
|---|---|---|---|---|---|---|
| exit0 | 0.90 | 0.89 | 0.88 | 0.89 | 1.06 | 1.05 |
| global-variable | 1.05 | 1.10 | 1.04 | 1.05 | 1.22 | 1.22 |
| assign-widths | 1.50 | 1.49 | 1.50 | 1.64 | 1.63 | 1.63 |
| many-values-at-once | 1.73 | 1.74 | 1.87 | 1.90 | 1.90 | 1.88 |
| unreached-function | 1.33 | 1.31 | 1.33 | 1.44 | 1.46 | 1.44 |
| export-visibility | 1.28 | 1.27 | 1.30 | 1.47 | 1.42 | 1.45 |
| digit-separators | 1.46 | 1.47 | 1.49 | 1.63 | 1.64 | 1.60 |
| boolean-values | 1.25 | 1.24 | 1.23 | 1.27 | 1.39 | 1.45 |
| bitwise-operators |  |  | 1.47 | 1.55 | 1.56 | 1.57 |
| bitwise-precedence |  |  | 1.22 | 1.22 | 1.42 | 1.40 |

Process
-------

The whole run, which is what someone waiting for the compiler waits
for.  For the bootstrap compiler this is mostly starting the interpreter and
importing the package, so it says little about code generation and a good deal
about how much of the compiler an ordinary compilation has to import.

| program | eee64a3 | af657ad | 2f564cb | 37dbb28 | 1c4ae78 | f391839 |
|---|---|---|---|---|---|---|
| exit0 | 69 | 69 | 67 | 68 | 71 | 67 |
| global-variable | 68 | 69 | 69 | 66 | 71 | 70 |
| assign-widths | 69 | 69 | 70 | 67 | 71 | 69 |
| many-values-at-once | 69 | 70 | 69 | 69 | 68 | 71 |
| unreached-function | 71 | 69 | 68 | 66 | 71 | 69 |
| export-visibility | 68 | 65 | 69 | 69 | 70 | 71 |
| digit-separators | 68 | 66 | 68 | 68 | 68 | 68 |
| boolean-values | 68 | 67 | 72 | 69 | 67 | 67 |
| bitwise-operators |  |  | 72 | 68 | 70 | 68 |
| bitwise-precedence |  |  | 68 | 67 | 70 | 68 |

What each column is:

- `eee64a3` -- ✨ A register allocator, by linear scan, which does not spill
- `af657ad` -- ✨ Conditional branches, selected with their comparison and turned round
- `2f564cb` -- ✨ Expressions, by precedence climbing, with the bitwise operators
- `37dbb28` -- 🐛 A diagnostic number no block covers is refused, not mis-grouped
- `1c4ae78` -- ✨ What the compiler leaves out is recorded in the decision log
- `f391839` -- ✨ A local the sweep removes is logged too, by the name it was given

What each sample exercises:

- `exit0` -- the smallest conforming program
- `global-variable` -- one variable, read once
- `assign-widths` -- a store of every width
- `many-values-at-once` -- four values live at once, which the allocator places
- `unreached-function` -- a function and a variable that are dropped
- `export-visibility` -- several definitions, some exported
- `digit-separators` -- literals in every base
- `boolean-values` -- truth values in both sections
- `bitwise-operators` -- an expression with four operators and two variables
- `bitwise-precedence` -- an expression the folder collapses entirely
