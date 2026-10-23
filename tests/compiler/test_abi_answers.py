"""A record laid out for C, answered whole: what each convention makes of it.

A function of the system's convention answers an `@[abi]` record as C does on
the architecture, which the representation after `largeanswers` says in the
function's signature: the words it answers in, or the room handed over first.
These are the shapes a C compiler gives the same `struct`s -- GCC's, read off
its output for each of the three -- so a classification that drifts is found
here, on every target, rather than only where a C host runs natively.
"""

from __future__ import annotations

import pytest

from conftest import describe, run_compiler

ARROW = "\N{RIGHTWARDS ARROW}"
OPEN = "\N{LEFT ANGLE BRACKET}"
CLOSE = "\N{RIGHT ANGLE BRACKET}"

#: The records, by name, as a definition writes their fields.
RECORDS = {
    "I8": "a : i32 ; b : i32",
    "F8": "a : f32 ; b : f32",
    "M8": "a : f32 ; b : i32",
    "I16": "a : i64 ; b : i64",
    "M16": "a : i32 ; b : f32 ; c : f64",
    "D16": "a : f64 ; b : i64",
    "F12": "a : f32 ; b : f32 ; c : f32",
    "F24": "a : f64 ; b : f64 ; c : f64",
    "I40": "a : i64 ; b : i64 ; c : i64 ; d : i64 ; e : i64",
    # An array's elements are members of their own to C.
    "FArr": "v : f32\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}2\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET} ; w : f64",
    "DArr": "v : f64\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}3\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}",
}

#: How a record whose fields a literal of their type does not make is made.
MADE = {
    "FArr": "FArr(.v \N{LEFTWARDS ARROW} \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1f32, 2f32\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}, .w \N{LEFTWARDS ARROW} 3f64)",
    "DArr": "DArr(.v \N{LEFTWARDS ARROW} \N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}1f64, 2f64, 3f64\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET})",
}


def _tuple(*members: str) -> str:
    return "".join((OPEN, ", ".join(members), CLOSE))


#: What each architecture answers each record in: the answer of the signature,
#: or `None` where it goes through room handed over first.
SHAPES = {
    "x86_64-linux-none": {
        "I8": "u64", "F8": "f64", "M8": "u64", "I16": _tuple("i64", "i64"),
        "M16": _tuple("u64", "f64"), "D16": _tuple("f64", "i64"),
        "F12": _tuple("f64", "f32"), "F24": None, "I40": None,
        "FArr": _tuple("f64", "f64"), "DArr": None,
    },
    "aarch64-linux-none": {
        "I8": "u64", "F8": _tuple("f32", "f32"), "M8": "u64",
        "I16": _tuple("i64", "i64"), "M16": _tuple("u64", "u64"),
        "D16": _tuple("u64", "i64"), "F12": _tuple("f32", "f32", "f32"),
        "F24": _tuple("f64", "f64", "f64"), "I40": None,
        "FArr": _tuple("u64", "u64"), "DArr": _tuple("f64", "f64", "f64"),
    },
    "riscv64-linux-none": {
        "I8": "u64", "F8": _tuple("f32", "f32"), "M8": _tuple("f32", "i32"),
        "I16": _tuple("i64", "i64"), "M16": _tuple("u64", "u64"),
        "D16": _tuple("f64", "i64"), "F12": _tuple("u64", "u64"), "F24": None,
        "I40": None, "FArr": _tuple("u64", "u64"), "DArr": None,
    },
}


def _program() -> str:
    """A function of each convention answering each record."""
    lines: list[str] = []
    for name, fields in RECORDS.items():
        made = MADE.get(name) or "".join((name, "(", ", ".join(
            "".join((".", field.split(":")[0].strip(), " \N{LEFTWARDS ARROW} 1",
                     field.split(":")[1].strip()))
            for field in fields.split(";")), ")"))
        lines.extend(("@[abi]", "".join(("type ", name, " = ", fields)), ""))
        lines.extend(("@[cdecl, opaque]", "".join(("fn c_", name, "() ", ARROW, " ",
                                                    name, ":")),
                      "".join(("    ", made)), ""))
        lines.extend(("@[opaque]", "".join(("fn p_", name, "() ", ARROW, " ", name, ":")),
                      "".join(("    ", made)), ""))
    lines.extend(("let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")", "",
                  "@[startup]", "".join(("fn main(init: std.Init) ", ARROW, " u6:"))))
    # Called, or nothing reaches them and they are not compiled at all.
    for name in RECORDS:
        lines.extend("".join(("    _ \N{LEFTWARDS ARROW} ", side, "_", name, "()"))
                     for side in "cp")
    lines.extend(("    0u6", ""))
    return "\n".join(lines)


@pytest.fixture(scope="module")
def signatures(tmp_path_factory) -> dict[str, dict[str, str]]:  # noqa: ANN001
    """Every function's signature line, by its name, for each target."""
    folder = tmp_path_factory.mktemp("abi-answers")
    source = folder / "t.pl4g"
    source.write_text(_program(), encoding="utf-8")
    found: dict[str, dict[str, str]] = {}
    for triple in SHAPES:
        output = folder / "".join((triple, ".ir"))
        proc = run_compiler(["--emit=ir", "-O0", "".join(("--target=", triple)),
                             "-o", str(output), str(source)])
        assert proc.returncode == 0, describe(proc)
        lines = output.read_text(encoding="utf-8").splitlines()
        found[triple] = {line.split("(", 1)[0].removeprefix("fn @"): line
                         for line in lines if line.startswith("fn @")}
    return found


@pytest.mark.parametrize("triple", sorted(SHAPES))
@pytest.mark.parametrize("name", sorted(RECORDS))
def test_the_system_convention_answers_as_c_does(signatures, triple: str,  # noqa: ANN001
                                                 name: str) -> None:
    """In the registers C answers the record in, or through room handed over
    first -- and on x86-64 answered back, in `rax`."""
    line = signatures[triple]["".join(("c_", name))]
    wanted = SHAPES[triple][name]
    if wanted is None:
        place = "".join(("ptr<mut ", name, ">"))
        back = place if triple.startswith("x86_64") else "void"
        assert line.startswith("".join(("fn @c_", name, "(", place, ") ", ARROW, " ",
                                        back, " "))), line
        assert "answer-in-storage answer-first" in line, line
    else:
        assert "".join((") ", ARROW, " ", wanted, " ")) in line, line
        assert "answer-in-storage" not in line, line


@pytest.mark.parametrize("triple", sorted(SHAPES))
@pytest.mark.parametrize("name", sorted(RECORDS))
def test_the_own_convention_answers_through_the_callers_room(  # noqa: ANN001
        signatures, triple: str, name: str) -> None:
    """Whatever the size, the place last, as every large answer has it."""
    line = signatures[triple]["".join(("p_", name))]
    assert line.startswith("".join(("fn @p_", name, "(ptr<mut ", name, ">) ", ARROW,
                                    " void "))), line
    assert "answer-first" not in line, line


def test_a_parameter_by_value_is_still_refused(tmp_path) -> None:  # noqa: ANN001
    """Only the answer: a record laid out for C is handed to a function by
    reference, as before (4596)."""
    source = tmp_path / "t.pl4g"
    source.write_text("".join((
        "@[abi]\ntype P = a : i64 ; b : i64\n\n",
        "@[cdecl]\nfn f(p: P) ", ARROW, " P:\n    p\n\n",
        "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n\n",
        "@[startup]\nfn main(init: std.Init) ", ARROW, " u6:\n    0u6\n")),
        encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(source)])
    assert proc.returncode != 0, describe(proc)
    assert proc.stderr.count("[PL4G-4596]") == 1, proc.stderr
