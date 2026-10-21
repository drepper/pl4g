"""A `match` on a sum that is somewhere reads it where it is.

What a reference names, an element of a list or an array and a field of a
record are places, and a sum in one is taken apart there: the tag read before
any arm runs, the part the chosen arm binds as that arm begins, and nothing of
the sum copied.  These look at what is generated -- the representation, and the
machine code on each target -- for the absence of the copy, which a program's
answer cannot show.
"""

from __future__ import annotations

import re

import pytest

from conftest import compiler_targets, describe, run_compiler

ARROW = "\N{RIGHTWARDS ARROW}"
POSITION = "\N{POSITION INDICATOR}"

#: A value the size of a JSON document's, reached through a reference and
#: through an element of a list: thirty-two bytes, of which a match reads the
#: tag and the eight bytes of the number.
SOURCE = "".join((
    "type Json = null : void | flag : bool | number : f64 | text : str | items : [Json]\n\n",
    "@[visible]\n",
    "fn through(j: &Json) ", ARROW, " f64:\n",
    "    match j", POSITION, ":\n",
    "        f64(x):\n",
    "            x\n",
    "        _:\n",
    "            0f64\n\n",
    "@[visible]\n",
    "fn element(l: [Json], i: u64 \N{CURRENCY SIGN}idx) ", ARROW, " f64:\n",
    "    match l\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}i\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}:\n",
    "        f64(x):\n",
    "            x\n",
    "        _:\n",
    "            0f64\n\n",
    "fn count(j: &Json) ", ARROW, " u32:\n",
    "    match j", POSITION, ":\n",
    "        [Json](l):\n",
    "            let n: mut u32 = 1u32\n",
    "            foreach &e: Json = l:\n",
    "                n \N{LEFTWARDS ARROW} n + count(e)\n",
    "            n\n",
    "        _:\n",
    "            1u32\n\n",
    "let std := \N{APL FUNCTIONAL SYMBOL QUAD}import(\"std\")\n\n",
    "@[startup]\nfn main(init: std.Init) ", ARROW, " u6:\n",
    "    let j: Json = Json(.items \N{LEFTWARDS ARROW} [Json(.null \N{LEFTWARDS ARROW} true)])\n",
    "    if count(&j) = 2u32: 0u6 else: 1u6\n"))


def emitted(tmp_path, kind: str, *extra: str) -> str:  # noqa: ANN001
    """What the compiler wrote for SOURCE when asked to stop at *kind*."""
    path = tmp_path / "t.pl4g"
    path.write_text(SOURCE, encoding="utf-8")
    out = tmp_path / "out"
    proc = run_compiler(["-o", str(out), "".join(("--emit=", kind)), *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    return out.read_text(encoding="utf-8")


def function_of(text: str, starts: str) -> list[str]:
    """The lines of the function whose first line begins with *starts*: up to
    the closing brace in the representation, and in a dump up to the next line
    in the first column that is not a label, a label beginning with a dot."""
    lines = text.splitlines()
    first = next(at for at, line in enumerate(lines) if line.startswith(starts))
    found: list[str] = []
    for line in lines[first + 1:]:
        if line.startswith("}") or (not starts.startswith("fn ") and line
                                    and not line.startswith((" ", "."))):
            break
        found.append(line)
    return found


@pytest.mark.parametrize("name", ["through", "element"])
def test_nothing_of_the_sum_is_copied(tmp_path, name: str) -> None:  # noqa: ANN001
    """The representation reads the tag and the number from the place, and
    makes no room to copy the sum into -- at `-O0`, where nothing after the
    checker could have taken a copy away."""
    body = function_of(emitted(tmp_path, "ir", "-O0"), "".join(("fn @", name, "(")))
    assert not any("frame" in line for line in body), "\n".join(body)
    loads = [line for line in body if " load." in line]
    assert [re.search(r"load\.(\S+)", line).group(1) for line in loads] == ["u8", "f64"], \
        "\n".join(body)


def test_a_recursive_value_is_walked_without_a_copy(tmp_path) -> None:  # noqa: ANN001
    """A walk down a recursive value through references -- each element of the
    list lent to the call below, which matches on what it names -- makes no
    room for a copy at any depth."""
    body = function_of(emitted(tmp_path, "ir", "-O0"), "fn @count(")
    assert body
    assert not any("frame" in line for line in body), "\n".join(body)


@pytest.mark.parametrize("opt", ["-O0", "-O1"])
@pytest.mark.parametrize("triple", compiler_targets())
def test_and_the_machine_code_has_no_frame(tmp_path, triple: str, opt: str) -> None:  # noqa: ANN001
    """With nothing copied there is nothing to copy into: the function reading
    through a reference makes no frame and writes no memory at all."""
    dump = emitted(tmp_path, "asm", opt, "".join(("--target=", triple)))
    body = function_of(dump, "through(")
    assert body, dump
    named = [line for line in body if re.search(r"\b(r?sp)\b", line)]
    assert named == [], "\n".join(body)
