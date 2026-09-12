"""The generated ELF image.

The image is checked three ways that share no code with the writer: an
independent reader written from the format, the elfutils reader, and an external
disassembler.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

import elfcheck
from conftest import OBJDUMP, describe, run_compiler

SOURCE = """\N{REFERENCE MARK} A program that exits with status 0.
@[startup]
fn main() \N{RIGHTWARDS ARROW} i32:
    0
"""

READELF = "eu-readelf"


@pytest.fixture(scope="module")
def binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile the smallest conforming program once for the whole module."""
    directory = tmp_path_factory.mktemp("elf")
    source = directory / "exit0.pl4g"
    source.write_text(SOURCE, encoding="utf-8")
    output = directory / "exit0"
    proc = run_compiler(["-o", str(output), "-O1", str(source)])
    assert proc.returncode == 0, describe(proc)
    return output


@pytest.fixture(scope="module")
def image(binary: Path) -> elfcheck.Image:
    """The compiled program, parsed independently of the writer."""
    return elfcheck.parse(binary.read_bytes())


def test_runs_natively(binary: Path) -> None:
    """The real test: the kernel loads it and it exits with status 0."""
    proc = subprocess.run([str(binary)], capture_output=True, timeout=30)
    assert proc.returncode == 0, describe(proc)
    assert proc.stdout == b""
    assert proc.stderr == b""


@pytest.mark.qemu
@pytest.mark.skipif(not shutil.which("qemu-x86_64"), reason="qemu-user is not installed")
def test_runs_under_qemu(binary: Path) -> None:
    """A second, independent loader accepts it too.

    This is also the harness the other architectures will use, so the plumbing
    exists from the first target.
    """
    proc = subprocess.run(["qemu-x86_64", str(binary)], capture_output=True, timeout=60)
    assert proc.returncode == 0, describe(proc)


@pytest.mark.skipif(not shutil.which("strace"), reason="strace is not installed")
def test_depends_on_nothing_from_the_system(binary: Path) -> None:
    """The process makes one system call: the one that ends it.

    That is the direct check that the binary uses neither a dynamic linker nor
    any system runtime.
    """
    proc = subprocess.run(["strace", "-f", str(binary)], capture_output=True,
                          text=True, timeout=60)
    calls = [line.split("(")[0] for line in proc.stderr.splitlines()
             if "(" in line and not line.startswith("+++")]
    assert calls == ["execve", "exit_group"], proc.stderr


def test_header_describes_a_static_executable(image: elfcheck.Image) -> None:
    """A fixed-address executable for x86-64, with no interpreter."""
    assert image.e_type == 2
    assert image.e_machine == 62
    assert all(s.p_type != 3 for s in image.segments), "there is a PT_INTERP"


def test_is_well_formed(image: elfcheck.Image) -> None:
    """Every requirement the format states holds."""
    assert elfcheck.check_well_formed(image) == []


def test_the_stack_is_not_executable(image: elfcheck.Image) -> None:
    """A missing PT_GNU_STACK would give an executable stack."""
    stack = [s for s in image.segments if s.p_type == elfcheck.PT_GNU_STACK]
    assert len(stack) == 1
    assert not stack[0].p_flags & elfcheck.PF_X


def test_sections_and_symbols_are_present(image: elfcheck.Image) -> None:
    """The symbol table is the map an incremental rebuild will read back."""
    assert [s.name for s in image.sections] == \
        ["", ".text", ".shstrtab", ".symtab", ".strtab"]
    start = image.symbol("_start")
    main = image.symbol("main")
    assert start is not None and main is not None
    assert start.value == image.e_entry
    assert main.size > 0 and start.size > 0
    files = [s.name for s in image.symbols if s.kind == 4]
    assert any(name.endswith("exit0.pl4g") for name in files), \
        "".join(("the source file is not recorded; found ", repr(files)))


def test_symbol_table_links_are_right(image: elfcheck.Image) -> None:
    """Getting these wrong gives a file readers accept and debuggers misread."""
    symtab = image.section(".symtab")
    strtab = image.section(".strtab")
    assert symtab is not None and strtab is not None
    assert image.sections[symtab.sh_link].name == ".strtab"
    locals_ = sum(1 for s in image.symbols if s.binding == elfcheck.STB_LOCAL)
    assert symtab.sh_info == locals_


def test_text_is_inside_the_loaded_segment(image: elfcheck.Image) -> None:
    """The code is actually mapped, at the address the section header claims."""
    text = image.section(".text")
    assert text is not None
    load = next(s for s in image.segments
                if s.p_type == elfcheck.PT_LOAD and s.p_flags & elfcheck.PF_X)
    assert load.p_vaddr <= text.sh_addr
    assert text.sh_addr + text.sh_size <= load.p_vaddr + load.p_filesz


@pytest.mark.skipif(not shutil.which(READELF), reason="elfutils is not installed")
def test_elfutils_accepts_it(binary: Path) -> None:
    """elfutils is stricter than most readers, which is what makes it a gate."""
    proc = subprocess.run([READELF, "-a", str(binary)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, describe(proc)
    assert proc.stderr.strip() == ""
    lowered = proc.stdout.lower()
    for complaint in ("<unknown>", "malformed", "invalid", "warning"):
        assert complaint not in lowered, "".join((complaint, " in:\n", proc.stdout))


@pytest.mark.objdump
@pytest.mark.skipif(not shutil.which(OBJDUMP), reason="objdump is not installed")
def test_disassembles_to_the_expected_code(binary: Path) -> None:
    """The bytes in the image are the instructions the backend meant to emit."""
    proc = subprocess.run([OBJDUMP, "-d", "-M", "intel", str(binary)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, describe(proc)
    body = [" ".join(line.split("\t")[2].split())
            for line in proc.stdout.splitlines()
            if len(line.split("\t")) >= 3 and line.split("\t")[2].strip()]
    body = [line for line in body if line != "int3"]
    assert body == [
        "xor eax,eax",
        "ret",
        "xor ebp,ebp",
        "call 4000b0 <main>",
        "mov edi,eax",
        "mov eax,0xe7",
        "syscall",
        "ud2",
    ], proc.stdout


def test_the_image_is_small(binary: Path) -> None:
    """Generating small code is a stated priority; this notices a regression."""
    assert binary.stat().st_size < 1024
