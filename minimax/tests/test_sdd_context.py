"""Unit tests of the context module that do not need a database.

Run with::

    cd minimax
    python -m pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
if str(MINIMAX_ROOT) not in sys.path:
    sys.path.insert(0, str(MINIMAX_ROOT))

from sdd_context.documents import collect_documents, parse_spec_file  # noqa: E402
from sdd_context.patcher import (  # noqa: E402
    PatchError,
    apply_unified_diff,
    extract_diff,
    make_unified_diff,
)
from sdd_context.textnorm import normalize_for_search, tokenize  # noqa: E402

DEMO = MINIMAX_ROOT / "demo_project"


# ---------------------------------------------------------------------------
# text normalisation
# ---------------------------------------------------------------------------

def test_cjk_is_split_into_single_characters():
    assert normalize_for_search("金额筛选") == "金 额 筛 选"


def test_identifiers_are_split_on_underscores():
    assert normalize_for_search("filter_orders") == "filter orders"


def test_mixed_text_is_normalised():
    assert normalize_for_search("min_amount >= 10.00") == "min amount 10 00"


def test_tokenize_returns_the_words():
    assert "min" in tokenize("min_amount")
    assert "amount" in tokenize("min_amount")


# ---------------------------------------------------------------------------
# document parsing
# ---------------------------------------------------------------------------

def test_spec_rules_become_separate_documents():
    documents = parse_spec_file(DEMO / "specs" / "spec_amount_filter.md", "specs/spec_amount_filter.md")
    locators = {document.locator for document in documents}
    assert "SPEC-ORD-003" in locators
    assert "SPEC-ORD-003.R1" in locators
    assert "SPEC-ORD-003.R7" in locators
    rule = next(document for document in documents if document.locator == "SPEC-ORD-003.R2")
    assert rule.applies_to == "orderbook.filtering.filter_orders"
    assert rule.meta["version"] == 1


def test_python_symbols_become_documents():
    documents = collect_documents(DEMO)
    locators = {document.locator for document in documents}
    assert "src/orderbook/filtering.py::filter_orders" in locators
    assert "src/orderbook/models.py::to_decimal" in locators
    assert "tests/test_filter_amount.py::test_min_amount_keeps_orders_at_or_above_the_bound" in locators


def test_test_documents_reference_the_covered_symbol():
    documents = collect_documents(DEMO)
    hit = next(
        document
        for document in documents
        if document.locator == "tests/test_filter_amount.py::test_min_amount_keeps_orders_at_or_above_the_bound"
    )
    assert "filter_orders" in hit.references


def test_code_documents_reference_the_used_symbols():
    documents = collect_documents(DEMO)
    hit = next(
        document
        for document in documents
        if document.locator == "src/orderbook/models.py::Order.from_mapping"
    )
    assert "to_decimal" in hit.references


# ---------------------------------------------------------------------------
# patch handling
# ---------------------------------------------------------------------------

def test_extract_diff_prefers_a_fenced_block():
    text = "说明文字\n```diff\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-a\n+b\n```\n"
    assert extract_diff(text).startswith("--- a/x.py")


def test_extract_diff_falls_back_to_a_plain_diff():
    text = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    assert "--- a/x.py" in extract_diff(text)


def test_extract_diff_raises_without_a_diff():
    with pytest.raises(PatchError):
        extract_diff("这里没有 diff")


def test_apply_unified_diff(tmp_path):
    target = tmp_path / "x.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    diff = make_unified_diff("one\ntwo\nthree\n", "one\nTWO\nthree\n", "x.py")
    touched = apply_unified_diff(tmp_path, diff)
    assert touched == ["x.py"]
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_unified_diff_tolerates_a_shifted_line_number(tmp_path):
    target = tmp_path / "x.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    diff = (
        "--- a/x.py\n+++ b/x.py\n"
        "@@ -9,3 +9,3 @@\n one\n-two\n+TWO\n three\n"
    )
    apply_unified_diff(tmp_path, diff)
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_unified_diff_rejects_a_wrong_context(tmp_path):
    target = tmp_path / "x.py"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n one\n-WRONG\n+TWO\n"
    with pytest.raises(PatchError):
        apply_unified_diff(tmp_path, diff)
    assert target.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_apply_unified_diff_refuses_to_escape_the_project(tmp_path):
    diff = "--- a/../outside.py\n+++ b/../outside.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    with pytest.raises(PatchError):
        apply_unified_diff(tmp_path, diff)
