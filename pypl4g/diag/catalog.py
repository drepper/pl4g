"""The diagnostic catalog, loaded from the shared JSON file.

The catalog is the contract between compiler implementations: the number, the
severity and the message text of a diagnostic come from here and never from the
call site, so that two implementations cannot drift apart.
"""

import json
from dataclasses import dataclass
from string import Formatter
from typing import Any, Final, Mapping

from ..paths import share_file

CATALOG_FILE: Final[str] = "diagnostics.json"

type DiagID = int


@dataclass(frozen=True, slots=True)
class SpecRef:
    """Where in the specification the requirement being enforced is written."""

    document: str
    section: str
    requirement: str | None = None


@dataclass(frozen=True, slots=True)
class DiagArg:
    """A declared placeholder of a diagnostic message."""

    name: str
    type: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class DiagInfo:
    """One entry of the catalog."""

    number: DiagID
    name: str
    severity: str
    message: str
    cause: str
    spec: SpecRef
    since: str
    args: tuple[DiagArg, ...] = ()
    default_enabled: bool = True
    internal: bool = False
    option: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def is_error(self) -> bool:
        """Whether this diagnostic prevents a successful compilation."""
        return self.severity in ("fatal", "error")


@dataclass(frozen=True, slots=True)
class NumberBlock:
    """A reserved range of the four-digit number space."""

    first: DiagID
    last: DiagID
    topic: str
    spec: SpecRef | None = None


@dataclass(frozen=True, slots=True)
class Catalog:
    """The whole catalog, indexed by number, by name and by warning option."""

    format_version: int
    blocks: tuple[NumberBlock, ...]
    by_number: Mapping[DiagID, DiagInfo]
    by_name: Mapping[str, DiagInfo]
    by_option: Mapping[str, DiagInfo]

    def get(self, ident: DiagID) -> DiagInfo:
        """Return the entry for *ident*, or raise ``KeyError``."""
        return self.by_number[ident]


def _spec_ref(raw: Mapping[str, Any]) -> SpecRef:
    """Build a ``SpecRef`` from its JSON form."""
    return SpecRef(document=raw["document"], section=raw["section"],
                   requirement=raw.get("requirement"))


def parse_catalog(raw: Mapping[str, Any]) -> Catalog:
    """Build a ``Catalog`` from the decoded JSON document."""
    entries: list[DiagInfo] = []
    for item in raw["diagnostics"]:
        entries.append(DiagInfo(
            number=item["number"],
            name=item["name"],
            severity=item["severity"],
            message=item["message"],
            cause=item["cause"],
            spec=_spec_ref(item["spec"]),
            since=item["since"],
            args=tuple(DiagArg(a["name"], a["type"], a.get("description"))
                       for a in item.get("args", ())),
            default_enabled=item.get("default_enabled", True),
            internal=item.get("internal", False),
            option=item.get("option"),
            notes=tuple(item.get("notes", ())),
        ))
    blocks = tuple(NumberBlock(
        first=b["first"], last=b["last"], topic=b["topic"],
        spec=_spec_ref(b["spec"]) if "spec" in b else None) for b in raw["blocks"])
    return Catalog(
        format_version=raw["format_version"],
        blocks=blocks,
        by_number={e.number: e for e in entries},
        by_name={e.name: e for e in entries},
        by_option={e.option: e for e in entries if e.option is not None},
    )


def load_catalog() -> Catalog:
    """Read and parse the shared catalog file."""
    with share_file(CATALOG_FILE).open(encoding="utf-8") as stream:
        return parse_catalog(json.load(stream))


_catalog: Catalog | None = None


def catalog() -> Catalog:
    """Return the catalog, reading it on first use.

    A compilation that emits no diagnostic never touches the file.
    """
    global _catalog
    if _catalog is None:
        _catalog = load_catalog()
    return _catalog


def message_placeholders(message: str) -> frozenset[str]:
    """Return the names of the ``str.format`` placeholders used in *message*."""
    return frozenset(name for _, name, _, _ in Formatter().parse(message)
                     if name is not None and name != "")


def generate_ids_source(cat: Catalog) -> str:
    """Return the text of ``pypl4g/diag/ids.py`` for *cat*.

    Keeping the identifiers in a generated Python module rather than looking them
    up by string gives static checking of every reference and keeps the JSON out
    of the path of a compilation that reports nothing.
    """
    out: list[str] = [
        '"""Symbolic names of the diagnostics.',
        "",
        "Generated from ``share/diagnostics.json`` by ``bin/pl4g-gen-diag-ids``.",
        "Do not edit; change the catalog and regenerate.",
        '"""',
        "",
        "from typing import Final",
        "",
        "from .catalog import DiagID",
        "",
    ]
    previous_block: NumberBlock | None = None
    for number in sorted(cat.by_number):
        entry = cat.by_number[number]
        block = next((b for b in cat.blocks if b.first <= number <= b.last), None)
        if block is not previous_block and block is not None:
            out.append("")
            out.append("".join(("# ", str(block.first), "-", str(block.last), ": ", block.topic)))
            previous_block = block
        out.append("".join((entry.name, ": Final[DiagID] = ", str(entry.number))))
    out.append("")
    return "\n".join(out)
