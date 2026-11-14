"""Selecting the engineering material that applies to a development task.

The selection combines three kinds of database queries:

1. **symbol lookup** - the task text is scanned for identifiers, the
   database is asked which of them are real project symbols.  This yields
   the code that has to change and the specifications whose ``applies_to``
   points at it.
2. **reference lookup** - the database is asked for the tests that cover the
   selected symbols.
3. **full text search** - the native seekdb / OceanBase full text index
   ranks the remaining material, which is what catches a task that is
   described in words instead of symbol names.

Every step is a real SQL statement; the module never scans the corpus in
Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config
from .db import DocumentStore, Hit

__all__ = ["Retriever", "Selection"]

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

_STOPWORDS = {
    "the",
    "and",
    "for",
    "add",
    "new",
    "not",
    "but",
    "you",
    "all",
    "any",
    "can",
    "has",
    "have",
    "must",
    "should",
    "with",
    "this",
    "that",
    "from",
    "into",
    "when",
    "then",
    "than",
    "use",
    "used",
    "using",
    "keep",
    "test",
    "tests",
    "code",
    "file",
    "files",
}


@dataclass
class Selection:
    """The material that applies to one task."""

    task_text: str
    specs: list[Hit] = field(default_factory=list)
    code: list[Hit] = field(default_factory=list)
    tests: list[Hit] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "task_text": self.task_text,
            "symbols": self.symbols,
            "spec_entries": [hit.as_dict() for hit in self.specs],
            "code": [hit.as_dict() for hit in self.code],
            "tests": [hit.as_dict() for hit in self.tests],
            "db_queries": self.queries,
        }


def _merge(primary: list[Hit], secondary: list[Hit], limit: int) -> list[Hit]:
    """Merge two result lists, keeping the order of ``primary`` first."""
    merged: list[Hit] = []
    seen: set[str] = set()
    for hit in list(primary) + list(secondary):
        if hit.doc_id in seen:
            continue
        seen.add(hit.doc_id)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return merged


class Retriever:
    """Retrieve the material that applies to a task."""

    def __init__(self, store: DocumentStore, config: Config):
        self.store = store
        self.config = config

    def _candidates(self, task_text: str) -> list[str]:
        found = []
        for identifier in _IDENTIFIER_RE.findall(task_text):
            lowered = identifier.lower()
            if lowered in _STOPWORDS:
                continue
            if identifier not in found:
                found.append(identifier)
        return found

    def select(self, task_text: str, limit_per_kind: int | None = None) -> Selection:
        limit = limit_per_kind or self.config.context_limit_per_kind
        selection = Selection(task_text=task_text)
        candidates = self._candidates(task_text)

        known, executed = self.store.match_symbols(candidates)
        selection.queries.extend(executed)
        selection.symbols = known

        # --- specifications that apply to the symbols of the task -------
        spec_hits: list[Hit] = []
        if known:
            hits, executed = self.store.find_like("spec", "applies_to", known)
            spec_hits.extend(hits)
            selection.queries.extend(executed)

        # --- the code that has to change ---------------------------------
        code_hits: list[Hit] = []
        if known:
            hits, executed = self.store.find_by_field("code", "symbol", known)
            code_hits.extend(hits)
            selection.queries.extend(executed)

        # --- the tests that cover the selected code ----------------------
        test_hits: list[Hit] = []
        covered = list(known)
        for hit in code_hits:
            if hit.symbol and hit.symbol != "__file__" and hit.symbol not in covered:
                covered.append(hit.symbol)
        if covered:
            hits, executed = self.store.find_by_reference("test", covered)
            test_hits.extend(hits)
            selection.queries.extend(executed)

        # --- full text ranking for everything else -----------------------
        for kind, bucket in (("spec", spec_hits), ("code", code_hits), ("test", test_hits)):
            hits, executed = self.store.search_fts(task_text, kinds=[kind], limit=limit * 2)
            bucket.extend(hits)
            selection.queries.extend(executed)

        selection.specs = _merge(spec_hits, [], limit)
        selection.code = _merge(code_hits, [], limit)
        selection.tests = _merge(test_hits, [], limit)
        return selection
