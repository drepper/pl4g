"""What the compiler chose that the program did not say.

A decision is not a diagnostic: nothing is wrong, and what is recorded is a
choice the source left open.  The checks here are that the choices a lambda
leaves open reach the log -- above all which variables a capture list saying
"all of them" turned out to bring in, which is the one thing about such a
lambda that cannot be read off the source.
"""

from __future__ import annotations

from pathlib import Path

from pypl4g.diag.engine import collecting_engine
from pypl4g.front.lexer import tokenize
from pypl4g.front.parser import parse
from pypl4g.ir.decisions import DecisionKind
from pypl4g.ir.module import Module
from pypl4g.sema.check import check
from pypl4g.sema.modules import ModuleRegistry
from pypl4g.source.manager import SourceManager

ARROW = "\N{RIGHTWARDS ARROW}"
LAMBDA = "\N{GREEK SMALL LETTER LAMDA}"
DEREF = "\N{POSITION INDICATOR}"
ASSIGN = "\N{LEFTWARDS ARROW}"
NE = "\N{NOT EQUAL TO}"


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
    brought = module.decisions.of_kind(DecisionKind.CAPTURE)
    assert [d.subject for d in brought] == ["n", "m"], \
        "the names, in the order the body writes them"
    assert all("by value" in d.reason for d in brought)
    named = module.decisions.of_kind(DecisionKind.NAME_LAMBDA)
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
    brought = module.decisions.of_kind(DecisionKind.CAPTURE)
    assert [d.subject for d in brought] == ["n"]
    assert "by reference" in brought[0].reason


def test_a_written_capture_list_is_not_a_decision() -> None:
    """The program said which names, so the compiler chose nothing."""
    module = compiled("".join((
        "    let n: u8 = 1u8\n",
        "    let f: fn(u8) ", ARROW, " u8 = ", LAMBDA, " a: u8 [n] ", ARROW,
        " u8 { a + n }\n",
        "    if f(1u8) ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    assert module.decisions.of_kind(DecisionKind.CAPTURE) == []
    assert module.decisions.of_kind(DecisionKind.NAME_LAMBDA) != [], \
        "the name is still the compiler's, whatever the list said"


def test_a_variable_put_in_storage_is_recorded() -> None:
    """The program wrote an ordinary name and the compiler chose memory."""
    module = compiled("".join((
        "    let n: mut u8 = 1u8\n",
        "    let r: &mut u8 = &mut n\n",
        "    r", DEREF, " ", ASSIGN, " 2u8\n",
        "    if r", DEREF, " ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    placed = module.decisions.of_kind(DecisionKind.PLACE_LOCAL)
    assert [d.subject for d in placed] == ["n"]
    assert "address" in placed[0].reason


def test_a_lifetime_worked_out_at_a_call_is_recorded() -> None:
    """A function promises its parameter's lifetime; the call says what that is.

    `first(&total)` for a variable at the top level answers with a reference
    that lasts as long as the program, and `first(&n)` for a local does not.
    Neither the signature nor the call writes that down, so the log does.
    """
    source = "".join((
        "let total: mut u8 = 3u8\n\n",
        "fn first(v: &u8) ", ARROW, " &u8 from v:\n    v\n\n",
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
    worked_out = module.decisions.of_kind(DecisionKind.LIFETIME)
    assert [d.subject for d in worked_out] == ["first"], \
        "only the call whose argument lasts that long is a decision"
    assert "'v'" in worked_out[0].reason, \
        "the reason names the parameter the lifetime was borrowed from"


def test_every_decision_says_why() -> None:
    """A log entry with no reason is one nobody can act on."""
    module = compiled("".join((
        "    let n: mut u8 = 1u8\n",
        "    let f: fn(u8) ", ARROW, " u8 = ", LAMBDA, " a: u8 [&] ", ARROW,
        " u8 { a + n }\n",
        "    if f(1u8) ", NE, " 2u8:\n        1u6\n    else:\n        0u6\n")))
    assert module.decisions.entries != []
    assert all(d.reason for d in module.decisions.entries)
