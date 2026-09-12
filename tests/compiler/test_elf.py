"""The generated ELF image, for every target the compiler supports.

Each image is checked four ways that share no code with the writer: it is run,
an independent reader parses it, elfutils reads it, and an external disassembler
shows what the backend actually emitted.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import elfcheck
from conftest import (ARCH_TOOLS, architecture_of, compiler_targets, describe,
                      run_compiler, runner_for)

SOURCE = """\N{REFERENCE MARK} A program that exits with status 0.
@[startup]
fn main() \N{RIGHTWARDS ARROW} u8:
    0
"""

READELF = "eu-readelf"

#: What each backend is expected to emit for the program above, and the machine
#: number the image must carry.  Writing the instructions out is the point: a
#: change in code generation should have to be acknowledged here.
EXPECTED = {
    "x86_64": (62, [
        "xor eax,eax",
        "ret",
        "xor ebp,ebp",
        "call <main>",
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
        "bl <main>",
        "mov x8, #0x5e",
        "svc #0x0",
        "brk #0x1",
    ]),
    "riscv64": (243, [
        "li a0,0",
        "ret",
        "li s0,0",
        "li ra,0",
        "jal <main>",
        "li a7,94",
        "ecall",
        "unimp",
    ]),
}

#: What a disassembler shows for the padding between functions.  One spells the
#: bytes out, the others collapse a run of zeros into a marker that carries no
#: mnemonic and is skipped before this is consulted.
FILLER = {"int3", "udf", "(bad)"}


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
    """Compile the smallest conforming program for one target."""
    triple = str(request.param)
    directory = tmp_path_factory.mktemp("".join(("elf-", architecture_of(triple))))
    source = directory / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = directory / "exit0"
    proc = run_compiler(["-o", str(output), "-O1", "".join(("--target=", triple)),
                         str(source)])
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
    """The symbol table is the map an incremental rebuild will read back."""
    assert [s.name for s in built.image.sections] == \
        ["", ".text", ".shstrtab", ".symtab", ".strtab"]
    start = built.image.symbol("_start")
    main = built.image.symbol("main")
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
        if text.split()[0] in FILLER:
            continue
        # A call prints the address it resolved to; only the symbol matters here.
        if "<" in text:
            text = "".join((text[:text.index("<")].split()[0], " ",
                            text[text.index("<"):].split()[0]))
        body.append(text.split("//")[0].strip())

    _, expected = EXPECTED[built.arch]
    assert body == expected, proc.stdout


def test_the_image_is_small(built: Built) -> None:
    """Generating small code is a stated priority; this notices a regression."""
    assert built.path.stat().st_size < 1024
