"""The `json` module held to Python's `json`, on documents made here.

What `json.encode` writes is what Python's `json.dumps` writes of the same value,
byte for byte: compact with ``separators=(',', ':')`` and ``ensure_ascii=False``,
pretty with ``indent=2``, and every character above ASCII escaped with
``ensure_ascii=True``.  So documents are made at random -- nested arrays and
objects, text in several scripts with every escape, whole numbers of every size
an ``int64`` and a ``uint64`` hold, floating-point numbers of every exponent --
handed to a program that decodes them and encodes what it read, and what it
writes is compared with what Python writes of what it read.

And documents are broken, a byte at a time and in the ways a hand or a wire
breaks them, and what the module refuses is what Python refuses -- where the
two mean the same document by the same bytes.  Python reads some things JSON
does not have, unasked: ``NaN`` and ``Infinity``, a number too large for any
float as an infinity, half of a surrogate pair; those are made to refuse here
too, which is what the module does by default.
"""

from __future__ import annotations

import json
import math
import random
import subprocess
from pathlib import Path

import pytest

from conftest import (HOST_ARCH, architecture_of, check_conformance, compiler_targets, describe,
                      run_compiler, runner_for)

#: The program the documents are read and written by, beside this file.
DRIVER = Path(__file__).resolve().parent / "data" / "json_driver.pl4g"

#: Words of text, of every length of UTF-8 and with what wants an escape.
WORDS = ("a", "zebra", "", " ", "naïve", "日本語", "😀", "€", "\"", "\\", "/", "\n", "\t",
         "\u0001", "\u001f", "\u007f", " ", "x y", "\U0001d11e")

#: How many documents each comparison is made over.
DOCUMENTS = 150


def _number(rng: random.Random) -> object:
    """A number of one of the kinds the module keeps apart."""
    pick = rng.random()
    if pick < 0.3:
        return rng.randrange(-1000, 1000)
    if pick < 0.45:
        return rng.randrange(-(1 << 63), 1 << 63)
    if pick < 0.55:
        return rng.randrange(1 << 63, 1 << 64)
    if pick < 0.8:
        return rng.uniform(-1e6, 1e6)
    while True:
        x = rng.uniform(0.5, 1.0) * 10.0 ** rng.randrange(-320, 308)
        if math.isfinite(x):
            return x if rng.random() < 0.5 else -x


def _value(rng: random.Random, depth: int) -> object:
    """A value at most *depth* levels deep."""
    pick = rng.random() if depth > 0 else rng.random() * 0.7
    if pick < 0.1:
        return None
    if pick < 0.2:
        return rng.random() < 0.5
    if pick < 0.45:
        return _number(rng)
    if pick < 0.7:
        return "".join(rng.choice(WORDS) for _ in range(rng.randrange(6)))
    if pick < 0.85:
        return [_value(rng, depth - 1) for _ in range(rng.randrange(5))]
    return {"".join(rng.choice(WORDS) for _ in range(rng.randrange(3))) + str(at): _value(rng, depth - 1)
            for at in range(rng.randrange(5))}


def _documents(seed: int) -> list[object]:
    """The documents of one comparison."""
    rng = random.Random(seed)
    return [_value(rng, 5) for _ in range(DOCUMENTS)]


def _written(documents: list[object]) -> bytes:
    """The documents one after another, as a writer of JSON Lines writes them --
    with white space in them, which reading takes out."""
    return "\n".join(json.dumps(one, ensure_ascii=False, indent=1) for one in documents).encode()


@pytest.fixture(scope="module", params=compiler_targets())
def driver(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> tuple[str, Path]:
    """The program, built once for each target."""
    triple = request.param
    where = tmp_path_factory.mktemp("".join(("json-", triple)))
    out = where / "driver"
    proc = run_compiler(["-O1", "".join(("--target=", triple)), "-o", str(out), str(DRIVER)])
    assert proc.returncode == 0, describe(proc)
    check_conformance(out)
    return triple, out


def _run(driver: tuple[str, Path], mode: str, document: bytes) -> subprocess.CompletedProcess[bytes]:
    """The program run on *document*."""
    triple, program = driver
    return subprocess.run([*runner_for(triple), str(program), mode], input=document,
                          capture_output=True, timeout=300, check=False)


@pytest.mark.parametrize("mode", ["c", "p", "a"])
def test_what_is_written_is_what_python_writes(driver: tuple[str, Path], mode: str) -> None:
    """Compact, pretty and ASCII: the bytes Python writes of the same values."""
    documents = _documents(7)
    proc = _run(driver, mode, _written(documents))
    assert proc.returncode == 0, (proc.returncode, proc.stdout[:200])
    if mode == "c":
        expected = json.dumps(documents, ensure_ascii=False, separators=(",", ":"))
    elif mode == "p":
        expected = json.dumps(documents, ensure_ascii=False, indent=2)
    else:
        expected = json.dumps(documents, ensure_ascii=True, separators=(",", ":"))
    assert proc.stdout.decode() == expected


def _python_reads(text: bytes) -> bool:
    """Whether Python's `json` reads *text* as JSON, held to what JSON is: no
    `NaN` or `Infinity`, no number past every float, no half of a surrogate pair."""
    def refuse(_: str) -> object:
        raise ValueError("not JSON")

    def finite(written: str) -> float:
        value = float(written)
        if not math.isfinite(value):
            raise ValueError("out of range")
        return value

    try:
        value = json.loads(text.decode("utf-8"), parse_constant=refuse, parse_float=finite)
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError):
        return False
    return True


#: What a document is broken with.
BREAKS = (b"\"", b"\\", b"{", b"}", b"[", b"]", b",", b":", b" ", b"1", b"x", b"\x80", b"\xff",
          b"\x00", b"\n", b"-", b".", b"e", b"\\u", b"\\ud800", b"0")


def test_what_is_refused_is_what_python_refuses(driver: tuple[str, Path]) -> None:
    """A byte changed, put in or taken out: both read the document, or neither.

    One program a document, so on the machine the tests run on only: the
    emulators are what the comparison above is for.
    """
    triple, _ = driver
    if architecture_of(triple) != HOST_ARCH:
        pytest.skip("each document is a run of its own, which is the native machine's to make")
    rng = random.Random(11)
    disagreements: list[tuple[bytes, bool, int]] = []
    for _ in range(DOCUMENTS):
        text = json.dumps(_value(rng, 3), ensure_ascii=rng.random() < 0.3).encode()
        at = rng.randrange(len(text) + 1)
        pick = rng.random()
        broken = (text[:at] + rng.choice(BREAKS) + text[at + 1:] if pick < 0.5
                  else text[:at] + rng.choice(BREAKS) + text[at:] if pick < 0.8
                  else text[:at] + text[at + 1:])
        theirs = _python_reads(broken)
        proc = _run(driver, "s", broken)
        ours = proc.returncode == 0
        if theirs != ours:
            disagreements.append((broken, theirs, proc.returncode))
    assert not disagreements, disagreements[:5]
