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
from ..front.doccomment import ORDER as DOC_ORDER, parse as parse_doc
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
            "textDocument/hover": self._hover,
            "textDocument/definition": self._definition,
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
                "hoverProvider": True,
                "definitionProvider": True,
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
            # What it is for, in one line: the prose a comment opens with, or
            # what its `\brief` said where it opens with one instead.
            brief = parse_doc(doc).brief
            if brief:
                out["detail"] = brief
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

    # -- what a name is, and where it was defined ------------------------------

    def _hover(self, params: Mapping[str, object]) -> object:
        """What the name under the cursor is.

        The checker's own answer where it resolved a name there, and the syntax
        tree's where the cursor is on a definition rather than on a use -- what a
        definition is, is written where it is written, and the checker has
        nothing to add to it.  Nothing at all anywhere else: a server that made
        something up for every position would be guessing at most of them.
        """
        found, loc = self._asked_about(params)
        if found is None or loc is None or found.sources is None:
            return None
        note = found.notes.at(loc) if found.notes is not None else None
        if note is not None:
            return self._as_hover(found, note.span, note.kind, note.name,
                                  note.detail, note.doc)
        made = self._definition_at(found, loc)
        if made is None:
            return None
        item, kind = made
        detail = getattr(item, "doc", None)
        return self._as_hover(found, item.name_span, kind, item.name, "",
                              detail if isinstance(detail, str) else None)

    def _as_hover(self, found: Analysis, span: Span, kind: str, name: str,
                  detail: str, doc: str | None) -> object:
        """One answer, written the way an editor renders one.

        A fenced block holding what the thing is, in this language, so that an
        editor with the grammar colours it the way it colours the program; then
        the documentation comment, which is prose where it is prose and a list
        where it is written the way Doxygen writes one.
        """
        assert found.sources is not None
        # A type whose name is what it is says it once: `type Pair`, not
        # `type Pair : Pair`.
        said = " ".join((kind, name)) if not detail or detail == name \
            else "".join((kind, " ", name, " : ", detail))
        lines = ["".join(("```pl4g\n", said, "\n```"))]
        lines.extend(_rendered(doc))
        answer: dict[str, object] = {
            "contents": {"kind": "markdown", "value": "\n\n".join(lines)}}
        where = places.range_of(found.sources, span, self._encoding)
        if where is not None:
            answer["range"] = where
        return answer

    def _definition(self, params: Mapping[str, object]) -> object:
        """Where the name under the cursor was defined.

        In this file or in another: a module's function is defined in the
        module, and the compiler read that file too, so the span it recorded
        places itself.  A name that is on its own definition answers with that
        definition, which is what makes the same key work either way round.
        """
        found, loc = self._asked_about(params)
        if found is None or loc is None or found.sources is None:
            return None
        note = found.notes.at(loc) if found.notes is not None else None
        if note is not None and note.defined.is_valid:
            spot = self._range_in(found, note.defined, path=None)
            if spot is not None:
                at, where = spot
                return {"uri": places.to_uri(at), "range": where}
        made = self._definition_at(found, loc)
        if made is None:
            return None
        item, _ = made
        where = places.range_of(found.sources, item.name_span, self._encoding)
        if where is None:
            return None
        return {"uri": places.to_uri(found.path), "range": where}

    def _definition_at(self, found: Analysis, loc: int
                       ) -> tuple[object, str] | None:
        """The definition whose name is written at *loc*, where one is.

        Only the name and not the whole definition: standing anywhere inside a
        function is not standing on its name, and answering with the function
        for every position in it would make hover say the same thing about every
        line of a body.
        """
        if found.unit is None:
            return None
        kinds = ((ast.FuncDef, "function"), (ast.TypeDef, "type"),
                 (ast.EnumDef, "type"), (ast.ModuleImport, "module"),
                 (ast.UnitDef, "unit"), (ast.VarDef, "variable"))
        for item in found.unit.items:
            kind = next((word for what, word in kinds if isinstance(item, what)),
                        None)
            named = getattr(item, "name_span", None)
            if kind is None or named is None or not isinstance(named, Span):
                continue
            if named.is_valid and named.start <= loc < named.end:
                return (item, kind)
        return None

    def _asked_about(self, params: Mapping[str, object]
                     ) -> tuple[Analysis | None, int | None]:
        """Which analysis a question is about, and where in the file it points.

        The position comes in the editor's units and has to be brought back to a
        place in the text; what does that is the line's own text, which the
        source manager holds.
        """
        path = self._path_of(self._uri_of(params))
        if path is None:
            return (None, None)
        found = self._seen.get(path)
        position = params.get("position")
        if found is None or found.sources is None \
                or not isinstance(position, dict):
            return (None, None)
        line = position.get("line")
        character = position.get("character")
        if not isinstance(line, int) or not isinstance(character, int):
            return (found, None)
        for one in found.sources.files:
            if one.path != path and one.path.resolve() != path.resolve():
                continue
            if line >= len(one.line_starts):
                return (found, None)
            start = one.line_starts[line]
            ends = one.text.find("\n", start)
            text = one.text[start:] if ends < 0 else one.text[start:ends]
            return (found, one.base + start
                    + places.characters(text, character, self._encoding))
        return (found, None)

    # -- odds and ends ---------------------------------------------------------

    def _uri_of(self, params: Mapping[str, object]) -> object:
        """The document a message is about."""
        document = params.get("textDocument")
        return document.get("uri") if isinstance(document, dict) else None

    def _path_of(self, uri: object) -> Path | None:
        """The file a URI names, where it names one this can compile."""
        return places.from_uri(uri) if isinstance(uri, str) else None


#: How each kind of part is introduced where an editor shows it.  The commands
#: that are about one named thing are gathered into a list under one heading;
#: the rest are a heading each, because each is a remark of its own.
_HEADINGS: Final[Mapping[str, str]] = {
    "param": "Takes", "return": "Answers with", "raises": "Refuses with",
    "pre": "Before", "post": "After", "invariant": "Always",
    "note": "Note", "warning": "Warning", "example": "Example", "see": "See",
    "since": "Since", "deprecated": "Deprecated", "todo": "To do",
    "author": "Author", "file": "File", "details": "",
}


def _rendered(doc: str | None) -> list[str]:
    """A documentation comment as the parts an editor shows.

    The summary first, because what a thing is for is what a reader wants first;
    then what it takes, what it answers with and what it refuses with, as a list
    each; then the remarks.  A command this does not know is shown as it was
    written rather than dropped -- the compiler has already said it is not one,
    and a reader looking at the hover should see what the comment says.
    """
    if not doc:
        return []
    found = parse_doc(doc)
    out: list[str] = []
    if found.summary:
        out.append(found.summary)
    for command in DOC_ORDER:
        parts = found.of(command)
        if not parts:
            continue
        heading = _HEADINGS.get(command, "")
        if command == "param":
            out.append("\n".join([
                "**Takes**", *("".join(("- `", one.subject, "` ", one.text))
                               for one in parts)]))
        elif heading:
            said = [one.text for one in parts if one.text]
            if len(said) == 1 and "\n" not in said[0]:
                # One short thing to say: said on the heading's own line, which
                # is a line rather than a paragraph and reads as one.
                out.append(" ".join(("".join(("**", heading, "**")), said[0])))
            else:
                out.append("\n\n".join(["".join(("**", heading, "**")), *said]))
        else:
            out.extend(one.text for one in parts if one.text)
    unknown = [one for one in found.parts if not one.known]
    if unknown:
        out.append("\n".join("".join((one.written, " ", one.text))
                              for one in unknown))
    return [one for one in out if one.strip()]


def serve(reader: BinaryIO | None = None, writer: BinaryIO | None = None) -> int:
    """Speak the protocol on *reader* and *writer*, which default to the streams.

    The standard output carries the protocol and nothing else, so everything the
    compiler would have printed goes to the standard error -- which an editor
    keeps as the server's log, and which is where a reader looks when the server
    itself is what is wrong.
    """
    return Server(Wire(reader if reader is not None else sys.stdin.buffer,
                       writer if writer is not None else sys.stdout.buffer)).run()
