"""文件发现、指纹、幂等写入、过期块清理与目录级 prune。

幂等的核心：
  - document_id / path_hash 由 rel_path 决定，重复导入落到同一行；
  - chunk_id = md5(document_id + content_hash + occurrence)，内容不变则 id 不变；
  - 每个文件一个事务，先删除本次未出现的旧 chunk_id，再按 ordinal 重排写入；
  - 文件整体 sha256 未变时直接跳过（快路径）。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymysql

from .chunking import chunk_note, parse_note
from .db import Database
from .errors import DatabaseError, ImportError_
from .models import ChunkDraft, FileResult, IdentifiedChunk, ImportReport, NoteMeta

__all__ = [
    "Importer",
    "PlannedDocument",
    "ChunkDiff",
    "ImportTarget",
    "plan_document",
    "assign_chunk_identity",
    "diff_chunk_ids",
    "compute_document_id",
    "compute_path_hash",
    "compute_file_hash",
    "compute_chunk_id",
    "normalize_rel_path",
    "discover_targets",
    "SUFFIXES",
]

SUFFIXES = {".md", ".markdown", ".txt"}

_ORDINAL_OFFSET = 1_000_001


# ---------------------------------------------------------------- 指纹（纯函数）
def normalize_rel_path(rel_path: str) -> str:
    rel = rel_path.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def compute_document_id(rel_path: str) -> str:
    return hashlib.md5(normalize_rel_path(rel_path).encode("utf-8")).hexdigest()


def compute_path_hash(rel_path: str) -> str:
    return hashlib.sha256(normalize_rel_path(rel_path).encode("utf-8")).hexdigest()


def compute_file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_chunk_id(document_id: str, content_hash: str, occurrence: int) -> str:
    return hashlib.md5(
        (document_id + content_hash + str(occurrence)).encode("utf-8")
    ).hexdigest()


def assign_chunk_identity(
    document_id: str, drafts: list[ChunkDraft] | tuple[ChunkDraft, ...]
) -> list[IdentifiedChunk]:
    """同文档内相同正文按出现顺序编号，避免 chunk_id 冲突。"""
    seen: dict[str, int] = {}
    result: list[IdentifiedChunk] = []
    for draft in drafts:
        occurrence = seen.get(draft.content_hash, 0)
        seen[draft.content_hash] = occurrence + 1
        result.append(
            IdentifiedChunk(
                draft=draft,
                occurrence=occurrence,
                chunk_id=compute_chunk_id(document_id, draft.content_hash, occurrence),
            )
        )
    return result


@dataclass(frozen=True)
class ChunkDiff:
    to_delete: tuple[str, ...]
    to_keep: tuple[str, ...]
    to_add: tuple[str, ...]


def diff_chunk_ids(existing_ids: list[str] | set[str], desired_ids: list[str] | set[str]) -> ChunkDiff:
    existing = set(existing_ids)
    desired = set(desired_ids)
    return ChunkDiff(
        to_delete=tuple(sorted(existing - desired)),
        to_keep=tuple(sorted(existing & desired)),
        to_add=tuple(sorted(desired - existing)),
    )


@dataclass(frozen=True)
class PlannedDocument:
    rel_path: str
    source_path: str
    import_root: str
    document_id: str
    path_hash: str
    file_hash: str
    meta: NoteMeta
    chunks: tuple[IdentifiedChunk, ...]


def plan_document(
    text: str,
    rel_path: str,
    max_chars: int = 800,
    *,
    source_path: str = "",
    import_root: str = "",
    file_hash: str = "",
) -> PlannedDocument:
    """把一篇笔记变成「文档 + 带 id 的块」，不接触数据库。"""
    rel = normalize_rel_path(rel_path)
    parsed = parse_note(text, rel)
    drafts = chunk_note(parsed, max_chars)
    document_id = compute_document_id(rel)
    identified = assign_chunk_identity(document_id, drafts)
    return PlannedDocument(
        rel_path=rel,
        source_path=source_path,
        import_root=import_root,
        document_id=document_id,
        path_hash=compute_path_hash(rel),
        file_hash=file_hash or hashlib.sha256(text.encode("utf-8")).hexdigest(),
        meta=parsed.meta,
        chunks=tuple(identified),
    )


# ---------------------------------------------------------------- 文件发现
@dataclass(frozen=True)
class ImportTarget:
    path: Path
    import_root: Path
    prune: bool


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUFFIXES:
            yield path


def discover_targets(paths: list[str] | tuple[str, ...], prune: bool | None = None) -> list[ImportTarget]:
    """目录递归收集 .md/.markdown/.txt；单文件只导入自身。

    prune=None 时：目录默认清理，单文件默认不清理（与技术设计 7.6 一致）。
    """
    targets: list[ImportTarget] = []
    seen_paths: set[Path] = set()

    for raw in paths:
        path = Path(raw).expanduser()
        if not path.exists():
            raise ImportError_(f"路径不存在：{raw}")
        resolved = path.resolve()
        if resolved.is_dir():
            for file in _iter_files(resolved):
                file = file.resolve()
                if file in seen_paths:
                    continue
                seen_paths.add(file)
                targets.append(
                    ImportTarget(path=file, import_root=resolved, prune=True if prune is None else prune)
                )
        elif resolved.is_file():
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            targets.append(
                ImportTarget(path=resolved, import_root=resolved.parent, prune=False if prune is None else prune)
            )
    return targets


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ImportError_(f"无法读取 {path}：{exc}") from exc


def _decode(data: bytes, path: Path) -> tuple[str, str]:
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030"), "gb18030"
        except UnicodeDecodeError as exc:
            raise ImportError_(
                f"{path} 既不是 UTF-8 也不是 GB18030，无法确定编码；请转存为 UTF-8 后重试。"
            ) from exc


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


# ---------------------------------------------------------------- 写入
@dataclass
class _WriteOutcome:
    status: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0


class Importer:
    def __init__(self, db: Database, max_chars: int = 800):
        self.db = db
        self.max_chars = max_chars

    def import_paths(self, paths: list[str] | tuple[str, ...], prune: bool | None = None) -> ImportReport:
        targets = discover_targets(paths, prune)
        report = ImportReport(files_seen=len(targets))
        seen_by_root: dict[str, set[str]] = defaultdict(set)

        for target in targets:
            rel_path = target.path.relative_to(target.import_root).as_posix()
            seen_by_root[target.import_root.as_posix()].add(rel_path)
            try:
                outcome, message = self._import_file(target, rel_path)
            except ImportError_ as exc:
                report.files_failed += 1
                report.errors.append(str(exc))
                report.files.append(FileResult(rel_path, "failed", 0, str(exc)))
                continue

            report.files.append(FileResult(rel_path, outcome.status, outcome.added + outcome.unchanged + outcome.updated, message))
            report.chunks_added += outcome.added
            report.chunks_updated += outcome.updated
            report.chunks_unchanged += outcome.unchanged
            report.chunks_removed += outcome.removed
            if outcome.status == "skipped":
                report.files_skipped += 1

        prune_roots = {t.import_root.as_posix() for t in targets if t.prune}
        for root in sorted(prune_roots):
            report.documents_pruned += self._prune_root(root, seen_by_root[root])

        self.db.set_meta("last_import_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return report

    def _import_file(self, target: ImportTarget, rel_path: str) -> tuple[_WriteOutcome, str]:
        data = _read_bytes(target.path)
        text, encoding = _decode(data, target.path)
        file_hash = compute_file_hash(data)

        existing = self.db.fetch_all(
            "SELECT document_id, content_hash FROM documents WHERE path_hash = %s",
            (compute_path_hash(rel_path),),
        )
        if existing and existing[0][1] == file_hash:
            return _WriteOutcome(status="skipped"), f"内容未变（{encoding}）"

        plan = plan_document(
            text,
            rel_path,
            self.max_chars,
            source_path=target.path.as_posix(),
            import_root=target.import_root.as_posix(),
            file_hash=file_hash,
        )
        status = "updated" if existing else "added"

        try:
            with self.db.transaction() as conn:
                outcome = self._write_plan(conn, plan, target.path)
        except pymysql.err.MySQLError as exc:
            raise DatabaseError(
                f"写入 {rel_path} 失败，已回滚：{exc}\n"
                "请检查数据库字符集（应为 utf8mb4）与账号写权限。"
            ) from exc

        message = f"编码 {encoding}，{len(plan.chunks)} 块"
        if encoding != "utf-8":
            message += "（已从 GB18030 回退解码，建议转存 UTF-8）"
        return _WriteOutcome(status=status, **outcome), message

    def _write_plan(self, conn, plan: PlannedDocument, path: Path) -> dict[str, int]:
        meta = plan.meta
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents
                  (document_id, path_hash, source_path, rel_path, import_root, title,
                   authors_text, tags_text, pub_year, content_hash, file_size, mtime)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                  source_path = VALUES(source_path), rel_path = VALUES(rel_path),
                  import_root = VALUES(import_root), title = VALUES(title),
                  authors_text = VALUES(authors_text), tags_text = VALUES(tags_text),
                  pub_year = VALUES(pub_year), content_hash = VALUES(content_hash),
                  file_size = VALUES(file_size), mtime = VALUES(mtime),
                  imported_at = CURRENT_TIMESTAMP
                """,
                (
                    plan.document_id,
                    plan.path_hash,
                    _clip(plan.source_path, 1024),
                    _clip(plan.rel_path, 512),
                    _clip(plan.import_root, 1024),
                    _clip(meta.title, 512),
                    _clip(meta.authors_text, 512),
                    _clip(meta.tags_text, 512),
                    meta.year,
                    plan.file_hash,
                    stat.st_size,
                    mtime,
                ),
            )

            cur.execute("DELETE FROM document_tags WHERE document_id = %s", (plan.document_id,))
            for tag in meta.tags:
                cur.execute(
                    "INSERT IGNORE INTO document_tags (document_id, tag) VALUES (%s, %s)",
                    (plan.document_id, _clip(tag, 128)),
                )
            cur.execute("DELETE FROM document_authors WHERE document_id = %s", (plan.document_id,))
            for author in meta.authors:
                cur.execute(
                    "INSERT IGNORE INTO document_authors (document_id, author) VALUES (%s, %s)",
                    (plan.document_id, _clip(author, 128)),
                )

            cur.execute(
                "SELECT chunk_id, ordinal, content_hash, heading_path, start_line, end_line, "
                "start_offset, end_offset FROM chunks WHERE document_id = %s",
                (plan.document_id,),
            )
            existing_rows = {
                str(row[0]): {
                    "ordinal": int(row[1]),
                    "content_hash": str(row[2]),
                    "heading_path": str(row[3]),
                    "start_line": int(row[4]),
                    "end_line": int(row[5]),
                    "start_offset": int(row[6]),
                    "end_offset": int(row[7]),
                }
                for row in cur.fetchall()
            }

            desired_ids = {chunk.chunk_id for chunk in plan.chunks}
            diff = diff_chunk_ids(set(existing_rows), desired_ids)
            if diff.to_delete:
                self._execute_in(cur, "DELETE FROM chunks WHERE chunk_id IN", diff.to_delete)

            remaining = len(desired_ids & set(existing_rows))
            if remaining:
                cur.execute(
                    "UPDATE chunks SET ordinal = -ordinal - %s WHERE document_id = %s",
                    (_ORDINAL_OFFSET, plan.document_id),
                )

            added = updated = unchanged = 0
            for ordinal, chunk in enumerate(plan.chunks):
                draft = chunk.draft
                old = existing_rows.get(chunk.chunk_id)
                if old is None:
                    added += 1
                elif self._row_changed(old, chunk):
                    updated += 1
                else:
                    unchanged += 1

                cur.execute(
                    """
                    INSERT INTO chunks
                      (chunk_id, document_id, ordinal, occurrence, heading_path, content,
                       search_text, content_hash, start_line, end_line, start_offset,
                       end_offset, char_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      ordinal = VALUES(ordinal), occurrence = VALUES(occurrence),
                      heading_path = VALUES(heading_path), content = VALUES(content),
                      search_text = VALUES(search_text), content_hash = VALUES(content_hash),
                      start_line = VALUES(start_line), end_line = VALUES(end_line),
                      start_offset = VALUES(start_offset), end_offset = VALUES(end_offset),
                      char_count = VALUES(char_count)
                    """,
                    (
                        chunk.chunk_id,
                        plan.document_id,
                        ordinal,
                        chunk.occurrence,
                        _clip(draft.heading_path, 512),
                        draft.content,
                        draft.search_text,
                        draft.content_hash,
                        draft.start_line,
                        draft.end_line,
                        draft.start_offset,
                        draft.end_offset,
                        draft.char_count,
                    ),
                )

            cur.execute(
                "DELETE FROM chunks WHERE document_id = %s AND ordinal >= %s",
                (plan.document_id, len(plan.chunks)),
            )

        return {
            "added": added,
            "updated": updated,
            "unchanged": unchanged,
            "removed": len(diff.to_delete),
        }

    @staticmethod
    def _row_changed(old: dict, chunk: IdentifiedChunk) -> bool:
        draft = chunk.draft
        return (
            old["content_hash"] != draft.content_hash
            or old["heading_path"] != _clip(draft.heading_path, 512)
            or old["start_line"] != draft.start_line
            or old["end_line"] != draft.end_line
            or old["start_offset"] != draft.start_offset
            or old["end_offset"] != draft.end_offset
        )

    @staticmethod
    def _execute_in(cur, prefix: str, values: tuple[str, ...]) -> None:
        placeholders = ", ".join(["%s"] * len(values))
        cur.execute(f"{prefix} ({placeholders})", values)

    def _prune_root(self, root: str, seen: set[str]) -> int:
        rows = self.db.fetch_all(
            "SELECT document_id, rel_path FROM documents WHERE import_root = %s",
            (root,),
        )
        stale = [str(row[0]) for row in rows if str(row[1]) not in seen]
        if not stale:
            return 0

        try:
            with self.db.transaction() as conn:
                with conn.cursor() as cur:
                    self._execute_in(cur, "DELETE FROM chunks WHERE document_id IN", tuple(stale))
                    self._execute_in(cur, "DELETE FROM document_tags WHERE document_id IN", tuple(stale))
                    self._execute_in(cur, "DELETE FROM document_authors WHERE document_id IN", tuple(stale))
                    self._execute_in(cur, "DELETE FROM documents WHERE document_id IN", tuple(stale))
        except pymysql.err.MySQLError as exc:
            raise DatabaseError(
                f"清理目录 {root} 中已删除的文件失败，已回滚：{exc}"
            ) from exc
        return len(stale)
