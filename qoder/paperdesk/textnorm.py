"""标签、作者与年份的归一化。检索用的文本归一化留到检索阶段。"""

from __future__ import annotations

import re

__all__ = ["split_authors", "split_tags", "parse_year"]

_SEPARATORS = re.compile(r"[,，;；、/|]+")
_AUTHOR_AND = re.compile(r"\s+(?:and|&)\s+", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _as_items(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        items: list[str] = []
        for value in raw:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                items.append(text)
        return items
    text = str(raw).strip()
    return [text] if text else []


def _split(raw: object, extra_split: re.Pattern[str] | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _as_items(raw):
        parts = _SEPARATORS.split(item)
        for part in parts:
            if extra_split is not None:
                part = extra_split.sub(",", part)
            for piece in _SEPARATORS.split(part):
                piece = piece.strip().strip("\"'").strip()
                if not piece:
                    continue
                key = piece.casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(piece)
    return result


def split_tags(raw: object) -> list[str]:
    """支持 YAML 列表或「逗号 / 顿号 / 分号」分隔的字符串。"""
    return _split(raw)


def split_authors(raw: object) -> list[str]:
    """作者切分规则同标签，额外把 ` and ` / ` & ` 视作分隔符。"""
    return _split(raw, _AUTHOR_AND)


def parse_year(raw: object) -> int | None:
    """取出 4 位年份；无法识别或超出 1000~9999 时返回 None。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value: int | None = raw
    else:
        match = _YEAR_RE.search(str(raw))
        if match is None:
            return None
        value = int(match.group(1))
    if value is None or not 1000 <= value <= 9999:
        return None
    return value
