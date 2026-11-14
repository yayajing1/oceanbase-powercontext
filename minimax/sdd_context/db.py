"""seekdb / OceanBase storage for the engineering material.

The store keeps one row per addressable document (a specification rule, a
code symbol or a test) and indexes the searchable text with a native full
text index.  Retrieval therefore runs inside the database: the context
module never re-implements the matching in Python.

The full text parser is detected at import time.  ``ik`` is preferred
(Chinese aware), ``beng`` and ``ngram`` are used as fallbacks and the
built-in parser is the last resort.  The detected parser is recorded in the
``sdd_meta`` table so that the run log shows which one was used.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pymysql

from .config import Config
from .documents import Document
from .textnorm import normalize_for_search, tokenize

__all__ = ["DocumentStore", "Hit", "SearchReport"]

TABLE = "sdd_document"
FULLTEXT_INDEX = "ft_sdd_search"
PARSER_CANDIDATES = ("ik", "beng", "ngram", None)

_DOCUMENT_COLUMNS = (
    "doc_id",
    "kind",
    "source_path",
    "symbol",
    "locator",
    "title",
    "applies_to",
    "ref_symbols",
    "tags",
    "body",
    "search_text",
    "meta",
    "content_hash",
)


@dataclass
class Hit:
    """One retrieved document."""

    doc_id: str
    kind: str
    source_path: str
    symbol: str
    locator: str
    title: str
    applies_to: str
    references: str
    body: str
    meta: dict
    score: float = 0.0
    matched_by: str = "fts"

    def as_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "kind": self.kind,
            "source_path": self.source_path,
            "symbol": self.symbol,
            "locator": self.locator,
            "title": self.title,
            "applies_to": self.applies_to,
            "references": self.references,
            "body": self.body,
            "meta": self.meta,
            "score": round(float(self.score), 6),
            "matched_by": self.matched_by,
        }


@dataclass
class SearchReport:
    """Everything that was executed against the database for one query."""

    queries: list[str]
    hits: list[Hit]

    def as_dict(self) -> dict:
        return {"queries": self.queries, "hits": [hit.as_dict() for hit in self.hits]}


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DocumentStore:
    """A thin, explicit client for the seekdb / OceanBase document table."""

    def __init__(self, config: Config):
        self.config = config

    # -- connection -----------------------------------------------------
    def connect(self):
        return pymysql.connect(
            host=self.config.db_host,
            port=self.config.db_port,
            user=self.config.db_user,
            password=self.config.db_password,
            charset="utf8mb4",
            connect_timeout=self.config.db_connect_timeout,
            autocommit=True,
        )

    def _server_version(self) -> str:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("select version()")
                row = cur.fetchone()
        return str(row[0]) if row else "unknown"

    # -- schema ---------------------------------------------------------
    def ensure_schema(self) -> dict:
        """Create the database, the table and the full text index if needed."""
        created_db = False
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "select schema_name from information_schema.schemata where schema_name = %s",
                    (self.config.db_name,),
                )
                if cur.fetchone() is None:
                    cur.execute(
                        f"create database `{self.config.db_name}` default charset utf8mb4"
                    )
                    created_db = True
                cur.execute(f"use `{self.config.db_name}`")
                cur.execute(
                    """
                    create table if not exists sdd_meta (
                        meta_key   varchar(64)  not null,
                        meta_value text         not null,
                        updated_at timestamp    not null default current_timestamp on update current_timestamp,
                        primary key (meta_key)
                    ) default charset = utf8mb4
                    """
                )
                cur.execute(
                    """
                    create table if not exists sdd_import_log (
                        batch_id     varchar(64)  not null,
                        imported_at  timestamp    not null default current_timestamp,
                        project_root varchar(512) not null,
                        doc_count    int          not null,
                        kind_counts  varchar(512) not null,
                        tool_version varchar(32)  not null,
                        primary key (batch_id)
                    ) default charset = utf8mb4
                    """
                )
                cur.execute(
                    f"""
                    create table if not exists {TABLE} (
                        doc_id       varchar(320) not null,
                        kind         varchar(16)  not null,
                        source_path  varchar(512) not null,
                        symbol       varchar(255) not null default '',
                        locator      varchar(512) not null default '',
                        title        varchar(512) not null default '',
                        applies_to   varchar(512) not null default '',
                        ref_symbols  varchar(512) not null default '',
                        tags         varchar(512) not null default '',
                        body         mediumtext   not null,
                        search_text  mediumtext   not null,
                        meta         text         not null,
                        content_hash char(64)     not null,
                        updated_at   timestamp    not null default current_timestamp on update current_timestamp,
                        primary key (doc_id),
                        key idx_kind (kind),
                        key idx_applies_to (applies_to),
                        key idx_symbol (symbol)
                    ) default charset = utf8mb4
                    """
                )
            parser, index_created = self._ensure_fulltext(conn)
        finally:
            conn.close()

        return {
            "database": self.config.db_name,
            "created_database": created_db,
            "fulltext_index": FULLTEXT_INDEX,
            "fulltext_parser": parser or "default",
            "fulltext_index_created": index_created,
            "server_version": self._server_version(),
        }

    def _ensure_fulltext(self, conn) -> tuple[str | None, bool]:
        with conn.cursor() as cur:
            cur.execute(
                "select index_name from information_schema.statistics "
                "where table_schema = %s and table_name = %s and index_name = %s",
                (self.config.db_name, TABLE, FULLTEXT_INDEX),
            )
            if cur.fetchone() is not None:
                return self.get_meta("fulltext_parser"), False

            errors: list[str] = []
            for parser in PARSER_CANDIDATES:
                statement = (
                    f"alter table {TABLE} add fulltext index {FULLTEXT_INDEX} (search_text) "
                    f"with parser {parser}"
                    if parser
                    else f"alter table {TABLE} add fulltext index {FULLTEXT_INDEX} (search_text)"
                )
                try:
                    cur.execute(statement)
                except Exception as exc:  # pragma: no cover - depends on the server build
                    errors.append(f"{parser or 'default'}: {exc}")
                    continue
                self.set_meta("fulltext_parser", parser or "default")
                return parser, True
            raise RuntimeError(
                "no usable full text parser could be created on this server:\n  "
                + "\n  ".join(errors)
            )

    # -- meta ------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"select meta_value from `{self.config.db_name}`.sdd_meta where meta_key = %s", (key,))
                row = cur.fetchone()
        return str(row[0]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"insert into `{self.config.db_name}`.sdd_meta (meta_key, meta_value) values (%s, %s) "
                    "on duplicate key update meta_value = values(meta_value)",
                    (key, value),
                )

    # -- writing ---------------------------------------------------------
    def upsert_documents(self, documents: Sequence[Document], batch_id: str, project_root: str) -> dict:
        """Insert or update ``documents`` and drop stale rows of the same files."""
        if not documents:
            return {"batch_id": batch_id, "inserted": 0, "updated": 0, "removed": 0}

        conn = self.connect()
        inserted = updated = 0
        try:
            with conn.cursor() as cur:
                cur.execute(f"select doc_id, content_hash from `{self.config.db_name}`.{TABLE}")
                existing = {row[0]: row[1] for row in cur.fetchall()}

                sql = (
                    f"insert into `{self.config.db_name}`.{TABLE} "
                    "(`" + "`, `".join(_DOCUMENT_COLUMNS) + "`) values (" + ", ".join(["%s"] * len(_DOCUMENT_COLUMNS)) + ") "
                    "on duplicate key update kind=values(kind), source_path=values(source_path), symbol=values(symbol), "
                    "locator=values(locator), title=values(title), applies_to=values(applies_to), ref_symbols=values(ref_symbols), "
                    "tags=values(tags), "
                    "body=values(body), search_text=values(search_text), meta=values(meta), content_hash=values(content_hash)"
                )
                touched_paths = set()
                for document in documents:
                    touched_paths.add(document.source_path)
                    content_hash = _hash_text(document.body + "\n" + json.dumps(document.meta, sort_keys=True))
                    values = (
                        document.doc_id,
                        document.kind,
                        document.source_path,
                        document.symbol,
                        document.locator,
                        document.title,
                        document.applies_to,
                        document.references,
                        document.tags,
                        document.body,
                        document.search_text,
                        json.dumps(document.meta, ensure_ascii=False, sort_keys=True),
                        content_hash,
                    )
                    cur.execute(sql, values)
                    if document.doc_id in existing:
                        updated += 1
                    else:
                        inserted += 1

                removed = 0
                cur.execute(
                    f"select doc_id, source_path from `{self.config.db_name}`.{TABLE} "
                    f"where source_path in ({','.join(['%s'] * len(touched_paths))})",
                    tuple(sorted(touched_paths)),
                )
                keep = {document.doc_id for document in documents}
                for doc_id, _ in cur.fetchall():
                    if doc_id not in keep:
                        cur.execute(f"delete from `{self.config.db_name}`.{TABLE} where doc_id = %s", (doc_id,))
                        removed += 1

                kind_counts: dict[str, int] = {}
                for document in documents:
                    kind_counts[document.kind] = kind_counts.get(document.kind, 0) + 1
                cur.execute(
                    f"insert into `{self.config.db_name}`.sdd_import_log "
                    "(batch_id, project_root, doc_count, kind_counts, tool_version) values (%s, %s, %s, %s, %s) "
                    "on duplicate key update imported_at = current_timestamp, doc_count = values(doc_count), "
                    "kind_counts = values(kind_counts)",
                    (
                        batch_id,
                        project_root,
                        len(documents),
                        json.dumps(kind_counts, sort_keys=True),
                        "sdd_context 1.0.0",
                    ),
                )
        finally:
            conn.close()
        return {
            "batch_id": batch_id,
            "inserted": inserted,
            "updated": updated,
            "removed": removed,
            "total": len(documents),
        }

    def import_log(self, limit: int = 20) -> list[dict]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"select batch_id, imported_at, project_root, doc_count, kind_counts "
                    f"from `{self.config.db_name}`.sdd_import_log order by imported_at desc limit %s",
                    (limit,),
                )
                rows = cur.fetchall()
        return [
            {
                "batch_id": row[0],
                "imported_at": str(row[1]),
                "project_root": row[2],
                "doc_count": row[3],
                "kind_counts": row[4],
            }
            for row in rows
        ]

    # -- reading ---------------------------------------------------------
    def _rows_to_hits(self, rows: Iterable[Sequence], matched_by: str) -> list[Hit]:
        hits: list[Hit] = []
        for row in rows:
            try:
                meta = json.loads(row[10]) if row[10] else {}
            except (TypeError, ValueError):
                meta = {}
            hits.append(
                Hit(
                    doc_id=row[0],
                    kind=row[1],
                    source_path=row[2],
                    symbol=row[3],
                    locator=row[4],
                    title=row[5],
                    applies_to=row[6],
                    references=row[7],
                    body=row[9],
                    meta=meta,
                    score=float(row[12] or 0.0),
                    matched_by=matched_by,
                )
            )
        return hits

    def search_fts(self, query: str, kinds: Sequence[str] | None = None, limit: int = 10) -> tuple[list[Hit], list[str]]:
        """Full text search.  Returns the hits and the executed SQL statements."""
        executed: list[str] = []
        normalized = normalize_for_search(query)
        tokens = [token for token in tokenize(query) if token]
        if not normalized or not tokens:
            return [], executed

        # boolean mode keeps every term available and avoids the
        # "present in more than 50% of the rows" rule of natural language mode.
        boolean_query = " ".join(f"+{token}" if len(token) >= 2 else token for token in tokens)

        sql = (
            "select doc_id, kind, source_path, symbol, locator, title, applies_to, ref_symbols, tags, "
            "body, meta, meta, match(search_text) against (%s) as score "
            f"from `{self.config.db_name}`.`{TABLE}` "
            "where match(search_text) against (%s)"
        )
        params: list[object] = [boolean_query, boolean_query]
        if kinds:
            sql += " and kind in (" + ",".join(["%s"] * len(kinds)) + ")"
            params.extend(kinds)
        sql += " order by score desc limit %s"
        params.append(limit)
        executed.append(_compact_sql(sql, params))

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._rows_to_hits(rows, "fts"), executed

    def find_by_field(self, kind: str, field: str, values: Sequence[str], limit: int = 50) -> tuple[list[Hit], list[str]]:
        """Exact lookup, e.g. ``symbol in (...)`` for code and test symbols."""
        if not values:
            return [], []
        sql = (
            "select doc_id, kind, source_path, symbol, locator, title, applies_to, ref_symbols, tags, "
            f"body, meta, meta, 0 from `{self.config.db_name}`.`{TABLE}` where kind = %s and "
        )
        sql += "(" + " or ".join([f"{field} = %s" for _ in values]) + ")"
        sql += " order by source_path, symbol limit %s"
        params: list[object] = [kind, *values, limit]
        executed = [_compact_sql(sql, params)]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._rows_to_hits(rows, "exact"), executed

    def find_by_reference(self, kind: str, symbols: Sequence[str], limit: int = 50) -> tuple[list[Hit], list[str]]:
        """Find documents of ``kind`` whose ``references`` mention ``symbols``."""
        if not symbols:
            return [], []
        sql = (
            "select doc_id, kind, source_path, symbol, locator, title, applies_to, ref_symbols, tags, "
            f"body, meta, meta, 0 from `{self.config.db_name}`.`{TABLE}` where kind = %s and ("
        )
        clauses = []
        params: list[object] = [kind]
        for symbol in symbols:
            clauses.append("find_in_set(%s, ref_symbols) > 0")
            params.append(symbol)
        sql += " or ".join(clauses) + ") order by source_path, symbol limit %s"
        params.append(limit)
        executed = [_compact_sql(sql, params)]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._rows_to_hits(rows, "reference"), executed

    def find_like(self, kind: str, field: str, needles: Sequence[str], limit: int = 50) -> tuple[list[Hit], list[str]]:
        """Find documents whose ``field`` contains any of ``needles``."""
        if not needles:
            return [], []
        sql = (
            "select doc_id, kind, source_path, symbol, locator, title, applies_to, ref_symbols, tags, "
            f"body, meta, meta, 0 from `{self.config.db_name}`.`{TABLE}` where kind = %s and ("
        )
        clauses = []
        params: list[object] = [kind]
        for needle in needles:
            clauses.append(f"{field} like %s")
            params.append(f"%{needle}%")
        sql += " or ".join(clauses) + ") order by source_path, symbol limit %s"
        params.append(limit)
        executed = [_compact_sql(sql, params)]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._rows_to_hits(rows, "like"), executed

    def match_symbols(self, candidates: Sequence[str]) -> tuple[list[str], list[str]]:
        """Return the candidates that exist as a symbol in the database."""
        if not candidates:
            return [], []
        sql = (
            f"select distinct symbol from `{self.config.db_name}`.`{TABLE}` "
            "where symbol in (" + ",".join(["%s"] * len(candidates)) + ")"
        )
        params: list[object] = list(candidates)
        executed = [_compact_sql(sql, params)]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return [str(row[0]) for row in rows], executed

    def all_documents(self, kind: str | None = None) -> list[Hit]:
        sql = (
            "select doc_id, kind, source_path, symbol, locator, title, applies_to, ref_symbols, tags, "
            f"body, meta, meta, 0 from `{self.config.db_name}`.`{TABLE}`"
        )
        params: list[object] = []
        if kind:
            sql += " where kind = %s"
            params.append(kind)
        sql += " order by kind, source_path, symbol"
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        return self._rows_to_hits(rows, "all")

    def stats(self) -> dict:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"select kind, count(*) from `{self.config.db_name}`.`{TABLE}` group by kind")
                counts = {row[0]: int(row[1]) for row in cur.fetchall()}
                cur.execute(
                    "select index_name, group_concat(column_name order by seq_in_index) "
                    "from information_schema.statistics where table_schema = %s and table_name = %s "
                    "and index_type = 'FULLTEXT' group by index_name",
                    (self.config.db_name, TABLE),
                )
                indexes = [
                    {"index_name": row[0], "columns": row[1].split(",") if row[1] else []}
                    for row in cur.fetchall()
                ]
        return {
            "database": self.config.db_name,
            "counts": counts,
            "total": sum(counts.values()),
            "fulltext_indexes": indexes,
            "fulltext_parser": self.get_meta("fulltext_parser"),
        }


def _compact_sql(sql: str, params: Sequence[object]) -> str:
    """Render a statement with its parameters for the run log."""
    rendered = sql
    for param in params:
        if isinstance(param, (int, float)):
            rendered = rendered.replace("%s", str(param), 1)
        else:
            escaped = str(param).replace("'", "''")
            rendered = rendered.replace("%s", f"'{escaped}'", 1)
    return " ".join(rendered.split())


def new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
