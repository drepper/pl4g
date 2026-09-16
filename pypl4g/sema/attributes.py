"""The attribute registry.

An attribute is written ``@[name]`` or ``@[name(arguments)]``, and several may be
given in one list: ``@[test(build), inline]``.  Arguments are positional first
and named after, as in a function call, which keeps a one-argument attribute
terse while letting an attribute that needs several stay self-describing.

Attributes apply to any kind of object, not only to functions.  Each attribute
declares which kinds it accepts, so that applying one where it has no meaning is
an error rather than a silently ignored annotation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Final, Mapping

from ..front import ast
from ..ir.function import SpecialKind


class AttrTarget(Flag):
    """The kinds of object an attribute may be applied to."""

    FUNCTION = auto()
    TYPE = auto()
    VARIABLE = auto()
    MODULE = auto()
    PARAMETER = auto()
    STATEMENT = auto()
    #: A lambda, and the function type a lambda is held by.  What may be said
    #: there is only what a caller reads off the type, since that is all a
    #: value handed from one name to another still carries.
    CALLABLE = auto()
    ANY = (FUNCTION | TYPE | VARIABLE | MODULE | PARAMETER | STATEMENT
           | CALLABLE)


#: How a target is named in a diagnostic.
TARGET_NAMES: Final[dict[AttrTarget, str]] = {
    AttrTarget.FUNCTION: "a function",
    AttrTarget.TYPE: "a type",
    AttrTarget.VARIABLE: "a variable",
    AttrTarget.MODULE: "a module",
    AttrTarget.PARAMETER: "a parameter",
    AttrTarget.STATEMENT: "a statement",
    AttrTarget.CALLABLE: "a lambda or a function type",
}


@dataclass(frozen=True, slots=True)
class AttrParam:
    """One parameter of an attribute."""

    name: str
    kind: str
    #: For a parameter whose value is one of a fixed set of names.
    choices: tuple[str, ...] | None = None
    default: object | None = None
    required: bool = True

    def describe(self) -> str:
        """How this parameter is named in a diagnostic."""
        if self.choices is not None:
            return "".join((self.name, ": one of ", ", ".join(self.choices)))
        return "".join((self.name, ": ", self.kind))


@dataclass(frozen=True, slots=True)
class AttrSpec:
    """The declaration of one attribute."""

    name: str
    targets: AttrTarget
    params: tuple[AttrParam, ...] = ()
    #: Attributes that cannot be applied to the same object as this one.
    group: str | None = None
    #: Whether it may be applied to one object more than once.  Most attributes
    #: say one thing about an object and repeating one says nothing new; an
    #: expectation names one diagnostic, and a construct may raise several.
    repeatable: bool = False
    doc: str = ""

    def signature(self) -> str:
        """The attribute's signature, as a diagnostic spells it."""
        if not self.params:
            return self.name
        return "".join((self.name, "(", ", ".join(p.describe() for p in self.params), ")"))


def _param(name: str, kind: str, choices: tuple[str, ...] | None = None,
           default: object | None = None, required: bool = True) -> AttrParam:
    """Build an attribute parameter."""
    return AttrParam(name=name, kind=kind, choices=choices, default=default,
                     required=required)


#: Every attribute the compiler knows.  Adding one is an entry here plus the
#: code that acts on it; the notation itself never changes.
REGISTRY: Final[Mapping[str, AttrSpec]] = {
    spec.name: spec for spec in (
        AttrSpec("startup", AttrTarget.FUNCTION, group="special",
                 doc="the function control is transferred to once startup completes"),
        AttrSpec("constructor", AttrTarget.FUNCTION,
                 (_param("priority", "integer", default=0, required=False),),
                 group="special",
                 doc="runs before control is transferred to the startup function"),
        AttrSpec("destructor", AttrTarget.FUNCTION,
                 (_param("priority", "integer", default=0, required=False),),
                 group="special",
                 doc="runs when the startup function returns"),
        AttrSpec("test", AttrTarget.FUNCTION,
                 (_param("kind", "name", choices=("always", "build", "suite"),
                         default="suite", required=False),),
                 group="special",
                 doc="a test of the given kind; `suite` where none is named"),
        AttrSpec("inline", AttrTarget.FUNCTION,
                 (_param("mode", "name", choices=("always", "never"),
                         default="always", required=False),),
                 doc="guides the decision whether to inline the function"),
        AttrSpec("cdecl", AttrTarget.FUNCTION | AttrTarget.VARIABLE,
                 (_param("variadic", "boolean", default=False, required=False),),
                 doc="the definition follows the system's conventions"),
        AttrSpec("listable", AttrTarget.FUNCTION | AttrTarget.CALLABLE,
                 doc="an array argument is walked and the answers make an array"),
        AttrSpec("impure", AttrTarget.FUNCTION,
                 doc="the function may change things that outlive the call"),
        AttrSpec("can_ignore", AttrTarget.FUNCTION,
                 doc="a caller need not take what the function answers with"),
        AttrSpec("abi", AttrTarget.FUNCTION,
                 (_param("name", "string"),
                  _param("variadic", "boolean", default=False, required=False)),
                 doc="the function follows the named calling convention"),
        AttrSpec("export",
                 AttrTarget.FUNCTION | AttrTarget.VARIABLE | AttrTarget.TYPE,
                 doc="a file importing this module may name the definition"),
        AttrSpec("visible", AttrTarget.FUNCTION | AttrTarget.VARIABLE,
                 doc="the finished image offers the definition's symbol"),
        AttrSpec("flag", AttrTarget.TYPE,
                 doc="an enumeration whose values are meant to be combined"),
        AttrSpec("align", AttrTarget.TYPE | AttrTarget.VARIABLE | AttrTarget.FUNCTION,
                 (_param("bytes", "integer"),),
                 doc="requests a minimum alignment"),
        AttrSpec("section", AttrTarget.FUNCTION | AttrTarget.VARIABLE,
                 (_param("name", "string"),),
                 doc="places the object in the named section of the image"),
        AttrSpec("packed", AttrTarget.TYPE,
                 doc="requests a layout without padding between fields"),
        AttrSpec("ignore", AttrTarget.STATEMENT | AttrTarget.FUNCTION
                 | AttrTarget.VARIABLE,
                 (_param("number", "integer"),), repeatable=True,
                 doc="this diagnostic is not reported for the construct"),
        AttrSpec("expect", AttrTarget.STATEMENT | AttrTarget.FUNCTION
                 | AttrTarget.VARIABLE,
                 (_param("number", "integer"),), repeatable=True,
                 doc="the construct raises this diagnostic, and it is not reported"),
    )
}

#: Which special kind each of the mutually exclusive attributes selects.
SPECIAL_OF_TEST_KIND: Final[dict[str, SpecialKind]] = {
    "always": SpecialKind.TEST_ALWAYS,
    "build": SpecialKind.TEST_BUILD,
    "suite": SpecialKind.TEST_SUITE,
}


@dataclass(slots=True)
class BoundAttr:
    """An attribute whose arguments have been checked against its declaration."""

    spec: AttrSpec
    node: ast.Attribute
    values: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The attribute's name."""
        return self.spec.name

    def as_int(self, param: str, fallback: int = 0) -> int:
        """The integer value of the parameter *param*.

        The value was already checked against the parameter's declared kind, so
        anything else here would be a defect in that check.
        """
        value = self.values.get(param)
        return value if isinstance(value, int) and not isinstance(value, bool) else fallback

    def as_str(self, param: str, fallback: str = "") -> str:
        """The string or name value of the parameter *param*."""
        value = self.values.get(param)
        return value if isinstance(value, str) else fallback

    def as_bool(self, param: str, fallback: bool = False) -> bool:
        """The boolean value of the parameter *param*."""
        value = self.values.get(param)
        return value if isinstance(value, bool) else fallback


def lookup(name: str) -> AttrSpec | None:
    """Return the declaration of the attribute *name*, if there is one."""
    return REGISTRY.get(name)
