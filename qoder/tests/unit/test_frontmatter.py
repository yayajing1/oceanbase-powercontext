from __future__ import annotations

import pytest

from paperdesk.chunking import chunk_note, parse_note
from paperdesk.errors import ImportError_


def test_parses_title_authors_year_tags():
    text = """---
title: 图对比学习在分子性质预测中的应用
authors: 李明, 王芳
year: 2023
tags: 对比学习, 图神经网络
---

# 图对比学习在分子性质预测中的应用

## 问题
正文。
"""
    parsed = parse_note(text, "p01.md")

    assert parsed.has_front_matter is True
    assert parsed.meta.title == "图对比学习在分子性质预测中的应用"
    assert parsed.meta.authors == ("李明", "王芳")
    assert parsed.meta.year == 2023
    assert parsed.meta.tags == ("对比学习", "图神经网络")


def test_accepts_yaml_lists_and_english_author_separator():
    text = """---
title: Paper
authors: [Alice, Bob]
year: "2021"
tags:
  - RAG
  - 评测
---
正文。
"""
    meta = parse_note(text, "paper.md").meta

    assert meta.authors == ("Alice", "Bob")
    assert meta.year == 2021
    assert meta.tags == ("RAG", "评测")


def test_author_and_separator_inside_string():
    text = "---\ntitle: T\nauthors: Alice and Bob\n---\n正文。\n"
    assert parse_note(text, "t.md").meta.authors == ("Alice", "Bob")


def test_invalid_year_becomes_none():
    for raw in ("abc", "12345", "20233"):
        text = f"---\ntitle: T\nyear: {raw}\n---\n正文。\n"
        assert parse_note(text, "t.md").meta.year is None


def test_missing_fields_use_h1_and_empty_defaults():
    text = "# 只有一级标题\n\n正文。\n"
    parsed = parse_note(text, "fallback.md")

    assert parsed.has_front_matter is False
    assert parsed.meta.title == "只有一级标题"
    assert parsed.meta.authors == ()
    assert parsed.meta.year is None
    assert parsed.meta.tags == ()


def test_no_front_matter_and_no_heading_uses_file_stem():
    parsed = parse_note("正文而已。\n", "notes/无名笔记.md")
    assert parsed.meta.title == "无名笔记"


def test_leading_blank_lines_before_front_matter_are_tolerated():
    text = "\n\n---\ntitle: 前有空行\n---\n\n# 正文\n\n内容。\n"
    parsed = parse_note(text, "t.md")
    assert parsed.meta.title == "前有空行"
    assert parsed.has_front_matter is True


def test_front_matter_is_not_part_of_chunks():
    text = """---
title: 标题
tags: 标签
---

# 标题

## 问题
正文。
"""
    chunks = chunk_note(parse_note(text, "t.md"), 800)
    assert chunks
    assert all("tags:" not in chunk.content for chunk in chunks)


def test_invalid_yaml_reports_file_name():
    text = "---\ntitle: [未闭合\n---\n正文。\n"
    with pytest.raises(ImportError_, match="front matter"):
        parse_note(text, "broken.md")


def test_non_mapping_front_matter_rejected():
    text = "---\n- 不是\n- 映射\n---\n正文。\n"
    with pytest.raises(ImportError_, match="键值映射"):
        parse_note(text, "list.md")


def test_unclosed_front_matter_rejected():
    text = "---\ntitle: 没有结束\n正文。\n"
    with pytest.raises(ImportError_, match="没有结束标记"):
        parse_note(text, "unclosed.md")
