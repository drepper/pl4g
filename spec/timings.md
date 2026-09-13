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

| program | eee64a3 | af657ad | 2f564cb |
|---|---|---|---|
| exit0 | 0.90 | 0.89 | 0.88 |
| global-variable | 1.05 | 1.10 | 1.04 |
| assign-widths | 1.50 | 1.49 | 1.50 |
| many-values-at-once | 1.73 | 1.74 | 1.87 |
| unreached-function | 1.33 | 1.31 | 1.33 |
| export-visibility | 1.28 | 1.27 | 1.30 |
| digit-separators | 1.46 | 1.47 | 1.49 |
| boolean-values | 1.25 | 1.24 | 1.23 |
| bitwise-operators |  |  | 1.47 |
| bitwise-precedence |  |  | 1.22 |

Process
-------

The whole run, which is what someone waiting for the compiler waits
for.  For the bootstrap compiler this is mostly starting the interpreter and
importing the package, so it says little about code generation and a good deal
about how much of the compiler an ordinary compilation has to import.

| program | eee64a3 | af657ad | 2f564cb |
|---|---|---|---|
| exit0 | 69 | 69 | 67 |
| global-variable | 68 | 69 | 69 |
| assign-widths | 69 | 69 | 70 |
| many-values-at-once | 69 | 70 | 69 |
| unreached-function | 71 | 69 | 68 |
| export-visibility | 68 | 65 | 69 |
| digit-separators | 68 | 66 | 68 |
| boolean-values | 68 | 67 | 72 |
| bitwise-operators |  |  | 72 |
| bitwise-precedence |  |  | 68 |

What each column is:

- `eee64a3` -- ✨ A register allocator, by linear scan, which does not spill
- `af657ad` -- ✨ Conditional branches, selected with their comparison and turned round
- `2f564cb` -- ✨ Expressions, by precedence climbing, with the bitwise operators

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
