"""What `constinit` variables are given before the program starts, read-only after.

A `constinit` variable's initializer is a function of its own, which the entry
point calls before the constructors; the variable is in a section of its own,
`.pl4g.relro`, which the writer puts in a segment of its own and the entry point
makes read-only once the constructors have run.  These check what that rests
on: that the checker refuses every way of writing one, that the pages are
nothing else's, that a write the checker cannot see -- through `⎕at` of the
address -- stops the program on the fault rather than changing what the rest of
it reads, and that an initializer the compiler can work out, or one nothing
reads, costs nothing at start.
"""

from __future__ import annotations

import signal
import subprocess

import pytest

import elfcheck
from conftest import (check_conformance, compiler_targets, describe, limited, run_compiler,
                      runner_for)

ARROW = "\N{RIGHTWARDS ARROW}"
GETS = "\N{LEFTWARDS ARROW}"
QUAD = "\N{APL FUNCTIONAL SYMBOL QUAD}"
OPEN, CLOSE = "\N{MATHEMATICAL LEFT WHITE SQUARE BRACKET}", "\N{MATHEMATICAL RIGHT WHITE SQUARE BRACKET}"
AT = "\N{POSITION INDICATOR}"
QUOTE, UNQUOTE = "\N{TOP LEFT CORNER}", "\N{TOP RIGHT CORNER}"
JOIN = "\N{DOUBLE PLUS}"

#: What the section is called, which a reader of the image is told.
SECTION = ".pl4g.relro"

#: The header that says which pages are read-only once written.
PT_GNU_RELRO = 0x6474E552

#: What a position-independent image is, as a shared object is.
ET_DYN = 3

#: What the page size is, which the auxiliary vector says and the compiler
#: cannot: what an initializer reading it gives is worked out at start.
PAGE = "".join((QUAD, "auxv(6) ?? 0u64"))

#: Initializers that run at start, one reading another, and a constructor
#: reading what they gave; the startup function reads them all.
WRITTEN = "".join((
    "let std := ", QUAD, "import(\"std\")\n\n",
    "type Point = x : u64 ; y : u64\n\n",
    "fn three(page: u64) ", ARROW, " u64:\n    if page = 0u64: 3u64 else: 3u64\n\n",
    "constinit let seed: u64 = point.x \N{MULTIPLICATION SIGN} 12u64 + 2u64\n\n",
    "constinit let point: Point = Point(.x ", GETS, " three(", PAGE, "), .y ", GETS, " 4u64)\n\n",
    "let plain: mut u64 = 0u64\n\n",
    "@[constructor, impure]\nfn fill():\n    plain ", GETS, " seed - 37u64\n\n",
    "@[startup]\nfn main(init: std.Init) ", ARROW, " u6:\n",
    "    ", QUAD, "narrow(seed + point.y + plain, ", QUOTE, "u6", UNQUOTE, ") ?? 63u6\n"))

#: And the startup function writes one through its address, which nothing can
#: check: the page is read-only by then.
FAULTS = "".join((
    "let std := ", QUAD, "import(\"std\")\n\n",
    "constinit let seed: u64 = ", PAGE, "\n\n",
    "@[startup, impure]\nfn main(init: std.Init) ", ARROW, " u6:\n",
    "    let at: u64 = ", QUAD, "address(&seed)\n",
    "    let there: &mut u64 = ", QUAD, "at(at, ", QUOTE, "&mut u64", UNQUOTE, ")\n",
    "    there", AT, " ", GETS, " 9u64\n",
    "    ", QUAD, "narrow(seed, ", QUOTE, "u6", UNQUOTE, ") ?? 63u6\n"))

#: Every way of writing one, each in a function of its own -- a constructor
#: among them, and a lambda inside one.
WRITES = "".join((
    "let std := ", QUAD, "import(\"std\")\n\n",
    "type Point = x : u32 ; y : u32\n\n",
    "fn filled(page: u64) ", ARROW, " u32", OPEN, "4", CLOSE, ":\n",
    "    ", OPEN, "0u32, 1u32, 2u32, ", QUAD, "narrow(page, ", QUOTE, "u32", UNQUOTE, ") ?? 0u32",
    CLOSE, "\n\n",
    "fn made(page: u64) ", ARROW, " [u8]:\n    [", QUAD, "narrow(page, ", QUOTE, "u8", UNQUOTE,
    ") ?? 0u8]\n\n",
    "fn at(page: u64) ", ARROW, " Point:\n    Point(.x ", GETS, " ", QUAD, "narrow(page, ", QUOTE,
    "u32", UNQUOTE, ") ?? 0u32, .y ", GETS, " 0u32)\n\n",
    "constinit let seed: u64 = ", PAGE, "\n\n",
    "constinit let table: u32", OPEN, "4", CLOSE, " = filled(", PAGE, ")\n\n",
    "constinit let point: Point = at(", PAGE, ")\n\n",
    "constinit let words: [u8] = made(", PAGE, ")\n\n",
    "fn bump(r: &mut u64) ", ARROW, " u64:\n    r", AT, " + 1u64\n\n",
    "@[impure]\nfn assigned():\n    seed ", GETS, " 2u64\n\n",
    "@[impure]\nfn element():\n    table", OPEN, "1", CLOSE, " ", GETS, " 3u32\n\n",
    "@[impure]\nfn field():\n    point.x ", GETS, " 3u32\n\n",
    "fn lent() ", ARROW, " u64:\n    bump(&mut seed)\n\n",
    "@[impure]\nfn walked():\n    foreach &mut e := table:\n        e", AT, " ", GETS, " 1u32\n\n",
    "@[impure]\nfn joined():\n    words ", JOIN, GETS, " [1u8]\n\n",
    "@[impure]\nfn viewed():\n    let v: mut u32", OPEN, CLOSE, " = table\n",
    "    v", OPEN, "0", CLOSE, " ", GETS, " 1u32\n\n",
    "@[impure]\nfn one_element():\n    let r: &mut u32 = &mut table", OPEN, "2", CLOSE, "\n",
    "    r", AT, " ", GETS, " 1u32\n\n",
    "@[constructor, impure]\nfn fill():\n    seed ", GETS, " 4u64\n",
    "    let f := \N{GREEK SMALL LETTER LAMDA} ", ARROW, " u64 { seed ", GETS, " 3u64; 0u64 }\n",
    "    _ ", GETS, " f()\n\n",
    "@[startup, impure]\nfn main(init: std.Init) ", ARROW, " u6:\n",
    "    assigned(); element(); field(); walked(); joined(); viewed(); one_element()\n",
    "    ", QUAD, "narrow(lent() + seed + ", QUAD, "widen(table", OPEN, "0", CLOSE, " + point.x, ",
    QUOTE, "u64", UNQUOTE, ") + #words, ", QUOTE, "u6", UNQUOTE, ") ?? 0u6\n"))

#: How many of those there are.
WRITTEN_WAYS = 10

#: Initializers the compiler works out itself: a number from constants and a
#: pure function, a record of such, and an address in the image.
FOLDED = "".join((
    "let std := ", QUAD, "import(\"std\")\n\n",
    "type Point = x : u64 ; y : u64\n\n",
    "fn twice(n: u64) ", ARROW, " u64:\n    n \N{MULTIPLICATION SIGN} 2u64\n\n",
    "let fallback: u64 = 5u64\n\n",
    "constinit let seed: u64 = twice(21u64)\n\n",
    "constinit let point: Point = Point(.x ", GETS, " 1u64, .y ", GETS, " 2u64)\n\n",
    "constinit let chosen: &static u64 = &fallback\n\n",
    "@[startup]\nfn main(init: std.Init) ", ARROW, " u6:\n",
    "    ", QUAD, "narrow(seed + point.y + chosen", AT, ", ", QUOTE, "u6", UNQUOTE, ") ?? 63u6\n"))

#: And one whose initializer would fault, which nothing reads.
UNREAD = "".join((
    "let std := ", QUAD, "import(\"std\")\n\n",
    "constinit let never: u64 = ", QUAD, "at(8u64, ", QUOTE, "&u64", UNQUOTE, ")", AT, "\n\n",
    "@[startup]\nfn main(init: std.Init) ", ARROW, " u6:\n    0u6\n"))


def _built(tmp_path, source: str, *extra: str):  # noqa: ANN001, ANN202
    """Compile *source*, which has to compile, and answer the image."""
    path = tmp_path / "t.pl4g"
    path.write_text(source, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), *extra, str(path)])
    assert proc.returncode == 0, describe(proc)
    check_conformance(output)
    return output


def _pages(segment: elfcheck.Segment) -> range:
    """The pages a loaded segment is mapped on, as its alignment counts them."""
    page = segment.p_align
    return range(segment.p_vaddr // page, (segment.p_vaddr + segment.p_memsz + page - 1) // page)


def test_every_way_of_writing_one_is_refused(tmp_path) -> None:  # noqa: ANN001
    """Assigning it, a field or an element of it, lending it `&mut`, walking it
    with `&mut`, joining onto it, a view or a reference that may write it -- in a
    constructor too, and in a lambda written in one."""
    path = tmp_path / "t.pl4g"
    path.write_text(WRITES, encoding="utf-8")
    proc = run_compiler(["-o", str(tmp_path / "out"), str(path)])
    assert proc.returncode != 0, describe(proc)
    assert proc.stderr.count("[PL4G-4393]") == WRITTEN_WAYS, describe(proc)
    assert proc.stderr.count("[PL4G-4394]") == WRITTEN_WAYS, describe(proc)


@pytest.mark.parametrize("triple", compiler_targets())
def test_initializers_give_and_the_startup_function_reads(triple: str, tmp_path) -> None:  # noqa: ANN001
    """What the initializers gave, one reading the other and a constructor
    reading both, is what the startup function reads, and the page it is on is
    made read-only between the two without anything noticing."""
    output = _built(tmp_path, WRITTEN, "".join(("--target=", triple)))
    proc = subprocess.run([*runner_for(triple), str(output)], capture_output=True, timeout=60)
    assert proc.returncode == 43, describe(proc)


@pytest.mark.parametrize("triple", compiler_targets())
def test_a_write_after_startup_faults(triple: str, tmp_path) -> None:  # noqa: ANN001
    """A write the checker cannot see stops the program on a fault, which it
    dies of: the stack's handler sees the address is not its guard and puts
    the default back."""
    output = _built(tmp_path, FAULTS, "".join(("--target=", triple)))
    # No core file: the signal is the answer, and a core is a large one.
    proc = subprocess.run(["prlimit", "--core=0", "--", *runner_for(triple), str(output)],
                          capture_output=True, timeout=60)
    assert proc.returncode == -signal.SIGSEGV, describe(proc)


@pytest.mark.parametrize("triple", compiler_targets())
def test_the_pages_are_nothing_elses(triple: str, tmp_path) -> None:  # noqa: ANN001
    """A segment of its own, writable as loaded, that no other segment has a page
    of -- so taking the right to write from it takes it from nothing else --
    and said with PT_GNU_RELRO, as a loader would be told it."""
    image = elfcheck.parse(_built(tmp_path, WRITTEN, "".join(("--target=", triple))).read_bytes())
    section = image.section(SECTION)
    assert section is not None
    assert section.sh_flags & elfcheck.SHF_WRITE
    loads = [one for one in image.segments if one.p_type == elfcheck.PT_LOAD]
    held = [one for one in loads
            if one.p_vaddr <= section.sh_addr < one.p_vaddr + one.p_memsz]
    assert len(held) == 1
    own = held[0]
    assert own.p_flags == elfcheck.PF_R | elfcheck.PF_W
    # Nothing else in it but what a position-independent image relocates and
    # then protects with it: what was read-only but for that.
    for other in image.sections:
        if other is not section and other.sh_flags & elfcheck.SHF_ALLOC \
                and own.p_vaddr <= other.sh_addr < own.p_vaddr + own.p_memsz:
            assert other.name.startswith(".data.rel.ro") and image.e_type == ET_DYN, other
    for other in loads:
        if other is not own:
            assert not set(_pages(other)) & set(_pages(own)), (other, own)
    relro = [one for one in image.segments if one.p_type == PT_GNU_RELRO]
    assert len(relro) == 1
    assert (relro[0].p_vaddr, relro[0].p_memsz) == (own.p_vaddr, own.p_memsz)


@pytest.mark.parametrize("source", [FOLDED, UNREAD], ids=["folded", "unread"])
def test_a_program_without_one_given_at_start_has_none(source: str, tmp_path) -> None:  # noqa: ANN001
    """No section, no segment, no header and no initializer: what costs a page
    is only there where something is given a value at start.  One the compiler
    works out is an ordinary constant, in the read-only data from the start; one
    nothing reads is not there at all, and its initializer does not run -- the
    program does not stop on the read it would make.  A position-independent
    image has the header for what it relocates, which is another matter."""
    output = _built(tmp_path, source)
    image = elfcheck.parse(output.read_bytes())
    assert image.section(SECTION) is None
    assert not [one for one in image.symbols if one.name.startswith("__pl4g_constinit.")]
    if image.e_type != ET_DYN:
        assert not [one for one in image.segments if one.p_type == PT_GNU_RELRO]
    proc = subprocess.run([*limited(), str(output)],
                          capture_output=True, timeout=60)
    assert proc.returncode == (49 if source is FOLDED else 0), describe(proc)


SHARED = "".join((
    "let base: i64 = 2i64\n\n",
    "fn pick() ", ARROW, " &static i64:\n    &base\n\n",
    "constinit let chosen: &static i64 = pick()\n\n",
    "@[visible, cdecl]\nfn run(n: i64) ", ARROW, " i64:\n    n + chosen", AT, "\n"))


def test_a_shared_object_keeps_them_out_of_its_relro(tmp_path) -> None:  # noqa: ANN001
    """The loader acts on PT_GNU_RELRO before it runs what `.init_array` lists,
    the initializers among it, so what they write is writable data of the
    object and not in that header."""
    image = elfcheck.parse(_built(tmp_path, SHARED, "-shared").read_bytes())
    section = image.section(SECTION)
    assert section is not None
    assert section.sh_flags & elfcheck.SHF_WRITE
    for one in image.segments:
        if one.p_type == PT_GNU_RELRO:
            assert not one.p_vaddr <= section.sh_addr < one.p_vaddr + one.p_memsz, one


def test_the_ir_says_which_variables_are_given_at_start(compile_source) -> None:  # noqa: ANN001
    """After the linkage, where a reader of the IR looks for what is said of
    where a variable goes; and the function giving it its value, marked as the
    entry point's to call."""
    proc, output = compile_source(WRITTEN, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    text = output.read_text(encoding="utf-8")
    assert "let @seed: mut u64 internal relro = undef" in text, text
    assert "let @plain: mut u64 internal = 0" in text, text
    assert "".join(("fn @__pl4g_constinit.seed() ", ARROW, " void internal cconv(pl4g) ",
                    "special(initializer) impure {")) in text, text
    proc, output = compile_source(FOLDED, "--emit=ir", "-O0")
    assert proc.returncode == 0, describe(proc)
    assert "let @seed: u64 internal = 42" in output.read_text(encoding="utf-8")
