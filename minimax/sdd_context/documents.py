"""Turn project files into addressable documents.

Three kinds of material are recognised:

``spec``
    Markdown files with a YAML front matter (``specs/*.md``).  Every
    normative rule (``- R1: ...``) becomes its own document so that the
    context can point at an exact rule.
``code``
    Python modules (``src/**/*.py``).  Every function and class - including
    methods - becomes a document, so that the context can point at an exact
    symbol.
``test``
    Python test modules (``tests/**/*.py``).  Every ``test_*`` function
    becomes a document.

All paths are relative to the project root, which keeps the database
portable between machines.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .textnorm import normalize_for_search

__all__ = ["Document", "collect_documents", "parse_spec_file", "parse_python_file"]

_RULE_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*(R\d+)\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

SPEC_DIR_NAMES = ("specs", "spec", "doc", "docs")
SRC_DIR_NAMES = ("src",)
TEST_DIR_NAMES = ("tests", "test")


@dataclass
class Document:
    """A single addressable piece of engineering material."""

    doc_id: str
    kind: str
    source_path: str
    symbol: str = ""
    locator: str = ""
    title: str = ""
    applies_to: str = ""
    references: str = ""
    tags: str = ""
    body: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def search_text(self) -> str:
        parts = [
            self.title,
            self.symbol,
            self.locator,
            self.applies_to,
            self.references,
            self.tags,
            self.body,
        ]
        return normalize_for_search(" ".join(part for part in parts if part))


# ---------------------------------------------------------------------------
# front matter
# ---------------------------------------------------------------------------

def _parse_front_matter(raw: str) -> tuple[dict, str]:
    """Parse ``---`` delimited front matter.

    PyYAML is used when available; a small fallback parser keeps the module
    usable in a minimal environment.
    """
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    block, body = match.group(1), match.group(2)
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(block) or {}
        if not isinstance(data, dict):
            data = {}
        return data, body
    except Exception:
        pass

    data: dict[str, object] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [item.strip().strip("'\"") for item in value[1:-1].split(",")]
            data[key.strip()] = [item for item in items if item]
        else:
            data[key.strip()] = value.strip("'\"")
    return data, body


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value)]


# ---------------------------------------------------------------------------
# specifications
# ---------------------------------------------------------------------------

def parse_spec_file(path: Path, rel: str) -> list[Document]:
    """Parse one specification markdown file into documents."""
    raw = path.read_text(encoding="utf-8")
    front, body = _parse_front_matter(raw)

    spec_id = str(front.get("id") or path.stem).strip()
    title = str(front.get("title") or "").strip()
    if not title:
        heading = _HEADING_RE.search(body)
        title = heading.group(1).strip() if heading else spec_id
    version = front.get("version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1
    applies_to = ",".join(_as_list(front.get("applies_to")))
    tags = _as_list(front.get("tags"))

    documents: list[Document] = []
    rules = list(_RULE_RE.finditer(body))
    intro = _RULE_RE.sub("", body).strip() if rules else body.strip()

    documents.append(
        Document(
            doc_id=f"spec:{spec_id}:__overview__",
            kind="spec",
            source_path=rel,
            symbol=spec_id,
            locator=spec_id,
            title=title,
            applies_to=applies_to,
            tags=",".join(tags),
            body=intro,
            meta={
                "spec_id": spec_id,
                "rule_id": "__overview__",
                "version": version,
                "tags": tags,
                "title": title,
                "applies_to": applies_to,
            },
        )
    )

    for match in rules:
        rule_id = match.group(1)
        rule_text = match.group(2).strip()
        documents.append(
            Document(
                doc_id=f"spec:{spec_id}:{rule_id}",
                kind="spec",
                source_path=rel,
                symbol=rule_id,
                locator=f"{spec_id}.{rule_id}",
                title=title,
                applies_to=applies_to,
                tags=",".join(tags),
                body=f"{rule_id}: {rule_text}",
                meta={
                    "spec_id": spec_id,
                    "rule_id": rule_id,
                    "version": version,
                    "tags": tags,
                    "title": title,
                    "applies_to": applies_to,
                },
            )
        )
    return documents


# ---------------------------------------------------------------------------
# python sources
# ---------------------------------------------------------------------------

def _python_symbols(source: str) -> list[tuple[str, str, int, int]]:
    """Return ``(qualname, kind, start_line, end_line)`` for a module."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    found: list[tuple[str, str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                found.append((qualname, "function", child.lineno, child.end_lineno or child.lineno))
                visit(child, f"{qualname}.")
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}{child.name}"
                found.append((qualname, "class", child.lineno, child.end_lineno or child.lineno))
                visit(child, f"{qualname}.")

    visit(tree, "")
    return found


def parse_python_file(path: Path, rel: str, kind: str = "code") -> list[Document]:
    """Parse one Python file into documents (one per symbol + one per file)."""
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    documents: list[Document] = []

    for qualname, symbol_kind, start, end in _python_symbols(source):
        snippet = "\n".join(lines[start - 1:end])
        docstring = ast.get_docstring(_find_node(source, qualname)) or ""
        summary = docstring.strip().splitlines()[0] if docstring.strip() else ""
        documents.append(
            Document(
                doc_id=f"{kind}:{rel}:{qualname}",
                kind=kind,
                source_path=rel,
                symbol=qualname,
                locator=f"{rel}::{qualname}",
                title=summary or qualname,
                body=snippet,
                meta={"symbol_kind": symbol_kind, "line_start": start, "line_end": end},
            )
        )

    documents.append(
        Document(
            doc_id=f"{kind}:{rel}:__file__",
            kind=kind,
            source_path=rel,
            symbol="__file__",
            locator=rel,
            title=rel,
            body=source,
            meta={"symbol_kind": "file", "line_start": 1, "line_end": len(lines)},
        )
    )
    return documents


def _find_node(source: str, qualname: str):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    parts = qualname.split(".")
    node: ast.AST | None = tree
    for part in parts:
        found = None
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == part:
                found = child
                break
        if found is None:
            return None
        node = found
    return node


# ---------------------------------------------------------------------------
# project walking
# ---------------------------------------------------------------------------

def _is_spec_file(path: Path, project_root: Path) -> bool:
    if path.suffix.lower() != ".md":
        return False
    relative_parts = path.relative_to(project_root).parts
    if any(part in SPEC_DIR_NAMES for part in relative_parts[:-1]):
        return True
    return path.name.startswith("spec")


def _is_test_file(path: Path, project_root: Path) -> bool:
    relative_parts = path.relative_to(project_root).parts
    return any(part in TEST_DIR_NAMES for part in relative_parts[:-1])


def collect_documents(project_root: Path) -> list[Document]:
    """Collect every document of ``project_root``."""
    project_root = Path(project_root).resolve()
    documents: list[Document] = []

    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__", ".pytest_cache", ".state"} for part in path.parts):
            continue
        rel = path.relative_to(project_root).as_posix()
        suffix = path.suffix.lower()
        if suffix == ".md":
            if _is_spec_file(path, project_root):
                documents.extend(parse_spec_file(path, rel))
            else:
                documents.append(
                    Document(
                        doc_id=f"doc:{rel}",
                        kind="doc",
                        source_path=rel,
                        symbol=path.stem,
                        locator=rel,
                        title=path.stem,
                        body=path.read_text(encoding="utf-8"),
                    )
                )
        elif suffix == ".py":
            kind = "test" if _is_test_file(path, project_root) else "code"
            documents.extend(parse_python_file(path, rel, kind=kind))

    _link_references(documents)
    return documents


def _link_references(documents: Iterable[Document]) -> None:
    """Fill the ``references`` column with the project symbols a document mentions."""
    documents = list(documents)
    names = sorted(
        {
            document.symbol
            for document in documents
            if document.kind in {"code", "test"} and document.symbol and document.symbol != "__file__"
        },
        key=len,
        reverse=True,
    )
    if not names:
        return
    pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in names) + r")\b")
    for document in documents:
        if document.symbol == "__file__":
            document.references = ""
            continue
        found = sorted({match for match in pattern.findall(document.body)})
        document.references = ",".join(found)
