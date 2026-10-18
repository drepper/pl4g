"""The hash a table's keys are hashed with: the runtime's and the compiler's.

The compiler lays some tables out in the image, and the runtime probes them, so
the two have to come to the same place for every key and every seed.  The C is
compiled here for the machine the suite runs on -- the hashing part of it, which
needs nothing of the system -- and asked about inputs of every length a branch
of it treats differently.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import describe, run_compiler, runner_for
from pypl4g.sema.rapidhash import rapidhash, word_hash

ROOT = Path(__file__).resolve().parents[2]

#: Lengths either side of where the hash changes how it reads: nought, under
#: four, the short reads, a block of sixteen, the loop of forty-eight and of
#: ninety-six, and what is left after each.
LENGTHS = (0, 1, 2, 3, 4, 7, 8, 15, 16, 17, 31, 32, 33, 47, 48, 49, 95, 96, 97,
           143, 144, 200, 300)

SEEDS = (0, 1, 0x0123456789ABCDEF, (1 << 64) - 1)

HARNESS = r"""
typedef unsigned long u64;
typedef unsigned int u32;
#include <stdio.h>
%s
int main(void)
{
  unsigned char bytes[300];
  for (int i = 0; i < 300; ++i)
    bytes[i] = (unsigned char) (i * 37 + 11);
  u64 seed;
  unsigned len;
  while (scanf("%%lu %%u", &seed, &len) == 2)
    printf("%%lu\n", pl4g_hash(bytes, len, seed));
  printf("%%lu\n", pl4g_hash_word(0x1122334455667788ul, 42));
  return 0;
}
"""


def _hashing_part() -> str:
    """The C between where hashing begins and where seeds are kept."""
    text = (ROOT / "runtime" / "io.c").read_text(encoding="utf-8")
    start = text.index("/* -- hashing")
    end = text.index("/* What every table's seed is worked out from")
    return text[start:end]


@pytest.mark.skipif(shutil.which("clang") is None, reason="no C compiler")
def test_the_runtime_and_the_compiler_hash_alike(tmp_path: Path) -> None:
    """Every length a branch of the hash treats differently, with every kind of
    seed, and a key that is a word."""
    source = tmp_path / "hash.c"
    source.write_text(HARNESS % _hashing_part(), encoding="utf-8")
    program = tmp_path / "hash"
    subprocess.run(["clang", "-O1", "-o", str(program), str(source)], check=True)
    asked = [(seed, length) for seed in SEEDS for length in LENGTHS]
    proc = subprocess.run([str(program)], check=True, capture_output=True,
                          text=True,
                          input="".join("%d %d\n" % one for one in asked))
    answers = [int(one) for one in proc.stdout.split()]
    data = bytes((i * 37 + 11) & 0xFF for i in range(300))
    assert answers[:-1] == [rapidhash(data[:length], seed)
                            for seed, length in asked]
    assert answers[-1] == word_hash(0x1122334455667788, 42)


def test_a_seed_changes_where_a_key_goes() -> None:
    """What makes the keys of a table nothing a program can choose to collide:
    the same keys with two seeds do not land alike."""
    keys = [str(one).encode() for one in range(64)]
    one = [rapidhash(key, 1) & 63 for key in keys]
    other = [rapidhash(key, 2) & 63 for key in keys]
    assert one != other


#: A program whose answer is the order it walked a dictionary of numbers in,
#: folded into six bits.
WALKED = """let std := \N{APL FUNCTIONAL SYMBOL QUAD}import("std")

@[startup, impure]
fn main(init: std.Init) \N{RIGHTWARDS ARROW} u6:
    let s: mut \N{LEFT DOUBLE PARENTHESIS}u64: u64\N{RIGHT DOUBLE PARENTHESIS} = \N{LEFT DOUBLE PARENTHESIS}0u64: 0u64\N{RIGHT DOUBLE PARENTHESIS}
    let n: mut u64 = 1u64
    while n < 40u64:
        s\N{LEFT DOUBLE PARENTHESIS}n\N{RIGHT DOUBLE PARENTHESIS} \N{LEFTWARDS ARROW} n
        n \N{LEFTWARDS ARROW} n + 1u64
    let folded: mut u64 = 0u64
    foreach k, _ := s:
        folded \N{LEFTWARDS ARROW} \N{APL FUNCTIONAL SYMBOL QUAD}wrap(folded \N{MULTIPLICATION SIGN} 31u64 + k)
    \N{APL FUNCTIONAL SYMBOL QUAD}narrow((folded % 64u64) ?? 0u64, \N{TOP LEFT CORNER}u6\N{TOP RIGHT CORNER}) ?? 0u6
"""


def _walks(tmp_path: Path, seeds: list[str | None]) -> list[int]:
    """What the program answers, run once for each seed -- nothing being no
    `PL4G_HASH_SEED` at all."""
    import os

    source = tmp_path / "walked.pl4g"
    source.write_text(WALKED, encoding="utf-8")
    program = tmp_path / "walked"
    proc = run_compiler(["-O1", "-o", str(program), str(source)])
    assert proc.returncode == 0, describe(proc)
    found: list[int] = []
    for seed in seeds:
        env = {key: value for key, value in os.environ.items()
               if key != "PL4G_HASH_SEED"}
        if seed is not None:
            env["PL4G_HASH_SEED"] = seed
        ran = subprocess.run([*runner_for("x86_64-linux-none"), str(program)],
                             env=env, capture_output=True, timeout=60)
        found.append(ran.returncode)
    return found


def test_one_seed_walks_alike(tmp_path: Path) -> None:
    """`PL4G_HASH_SEED` is what makes a run one that can be had again."""
    walks = _walks(tmp_path, ["7", "7", "7"])
    assert len(set(walks)) == 1, walks


def test_seeds_walk_differently(tmp_path: Path) -> None:
    """Two seeds put the keys in two places, so a walk meets them in two
    orders -- for some of six, which is all a hash can promise."""
    walks = _walks(tmp_path, ["1", "2", "3", "4", "5", "6"])
    assert len(set(walks)) > 1, walks


def test_a_run_seeds_its_tables_itself(tmp_path: Path) -> None:
    """With nothing said, the kernel's random bytes are the base: six runs do
    not all walk alike, which they would one time in some billions if they
    did not draw a seed each."""
    walks = _walks(tmp_path, [None] * 6)
    assert len(set(walks)) > 1, walks
