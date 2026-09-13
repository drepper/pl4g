"""The diagnostic catalog is the contract between implementations.

These tests check the properties that contract relies on: stable four-digit
numbers in declared blocks, symbolic names derived from the specification, and a
generated identifier module that matches the catalog it was generated from.
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from pypl4g.diag import ids as D
from pypl4g.diag.catalog import (UnblockedNumber, generate_ids_source,
                                 load_catalog, message_placeholders)
from pypl4g.paths import share_file

NAME_PATTERN = re.compile(r"^(LANG|IMPL)_[A-Z][A-Z0-9_]*$")


@pytest.fixture(scope="module")
def raw() -> dict:
    """The catalog as it is written on disk."""
    with share_file("diagnostics.json").open(encoding="utf-8") as stream:
        return json.load(stream)


@pytest.fixture(scope="module")
def schema() -> dict:
    """The schema the catalog must satisfy."""
    with share_file("diagnostics.schema.json").open(encoding="utf-8") as stream:
        return json.load(stream)


def test_schema_is_valid(schema: dict) -> None:
    """The schema itself is a well-formed JSON Schema."""
    jsonschema.Draft202012Validator.check_schema(schema)


def test_catalog_matches_schema(raw: dict, schema: dict) -> None:
    """Every entry of the catalog satisfies the schema."""
    jsonschema.Draft202012Validator(schema).validate(raw)


def test_numbers_are_unique_and_four_digits(raw: dict) -> None:
    """Numbers identify a diagnostic and always print as four digits."""
    numbers = [d["number"] for d in raw["diagnostics"]]
    assert len(numbers) == len(set(numbers))
    assert all(1000 <= n <= 9999 for n in numbers)


def test_every_number_lies_in_a_declared_block(raw: dict) -> None:
    """Related diagnostics stay in the block their family was given."""
    blocks = [(b["first"], b["last"]) for b in raw["blocks"]]
    for entry in raw["diagnostics"]:
        assert any(first <= entry["number"] <= last for first, last in blocks), \
            entry["name"]


def test_blocks_do_not_overlap(raw: dict) -> None:
    """The blocks partition the number space."""
    ordered = sorted((b["first"], b["last"]) for b in raw["blocks"])
    for (_, previous_last), (next_first, _) in zip(ordered, ordered[1:]):
        assert previous_last < next_first


def test_names_are_unique_and_derived_from_the_specification(raw: dict) -> None:
    """A symbolic name says which document states the requirement."""
    names = [d["name"] for d in raw["diagnostics"]]
    assert len(names) == len(set(names))
    for entry in raw["diagnostics"]:
        assert NAME_PATTERN.match(entry["name"]), entry["name"]
        prefix = "LANG" if entry["spec"]["document"] == "spec/spec.md" else "IMPL"
        assert entry["name"].startswith(prefix), entry["name"]


def test_message_placeholders_match_declared_arguments() -> None:
    """A message uses exactly the arguments its entry declares."""
    catalog = load_catalog()
    for entry in catalog.by_number.values():
        declared = frozenset(a.name for a in entry.args)
        used = message_placeholders(entry.message)
        assert used == declared, entry.name


def test_notes_reference_existing_note_diagnostics() -> None:
    """Every sub-diagnostic an entry may attach exists and is a note."""
    catalog = load_catalog()
    for entry in catalog.by_number.values():
        for name in entry.notes:
            attached = catalog.by_name[name]
            assert attached.severity == "note", name


def test_controllable_diagnostics_are_warnings() -> None:
    """Only a warning can be turned off; an error is not negotiable."""
    catalog = load_catalog()
    for entry in catalog.by_number.values():
        if entry.option is not None:
            assert entry.severity == "warning", entry.name


def test_generated_ids_module_is_current(root: Path) -> None:
    """The checked-in identifier module matches the catalog."""
    generated = generate_ids_source(load_catalog())
    on_disk = (root / "pypl4g" / "diag" / "ids.py").read_text(encoding="utf-8")
    assert generated == on_disk, \
        "run bin/pl4g-gen-diag-ids after changing share/diagnostics.json"


def test_every_identifier_resolves() -> None:
    """Every name the generated module exports is in the catalog."""
    catalog = load_catalog()
    for name in dir(D):
        if name.startswith("_") or not name.isupper():
            continue
        assert getattr(D, name) in catalog.by_number


# -- the two places the blocks are written down --------------------------------

def declared_blocks(root: Path) -> list[tuple[int, int, str]]:
    """The block table as `spec/details.md` states it."""
    text = (root / "spec" / "details.md").read_text(encoding="utf-8")
    found: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        match = re.fullmatch(r"\|\s*(\d{4})-(\d{4})\s*\|\s*(.+?)\s*\|", line)
        if match is not None:
            found.append((int(match.group(1)), int(match.group(2)), match.group(3)))
    return found


def test_the_documented_blocks_are_the_declared_blocks(root: Path) -> None:
    """The specification states the blocks and so does the catalog.

    Two places saying the same thing is two places to change, and a block added
    to one and not the other leaves the document quietly wrong.  Nothing else
    checks the table, so this does.
    """
    catalog = load_catalog()
    assert declared_blocks(root) == [(b.first, b.last, b.topic) for b in catalog.blocks], \
        "the block table in spec/details.md and share/diagnostics.json disagree"


def test_a_number_outside_every_block_is_refused() -> None:
    """Not merely reported by a test: the generator itself refuses it.

    Writing such a number out anyway would put it under the heading of whatever
    block came before, saying it belongs to a family it does not -- which is the
    one way the generated file could be quietly misleading.
    """
    catalog = load_catalog()
    entry = next(iter(catalog.by_number.values()))
    stray = replace(entry, number=4700)
    broken = replace(catalog, by_number={4700: stray})
    with pytest.raises(UnblockedNumber, match="between two blocks"):
        generate_ids_source(broken)
