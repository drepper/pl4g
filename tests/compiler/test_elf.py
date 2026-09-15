"""The generated ELF image, for every target the compiler supports.

Each image is checked four ways that share no code with the writer: it is run,
an independent reader parses it, elfutils reads it, and an external disassembler
shows what the backend actually emitted.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import elfcheck
from conftest import (ARCH_TOOLS, ELFLINT, architecture_of, check_conformance,
                      compiler_targets, describe, run_compiler, runner_for)

SOURCE = """\N{REFERENCE MARK} A program that exits with status 0.
@[startup, impure]
fn main() \N{RIGHTWARDS ARROW} u6:
    0
"""

READELF = "eu-readelf"

#: The symbol the startup function above is known by.  A mangled name is the
#: signature written out, so it needs no demangler to read -- which is the point
#: of it, and is why the expectations below can simply state it.
MAIN = "main()u6"

#: What each backend is expected to emit for the program above, and the machine
#: number the image must carry.  Writing the instructions out is the point: a
#: change in code generation should have to be acknowledged here.
EXPECTED = {
    "x86_64": (62, [
        "xor eax,eax",
        "ret",
        "xor ebp,ebp",
        "".join(("call <", MAIN, ">")),
        "mov edi,eax",
        "mov eax,0xe7",
        "syscall",
        "ud2",
    ]),
    "aarch64": (183, [
        "mov w0, #0x0",
        "ret",
        "mov x29, xzr",
        "mov x30, xzr",
        "".join(("bl <", MAIN, ">")),
        "mov x8, #0x5e",
        "svc #0x0",
        "udf #1",
    ]),
    "riscv64": (243, [
        "li a0,0",
        "ret",
        "li s0,0",
        "li ra,0",
        "".join(("jal <", MAIN, ">")),
        "li a7,94",
        "ecall",
        "unimp",
    ]),
}

#: What a disassembler shows for the padding between functions.  One spells the
#: bytes out, the others collapse a run of zeros into a marker that carries no
#: mnemonic and is skipped before this is consulted.  On AArch64 the padding and
#: a deliberate trap are the same instruction with different immediates, which
#: is why this matches the whole text as well as the mnemonic alone.
FILLER = {"int3", "(bad)", "udf #0"}


@dataclass(frozen=True, slots=True)
class Built:
    """One compiled image and the target it was built for."""

    triple: str
    path: Path
    image: elfcheck.Image

    @property
    def arch(self) -> str:
        """The architecture the image is for."""
        return architecture_of(self.triple)


@pytest.fixture(scope="module", params=compiler_targets())
def built(request: pytest.FixtureRequest,
          tmp_path_factory: pytest.TempPathFactory) -> Built:
    """Compile the smallest conforming program for one target.

    At the oldest microarchitecture level, which on x86-64 is what makes it the
    smallest: every level above it has the program ask the processor whether it
    can run at all, which is code and is meant to be.  What that costs is said
    below, on its own, rather than mixed into what a program is.
    """
    triple = str(request.param)
    directory = tmp_path_factory.mktemp("".join(("elf-", architecture_of(triple))))
    source = directory / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = directory / "exit0"
    arguments = ["-o", str(output), "-O1", "".join(("--target=", triple))]
    if architecture_of(triple) == "x86_64":
        arguments.append("--mclevel=v1")
    proc = run_compiler([*arguments, str(source)])
    assert proc.returncode == 0, describe(proc)
    return Built(triple=triple, path=output, image=elfcheck.parse(output.read_bytes()))


def test_it_runs_and_exits_zero(built: Built) -> None:
    """The real test: a loader accepts the image and it exits with status 0."""
    runner = runner_for(built.triple)
    if runner and not shutil.which(runner[0]):
        pytest.skip("".join((runner[0], " is not installed")))
    proc = subprocess.run([*runner, str(built.path)], capture_output=True, timeout=60)
    assert proc.returncode == 0, describe(proc)
    assert proc.stdout == b""
    assert proc.stderr == b""


def test_header_describes_a_static_executable(built: Built) -> None:
    """A fixed-address executable for the right machine, with no interpreter."""
    machine, _ = EXPECTED[built.arch]
    assert built.image.e_type == 2
    assert built.image.e_machine == machine
    assert all(s.p_type != 3 for s in built.image.segments), "there is a PT_INTERP"


def test_is_well_formed(built: Built) -> None:
    """Every requirement the format states holds."""
    assert elfcheck.check_well_formed(built.image) == []


@pytest.mark.skipif(not shutil.which(ELFLINT), reason="elfutils is not installed")
def test_it_conforms_to_the_format(built: Built) -> None:
    """Checked against the format by something that did not help write it.

    The compiler writes the image itself, so nothing else would notice a field
    filled in wrongly; --strict also reports what common practice allows but the
    standard does not, which is the level to hold such a compiler to.
    """
    check_conformance(built.path)


@pytest.mark.skipif(not shutil.which(ELFLINT), reason="elfutils is not installed")
def test_the_conformance_check_is_not_a_formality(built: Built, tmp_path) -> None:  # noqa: ANN001
    """A gate that cannot fail guards nothing, so this one is made to fail.

    The header says which section holds the section names; pointing it at a
    section that holds something else leaves a file nothing can read, and the
    check must say so.
    """
    damaged = bytearray(built.path.read_bytes())
    shstrndx_at = 62
    damaged[shstrndx_at:shstrndx_at + 2] = (1).to_bytes(2, "little")
    broken = tmp_path / "damaged"
    broken.write_bytes(bytes(damaged))
    with pytest.raises(AssertionError, match="rejects"):
        check_conformance(broken)


def test_the_stack_is_not_executable(built: Built) -> None:
    """A missing PT_GNU_STACK would give an executable stack."""
    stack = [s for s in built.image.segments if s.p_type == elfcheck.PT_GNU_STACK]
    assert len(stack) == 1
    assert not stack[0].p_flags & elfcheck.PF_X


def test_the_loaded_segment_is_congruent_with_its_address(built: Built) -> None:
    """A kernel refuses to map a segment whose offset and address disagree.

    The alignment is the largest page size the target may be configured with, so
    that one image loads whatever the running kernel chose.
    """
    load = next(s for s in built.image.segments
                if s.p_type == elfcheck.PT_LOAD and s.p_flags & elfcheck.PF_X)
    assert load.p_align >= 0x1000
    assert load.p_offset % load.p_align == load.p_vaddr % load.p_align


def test_sections_and_symbols_are_present(built: Built) -> None:
    """The symbol table is the map an incremental rebuild will read back.

    The function appears under its mangled name, which is readable as it stands:
    a symbol table listing shows the signature without a demangler.
    """
    # RISC-V images carry one more: what the program was built for, which that
    # architecture has to say in the file because its base is small and
    # everything else is an extension.
    extra = [".riscv.attributes"] if built.arch == "riscv64" else []
    assert [s.name for s in built.image.sections] == \
        ["", ".text", *extra, ".shstrtab", ".symtab", ".strtab"]
    start = built.image.symbol("_start")
    main = built.image.symbol(MAIN)
    assert start is not None and main is not None
    assert start.value == built.image.e_entry
    assert main.size > 0 and start.size > 0
    files = [s.name for s in built.image.symbols if s.kind == 4]
    assert any(name.endswith("exit0.pl4g") for name in files), \
        "".join(("the source file is not recorded; found ", repr(files)))


def test_symbol_table_links_are_right(built: Built) -> None:
    """Getting these wrong gives a file readers accept and debuggers misread."""
    symtab = built.image.section(".symtab")
    strtab = built.image.section(".strtab")
    assert symtab is not None and strtab is not None
    assert built.image.sections[symtab.sh_link].name == ".strtab"
    locals_ = sum(1 for s in built.image.symbols if s.binding == elfcheck.STB_LOCAL)
    assert symtab.sh_info == locals_


def test_text_is_inside_the_loaded_segment(built: Built) -> None:
    """The code is actually mapped, at the address the section header claims."""
    text = built.image.section(".text")
    assert text is not None
    load = next(s for s in built.image.segments
                if s.p_type == elfcheck.PT_LOAD and s.p_flags & elfcheck.PF_X)
    assert load.p_vaddr <= text.sh_addr
    assert text.sh_addr + text.sh_size <= load.p_vaddr + load.p_filesz


@pytest.mark.skipif(not shutil.which(READELF), reason="elfutils is not installed")
def test_elfutils_accepts_it(built: Built) -> None:
    """elfutils is stricter than most readers, which is what makes it a gate."""
    proc = subprocess.run([READELF, "-a", str(built.path)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == ""
    lowered = proc.stdout.lower()
    for complaint in ("<unknown>", "malformed", "invalid", "warning"):
        assert complaint not in lowered, "".join((complaint, " in:\n", proc.stdout))


@pytest.mark.objdump
def test_disassembles_to_the_expected_code(built: Built) -> None:
    """The bytes in the image are the instructions the backend meant to emit."""
    tools = ARCH_TOOLS[built.arch]
    objdump = tools["objdump"]
    if not shutil.which(objdump):
        pytest.skip("".join((objdump, " is not installed")))
    command = [objdump, "-d", str(built.path)]
    if tools["flavour"]:
        command[1:1] = ["-M", tools["flavour"]]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, describe(proc)

    body: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].strip():
            continue
        # One disassembler puts the mnemonic and its operands in one field,
        # the other separates them, so take everything after the bytes.
        text = " ".join(" ".join(parts[2:]).split())
        if text in FILLER or text.split()[0] in FILLER:
            continue
        # A call prints the address it resolved to; only the symbol matters here.
        if "<" in text:
            text = "".join((text[:text.index("<")].split()[0], " ",
                            text[text.index("<"):].split()[0]))
        body.append(text.split("//")[0].strip())

    _, expected = EXPECTED[built.arch]
    assert body == expected, proc.stdout


def test_the_image_is_small(built: Built) -> None:
    """Generating small code is a stated priority; this notices a regression.

    What a RISC-V image says about what it was built for is left out of the
    count.  It is as long as the normalized ISA string is, it is not code, and
    nothing about the code generator moves it -- so counting it here would make
    a number about the program into a number about the default profile.
    """
    said = sum(s.sh_size for s in built.image.sections
               if s.name == ".riscv.attributes")
    assert built.path.stat().st_size - said < 1024


def test_asking_the_processor_is_what_a_level_costs(tmp_path: Path) -> None:
    """What the default level adds to every program, said once and in one place.

    A few hundred bytes of `CPUID` and a message, run once before anything else.
    The number is here so that a change to it is a thing somebody chose rather
    than something that happened, and so that a reader deciding between the
    levels can see what the choice is about.
    """
    source = tmp_path / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    sizes: dict[str, int] = {}
    for level in ("v1", "v2", "v3", "v4"):
        output = tmp_path / level
        proc = run_compiler(["-o", str(output), "-O1",
                             "--target=x86_64-linux-none",
                             "".join(("--mclevel=", level)), str(source)])
        assert proc.returncode == 0, describe(proc)
        sizes[level] = output.stat().st_size
    assert sizes["v1"] < sizes["v2"] < sizes["v3"], sizes
    # v4 asks one more leaf than v3 does and asks it of a leaf already being
    # asked, so it costs nothing beyond the wider mask.
    assert sizes["v4"] == sizes["v3"], sizes
    assert sizes["v4"] - sizes["v1"] < 512, sizes


def test_a_group_with_nothing_in_it_costs_no_segment(built: Built) -> None:
    """An empty segment would still take a page, so it is not produced.

    The program above has neither constants nor variables, so the code is all
    there is to map and one loadable segment covers it.
    """
    loads = [s for s in built.image.segments if s.p_type == elfcheck.PT_LOAD]
    assert len(loads) == 1
    assert loads[0].p_flags == elfcheck.PF_R | elfcheck.PF_X


def test_a_section_both_writable_and_executable_is_refused() -> None:
    """No segment grants that combination, so nothing may ask for it.

    Dropping such a section instead would produce an image whose code referred
    to bytes that were never written, which is worse than refusing it.
    """
    from pypl4g.elf.writer import ElfWriter, ImageError, ImageSettings
    from pypl4g.mc.fragment import MCDataFragment
    from pypl4g.mc.symbol import MCSection

    section = MCSection(name=".text", alloc=True, writable=True, executable=True,
                        fragments=[MCDataFragment(contents=bytearray(b"\x00"))])
    writer = ElfWriter(ImageSettings(machine=62, base_vaddr=0x400000, page_size=0x1000,
                                     entry_symbol="_start"), [section], [], [])
    with pytest.raises(ImageError, match="permissions"):
        writer.plan()


# -- the architecture's own flag word -------------------------------------------

@pytest.mark.parametrize("triple", compiler_targets())
def test_the_header_carries_the_flags_the_target_states(triple: str,
                                                        tmp_path: Path) -> None:
    """The flag word of the header is the architecture's, and only RISC-V has
    anything to say in it.

    Zero is not "unset" there: it says the base integer set and the soft-float
    convention, which is what is emitted.  What this checks is that whatever the
    target states arrives in the header -- so that the day floating point picks
    a convention, saying so is a constant and not a change to the writer.
    """
    from pypl4g.target.registry import lookup as lookup_target

    target = lookup_target(triple)
    assert target is not None
    source = tmp_path / "t.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "out"
    proc = run_compiler(["-o", str(output), "".join(("--target=", triple)),
                         str(source)])
    assert proc.returncode == 0, describe(proc)
    header = output.read_bytes()[:64]
    # e_flags is four bytes at offset 48 of a sixty-four bit header.
    flags = int.from_bytes(header[48:52], "little")
    assert flags == target.image_defaults().header_flags
