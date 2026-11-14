"""Text normalisation shared by the importer and the retriever.

The search text that is stored in the full text index is built with
:func:`normalize_for_search`.  The very same function is applied to the
query, so both sides see the same tokens.

Two properties matter for the retrieval quality:

* CJK characters are separated by spaces.  That way a plain keyword parser
  (which only understands whitespace separated words) can still match
  Chinese specifications, while a dedicated Chinese parser such as ``ik``
  keeps working as well.
* Identifiers are split on ``_`` / ``-`` so that ``filter_orders`` and the
  words ``filter`` / ``orders`` are found by the same query.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalize_for_search", "is_cjk", "tokenize", "symbol_tokens"]

_CJK_RANGES = (
    (0x3400, 0x4DBF),   # CJK unified ideographs extension A
    (0x4E00, 0x9FFF),   # CJK unified ideographs
    (0xF900, 0xFAFF),   # CJK compatibility ideographs
    (0x20000, 0x2A6DF),  # CJK extension B
    (0x3000, 0x303F),   # CJK punctuation
    (0xFF00, 0xFF65),   # full width forms
)

_ASCII_TOKEN = re.compile(r"[a-z0-9]+")


def is_cjk(char: str) -> bool:
    """Return ``True`` when ``char`` belongs to a CJK-ish block."""
    code = ord(char)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


def normalize_for_search(text: str) -> str:
    """Return a normalised, parser friendly representation of ``text``."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    pieces: list[str] = []
    for char in text:
        if is_cjk(char):
            pieces.append(" ")
            pieces.append(char)
            pieces.append(" ")
        elif char.isalnum():
            pieces.append(char)
        elif char in "_-":
            pieces.append(" ")
        else:
            pieces.append(" ")
    joined = "".join(pieces).lower()
    return re.sub(r"\s+", " ", joined).strip()


def tokenize(text: str) -> list[str]:
    """Return the normalised tokens of ``text``."""
    normalized = normalize_for_search(text)
    tokens: list[str] = []
    for word in normalized.split(" "):
        if not word:
            continue
        tokens.append(word)
        if len(word) > 2 and word.isascii():
            for part in word.split("_"):
                if part and part != word:
                    tokens.append(part)
    return tokens


def symbol_tokens(symbol: str) -> list[str]:
    """Return the searchable tokens of an identifier."""
    return [token for token in tokenize(symbol) if token]
