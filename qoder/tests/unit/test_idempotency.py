from __future__ import annotations

import pytest

from paperdesk.chunking import content_hash
from paperdesk.errors import ImportError_
from paperdesk.importer import (
    assign_chunk_identity,
    compute_chunk_id,
    compute_document_id,
    compute_file_hash,
    diff_chunk_ids,
    discover_targets,
    normalize_rel_path,
    plan_document,
)
from paperdesk.models import ChunkDraft

TEXT = """---
title: 示例文献
year: 2023
---

# 示例文献

## 问题
问题内容。

## 方法
方法内容。

## 结论
结论内容。
"""


def _draft(body: str) -> ChunkDraft:
    return ChunkDraft(
        heading_path="T",
        content=body,
        start_line=1,
        end_line=1,
        start_offset=0,
        end_offset=len(body),
        search_text=body,
        content_hash=content_hash(body),
    )


def test_relative_path_is_normalized_before_hashing():
    assert normalize_rel_path("./notes\\a.md") == "notes/a.md"
    assert compute_document_id("notes\\a.md") == compute_document_id("notes/a.md")


def test_planning_same_file_twice_produces_identical_ids():
    first = plan_document(TEXT, "notes/a.md")
    second = plan_document(TEXT, "notes/a.md")

    assert first.document_id == second.document_id
    assert first.path_hash == second.path_hash
    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]


def test_same_content_in_different_documents_gets_different_ids():
    first = plan_document(TEXT, "a.md")
    second = plan_document(TEXT, "b.md")

    assert {c.chunk_id for c in first.chunks}.isdisjoint({c.chunk_id for c in second.chunks})


def test_repeated_paragraph_uses_occurrence_to_avoid_id_collision():
    identified = assign_chunk_identity("d" * 32, [_draft("重复。"), _draft("重复。")])

    assert [item.occurrence for item in identified] == [0, 1]
    assert identified[0].chunk_id != identified[1].chunk_id
    assert identified[0].chunk_id == compute_chunk_id("d" * 32, content_hash("重复。"), 0)


def test_unchanged_file_has_nothing_to_delete_or_add():
    first = plan_document(TEXT, "a.md")
    second = plan_document(TEXT, "a.md")

    diff = diff_chunk_ids({c.chunk_id for c in first.chunks}, {c.chunk_id for c in second.chunks})
    assert diff.to_delete == ()
    assert diff.to_add == ()
    assert len(diff.to_keep) == len(first.chunks)


def test_changed_body_replaces_only_the_affected_chunk():
    before = plan_document(TEXT, "a.md")
    after = plan_document(TEXT.replace("问题内容。", "问题内容已修改。"), "a.md")

    diff = diff_chunk_ids({c.chunk_id for c in before.chunks}, {c.chunk_id for c in after.chunks})
    assert len(diff.to_delete) == 1
    assert len(diff.to_add) == 1
    assert len(diff.to_keep) == 2


def test_removed_section_is_deleted_on_reimport():
    before = plan_document(TEXT, "a.md")
    after = plan_document(TEXT.replace("## 方法\n方法内容。\n", ""), "a.md")

    diff = diff_chunk_ids({c.chunk_id for c in before.chunks}, {c.chunk_id for c in after.chunks})
    assert len(diff.to_delete) == 1
    assert diff.to_add == ()
    assert len(after.chunks) == 2


def test_file_hash_changes_only_when_bytes_change():
    assert compute_file_hash(b"abc") == compute_file_hash(b"abc")
    assert compute_file_hash(b"abc") != compute_file_hash(b"abd")


def test_planning_from_tmp_file_is_repeatable(tmp_path):
    note = tmp_path / "note.md"
    note.write_text(TEXT, encoding="utf-8")
    data = note.read_bytes()
    text = data.decode("utf-8")

    first = plan_document(text, "note.md", file_hash=compute_file_hash(data))
    second = plan_document(text, "note.md", file_hash=compute_file_hash(data))
    assert [c.chunk_id for c in first.chunks] == [c.chunk_id for c in second.chunks]


def test_directory_discovery_defaults_to_prune(tmp_path):
    (tmp_path / "a.md").write_text("# A\n\n正文。\n", encoding="utf-8")
    (tmp_path / "ignore.pdf").write_text("x", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.markdown").write_text("# B\n\n正文。\n", encoding="utf-8")

    targets = discover_targets([str(tmp_path)])

    assert {target.path.name for target in targets} == {"a.md", "b.markdown"}
    assert all(target.prune for target in targets)
    assert all(target.import_root == tmp_path.resolve() for target in targets)


def test_single_file_discovery_does_not_prune_by_default(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("# A\n\n正文。\n", encoding="utf-8")

    (target,) = discover_targets([str(note)])
    assert target.prune is False
    assert target.import_root == tmp_path.resolve()


def test_explicit_prune_override_applies_to_single_file(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("# A\n\n正文。\n", encoding="utf-8")

    (target,) = discover_targets([str(note)], prune=True)
    assert target.prune is True


def test_missing_path_is_an_actionable_error(tmp_path):
    with pytest.raises(ImportError_, match="路径不存在"):
        discover_targets([str(tmp_path / "nope.md")])
