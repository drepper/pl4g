"""A string or a list answered without its allocator, where the caller knows it.

Run tests cannot see this: an allocator word that came back wrong makes the give-back
dispatch do nothing, and the program still answers what it should.  So what is
checked is the code -- that the function answers two words and that every call puts
back the allocator it knows.
"""

from __future__ import annotations

from conftest import describe

PROGRAM = """\
fn joined(a: str, b: str) \N{RIGHTWARDS ARROW} str:
    a \N{DOUBLE PLUS} b

fn sometimes(c: bool) \N{RIGHTWARDS ARROW} str:
    if c:
        return "x"
    "y" \N{DOUBLE PLUS} "z"

fn fixed(c: bool) \N{RIGHTWARDS ARROW} str in \N{APL FUNCTIONAL SYMBOL QUAD}heap:
    if c:
        return "x"
    "y" \N{DOUBLE PLUS} "z"

fn into(a: &mut arena, s: str) \N{RIGHTWARDS ARROW} str in a:
    s \N{DOUBLE PLUS} "!" in a

@[startup]
fn main() \N{RIGHTWARDS ARROW} u6:
    let p: mut arena = \N{APL FUNCTIONAL SYMBOL QUAD}arena
    defer \N{APL FUNCTIONAL SYMBOL QUAD}empty(p)
    let one: str = joined("a", "b")
    let two: str = sometimes(true)
    let three: str = fixed(false)
    let four: str = into(&mut p, "q")
    let kept: str\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}into(&mut p, "w")\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} in p
    if one = "ab" \N{LOGICAL AND} two = "x" \N{LOGICAL AND} three = "yz" \N{LOGICAL AND} four = "q!" \N{LOGICAL AND} kept\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}0\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} = "w!": 0u6 else: 1u6
"""

#: What a function answering two words says it answers.
THIN = "\N{RIGHTWARDS ARROW} \N{LEFT ANGLE BRACKET}ptr<mut u8>, u64\N{RIGHT ANGLE BRACKET}"


def _ir(compile_source) -> str:  # noqa: ANN001
    proc, output = compile_source(PROGRAM, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    return output.read_text(encoding="utf-8")


def _signature(text: str, name: str) -> str:
    return next(line for line in text.splitlines()
                if line.startswith("".join(("fn @", name, "("))))


def test_a_join_answers_two_words(compile_source) -> None:  # noqa: ANN001
    """Every way out answers what the heap just made, which the body shows."""
    assert THIN in _signature(_ir(compile_source), "joined")


def test_text_in_the_image_keeps_the_allocator(compile_source) -> None:  # noqa: ANN001
    """A literal on one way out is not the heap's, and saying nothing keeps it so:
    the answer carries its allocator, which says none for that one."""
    assert "\N{RIGHTWARDS ARROW} str " in _signature(_ir(compile_source), "sometimes")


def test_saying_heap_fixes_it(compile_source) -> None:  # noqa: ANN001
    """`in ⎕heap` puts the literal in the heap too, so the answer is thin."""
    text = _ir(compile_source)
    assert THIN in _signature(text, "fixed")
    body = text.split("fn @fixed(")[1].split("\n}")[0]
    assert "call" in body, body


def test_an_arena_with_text_in_the_image_keeps_the_allocator(
        compile_source) -> None:  # noqa: ANN001
    """`in a` lets a literal through as it is, so where one is answered the
    answer carries its allocator rather than copying the literal into `a`."""
    proc, output = compile_source(PROGRAM.replace(
        "    s \N{DOUBLE PLUS} \"!\" in a\n",
        "    if #s = 0:\n        return \"none\"\n    s \N{DOUBLE PLUS} \"!\" in a\n"),
        "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "\N{RIGHTWARDS ARROW} str " in _signature(text, "into")


def test_the_caller_puts_the_allocator_back(compile_source) -> None:  # noqa: ANN001
    """The heap's address for one, and the arena handed over for the other."""
    text = _ir(compile_source)
    main = text.split("fn @main(")[1].split("\n}")[0]
    # The answers of the heap's are only compared, which reads where and how
    # many: none is needed whole, so the heap's address is never put back.
    assert "address.ptr<mut arena> @__pl4g_heap" not in main, main
    assert THIN in _signature(text, "into")
    # The call to `into` hands over the arena first, and where what it answers is
    # needed whole -- stored in an array -- the string rebuilt from it names that
    # same value as its allocator.  Where it is only compared it stays two words.
    lines = main.splitlines()
    arena = next(line for line in lines
                 if "frame.ptr<mut arena>" in line).split("=")[0].strip()
    rebuilt = [line for line in lines if "tuple.str" in line
               and line.rstrip().endswith("".join((", ", arena)))]
    assert rebuilt, main
