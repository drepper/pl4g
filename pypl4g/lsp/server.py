"""The server: what an editor asks, and what the compiler answers.

One loop, one message at a time, and no threads.  A request is answered before
the next is read, which is what makes the state easy to believe and is affordable
because the compiler is fast: the analysis a keystroke asks for is a few
milliseconds, and an editor sends one only after it has stopped hearing
keystrokes.  Nothing here is asynchronous, so nothing here can answer out of
order, and a cancellation is therefore something to ignore rather than to race.

**What it can do** is say what is wrong (`publishDiagnostics`) and what is in a
file (`documentSymbol`).  Both come from the compiler: the first is its
diagnostics, placed where it placed them, and the second is the syntax tree it
parsed.  Nothing is added to either -- a server that invented a message the
compiler does not make would be telling a reader about a language nobody
implements.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import BinaryIO, Callable, Final, Mapping, Sequence

from ..diag.engine import Diagnostic
from ..front import ast
from ..source.location import Span
from .. import VERSION
from . import places
from .analysis import Analysis, analyse
from .wire import Closed, Wire

#: The name the server goes by, which an editor shows where it names one.
NAME: Final[str] = "pypl4g"

#: How the editor is to keep the server up to date: the whole text of a document
#: each time it changes.  The protocol offers a form that sends the changed part
#: alone, and the saving it is worth here is nothing -- the compiler reads the
#: whole file anyway, and a file of this language is a few kilobytes.
FULL_SYNC: Final[int] = 1

#: What each severity of the compiler's comes to in the protocol's numbering.
#: Everything the compiler calls fatal is an error to an editor: the distinction
#: is about whether the compiler can carry on, which is nothing to the reader.
SEVERITY: Final[Mapping[str, int]] = {
    "fatal": 1, "error": 1, "warning": 2, "note": 4}

#: What each kind of definition is, in the protocol's numbering of symbol kinds.
KIND_FUNCTION: Final[int] = 12
KIND_VARIABLE: Final[int] = 13
KIND_STRUCT: Final[int] = 23
KIND_ENUM: Final[int] = 10
KIND_ENUM_MEMBER: Final[int] = 22
KIND_FIELD: Final[int] = 8
KIND_MODULE: Final[int] = 2
KIND_UNIT: Final[int] = 11


class Server:
    """One session: the documents it has been sent, and what it made of them."""

    def __init__(self, wire: Wire) -> None:
        self._wire = wire
        self._encoding = places.DEFAULT
        #: The text of every document the editor has open, which is what gets
        #: compiled -- and what an import from one of them reads.
        self._held: dict[Path, str] = {}
        self._seen: dict[Path, Analysis] = {}
        self._running = True
        self._asked_to_stop = False

    # -- the loop --------------------------------------------------------------

    def run(self) -> int:
        """Answer messages until the editor says to stop, or goes away."""
        while self._running:
            try:
                message = self._wire.read()
            except Closed:
                break
            if message:
                self._dispatch(message)
        # A session the editor ended properly is a success; one it abandoned is
        # not something the server can call wrong either, there being nothing
        # left to tell.
        return 0

    def _dispatch(self, message: Mapping[str, object]) -> None:
        """Answer one message, or put down what is wrong with it."""
        method = message.get("method")
        ident = message.get("id")
        if not isinstance(method, str):
            return
        handler = self._handlers().get(method)
        if handler is None:
            # A request has to be answered even where the answer is that there
            # is no such method; a notification does not.
            if ident is not None:
                self._error(ident, -32601, "".join(("no such method: ", method)))
            return
        params = message.get("params")
        answer = handler(params if isinstance(params, dict) else {})
        if ident is not None:
            self._answer(ident, answer)

    def _handlers(self) -> Mapping[str, Callable[[Mapping[str, object]], object]]:
        """Every method this server knows, by the name the protocol gives it."""
        return {
            "initialize": self._initialize,
            "initialized": lambda params: None,
            "shutdown": self._shutdown,
            "exit": self._exit,
            "$/setTrace": lambda params: None,
            "$/cancelRequest": lambda params: None,
            "textDocument/didOpen": self._did_open,
            "textDocument/didChange": self._did_change,
            "textDocument/didSave": self._did_save,
            "textDocument/didClose": self._did_close,
            "textDocument/documentSymbol": self._document_symbol,
        }

    def _answer(self, ident: object, result: object) -> None:
        """Send the answer to the request called *ident*."""
        self._wire.write({"jsonrpc": "2.0", "id": ident, "result": result})

    def _error(self, ident: object, code: int, message: str) -> None:
        """Send a refusal, which an answer to a request may also be."""
        self._wire.write({"jsonrpc": "2.0", "id": ident,
                          "error": {"code": code, "message": message}})

    def _notify(self, method: str, params: object) -> None:
        """Say something the editor did not ask for, which is most of the work."""
        self._wire.write({"jsonrpc": "2.0", "method": method, "params": params})

    # -- the session -----------------------------------------------------------

    def _initialize(self, params: Mapping[str, object]) -> object:
        """Agree on how to count, and say what this server can do."""
        capabilities = params.get("capabilities")
        general = capabilities.get("general") if isinstance(capabilities, dict) else None
        offered = general.get("positionEncodings") if isinstance(general, dict) else None
        self._encoding = places.agreed(offered)
        return {
            "capabilities": {
                "positionEncoding": str(self._encoding),
                "textDocumentSync": {
                    "openClose": True,
                    "change": FULL_SYNC,
                    # Without the text: the server has it already, every change
                    # having been sent, and a save that also sent it would be
                    # the same bytes twice.
                    "save": {"includeText": False},
                },
                "documentSymbolProvider": True,
            },
            "serverInfo": {"name": NAME, "version": VERSION},
        }

    def _shutdown(self, params: Mapping[str, object]) -> object:
        """Agree to stop.  What stops is the next message, which is `exit`."""
        self._asked_to_stop = True
        return None

    def _exit(self, params: Mapping[str, object]) -> object:
        """Stop."""
        self._running = False
        return None

    # -- documents -------------------------------------------------------------

    def _did_open(self, params: Mapping[str, object]) -> object:
        """A document the editor has opened, with its text."""
        document = params.get("textDocument")
        if not isinstance(document, dict):
            return None
        path = self._path_of(document.get("uri"))
        text = document.get("text")
        if path is None or not isinstance(text, str):
            return None
        self._held[path] = text
        self._publish(path, whole=True)
        return None

    def _did_change(self, params: Mapping[str, object]) -> object:
        """A document the editor has changed, with the whole of its new text."""
        path = self._path_of(self._uri_of(params))
        changes = params.get("contentChanges")
        if path is None or not isinstance(changes, list) or not changes:
            return None
        last = changes[-1]
        if not isinstance(last, dict) or not isinstance(last.get("text"), str):
            return None
        self._held[path] = str(last["text"])
        self._publish(path, whole=False)
        return None

    def _did_save(self, params: Mapping[str, object]) -> object:
        """A document the editor has saved, which is when the back end is asked."""
        path = self._path_of(self._uri_of(params))
        if path is not None:
            self._publish(path, whole=True)
        return None

    def _did_close(self, params: Mapping[str, object]) -> object:
        """A document the editor has closed.

        Its diagnostics go with it: what an editor shows for a file it is not
        showing is nothing, and leaving them would leave a reader with a list of
        complaints about text nobody can see.
        """
        path = self._path_of(self._uri_of(params))
        if path is None:
            return None
        self._held.pop(path, None)
        self._seen.pop(path, None)
        self._notify("textDocument/publishDiagnostics",
                     {"uri": places.to_uri(path), "diagnostics": []})
        return None

    def _publish(self, path: Path, *, whole: bool) -> None:
        """Compile what is held and say what is wrong with it."""
        found = analyse(path, self._held, whole=whole,
                        module_path=[path.parent])
        self._seen[path] = found
        self._notify("textDocument/publishDiagnostics", {
            "uri": places.to_uri(path),
            "diagnostics": [one for one in
                            (self._as_diagnostic(found, diag)
                             for diag in found.diagnostics) if one is not None],
        })

    def _as_diagnostic(self, found: Analysis, diag: Diagnostic
                       ) -> dict[str, object] | None:
        """One diagnostic of the compiler's, as the protocol has them.

        Only the ones about this file: a diagnostic in a module the program
        imports belongs to that file, and an editor showing it here would put it
        at a line of the wrong document.  The notes travel with the diagnostic
        they belong to, as related information, which is where an editor looks
        for them.
        """
        if found.sources is None:
            return None
        where = self._range_in(found, diag.span, path=found.path)
        if where is None:
            return None
        out: dict[str, object] = {
            "range": where,
            "severity": SEVERITY.get(diag.info.severity, 1),
            "code": diag.info.number,
            "source": NAME,
            "message": diag.text,
        }
        related: list[dict[str, object]] = []
        for note in diag.notes:
            spot = self._range_in(found, note.span, path=None)
            if spot is None:
                continue
            at, span_range = spot
            related.append({
                "location": {"uri": places.to_uri(at), "range": span_range},
                "message": note.text})
        if related:
            out["relatedInformation"] = related
        return out

    def _range_in(self, found: Analysis, span: Span, *, path: Path | None
                  ) -> object:
        """Where a span is, refusing one that is not in *path* where one is given.

        With a path, the answer is the range alone and nothing outside that file
        has one; without, it is the file and the range, which is what a note
        pointing into another file needs.
        """
        assert found.sources is not None
        if path is not None:
            if span.is_valid:
                at = found.sources.file_of(span.start)
                if at is not None and at.path != path:
                    return None
            return places.range_of(found.sources, span,
                                   self._encoding) or places.WHOLE_FILE
        if not span.is_valid:
            return None
        at = found.sources.file_of(span.start)
        made = places.range_of(found.sources, span, self._encoding)
        if at is None or made is None:
            return None
        return (at.path, made)

    # -- what is in a file -----------------------------------------------------

    def _document_symbol(self, params: Mapping[str, object]) -> object:
        """Everything the file defines, in the order it defines it.

        From the tree the compiler parsed, so what an outline shows is what the
        compiler found -- including, where the file does not parse, everything
        before the place it stopped.
        """
        path = self._path_of(self._uri_of(params))
        if path is None:
            return []
        found = self._seen.get(path)
        if found is None or found.unit is None or found.sources is None:
            return []
        return [one for one in (self._symbol(found, item)
                                for item in found.unit.items) if one is not None]

    def _symbol(self, found: Analysis, item: object) -> dict[str, object] | None:
        """One definition as a symbol, with whatever it holds beneath it."""
        assert found.sources is not None
        kinds: Sequence[tuple[type, int]] = (
            (ast.FuncDef, KIND_FUNCTION), (ast.TypeDef, KIND_STRUCT),
            (ast.EnumDef, KIND_ENUM), (ast.ModuleImport, KIND_MODULE),
            (ast.UnitDef, KIND_UNIT), (ast.VarDef, KIND_VARIABLE))
        kind = next((number for what, number in kinds if isinstance(item, what)),
                    None)
        name = getattr(item, "name", None)
        if kind is None or not isinstance(name, str) or not name:
            return None
        whole = places.range_of(found.sources, item.span, self._encoding)
        named = places.range_of(found.sources, item.name_span, self._encoding)
        if whole is None or named is None:
            return None
        out: dict[str, object] = {"name": name, "kind": kind,
                                  "range": whole, "selectionRange": named}
        doc = getattr(item, "doc", None)
        if isinstance(doc, str) and doc:
            out["detail"] = doc.strip().splitlines()[0]
        inside = self._inside(found, item)
        if inside:
            out["children"] = inside
        return out

    def _inside(self, found: Analysis, item: object) -> list[dict[str, object]]:
        """What a definition holds: a record's fields, an enumeration's values."""
        assert found.sources is not None
        parts: list[tuple[str, Span, Span, int]] = []
        if isinstance(item, ast.TypeDef):
            parts = [(one.name, one.span, one.name_span, KIND_FIELD)
                     for one in item.fields]
        elif isinstance(item, ast.EnumDef):
            parts = [(one.name, one.span, one.name_span, KIND_ENUM_MEMBER)
                     for one in item.members]
        out: list[dict[str, object]] = []
        for name, span, named, kind in parts:
            whole = places.range_of(found.sources, span, self._encoding)
            where = places.range_of(found.sources, named, self._encoding)
            if whole is None or where is None:
                continue
            out.append({"name": name, "kind": kind, "range": whole,
                        "selectionRange": where})
        return out

    # -- odds and ends ---------------------------------------------------------

    def _uri_of(self, params: Mapping[str, object]) -> object:
        """The document a message is about."""
        document = params.get("textDocument")
        return document.get("uri") if isinstance(document, dict) else None

    def _path_of(self, uri: object) -> Path | None:
        """The file a URI names, where it names one this can compile."""
        return places.from_uri(uri) if isinstance(uri, str) else None


def serve(reader: BinaryIO | None = None, writer: BinaryIO | None = None) -> int:
    """Speak the protocol on *reader* and *writer*, which default to the streams.

    The standard output carries the protocol and nothing else, so everything the
    compiler would have printed goes to the standard error -- which an editor
    keeps as the server's log, and which is where a reader looks when the server
    itself is what is wrong.
    """
    return Server(Wire(reader if reader is not None else sys.stdin.buffer,
                       writer if writer is not None else sys.stdout.buffer)).run()
