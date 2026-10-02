"""Tokens.

The language is meant to be generated rather than typed, so several operators are
written with Unicode glyphs.  An ASCII substitute exists only where it is a
sequence of more than one character: a single character is never a substitute,
so that it stays available for a future language feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..ir.types import BUILTIN_TYPES, IntType

from ..source.location import Span

#: Introduces a comment that runs to the end of the line.
COMMENT_GLYPH: Final[str] = "\N{REFERENCE MARK}"

#: Introduces a documentation comment.
DOC_COMMENT_GLYPH: Final[str] = COMMENT_GLYPH * 2

#: Separates a function's parameter list from its return type.
ARROW_GLYPH: Final[str] = "\N{RIGHTWARDS ARROW}"

#: Assigns a value to a variable.  It has no ASCII substitute: the only
#: candidate, '<-', cannot be told apart from a comparison against a negated
#: value without depending on the spaces around it, which is a distinction this
#: language does not make.
ASSIGN_GLYPH: Final[str] = "\N{LEFTWARDS ARROW}"

#: Marks a literal as negative.  It is part of the literal, not an operator, so
#: nothing may stand between it and the digits.  A separate glyph is what lets
#: subtraction keep '-' without either meaning having to be worked out from the
#: spaces around it, which is the distinction APL draws with its own high minus.
NEGATIVE_GLYPH: Final[str] = "\N{SUPERSCRIPT MINUS}"

#: Compares two values.  The three that have a glyph are written with one; the
#: other three are the characters everyone already writes them with.  There is
#: no separate operator for assignment to be confused with, since assignment is
#: written with an arrow, so '=' asks a question and nothing else.
NOT_EQUAL_GLYPH: Final[str] = "\N{NOT EQUAL TO}"
LESS_EQUAL_GLYPH: Final[str] = "\N{LESS-THAN OR EQUAL TO}"
GREATER_EQUAL_GLYPH: Final[str] = "\N{GREATER-THAN OR EQUAL TO}"

#: Compares two floating-point values, allowing for the small errors that
#: accumulate in one.  Each is the exact comparison beside it with the question
#: asked of the tolerance rather than of the values: `≅` is `=`, `≇` is `≠`, `⪅` is
#: `≤`, `⪆` is `≥`, `⪉` is `<` and `⪊` is `>`.  None has an ASCII
#: substitute: there is no sequence of ASCII characters that says "approximate"
#: without being read as something else.
ALIKE_GLYPH: Final[str] = "\N{APPROXIMATELY EQUAL TO}"
UNALIKE_GLYPH: Final[str] = "\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}"
BELOW_OR_ALIKE_GLYPH: Final[str] = "\N{LESS-THAN OR APPROXIMATE}"
ABOVE_OR_ALIKE_GLYPH: Final[str] = "\N{GREATER-THAN OR APPROXIMATE}"
BELOW_NOT_ALIKE_GLYPH: Final[str] = "\N{LESS-THAN AND NOT APPROXIMATE}"
ABOVE_NOT_ALIKE_GLYPH: Final[str] = "\N{GREATER-THAN AND NOT APPROXIMATE}"

#: What encloses a tuple, and the type of one.  Angle brackets rather than
#: parentheses, which group an expression, so that a tuple of one thing is
#: still a tuple and not the thing with brackets round it.
TUPLE_OPEN_GLYPH: Final[str] = "\N{LEFT ANGLE BRACKET}"
TUPLE_CLOSE_GLYPH: Final[str] = "\N{RIGHT ANGLE BRACKET}"

#: What encloses a set or a dictionary, and what a lookup in one is written
#: with.  A double parenthesis rather than braces, which are the explicit block
#: notation, and rather than square brackets, which an array will want: a
#: collection written down and a lookup in one are the same shape here, as a
#: call and a function's parameter list are.
SET_OPEN_GLYPH: Final[str] = "\N{LEFT DOUBLE PARENTHESIS}"
SET_CLOSE_GLYPH: Final[str] = "\N{RIGHT DOUBLE PARENTHESIS}"

#: How a walk over a list is moved along and back: `\N{UPWARDS WHITE ARROW}it` is the cursor at the next
#: element and `\N{DOWNWARDS WHITE ARROW}it` the one at the element before.  Hollow arrows, because what
#: they move is where a walk is and not what it holds -- the solid ones are
#: assignment and the ordinary shifts.
NEXT_GLYPH: Final[str] = "\N{UPWARDS WHITE ARROW}"
PREV_GLYPH: Final[str] = "\N{DOWNWARDS WHITE ARROW}"

#: What takes a key out of one, written before the lookup it undoes: `\N{DAGGER}d\N{LEFT DOUBLE PARENTHESIS}k\N{RIGHT DOUBLE PARENTHESIS}`.
#: A dagger, which is what a mark against a name has meant for "no longer with
#: us" in print for centuries, and which nothing else in the language uses.
TAKE_GLYPH: Final[str] = "\N{DAGGER}"

#: What hands a tuple over to a call as several arguments rather than as one.
#: An asterism, which is three asterisks arranged as one mark: what it says is
#: that several things stand where one is written, and Python spells the same
#: thing with the one asterisk this language leaves free.
SPREAD_GLYPH: Final[str] = "\N{ASTERISM}"

#: What names a loop, so that `break` and `continue` can say which one they mean.
#: A section sign, which is what marks a named division of a text: a label names
#: a part of a program the same way, and the glyph is free where every plain
#: character that might have done is spent or wanted elsewhere.
LABEL_GLYPH: Final[str] = "\N{SECTION SIGN}"

#: What encloses an array: its type, a value of one written down, and a lookup
#: in one.  A white square bracket rather than the plain one, which an index
#: into something else may yet want, and rather than the double parenthesis a
#: collection uses -- an array is found by its place and a collection by its
#: key, and the two are different questions however alike they read.
ARRAY_OPEN_GLYPH: Final[str] = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}"
ARRAY_CLOSE_GLYPH: Final[str] = "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"

#: What separates the ends of a range from each other.  One character and not
#: three dots: a range is one thing, and spelling it out of three copies of the
#: character a member access is written with would make the lexer's job a
#: question of how far it can look ahead.
RANGE_GLYPH: Final[str] = "\N{HORIZONTAL ELLIPSIS}"

#: The arena the compiler provides, which everything that allocates and says no
#: other one comes out of, and the name that stands for an arena with nothing in
#: it yet -- what a program writes to make one of its own.
HEAP_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}heap"
EMPTY_ARENA_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}arena"
#: The allocator that is no allocator: what is in the image, made while compiling,
#: lasting as long as the program and never given back.
STATIC_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}static"

#: What encloses an operator where the operator stands as a *name*: a definition
#: of one, and nothing else so far.  The grave accent is the one character of ASCII
#: the language gave no meaning, and it reads as a quotation of the glyph -- which
#: is what it is, the name of the operator rather than the operator itself.
OPNAME_GLYPH: Final[str] = "`"

#: What marks a hole in a macro's pattern and fills one in its template.  Unicode
#: calls it a currency symbol, so it is not a glyph a program could define as an
#: operator, and the language gave it no other meaning.
HOLE_GLYPH: Final[str] = "$"

#: Which single glyphs a program may name as an operator.  Unicode decides most of
#: it: a glyph in the symbol categories is one, `Sm` being the mathematical
#: symbols -- `+`, `\N{NOT EQUAL TO}`, `\N{SQUARED PLUS}`, `\N{DIVISION SIGN}` -- and `So` the other symbols, which is where
#: `\N{APL FUNCTIONAL SYMBOL RHO}` and the arrows live.  What the categories leave out is the ASCII
#: punctuation the grammar needs for itself, which is the point of asking them.
#:
#: The language's own operators are not all in those two categories -- `-` is a
#: dash, `^` a modifier symbol, `\N{LEFT CEILING}` and `\N{LEFT FLOOR}` are brackets as far as Unicode is
#: concerned -- so a glyph the language already uses as an operator qualifies too.
#: That second clause is what makes every builtin operator writable in this
#: notation, which is what the specification needs to define them with it.
OPERATOR_CATEGORIES: Final[frozenset[str]] = frozenset({"Sm", "So"})

#: Which glyphs may open and close a *pair* -- an operator written around what it
#: is applied to rather than before or between.  Unicode decides here too: `Ps` is
#: the opening punctuation and `Pe` the closing, which is every bracket there is
#: and nothing else.
OPENER_CATEGORY: Final[str] = "Ps"
CLOSER_CATEGORY: Final[str] = "Pe"

#: What a function answered, which is the one thing a post-condition is about
#: and which there is nothing else to call.  A name the compiler provides rather
#: than a keyword, because that is what the language already does for a name it
#: gives a meaning to -- and it cannot collide, the glyph being the compiler's.
ANSWER_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}answer"

#: What a parameter held when the function was entered, which a post-condition
#: needs where the body has bound the name to something else since.  It takes the
#: name of a parameter and nothing else: a parameter's value at entry is already
#: there to be named -- it is what arrived -- so nothing is copied for it, which
#: is what an `old` over an arbitrary expression could not promise.
ENTRY_NAME: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}entry"

#: What a `match` arm that takes every alternative left is written with.  It is
#: a name no definition may have, so nothing a program writes can be mistaken
#: for it; every language with pattern matching spells it this way.
WILDCARD_NAME: Final[str] = "_"

#: The two operators a result type has.  `?` written after an expression takes
#: the answer out of it and leaves the function with the error where there is
#: none; `??` takes the answer or the value written after it.  Both are ASCII
#: because neither is a question about numbers: there is no mathematical glyph
#: for "or else" that a reader would recognize, and `?` for "this may have no
#: answer" is what Swift, Kotlin, C#, Zig and Rust have all settled on.
QUESTION_GLYPH: Final[str] = "?"
OR_ELSE_GLYPH: Final[str] = "??"

#: What the arm of a `match` that takes the error case is written with.  A
#: result's two arms may name one type -- `u8?u8` is a perfectly good type --
#: so which arm is which cannot be said by naming a type, and this says it.
#: The glyph is logic's "bottom", the proposition that never holds, which is as
#: close to "there is no answer" as a single character comes.
BOTTOM_GLYPH: Final[str] = "\N{UP TACK}"

#: What a name the compiler provides begins with.  A program may read and write
#: the ones that exist and may not define one of its own, so the glyph is what
#: keeps the two apart: no name a program writes can begin with it, and there is
#: therefore no name the compiler can add later that takes a program's name
#: away.  It is APL's quad, which marks that language's system names for the
#: same reason.
BUILTIN_GLYPH: Final[str] = "\N{APL FUNCTIONAL SYMBOL QUAD}"

#: What is written around an expression whose operators are to go past the ends
#: of their types rather than stopping the program.  It is not a function -- it
#: takes no arguments and answers with nothing of its own -- which is why it is
#: one of the names the compiler provides rather than something a program could
#: have defined: what it does is decide what the operators inside it mean.
WRAP_NAME: Final[str] = "".join((BUILTIN_GLYPH, "wrap"))

#: The two conversions between a code point and the number Unicode gave it.
#: They are the compiler's rather than a program's because neither can be
#: written in the language -- one reads the bits of a value as another type and
#: the other checks a number against the last code point there is -- and they
#: carry the sigil for the same reason every compiler-provided name does: so
#: that no program has to give up the names `ord` and `chr`.
TYPEOF_NAME: Final[str] = "".join((BUILTIN_GLYPH, "typeof"))

#: Walking something and counting the turns at the same time.  It is a name the
#: compiler provides rather than an operator because what it makes is an
#: iterator, which is a thing a loop takes and nothing else does.
ENUMERATE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "enumerate"))

#: Taking a unit off a number, and putting one on.  A unit is part of a type
#: and nothing converts between types on its own, so these are the two places
#: where a program says it means to cross from one to the other -- and they are
#: the compiler's names because neither can be written in the language: what
#: they do is change a type and no bits at all.
#: Making a value of a narrower type out of one of a wider, and saying so
#: where it will not fit.  It is the compiler's name because what it answers
#: with is a result whose error is a type the compiler provides, and because
#: nothing in the language can be written that checks a value against the ends
#: of a type it is not yet of.
NARROW_NAME: Final[str] = "".join((BUILTIN_GLYPH, "narrow"))

#: Making a value of a wider type out of one of a narrower.  It answers the
#: value itself and not a result, which it may do only because it cannot fail:
#: every value of the type written is a value of the type wanted.  It is the
#: compiler's name for the reason its opposite is -- nothing in this language
#: widens or narrows on its own, so a value that is to become one of another
#: type is written as becoming one.
WIDEN_NAME: Final[str] = "".join((BUILTIN_GLYPH, "widen"))

#: Bringing a module into a file.  It carries the sigil every name the
#: compiler provides carries, for the reason every one of them does: a program
#: that wanted a variable called `import` should not have to give it up because
#: the compiler wanted the word.  What it looks like is a call and what it does
#: is not one -- a module is not a value -- so the parser reads it rather than
#: the checker.
IMPORT_NAME: Final[str] = "".join((BUILTIN_GLYPH, "import"))

#: A request to the kernel, written out.  The number and the arguments are the
#: kernel's, not the language's: nothing here knows what call 1 is, and a program
#: that asks for it is asking the system and not the compiler.  It is how the
#: `std` module reaches anything outside the process, and it is the only way.
SYSCALL_NAME: Final[str] = "".join((BUILTIN_GLYPH, "syscall"))

#: Reading a place so that nothing written after is seen by another observer to
#: have happened before, and writing one so that nothing written before is seen
#: to have happened after.  They are the compiler's names because what they say
#: is about the machine and not about the value: nothing a program could write
#: for itself would make a read acquire.  Said at the access that wants it,
#: rather than by a type, so that the same place read the ordinary way elsewhere
#: is still an ordinary read.
ACQUIRE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "acquire"))
RELEASE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "release"))

#: A place at an address the program worked out, and a run of places from one.
#: The compiler's names because nothing in the language makes a place out of a
#: number: what is there is what the program says is there, and the compiler has
#: no way to check it.  They are how a program reaches memory something else
#: gave it -- what `mmap` answered, what a device said -- and they are the only
#: way, which is what keeps that door in one place.
AT_NAME: Final[str] = "".join((BUILTIN_GLYPH, "at"))
SPAN_NAME: Final[str] = "".join((BUILTIN_GLYPH, "span"))

#: The bytes a string is made of.  The compiler's name because nothing in the
#: language reaches inside a string: there is no index, `#` answers characters,
#: and what a value of one *is* -- where the bytes are and how many there are --
#: is the representation's business.  It is how text reaches anything that takes
#: bytes, a device among them, and it says which encoding is being written by
#: being the one thing that answers them.
BYTES_NAME: Final[str] = "".join((BUILTIN_GLYPH, "bytes"))

#: What a type turns out to be, asked of the type rather than of a value.  Three
#: questions, and they are the compiler's names because the answers are not
#: things a program can work out: a type is not a value here, so nothing a
#: program writes could look at one.
#:
#: `⎕isrecord(⌜T⌝)` is a truth the compiler settles, so it stands in a
#: `comptime if` and nowhere else -- the same rule `⎕typeof` follows and for the
#: same reason, there being nothing for it to be at run time.  It is what lets one
#: generic definition say a different thing for a record than for a number, which
#: is what this language has in place of overloading.
IS_RECORD_NAME: Final[str] = "".join((BUILTIN_GLYPH, "isrecord"))

#: `⎕fields(v)` answers a record's fields as a tuple of pairs, each pair the
#: field's name and its value, **in the order the type declares them**.  A tuple
#: because `comptime foreach` walks one and because the fields are of different
#: types, which is the one thing a tuple is for; pairs rather than two tuples
#: because two walks cannot be taken together.  A record already travels as its
#: fields, so this costs nothing but the names.
FIELDS_NAME: Final[str] = "".join((BUILTIN_GLYPH, "fields"))

#: `⎕typename(⌜T⌝)` is the type written out, as text.  Defined for every type and
#: not only a record's: it is what a value's text needs in front of its fields,
#: and a program that wants it for anything else may have it.
TYPENAME_NAME: Final[str] = "".join((BUILTIN_GLYPH, "typename"))

#: A string of one character.  The compiler's name because it is the third way a
#: `str` is made -- a literal, a join, and this -- and because a string's bytes
#: being well-formed UTF-8 is an invariant, which encoding one code point here
#: keeps true by construction.  `⎕bytes` is the way out of a string and this is
#: one of the ways in; there is deliberately no way in from bytes, which would
#: have to be checked.
STR_OF_NAME: Final[str] = "".join((BUILTIN_GLYPH, "str"))

#: Everything an arena holds, given back at once.  The one granularity the
#: allocator has: nothing is given back on its own, so this is what makes an arena
#: a pool -- room is taken from it for as long as it is wanted and the whole of it
#: goes in one call.  The arena is left as an arena with nothing in it, so taking
#: room from it again simply asks the system for a first chunk.
EMPTY_NAME: Final[str] = "".join((BUILTIN_GLYPH, "empty"))

#: And the way back: a place as the number it is.  The inverse of `⎕at`, and
#: the compiler's name for the same reason -- what a program does with a number
#: that was a place is nothing the compiler can check.  It is what a program has
#: for handing an address to something outside it: a request to the kernel takes
#: one as an argument and a ring's submission entry holds one in a field.
ADDRESS_NAME: Final[str] = "".join((BUILTIN_GLYPH, "address"))

#: What stands between a name the compiler provides and the key it is asked
#: about: `⎕sc@write` is one name and the whole of it is looked up.  Only a name
#: beginning with the quad may carry one, so the mark means nothing new
#: anywhere else -- `@[` still begins an attribute list and nothing else does.
KEY_GLYPH: Final[str] = "@"

#: The number of a system call, for the architecture being built for:
#: `⎕sc@write` is 1 on x86-64 and 64 on the other two.  The compiler holds the
#: table because it is the compiler that knows which architecture it is, and a
#: module cannot ask the question any other way.
SYSCALL_NUMBER_PREFIX: Final[str] = "".join((BUILTIN_GLYPH, "sc", KEY_GLYPH))

DROP_NAME: Final[str] = "".join((BUILTIN_GLYPH, "drop"))
UNIT_NAME: Final[str] = "".join((BUILTIN_GLYPH, "unit"))

ORD_NAME: Final[str] = "".join((BUILTIN_GLYPH, "ord"))
CHR_NAME: Final[str] = "".join((BUILTIN_GLYPH, "chr"))

#: How many bits of a value are set, and how many zeroes stand above the
#: highest one that is.  Both are questions about the bits of a value at the
#: width of its own type, so `⎕lead` of nought is the width and not something
#: undefined -- which is what the instructions that answer them on two of the
#: three architectures leave it as, and is why the language says it.
ONES_NAME: Final[str] = "".join((BUILTIN_GLYPH, "ones"))
LEAD_NAME: Final[str] = "".join((BUILTIN_GLYPH, "lead"))

#: The tolerance the approximate comparisons read, and what it holds until a
#: program sets it.  APL's own tolerance defaults to the same number.
TOLERANCE_NAME: Final[str] = "".join((BUILTIN_GLYPH, "tolerance"))
TOLERANCE_DEFAULT: Final[float] = 1e-13

#: The environment, which a program reads under this name and never writes.  It
#: is the compiler's to provide for the reason the heap is: what it stands for
#: is made before the program runs, out of what only the entry point can reach.
ENVIRON_NAME: Final[str] = "".join((BUILTIN_GLYPH, "environ"))

#: What makes a cursor over a list: `\N{APL FUNCTIONAL SYMBOL QUAD}iter(l)` is where a walk over `l` begins.  It
#: is given the *place* the list is in and not the list, because a walk may take
#: an element out and what is left has to go back where the list was.
ITER_NAME: Final[str] = "".join((BUILTIN_GLYPH, "iter"))

#: The logical operators, which work on truth values and on nothing else.  Each
#: is a glyph, and none has an ASCII substitute: the candidates would be `&&`,
#: `||` and `!`, and spelling two of them with the characters the *bitwise*
#: operators use is the one confusion this language is built to avoid.  `and`
#: and `or` are words rather than glyphs because they differ from `\N{LOGICAL AND}` and `\N{LOGICAL OR}`
#: in when they evaluate their right operand, which is a thing a reader has to
#: be told rather than shown.
AND_GLYPH: Final[str] = "\N{LOGICAL AND}"
OR_GLYPH: Final[str] = "\N{LOGICAL OR}"
XOR_GLYPH: Final[str] = "\N{CIRCLED PLUS}"
NAND_GLYPH: Final[str] = "\N{NAND}"
NOR_GLYPH: Final[str] = "\N{NOR}"
NOT_GLYPH: Final[str] = "\N{NOT SIGN}"

#: The arithmetic that faults where the answer will not fit.  Multiplication is
#: a glyph rather than an asterisk because the asterisk is one character and
#: says nothing; division is the same.
TIMES_GLYPH: Final[str] = "\N{MULTIPLICATION SIGN}"
DIVIDE_GLYPH: Final[str] = "\N{DIVISION SIGN}"

#: Moving the bits of a number sideways.  The two angle quotation marks point
#: the way the bits go, and the two circle arrows turn them round.  A shift
#: drops what falls off the end; a rotation puts it back at the other end.
SHIFT_LEFT_GLYPH: Final[str] = "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}"
SHIFT_RIGHT_GLYPH: Final[str] = "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"
ROTATE_LEFT_GLYPH: Final[str] = "\N{ANTICLOCKWISE OPEN CIRCLE ARROW}"
ROTATE_RIGHT_GLYPH: Final[str] = "\N{CLOCKWISE OPEN CIRCLE ARROW}"

#: The saturating operations, which answer with the nearest value their type can
#: hold rather than going past it.  Each is the sign of the operation it is built
#: from, in a box: what the box says is that the answer stays inside something.
SAT_ADD_GLYPH: Final[str] = "\N{SQUARED PLUS}"
SAT_SUB_GLYPH: Final[str] = "\N{SQUARED MINUS}"
SAT_MUL_GLYPH: Final[str] = "\N{SQUARED TIMES}"

#: Joining two arrays end to end.  A plus doubled, which is what every language
#: that has a notation for this uses: one array after another is not an addition
#: but it is the nearest thing to one, and the doubling is what says "of the
#: things, not of the values".
CONCAT_GLYPH: Final[str] = "\N{DOUBLE PLUS}"

#: The shape of an array, and the making of one with a shape.  It is APL's rho
#: and does APL's two jobs: written before one thing it answers that thing's
#: shape, and written between two it makes something of the shape on its left
#: out of the values on its right.
SHAPE_GLYPH: Final[str] = "\N{APL FUNCTIONAL SYMBOL RHO}"

#: The larger and the smaller of two things, and the largest and smallest of
#: several.  They are APL's ceiling and floor, which do these two jobs there and
#: whose shapes say which is which: the one open at the top takes the top.
MAX_GLYPH: Final[str] = "\N{LEFT CEILING}"
MIN_GLYPH: Final[str] = "\N{LEFT FLOOR}"

#: The four roundings of a floating-point number, written before it.  The arrows
#: say which way the value moves: down to the whole number below it, up to the
#: one above, either way to whichever is nearer, and the double arrow to
#: whichever the processor's own rounding mode says.
FLOOR_GLYPH: Final[str] = "\N{DOWNWARDS ARROW}"
CEILING_GLYPH: Final[str] = "\N{UPWARDS ARROW}"
NEAREST_GLYPH: Final[str] = "\N{UP DOWN ARROW}"
ROUNDED_GLYPH: Final[str] = "\N{UP DOWN DOUBLE ARROW}"

#: Raising something to a power.  The glyph is the letter mathematics writes an
#: exponent that is not a number with, raised the way an exponent is written --
#: so `a \N{SUPERSCRIPT LATIN SMALL LETTER N} b` and `a\N{SUPERSCRIPT TWO}` are the same operation written the two ways it is
#: written on paper.
POWER_GLYPH: Final[str] = "\N{SUPERSCRIPT LATIN SMALL LETTER N}"

#: Whether one number divides another without anything left over.  Written
#: before one operand it asks whether two does, which is the same question as
#: whether the number is even.
DIVIDES_GLYPH: Final[str] = "\N{DIVIDES}"
NOT_DIVIDES_GLYPH: Final[str] = "\N{DOES NOT DIVIDE}"

#: Lifting what is written between them out of the program and into the
#: compiler: `\N{TOP LEFT CORNER}u32\N{TOP RIGHT CORNER}` is the type and not a value of it, and `\N{TOP LEFT CORNER}a\N{TOP RIGHT CORNER}` is the name and
#: not what it stands for.  The brackets are what keeps the grammar
#: context-free: a type's name and a value's name are both identifiers, and a
#: type written out in full is not an expression at all, so without them what
#: follows `\N{APL FUNCTIONAL SYMBOL QUAD}typeof` would have to be decided by what the names turned out to
#: mean.
LIFT_OPEN_GLYPH: Final[str] = "\N{TOP LEFT CORNER}"
LIFT_CLOSE_GLYPH: Final[str] = "\N{TOP RIGHT CORNER}"

#: What reads through a reference, written after it.  `&` says a type is a
#: reference and `⌖` says what is at the place one names, which are the two
#: halves of the same idea and the only two marks a reference needs: `r ← &n`
#: binds the name to a place, `r⌖ ← 3` writes that place.  It stands after its
#: operand so that reaching further into what it answers -- an element of it, a
#: field of it -- reads left to right without brackets, which is what Pascal,
#: Modula, Ada and Odin put a mark after a pointer for.  U+2316 is the one
#: glyph in Unicode whose name says "position", which is what a reference
#: holds, and it is in no family with the arrows the roundings use.
DEREF_GLYPH: Final[str] = "\N{POSITION INDICATOR}"

#: What walks a value: `ps¨.age` is the field of each element, `f(v¨)` the call
#: made for each.  APL's each, written after the operand for the reason the mark
#: of a reference is -- what is done to each element reads left to right after
#: it.  Written twice it walks two dimensions.
EACH_GLYPH: Final[str] = "\N{DIAERESIS}"

#: What says a unit follows.  A unit is part of a type and not a type of its
#: own, so it is written after one -- `u64 ¤meter` -- and the mark is what
#: tells the unit from the array suffix that may follow it.  U+00A4 is the
#: currency sign, which stands for "some unit of account" and for nothing else
#: in this language.
UNIT_GLYPH: Final[str] = "\N{CURRENCY SIGN}"

#: What begins a function written where a value is wanted.  The letter
#: mathematics has used for one since Church, and the letter every language
#: that has the idea names it after -- so a reader who has met the idea at all
#: has met this mark.
LAMBDA_GLYPH: Final[str] = "\N{GREEK SMALL LETTER LAMDA}"

#: What says a lifetime name follows.  A lifetime is how long what a reference
#: names lives, so the mark is the one thing in Unicode that means "how long":
#: U+29D6 WHITE HOURGLASS.  The name comes after it -- `&mut \N{WHITE HOURGLASS}x u32` -- where
#: `static` would stand, the two being the same slot answering the same
#: question.  One character, so it has no ASCII substitute and claims nothing.
LIFETIME_GLYPH: Final[str] = "\N{WHITE HOURGLASS}"

#: The one word the language reads where nothing else could stand, and which
#: is therefore not a keyword: a program may still have a type called `static`.
#: It is read after `&` and only where a type follows it, so a reference to a
#: type of that name still reads.  Taking the word outright would have cost
#: every program a name a range wants.
LASTING_WORD: Final[str] = "static"

#: The digits written raised, which is how an exponent that *is* a number is
#: written.  The first three are where Latin-1 put them and the rest are where
#: Unicode put the ones Latin-1 had not got, which is why this is a table and
#: not a range.
SUPERSCRIPT_DIGITS: Final[dict[str, int]] = {
    "\N{SUPERSCRIPT ZERO}": 0, "\N{SUPERSCRIPT ONE}": 1,
    "\N{SUPERSCRIPT TWO}": 2, "\N{SUPERSCRIPT THREE}": 3,
    "\N{SUPERSCRIPT FOUR}": 4, "\N{SUPERSCRIPT FIVE}": 5,
    "\N{SUPERSCRIPT SIX}": 6, "\N{SUPERSCRIPT SEVEN}": 7,
    "\N{SUPERSCRIPT EIGHT}": 8, "\N{SUPERSCRIPT NINE}": 9,
}

#: Accepted substitute for the arrow.  Two characters, so it claims nothing.
ARROW_ASCII: Final[str] = "->"

#: Every ASCII substitute, and the glyph it stands for.  Each is at least two
#: characters, by the rule at the top of this module, and each is tried before
#: the single characters are, since '<=' begins with one of them.
ASCII_SUBSTITUTES: Final[dict[str, str]] = {
    ARROW_ASCII: ARROW_GLYPH,
    "<=": LESS_EQUAL_GLYPH,
    ">=": GREATER_EQUAL_GLYPH,
    # This language has no operator that adds one to something, so two plus
    # signs are not the beginning of anything else.  It is Haskell's spelling of
    # the same operation.
    "++": CONCAT_GLYPH,
}


class TokKind(StrEnum):
    """The kinds of token the lexer produces."""

    IDENT = "identifier"
    INT = "integer literal"
    FLOAT = "floating-point literal"
    STRING = "string literal"
    CHAR = "character literal"

    KW_FN = "'fn'"
    KW_RETURN = "'return'"
    KW_BREAK = "'break'"
    KW_CONTINUE = "'continue'"
    KW_DEFER = "'defer'"
    KW_LET = "'let'"
    KW_MUT = "'mut'"
    KW_UNIT = "'unit'"
    KW_TYPE = "'type'"
    #: A name written between grave accents, which is an operator standing where
    #: a name goes: `` `+` `` is the name of the operator and not the operator.
    #: What marks a hole in a pattern and what fills one in a template.
    DOLLAR = "'$'"
    OPNAME = "an operator name"
    #: A glyph the language gives no meaning, standing where an operator stands.
    #: What it means is what a program said it means, and nothing where a program
    #: said nothing.
    OPERATOR = "an operator"
    #: A bracket the language gives no meaning, opening or closing a pair a
    #: program defined.  Two kinds rather than one, because where a bracket may
    #: stand depends on which of the two it is.
    OPEN_OPERATOR = "an opening bracket"
    CLOSE_OPERATOR = "a closing bracket"

    KW_PRE = "'pre'"
    KW_POST = "'post'"
    KW_BUNDLE = "'bundle'"
    KW_MACRO = "'macro'"
    KW_MATCH = "'match'"
    KW_ENUM = "'enum'"
    KW_IF = "'if'"
    KW_ELIF = "'elif'"
    KW_ELSE = "'else'"
    KW_WHILE = "'while'"
    KW_UNLESS = "'unless'"
    KW_FOREACH = "'foreach'"
    KW_COMPTIME = "'comptime'"
    KW_IN = "'in'"
    KW_TRUE = "'true'"
    KW_FALSE = "'false'"
    KW_AND = "'and'"
    KW_OR = "'or'"

    AT_LBRACKET = "'@['"
    LPAREN = "'('"
    RPAREN = "')'"
    LBRACKET = "'['"
    RBRACKET = "']'"
    LBRACE = "'{'"
    RBRACE = "'}'"
    COMMA = "','"
    COLON = "':'"
    SEMICOLON = "';'"
    EQUALS = "'='"
    ARROW = "'\N{RIGHTWARDS ARROW}'"
    ASSIGN = "'\N{LEFTWARDS ARROW}'"

    DOT = "'.'"
    AMPERSAND = "'&'"
    PIPE = "'|'"
    CARET = "'^'"
    TILDE = "'~'"

    NOT_EQUAL = "'\N{NOT EQUAL TO}'"
    LESS = "'<'"
    GREATER = "'>'"
    LESS_EQUAL = "'\N{LESS-THAN OR EQUAL TO}'"
    GREATER_EQUAL = "'\N{GREATER-THAN OR EQUAL TO}'"

    TUPLE_OPEN = "'\N{LEFT ANGLE BRACKET}'"
    TUPLE_CLOSE = "'\N{RIGHT ANGLE BRACKET}'"

    TAKE = "'\N{DAGGER}'"
    NEXT = "'\N{UPWARDS WHITE ARROW}'"
    PREV = "'\N{DOWNWARDS WHITE ARROW}'"
    SET_OPEN = "'\N{LEFT DOUBLE PARENTHESIS}'"
    SET_CLOSE = "'\N{RIGHT DOUBLE PARENTHESIS}'"

    SPREAD = "'\N{ASTERISM}'"
    LABEL = "'\N{SECTION SIGN}'"

    ARRAY_OPEN = "'\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}'"
    ARRAY_CLOSE = "'\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}'"

    RANGE = "'\N{HORIZONTAL ELLIPSIS}'"

    QUESTION = "'?'"
    BOTTOM = "'\N{UP TACK}'"
    OR_ELSE = "'??'"

    ALIKE = "'\N{APPROXIMATELY EQUAL TO}'"
    UNALIKE = "'\N{NEITHER APPROXIMATELY NOR ACTUALLY EQUAL TO}'"
    BELOW_OR_ALIKE = "'\N{LESS-THAN OR APPROXIMATE}'"
    ABOVE_OR_ALIKE = "'\N{GREATER-THAN OR APPROXIMATE}'"
    BELOW_NOT_ALIKE = "'\N{LESS-THAN AND NOT APPROXIMATE}'"
    ABOVE_NOT_ALIKE = "'\N{GREATER-THAN AND NOT APPROXIMATE}'"

    LOGIC_AND = "'\N{LOGICAL AND}'"
    LOGIC_OR = "'\N{LOGICAL OR}'"
    LOGIC_XOR = "'\N{CIRCLED PLUS}'"
    LOGIC_NAND = "'\N{NAND}'"
    LOGIC_NOR = "'\N{NOR}'"
    LOGIC_NOT = "'\N{NOT SIGN}'"

    PLUS = "'+'"
    MINUS = "'-'"
    TIMES = "'\N{MULTIPLICATION SIGN}'"
    DIVIDE = "'\N{DIVISION SIGN}'"
    PERCENT = "'%'"
    SHIFT_LEFT = "'\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}'"
    SHIFT_RIGHT = "'\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}'"
    ROTATE_LEFT = "'\N{ANTICLOCKWISE OPEN CIRCLE ARROW}'"
    ROTATE_RIGHT = "'\N{CLOCKWISE OPEN CIRCLE ARROW}'"

    SAT_ADD = "'\N{SQUARED PLUS}'"
    SAT_SUB = "'\N{SQUARED MINUS}'"
    SAT_MUL = "'\N{SQUARED TIMES}'"

    CONCAT = "'\N{DOUBLE PLUS}'"
    LENGTH = "'#'"
    SHAPE = "'\N{APL FUNCTIONAL SYMBOL RHO}'"
    MAX = "'\N{LEFT CEILING}'"
    MIN = "'\N{LEFT FLOOR}'"
    FLOOR = "'\N{DOWNWARDS ARROW}'"
    CEILING = "'\N{UPWARDS ARROW}'"
    NEAREST = "'\N{UP DOWN ARROW}'"
    ROUNDED = "'\N{UP DOWN DOUBLE ARROW}'"
    POWER = "'\N{SUPERSCRIPT LATIN SMALL LETTER N}'"
    DIVIDES = "'\N{DIVIDES}'"
    NOT_DIVIDES = "'\N{DOES NOT DIVIDE}'"
    LIFT_OPEN = "'\N{TOP LEFT CORNER}'"
    LIFT_CLOSE = "'\N{TOP RIGHT CORNER}'"
    DEREF = "'\N{POSITION INDICATOR}'"
    EACH = "'\N{DIAERESIS}'"
    UNIT = "'\N{CURRENCY SIGN}'"
    LAMBDA = "'\N{GREEK SMALL LETTER LAMDA}'"
    LIFETIME = "'\N{WHITE HOURGLASS}'"
    #: A number written raised, which is an exponent and its operator at once.
    EXPONENT = "a raised number"

    DOC_COMMENT = "documentation comment"
    NEWLINE = "end of line"
    INDENT = "indentation"
    DEDENT = "end of indented block"
    EOF = "end of file"


#: The types a floating-point literal may name with its suffix.  There is no
#: untyped one: a literal says what it is, as an integer literal does.
FLOAT_TYPE_NAMES: Final[frozenset[str]] = frozenset(("f32", "f64"))

#: The types an integer literal may name with its suffix.  Taken from the type
#: table rather than listed again, so that the two cannot disagree.
INTEGER_TYPE_NAMES: Final[frozenset[str]] = frozenset(
    name for name, ty in BUILTIN_TYPES.items() if isinstance(ty, IntType))


#: The glyphs the language itself uses as an operator and that Unicode does not
#: call a symbol: a dash, a modifier symbol, two brackets, two quotation marks and
#: a raised letter.  They qualify because the language already treats them as
#: operators, which is what makes every builtin operator writable in the notation.
#: A test checks this against the parser's own tables, so it cannot go stale.
OPERATOR_GLYPHS: Final[frozenset[str]] = frozenset({
    "-", "^", "&", "%", "#",
    MAX_GLYPH, MIN_GLYPH, SHIFT_LEFT_GLYPH, SHIFT_RIGHT_GLYPH, POWER_GLYPH,
})

#: The glyphs a program may not name, whatever Unicode calls them.  Two groups.
#:
#: **Operators a function cannot stand for.**  `\N{LOGICAL AND}` and `\N{LOGICAL OR}` decide *whether* to work
#: something out, and a function takes its arguments already worked out -- so one
#: written for them would change when things happen and not what they mean, which
#: is the trap C++ left open on `&&`.  `?` and its pair are about a result rather
#: than about what a result holds, and `\N{POSITION INDICATOR}` is about a reference.
#:
#: **Glyphs with a meaning that is not an operator**, which Unicode calls symbols
#: all the same: the arrow of a signature, the arrow that binds a name, the failure
#: value, the quad that begins a name the compiler provides, a lifetime, the two
#: lifting marks, and the raised minus of a negative literal.
OPERATORS_NOT_NAMEABLE: Final[frozenset[str]] = frozenset({
    AND_GLYPH, OR_GLYPH, QUESTION_GLYPH, DEREF_GLYPH, EACH_GLYPH,
    ARROW_GLYPH, ASSIGN_GLYPH, BOTTOM_GLYPH, BUILTIN_GLYPH, LIFETIME_GLYPH,
    LIFT_OPEN_GLYPH, LIFT_CLOSE_GLYPH, NEGATIVE_GLYPH,
    # And the brackets the grammar needs for itself: a group, a list of
    # attributes, a block, and the members of a tuple.  The array brackets and the
    # collection brackets are *not* here: the language uses them as operators, so
    # a program may say what they mean for a type of its own, exactly as it may
    # for `+`.
    "(", ")", "[", "]", "{", "}", TUPLE_OPEN_GLYPH, TUPLE_CLOSE_GLYPH,
})

KEYWORDS: Final[dict[str, TokKind]] = {
    "fn": TokKind.KW_FN,
    "return": TokKind.KW_RETURN,
    "let": TokKind.KW_LET,
    "mut": TokKind.KW_MUT,
    "unit": TokKind.KW_UNIT,
    "type": TokKind.KW_TYPE,
    "pre": TokKind.KW_PRE,
    "post": TokKind.KW_POST,
    "bundle": TokKind.KW_BUNDLE,
    "macro": TokKind.KW_MACRO,
    "match": TokKind.KW_MATCH,
    "enum": TokKind.KW_ENUM,
    "if": TokKind.KW_IF,
    "elif": TokKind.KW_ELIF,
    "else": TokKind.KW_ELSE,
    "while": TokKind.KW_WHILE,
    "unless": TokKind.KW_UNLESS,
    "foreach": TokKind.KW_FOREACH,
    "comptime": TokKind.KW_COMPTIME,
    "break": TokKind.KW_BREAK,
    "continue": TokKind.KW_CONTINUE,
    "defer": TokKind.KW_DEFER,
    "in": TokKind.KW_IN,
    "true": TokKind.KW_TRUE,
    "false": TokKind.KW_FALSE,
    "and": TokKind.KW_AND,
    "or": TokKind.KW_OR,
}


@dataclass(frozen=True, slots=True)
class Token:
    """One token, with the source text it came from."""

    kind: TokKind
    span: Span
    text: str = ""
    #: Value of an integer literal, or ``None`` for every other kind.
    int_value: int | None = None
    #: Value of a floating-point literal, and the type it named.  The value is
    #: kept as the number it is and not as the bits, so that what is written in
    #: the source and what reaches the image are one rounding apart and not two.
    float_value: float | None = None
    float_type: str | None = None
    #: The type an integer literal named with its suffix, if it named one.
    int_type: str | None = None
    #: Decoded value of a string literal, or ``None`` for every other kind.
    str_value: str | None = None

    def describe(self) -> str:
        """How this token is named in a diagnostic."""
        if self.kind in (TokKind.IDENT, TokKind.INT, TokKind.FLOAT):
            return "".join((str(self.kind), " '", self.text, "'"))
        return str(self.kind)
