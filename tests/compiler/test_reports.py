"""What the compiler chose that the program did not say.

A report is not a diagnostic: nothing is wrong, and what is recorded is a
choice the source left open.  The checks here are that the choices a lambda
leaves open reach the log -- above all which variables a capture list saying
"all of them" turned out to bring in, which is the one thing about such a
lambda that cannot be read off the source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from pypl4g.diag.engine import collecting_engine
from pypl4g.front.lexer import tokenize
from pypl4g.front.parser import parse
from pypl4g.ir.reports import ReportKind
from pypl4g.ir.module import Module
from pypl4g.sema.check import check
from pypl4g.sema.modules import ModuleRegistry
from pypl4g.source.manager import SourceManager

ARROW = "\N{RIGHTWARDS ARROW}"
LAMBDA = "\N{GREEK SMALL LETTER LAMDA}"
DEREF = "\N{POSITION INDICATOR}"
ASSIGN = "\N{LEFTWARDS ARROW}"
NE = "\N{NOT EQUAL TO}"
OR = "\N{LOGICAL OR}"
LIFETIME = "\N{WHITE HOURGLASS}"


#: The kinds that are something the compiler said rather than something it
#: chose, which is what the catalog numbers.
_SAID: Final[frozenset[ReportKind]] = frozenset((
    ReportKind.FATAL, ReportKind.ERROR, ReportKind.WARNING, ReportKind.NOTE))

#: A function whose answer may come from either of two parameters, and a
#: variable at the top level to hand it.
EITHER: Final[str] = "".join((
    "let top: u32 = 0u32\nlet other: u32 = 2u32\n\n",
    "fn either(a: bool, b: &\N{WHITE HOURGLASS}x u32, c: &\N{WHITE HOURGLASS}x u32) ",
    "\N{RIGHTWARDS ARROW} &\N{WHITE HOURGLASS}x u32:\n    if a: b else: c\n\n"))


def checked(body: str, around: str = "") -> tuple[Module, list]:
    """Check a startup function with *body*, and answer with what was said too.

    The engine writes into the module\'s log, which is what the compiler does:
    one log holding what was said beside what was chosen, in the order the two
    happened.
    """
    source = "".join((around, "@[startup, impure]\nfn main() ", ARROW,
                      " u6:\n", body))
    sources = SourceManager()
    unit_source = sources.add(Path("t.pl4g"), source)
    module = Module("t")
    engine, collected = collecting_engine(None)
    engine.write_into(module.reports)
    unit = parse(tokenize(unit_source, engine), "t.pl4g", engine)
    check(module, [unit], engine, ModuleRegistry(), sources)
    assert [d.info.name for d in collected if d.info.severity == "error"] == []
    return module, collected


def compiled(body: str) -> Module:
    """Check a startup function with *body* and answer with the module.

    The optimizer is not run: what is being asked about is what the checker
    decided, and a pass that dropped something would only add to it.
    """
    source = "".join(("@[startup, impure]\nfn main() ", ARROW, " u6:\n", body))
    sources = SourceManager()
    unit_source = sources.add(Path("t.pl4g"), source)
    engine, collected = collecting_engine(None)
    unit = parse(tokenize(unit_source, engine), "t.pl4g", engine)
    module = Module("t")
    check(module, [unit], engine, ModuleRegistry(), sources)
    assert [d.info.name for d in collected if d.info.severity == "error"] == []
    return module


def test_what_a_capture_list_brought_in_is_recorded() -> None:
    """`[=]` and `[&]` leave the names to the compiler, so the log says which.

    One entry per name, so that "which variables were brought in" is a question
    the log answers without anything having to read prose.
    """
    module = compiled("".join((
        "    let n: mut u8 = 1u8\n",
        "    let m: u8 = 2u8\n",
        "    let f: fn(u8) ", ARROW, " u8 = ", LAMBDA, " a: u8 [=] ", ARROW,
        " u8 { a + n + m }\n",
        "    if f(1u8) ", NE, " 4u8:\n        1u6\n    else:\n        0u6\n")))
    brought = module.reports.of_kind(ReportKind.CAPTURE)
    assert [d.subject for d in brought] == ["n", "m"], \
        "the names, in the order the body writes them"
    assert all("by value" in d.reason for d in brought)
    named = module.reports.of_kind(ReportKind.NAME_LAMBDA)
    assert len(named) == 1 and named[0].subject.endswith("lambda1")
    assert named[0].subject in brought[0].reason, \
        "a capture has to say which lambda brought it in"
    assert all(d.span.is_valid for d in brought), \
        "a reader has to be able to be pointed at the lambda"


def test_by_reference_says_so() -> None:
    """The two lists differ in what they do, so the log has to differ too."""
    module = compiled("".join((
        "    let n: mut u8 = 1u8\n",
        "    let f: fn(u8) ", ARROW, " u8 = ", LAMBDA, " a: u8 [&] ", ARROW,
        " u8 { a + n }\n",
        "    if f(1u8) ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    brought = module.reports.of_kind(ReportKind.CAPTURE)
    assert [d.subject for d in brought] == ["n"]
    assert "by reference" in brought[0].reason


def test_a_written_capture_list_is_not_a_report() -> None:
    """The program said which names, so the compiler chose nothing."""
    module = compiled("".join((
        "    let n: u8 = 1u8\n",
        "    let f: fn(u8) ", ARROW, " u8 = ", LAMBDA, " a: u8 [n] ", ARROW,
        " u8 { a + n }\n",
        "    if f(1u8) ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    assert module.reports.of_kind(ReportKind.CAPTURE) == []
    assert module.reports.of_kind(ReportKind.NAME_LAMBDA) != [], \
        "the name is still the compiler's, whatever the list said"


def test_a_variable_put_in_storage_is_recorded() -> None:
    """The program wrote an ordinary name and the compiler chose memory."""
    module = compiled("".join((
        "    let n: mut u8 = 1u8\n",
        "    let r: &mut u8 = &mut n\n",
        "    r", DEREF, " ", ASSIGN, " 2u8\n",
        "    if r", DEREF, " ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    placed = module.reports.of_kind(ReportKind.PLACE_LOCAL)
    assert [d.subject for d in placed] == ["n"]
    assert "address" in placed[0].reason


def test_a_lifetime_worked_out_at_a_call_is_recorded() -> None:
    """A function promises its parameters' lifetime; the call says what that is.

    `first(&total)` for a variable at the top level answers with a reference
    that lasts as long as the program, and `first(&n)` for a local answers with
    one that lives as long as `n`.  Neither the signature nor the call writes
    either of those down, so the log does -- both of them, since the answer is
    what was worked out and not only the longer of the two.
    """
    source = "".join((
        "let total: mut u8 = 3u8\n\n",
        "fn first(v: &", LIFETIME, "a u8) ", ARROW, " &", LIFETIME,
        "a u8:\n    v\n\n",
        "@[startup, impure]\nfn main() ", ARROW, " u6:\n",
        "    let g: &static u8 = first(&total)\n",
        "    let n: u8 = 1u8\n",
        "    let s: &u8 = first(&n)\n",
        "    if g", DEREF, " + s", DEREF, " ", NE, " 4u8:\n",
        "        1u6\n    else:\n        0u6\n"))
    sources = SourceManager()
    unit_source = sources.add(Path("t.pl4g"), source)
    engine, collected = collecting_engine(None)
    unit = parse(tokenize(unit_source, engine), "t.pl4g", engine)
    module = Module("t")
    check(module, [unit], engine, ModuleRegistry(), sources)
    assert [d.info.name for d in collected if d.info.severity == "error"] == []
    worked_out = module.reports.of_kind(ReportKind.LIFETIME)
    assert [one.subject for one in worked_out] == ["first", "first"], \
        "every call that borrows a lifetime says what it came to"
    assert "as long as the program" in worked_out[0].reason \
        and "'v'" in worked_out[0].reason, \
        "the argument was a variable at the top level, and the reason says which"
    assert "as long as 'n'" in worked_out[1].reason, \
        "the argument was a local, and the reason names it"


def test_what_the_compiler_said_is_in_the_log_too() -> None:
    """A diagnostic and a choice are one log and one order.

    The question a reader has -- what happened to my program -- is not a
    question about only one of them, and a warning that reached the terminal and
    not the log would be a thing the log was silently missing.  The number is
    what tells the two apart for something matching on the log: a choice has
    none, nothing being wrong with any of it.
    """
    module, collected = checked("".join((
        "    let a: mut u6 = 5u6\n    a ", ASSIGN, " 4u6\n    a\n")))
    assert [d.info.number for d in collected] == [4006], \
        "the value given to `a` and never read is the one thing said"
    said = [one for one in module.reports.entries
            if one.kind is ReportKind.WARNING]
    assert [one.subject for one in said] == ["LANG_VARDEF_VALUE_UNUSED"]
    assert said[0].number == 4006
    assert "never read" in said[0].reason
    assert all(one.number is None for one in module.reports.entries
               if one.kind not in _SAID), "a choice has no number"


def test_a_lifetime_over_two_parameters_names_what_decided_it() -> None:
    """A name on two parameters means the shorter of what the two named.

    Which of them that was is a thing about the call and about nothing else: the
    signature says the two are equal and the call says which one the answer took
    its lifetime from.  Where the two live equally long both are named, since
    saying one of them would read as though the other had been turned down.
    """
    module, collected = checked("".join((
        "    let n: u32 = 1u32\n",
        "    let one: &u32 = either(true, &top, &n)\n",
        "    let two: &static u32 = either(true, &top, &other)\n",
        "    if one", DEREF, " ", NE, " 0u32 ", OR, " two", DEREF, " ", NE,
        " 2u32: 1u6 else: 0u6\n")), around=EITHER)
    assert [d.info.number for d in collected] == []
    found = module.reports.of_kind(ReportKind.LIFETIME)
    assert len(found) == 2
    assert "as long as 'n'" in found[0].reason \
        and "'b' and 'c'" in found[0].reason, \
        "the shorter of the two is what the answer took, and the reason says so"
    assert "as long as the program" in found[1].reason, \
        "both arguments lasted that long, so the answer does"


def test_every_report_says_why() -> None:
    """A log entry with no reason is one nobody can act on."""
    module = compiled("".join((
        "    let n: mut u8 = 1u8\n",
        "    let f: fn(u8) ", ARROW, " u8 = ", LAMBDA, " a: u8 [&] ", ARROW,
        " u8 { a + n }\n",
        "    if f(1u8) ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    assert module.reports.entries != []
    assert all(d.reason for d in module.reports.entries)
