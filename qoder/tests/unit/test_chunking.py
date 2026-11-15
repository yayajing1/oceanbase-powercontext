from __future__ import annotations

from paperdesk.chunking import chunk_note, parse_note


def test_heading_sections_and_line_numbers():
    text = "\n".join(
        [
            "# 论文标题",
            "",
            "## 问题",
            "问题正文第一行。",
            "问题正文第二行。",
            "",
            "## 方法",
            "方法正文。",
            "",
            "## 结论",
            "结论正文。",
        ]
    )
    parsed = parse_note(text, "note.md")
    chunks = chunk_note(parsed, 800)

    assert parsed.meta.title == "论文标题"
    assert [c.heading_path for c in chunks] == [
        "论文标题 > 问题",
        "论文标题 > 方法",
        "论文标题 > 结论",
    ]
    assert [(c.start_line, c.end_line) for c in chunks] == [(4, 5), (8, 8), (11, 11)]
    assert chunks[0].content == "问题正文第一行。\n问题正文第二行。"
    assert chunks[0].search_text.startswith("论文标题\n论文标题 > 问题")


def test_nested_headings_build_full_path():
    text = "# T\n\n## A\n\nA 的正文。\n\n### A1\n\nA1 的正文。\n"
    chunks = chunk_note(parse_note(text, "t.md"), 800)

    assert [c.heading_path for c in chunks] == ["T > A", "T > A > A1"]
    assert chunks[0].content == "A 的正文。"
    assert chunks[1].content == "A1 的正文。"


def test_preamble_before_first_heading_becomes_own_chunk():
    text = "这是导语。\n\n# 标题\n\n## 小节\n\n小节正文。\n"
    chunks = chunk_note(parse_note(text, "t.md"), 800)

    assert chunks[0].heading_path == "标题"
    assert chunks[0].content == "这是导语。"
    assert chunks[1].heading_path == "标题 > 小节"


def test_without_headings_chunks_by_paragraph():
    text = "第一段。\n\n第二段。\n"
    parsed = parse_note(text, "note.md")
    big = chunk_note(parsed, 800)
    small = chunk_note(parsed, 6)

    assert len(big) == 1
    assert big[0].content == "第一段。\n\n第二段。"
    assert [c.content for c in small] == ["第一段。", "第二段。"]
    assert [(c.start_line, c.end_line) for c in small] == [(1, 1), (3, 3)]


def test_long_paragraph_is_hard_split_with_exact_offsets():
    paragraph = "".join(str(index % 10) for index in range(100))
    text = f"# 标题\n\n{paragraph}\n"
    chunks = chunk_note(parse_note(text, "t.md"), 30)

    assert len(chunks) == 4
    assert all(len(chunk.content) <= 30 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == paragraph

    start = text.index(paragraph)
    assert [chunk.start_offset for chunk in chunks] == [start, start + 30, start + 60, start + 90]
    assert chunks[0].start_line == chunks[0].end_line == 3
    assert all(chunk.end_offset - chunk.start_offset <= 30 for chunk in chunks)


def test_hard_split_across_lines_tracks_line_range():
    first = "a" * 20
    second = "b" * 20
    text = f"## 小节\n\n{first}\n{second}\n"
    chunks = chunk_note(parse_note(text, "t.md"), 25)

    assert len(chunks) == 2
    assert chunks[0].content == first + "\n" + "b" * 4
    assert (chunks[0].start_line, chunks[0].end_line) == (3, 4)
    assert (chunks[1].start_line, chunks[1].end_line) == (4, 4)


def test_paragraph_greedy_packing_respects_max_chars():
    paragraphs = ["x" * 30 for _ in range(5)]
    text = "## 小节\n\n" + "\n\n".join(paragraphs) + "\n"
    chunks = chunk_note(parse_note(text, "t.md"), 100)

    assert len(chunks) == 2
    assert all(len(chunk.content) <= 100 for chunk in chunks)
    assert chunks[0].content == "\n\n".join(paragraphs[:3])


def test_code_fence_headings_are_ignored():
    text = "# T\n\n```\n# 不是标题\n## 也不是\n```\n\n## 真标题\n\n正文。\n"
    chunks = chunk_note(parse_note(text, "t.md"), 800)

    assert [c.heading_path for c in chunks] == ["T", "T > 真标题"]
    assert "# 不是标题" in chunks[0].content


def test_empty_section_is_skipped():
    text = "# T\n\n## 空小节\n\n## 有内容\n\n正文。\n"
    chunks = chunk_note(parse_note(text, "t.md"), 800)

    assert len(chunks) == 1
    assert chunks[0].heading_path == "T > 有内容"


def test_crlf_input_keeps_line_numbers():
    text = "# T\r\n\r\n## A\r\n正文。\r\n"
    chunks = chunk_note(parse_note(text, "t.md"), 800)

    assert chunks[0].content == "正文。"
    assert chunks[0].start_line == 4


def test_empty_document_produces_no_chunks():
    assert chunk_note(parse_note("", "empty.md"), 800) == []
