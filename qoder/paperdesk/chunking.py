"""Markdown front matter 解析、按标题层级分节与限量切块。

全部是纯函数：不连接数据库，输入文本与相对路径，输出 ParsedNote / ChunkDraft。
字符偏移基于换行符统一为 LF 之后的文本，行号对应用户在编辑器里看到的行号。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath

import yaml

from .errors import ImportError_
from .models import ChunkDraft, NoteMeta, ParsedNote, Section, SourceLine
from .textnorm import parse_year, split_authors, split_tags

__all__ = ["parse_note", "chunk_note", "build_search_text", "content_hash"]

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_search_text(title: str, heading_path: str, content: str) -> str:
    parts = [title.strip(), heading_path.strip(), content.strip()]
    return "\n".join(part for part in parts if part)


def _split_lines(text: str) -> list[SourceLine]:
    lines: list[SourceLine] = []
    offset = 0
    for number, raw in enumerate(text.split("\n"), start=1):
        lines.append(SourceLine(number=number, text=raw, char_start=offset, char_end=offset + len(raw)))
        offset += len(raw) + 1
    return lines


def _extract_front_matter(
    lines: list[SourceLine], rel_path: str
) -> tuple[dict, int, bool]:
    first = next((i for i, line in enumerate(lines) if line.text.strip()), None)
    if first is None or lines[first].text.strip() != "---":
        return {}, 0, False

    for closing in range(first + 1, len(lines)):
        if lines[closing].text.strip() in ("---", "..."):
            block = "\n".join(line.text for line in lines[first + 1 : closing])
            try:
                data = yaml.safe_load(block) if block.strip() else {}
            except yaml.YAMLError as exc:
                raise ImportError_(f"{rel_path}: front matter 不是合法 YAML：{exc}") from exc
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ImportError_(
                    f"{rel_path}: front matter 必须是键值映射，实际是 {type(data).__name__}"
                )
            normalized = {str(key).strip().lower(): value for key, value in data.items()}
            return normalized, closing + 1, True

    raise ImportError_(f"{rel_path}: front matter 以 --- 开始但没有结束标记")


def _scan_regions(
    lines: list[SourceLine], body_index: int
) -> tuple[list[tuple[str | None, list[SourceLine]]], list[tuple[int, str]]]:
    """按标题切出正文区域；heading_path 为 None 表示第一个标题之前的导语。"""
    regions: list[tuple[str | None, list[SourceLine]]] = [(None, [])]
    headings: list[tuple[int, str]] = []
    stack: list[tuple[int, str]] = []
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in lines[body_index:]:
        fence = _FENCE_RE.match(line.text)
        if fence:
            marker = fence.group(1)
            info = fence.group(2)
            if not in_fence:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_len and not info.strip():
                in_fence = False
            regions[-1][1].append(line)
            continue

        if in_fence:
            regions[-1][1].append(line)
            continue

        heading = _HEADING_RE.match(line.text)
        if heading is None:
            regions[-1][1].append(line)
            continue

        level = len(heading.group(1))
        text = heading.group(2).strip().rstrip("#").strip()
        if not text:
            regions[-1][1].append(line)
            continue

        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        headings.append((level, text))
        regions.append((" > ".join(item[1] for item in stack), []))

    return regions, headings


def _build_meta(front_matter: dict, headings: list[tuple[int, str]], rel_path: str) -> NoteMeta:
    title = str(front_matter.get("title") or "").strip()
    if not title:
        title = next((text for level, text in headings if level == 1), "")
    if not title:
        title = PurePosixPath(rel_path.replace("\\", "/")).stem

    return NoteMeta(
        title=title,
        authors=tuple(split_authors(front_matter.get("authors"))),
        year=parse_year(front_matter.get("year")),
        tags=tuple(split_tags(front_matter.get("tags"))),
    )


def parse_note(text: str, rel_path: str) -> ParsedNote:
    """解析 front matter、正文标题层级与段落结构。"""
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = _split_lines(text)

    front_matter, body_index, has_front_matter = _extract_front_matter(lines, rel_path)
    regions, headings = _scan_regions(lines, body_index)
    meta = _build_meta(front_matter, headings, rel_path)

    sections: list[Section] = []
    for heading_path, region in regions:
        if not any(line.text.strip() for line in region):
            continue
        sections.append(
            Section(
                heading_path=meta.title if heading_path is None else heading_path,
                lines=tuple(region),
            )
        )

    body_start_line = lines[body_index].number if body_index < len(lines) else len(lines) + 1
    return ParsedNote(
        rel_path=rel_path,
        meta=meta,
        text=text,
        sections=tuple(sections),
        body_start_line=body_start_line,
        has_front_matter=has_front_matter,
        front_matter=front_matter,
    )


def _paragraphs(lines: tuple[SourceLine, ...]) -> list[list[SourceLine]]:
    groups: list[list[SourceLine]] = []
    current: list[SourceLine] = []
    for line in lines:
        if line.text.strip():
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _lines_for_span(lines: tuple[SourceLine, ...], start: int, end: int) -> tuple[int, int]:
    start_line = next((line.number for line in lines if line.char_end > start), lines[-1].number)
    end_line = start_line
    for line in lines:
        if line.char_start <= end - 1:
            end_line = line.number
    return start_line, end_line


def _make_chunk(
    parsed: ParsedNote,
    heading_path: str,
    start: int,
    end: int,
    start_line: int,
    end_line: int,
) -> ChunkDraft | None:
    content = parsed.text[start:end]
    if not content.strip():
        return None
    return ChunkDraft(
        heading_path=heading_path,
        content=content,
        start_line=start_line,
        end_line=end_line,
        start_offset=start,
        end_offset=end,
        search_text=build_search_text(parsed.meta.title, heading_path, content),
        content_hash=content_hash(content),
    )


def _chunk_section(parsed: ParsedNote, section: Section, max_chars: int) -> list[ChunkDraft]:
    paragraphs = _paragraphs(section.lines)
    if not paragraphs:
        return []

    first, last = paragraphs[0][0], paragraphs[-1][-1]
    if last.char_end - first.char_start <= max_chars:
        chunk = _make_chunk(
            parsed, section.heading_path, first.char_start, last.char_end,
            first.number, last.number,
        )
        return [chunk] if chunk else []

    chunks: list[ChunkDraft] = []
    group: list[list[SourceLine]] = []
    group_start = 0

    def flush() -> None:
        nonlocal group, group_start
        if not group:
            return
        start = group_start
        end = group[-1][-1].char_end
        chunk = _make_chunk(
            parsed, section.heading_path, start, end,
            group[0][0].number, group[-1][-1].number,
        )
        if chunk:
            chunks.append(chunk)
        group = []

    for paragraph in paragraphs:
        start = paragraph[0].char_start
        end = paragraph[-1].char_end
        if end - start > max_chars:
            flush()
            cursor = start
            while cursor < end:
                piece_end = min(cursor + max_chars, end)
                start_line, end_line = _lines_for_span(section.lines, cursor, piece_end)
                chunk = _make_chunk(
                    parsed, section.heading_path, cursor, piece_end, start_line, end_line
                )
                if chunk:
                    chunks.append(chunk)
                cursor = piece_end
            continue

        if not group:
            group = [paragraph]
            group_start = start
        elif end - group_start <= max_chars:
            group.append(paragraph)
        else:
            flush()
            group = [paragraph]
            group_start = start

    flush()
    return chunks


def chunk_note(parsed: ParsedNote, max_chars: int = 800) -> list[ChunkDraft]:
    """每个小节优先作为一块；超过 max_chars 时按段落分组，单段仍超限则硬切。"""
    if max_chars < 1:
        raise ValueError("max_chars 必须 >= 1")
    chunks: list[ChunkDraft] = []
    for section in parsed.sections:
        chunks.extend(_chunk_section(parsed, section, max_chars))
    return chunks
