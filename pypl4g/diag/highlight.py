"""What the tree-sitter grammar says each piece of a source line is.

The compiler has a lexer of its own and could colour a line from it, but a lexer
knows a name is a name and not that this one is a type and that one a parameter.
The grammar knows, and the queries beside it are what an editor already uses, so
a snippet in a diagnostic is coloured the way the same line is coloured where it
was written -- one description of the language rather than two that drift.

None of it is required.  The grammar is loaded on the first snippet that wants
colour, and where the module or the built library is missing there is simply no
highlighting: the compiler has no dependency outside the standard library and
this does not give it one.
"""

from __future__ import annotations

import ctypes
import warnings
from pathlib import Path
from typing import Final

#: Where the built library and the queries are, relative to this file.
_GRAMMAR: Final[Path] = Path(__file__).resolve().parent.parent.parent \
    / "tree-sitter-pl4g"
_LIBRARY: Final[Path] = _GRAMMAR / "pl4g.so"
_QUERIES: Final[Path] = _GRAMMAR / "queries" / "highlights.scm"


class Highlighter:
    """The grammar, loaded once, and what it says about a file.

    A file is parsed once however many diagnostics point into it: a line on its
    own does not parse -- it is a piece of a block -- so what is asked is always
    about the whole text, and asking it repeatedly would be the same answer
    worked out again.
    """

    def __init__(self) -> None:
        self._ready = False
        self._parser: object | None = None
        self._query: object | None = None
        self._cursor: type | None = None
        # Keyed by the identity of the text and holding it as well, so that
        # the string cannot be freed and another take its address.
        self._cache: dict[int, tuple[str, list[tuple[int, int, str]]]] = {}

    def _load(self) -> bool:
        """Get the grammar ready, answering whether there is one.

        Everything that can be missing is missing quietly: a compiler that said
        it could not colour something would be saying it about every line of
        every diagnostic, and none of it is about the program.
        """
        if self._ready:
            return self._parser is not None
        self._ready = True
        try:
            from tree_sitter import Language, Parser, Query, QueryCursor
        except ImportError:
            return False
        if not _LIBRARY.is_file() or not _QUERIES.is_file():
            return False
        try:
            library = ctypes.cdll.LoadLibrary(str(_LIBRARY))
            entry = library.tree_sitter_pl4g
            entry.restype = ctypes.c_void_p
            with warnings.catch_warnings():
                # The pointer out of a library loaded by hand is what there is;
                # the capsule the newer form wants comes from a packaged
                # module, and this grammar is built beside the compiler.
                warnings.simplefilter("ignore", DeprecationWarning)
                language = Language(entry())
            self._parser = Parser(language)
            self._query = Query(language, _QUERIES.read_text(encoding="utf-8"))
            self._cursor = QueryCursor
        except Exception:
            # Anything at all: a library built for another version of
            # tree-sitter, a query naming a node the grammar has not got.  What
            # is lost is colour.
            self._parser = None
            return False
        return True

    def _of_source(self, text: str) -> list[tuple[int, int, str]]:
        """Every highlighted run of *text*, in characters, earliest first.

        tree-sitter counts bytes and everything above counts characters, and
        this language is written in glyphs that are three bytes each -- so the
        one place the two meet is here, and it is where they are converted.
        """
        found = self._cache.get(id(text))
        if found is not None and found[0] is text:
            return found[1]
        data = text.encode("utf-8")
        # Where each byte lands once the text is characters.  Built once per
        # file rather than worked out per capture, which would be the same walk
        # over the same bytes for every one of them.
        at_byte = [0] * (len(data) + 1)
        byte = 0
        for index, letter in enumerate(text):
            for _ in range(len(letter.encode("utf-8"))):
                at_byte[byte] = index
                byte += 1
        at_byte[len(data)] = len(text)
        assert self._parser is not None and self._cursor is not None
        tree = self._parser.parse(data)  # type: ignore[attr-defined]
        found: list[tuple[int, int, int, str]] = []
        for pattern, captured in self._cursor(  # type: ignore[misc]
                self._query).matches(tree.root_node):
            for name, nodes in captured.items():
                for node in nodes:
                    found.append((at_byte[node.start_byte],
                                  at_byte[node.end_byte], pattern, name))
        # Earliest first; of two beginning together the longer first, and of two
        # the same the one written earlier in the query file.  That last is the
        # rule tree-sitter's own highlighter follows, and it is what the queries
        # are written to: `(type (identifier) @type)` stands above the line that
        # calls every name a variable, and so wins wherever both match.
        found.sort(key=lambda one: (one[0], -one[1], one[2]))
        runs = [(begins, ends, name) for begins, ends, _, name in found]
        self._cache[id(text)] = (text, runs)
        return runs

    def of_line(self, text: str, start: int, length: int
                ) -> list[tuple[int, int, str]]:
        """What each piece of one line is, as offsets within that line.

        *start* is where the line begins in *text* and *length* how long it is.
        Runs that reach past either end are cut to it, since a string spanning
        two lines is still coloured on each of them.
        """
        if not self._load():
            return []
        try:
            runs = self._of_source(text)
        except Exception:
            return []
        end = start + length
        found: list[tuple[int, int, str]] = []
        last = 0
        for begins, ends, name in runs:
            if ends <= start or begins >= end:
                continue
            here = max(begins - start, 0)
            there = min(ends - start, length)
            if here < last or there <= here:
                # Inside something already written, or empty.  The outer run
                # was the coarser answer and this is the finer one, but both
                # cannot be written and the first is the one already there.
                continue
            found.append((here, there, name))
            last = there
        return found
