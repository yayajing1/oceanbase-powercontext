"""纯数据类：不依赖数据库，便于单元测试。"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "NoteMeta",
    "SourceLine",
    "Section",
    "ParsedNote",
    "ChunkDraft",
    "IdentifiedChunk",
    "FileResult",
    "ImportReport",
]


@dataclass(frozen=True)
class NoteMeta:
    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    tags: tuple[str, ...] = ()

    @property
    def authors_text(self) -> str:
        return ", ".join(self.authors)

    @property
    def tags_text(self) -> str:
        return ", ".join(self.tags)


@dataclass(frozen=True)
class SourceLine:
    """原文中的一行，字符偏移相对整个解码后的文本。"""

    number: int
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Section:
    """一个标题小节（或没有标题时的整篇正文/导语）。"""

    heading_path: str
    lines: tuple[SourceLine, ...]


@dataclass(frozen=True)
class ParsedNote:
    rel_path: str
    meta: NoteMeta
    text: str
    sections: tuple[Section, ...]
    body_start_line: int = 1
    has_front_matter: bool = False
    front_matter: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkDraft:
    heading_path: str
    content: str
    start_line: int
    end_line: int
    start_offset: int
    end_offset: int
    search_text: str
    content_hash: str

    @property
    def char_count(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class IdentifiedChunk:
    """加上内容寻址 id 的块。"""

    draft: ChunkDraft
    occurrence: int
    chunk_id: str


@dataclass
class FileResult:
    rel_path: str
    status: str  # added / updated / skipped / failed
    chunks: int = 0
    message: str = ""


@dataclass
class ImportReport:
    files_seen: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    chunks_added: int = 0
    chunks_updated: int = 0
    chunks_unchanged: int = 0
    chunks_removed: int = 0
    documents_pruned: int = 0
    files: list[FileResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def files_added(self) -> int:
        return sum(1 for item in self.files if item.status == "added")

    @property
    def files_updated(self) -> int:
        return sum(1 for item in self.files if item.status == "updated")

    def as_dict(self) -> dict:
        return {
            "files_seen": self.files_seen,
            "files_added": self.files_added,
            "files_updated": self.files_updated,
            "files_skipped": self.files_skipped,
            "files_failed": self.files_failed,
            "chunks_added": self.chunks_added,
            "chunks_updated": self.chunks_updated,
            "chunks_unchanged": self.chunks_unchanged,
            "chunks_removed": self.chunks_removed,
            "documents_pruned": self.documents_pruned,
            "errors": list(self.errors),
            "files": [
                {
                    "rel_path": f.rel_path,
                    "status": f.status,
                    "chunks": f.chunks,
                    "message": f.message,
                }
                for f in self.files
            ],
        }
