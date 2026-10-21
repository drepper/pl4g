"""Check what the C++ compiler makes of every test program with ``-fpie``.

Run as ``pie_agrees.py PL4G ROOT WORK TRIPLE [NAME...]``.  Each test program
that compiles is compiled for TRIPLE with ``-fpie`` at every level its test asks
for, and its image has to be what a static position-independent executable is:
of type ET_DYN, with no interpreter and no dynamic section, its segments from
address nought, and a table of relative relocations in the encoding of ELF's
SHT_RELR that says exactly what binutils' ``readelf`` reads out of it -- written
as the canonical encoding (what lld and glibc write), naming only eight-byte
places that are eight-byte aligned and in a writable segment.  What is
read-only but for being relocated is one PT_GNU_RELRO, which is exactly one of
the writable segments and shares no page with any other -- the pages the
startup code makes read-only again once it has relocated them.  Running the
images is the test suite's: ``PL4G_COMPILER_ARGS=-fpie``.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from codegen_agrees import directives  # noqa: E402

ET_DYN = 3
PT_LOAD, PT_DYNAMIC, PT_INTERP = 1, 2, 3
PT_GNU_RELRO = 0x6474E552
PAGE = 0x1000
PF_W = 2
SHT_RELR = 19
WORD = 8
BITS = 63


def decoded(entries: list[int]) -> list[int]:
    """The places a RELR table names, in order."""
    places: list[int] = []
    where = 0
    for entry in entries:
        if entry & 1 == 0:
            places.append(entry)
            where = entry + WORD
            continue
        for bit in range(BITS):
            if entry >> (bit + 1) & 1:
                places.append(where + bit * WORD)
        where += BITS * WORD
    return places


def encoded(places: list[int]) -> list[int]:
    """The canonical table for sorted, aligned *places*, as lld writes it."""
    out: list[int] = []
    at = 0
    while at < len(places):
        out.append(places[at])
        base = places[at] + WORD
        at += 1
        while True:
            bitmap = 0
            while at < len(places) and 0 <= places[at] - base < BITS * WORD and (places[at] - base) % WORD == 0:
                bitmap |= 1 << ((places[at] - base) // WORD)
                at += 1
            if bitmap == 0:
                break
            out.append(bitmap << 1 | 1)
            base += BITS * WORD
    return out


def examined(image: Path) -> str | None:
    """What is wrong with *image* as a static position-independent one, or nothing."""
    data = image.read_bytes()
    e_type = struct.unpack_from("<H", data, 16)[0]
    if e_type != ET_DYN:
        return "".join(("type ", str(e_type), ", not ET_DYN"))
    e_phoff, e_shoff = struct.unpack_from("<QQ", data, 32)
    e_phentsize, e_phnum, e_shentsize, e_shnum = struct.unpack_from("<HHHH", data, 54)
    writable: list[tuple[int, int]] = []
    loads: list[tuple[int, int]] = []
    relro: list[tuple[int, int]] = []
    lowest = None
    for at in range(e_phnum):
        p_type, p_flags, _, p_vaddr, _, _, p_memsz, _ = struct.unpack_from("<IIQQQQQQ", data,
                                                                            e_phoff + at * e_phentsize)
        if p_type in (PT_INTERP, PT_DYNAMIC):
            return "".join(("a segment of type ", str(p_type)))
        if p_type == PT_LOAD:
            lowest = p_vaddr if lowest is None else min(lowest, p_vaddr)
            loads.append((p_vaddr, p_vaddr + p_memsz))
            if p_flags & PF_W:
                writable.append((p_vaddr, p_vaddr + p_memsz))
        elif p_type == PT_GNU_RELRO:
            relro.append((p_vaddr, p_vaddr + p_memsz))
    if lowest != 0:
        return "".join(("the first segment is at ", hex(lowest or -1), ", not nought"))
    if len(relro) > 1:
        return "more than one PT_GNU_RELRO"
    for lo, hi in relro:
        if (lo, hi) not in writable:
            return "the PT_GNU_RELRO is not one of the writable segments"
        pages = (lo // PAGE, (hi + PAGE - 1) // PAGE)
        for other in loads:
            if other != (lo, hi) and other[0] // PAGE < pages[1] and (other[1] + PAGE - 1) // PAGE > pages[0]:
                return "the PT_GNU_RELRO shares a page with another segment"
    entries: list[int] = []
    for at in range(e_shnum):
        _, sh_type, _, _, sh_offset, sh_size, _, _, _, sh_entsize = struct.unpack_from(
            "<IIQQQQIIQQ", data, e_shoff + at * e_shentsize)
        if sh_type == SHT_RELR:
            if sh_entsize != WORD:
                return "".join(("the table's entries are ", str(sh_entsize), " bytes"))
            entries = list(struct.unpack_from("".join(("<", str(sh_size // WORD), "Q")), data, sh_offset))
    places = decoded(entries)
    if any(one % WORD for one in places):
        return "a place that is not eight-byte aligned"
    if sorted(set(places)) != places:
        return "places out of order or named twice"
    if encoded(places) != entries:
        return "the table is not the canonical encoding of what it names"
    stray = [one for one in places if not any(lo <= one and one + WORD <= hi for lo, hi in writable)]
    if stray:
        return "".join(("a place outside every writable segment: ", hex(stray[0])))
    # Binutils' reader, a decoder that is not this one: an entry's line says the
    # entry and the first place it names, and a line under it each further one.
    shown = subprocess.run(["/usr/bin/readelf", "-rW", str(image)], capture_output=True, text=True,
                           check=False).stdout
    theirs = [int(one, 16) for one in re.findall(r"^(?:[0-9]{4}:\s+[0-9a-f]{16}|\s+)\s([0-9a-f]{16})\s",
                                                 shown, re.MULTILINE)]
    if theirs != places:
        return "".join(("readelf reads ", str(len(theirs)), " places out of the table, which names ", str(len(places))))
    return None


def one(run: str, path: Path, work: Path, triple: str, level: int, extra: list[str]) -> str | None:
    """What is wrong with the image made of *path* at *level*, or nothing."""
    stem = "".join((path.parent.name, "--", path.stem, "-O", str(level), "-", triple))
    image = work / "".join((stem, ".pie"))
    image.unlink(missing_ok=True)
    sanitized = run.endswith("-asan")
    limit = ["prlimit", "--cpu=900", "--"] if sanitized else ["prlimit", "--as=8589934592", "--cpu=120", "--"]
    made = subprocess.run([*limit, run, "-fpie", "--target", triple, "".join(("-O", str(level))), *extra,
                           "-o", str(image), str(path)], capture_output=True, text=True, check=False,
                          cwd=path.parent, env={**os.environ, "ASAN_OPTIONS": "hard_rss_limit_mb=4096:detect_leaks=0"})
    if made.returncode != 0:
        # What does not compile at a fixed address does not here either; a
        # program that does and is refused here is a failure.
        return None if made.returncode == 1 and "PL4G-9901" not in made.stderr else \
            "".join(("the compiler failed (", str(made.returncode), "): ", made.stderr[-1500:]))
    return examined(image)


def main() -> int:
    run, root, work, triple = str(Path(sys.argv[1]).resolve()), Path(sys.argv[2]).resolve(), \
        Path(sys.argv[3]).resolve(), sys.argv[4]
    picked = set(sys.argv[5:])
    work.mkdir(parents=True, exist_ok=True)
    jobs = []
    for path in sorted((root / "tests" / "language").rglob("*.pl4g")):
        if picked and path.parent.name not in picked:
            continue
        compiles, levels, extra = directives(path)
        # A shared object is position-independent by being one and is not an
        # executable that relocates itself, which is what this checks.
        if compiles and "-shared" not in extra:
            jobs.extend((path, level, extra) for level in levels)
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:
        found = list(pool.map(lambda job: (job, one(run, job[0], work, triple, job[1], job[2])), jobs))
    failures = [(job, why) for job, why in found if why is not None]
    for (path, level, _), why in failures:
        print("".join((str(path.relative_to(root)), " -O", str(level), ": ", why)))
    print("".join((str(len(jobs) - len(failures)), " of ", str(len(jobs)), " position-independent images are what "
                   "they should be for ", triple)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
