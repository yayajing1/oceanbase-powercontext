"""Extracting and applying unified diffs.

The MiniMax answer is asked for a unified diff.  This module extracts it
from the model answer and applies it to the working tree.  The applier is
implemented in pure Python so that the module does not need ``git`` or
``patch`` to be installed.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "PatchError",
    "extract_diff",
    "apply_unified_diff",
    "make_unified_diff",
    "diff_files",
]

_FENCE_RE = re.compile(r"```(?:diff|patch)?\s*\n(.*?)```", re.DOTALL)
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(RuntimeError):
    """Raised when a diff cannot be extracted or applied."""


def extract_diff(text: str) -> str:
    """Return the unified diff contained in ``text``.

    A fenced ```diff block is preferred; otherwise the first ``---`` /
    ``+++`` pair is used.
    """
    if not text:
        raise PatchError("the model answer contains no diff")
    for match in _FENCE_RE.finditer(text):
        block = match.group(1)
        if "--- " in block and "+++ " in block:
            return block.strip("\n")
    start = text.find("--- ")
    if start == -1:
        raise PatchError("the model answer contains no unified diff")
    tail = text[start:]
    end = tail.find("\n```")
    if end != -1:
        tail = tail[:end]
    return tail.strip("\n")


@dataclass
class FilePatch:
    """One file section of a unified diff."""

    old_path: str
    new_path: str
    hunks: list[tuple[int, list[str]]]


def _parse_hunks(lines: list[str]) -> list[FilePatch]:
    patches: list[FilePatch] = []
    current: FilePatch | None = None
    old_path = new_path = ""
    hunk: list[str] | None = None

    for line in lines:
        if line.startswith("--- "):
            old_path = line[4:].split("\t")[0].strip()
            current = None
            hunk = None
            continue
        if line.startswith("+++ "):
            new_path = line[4:].split("\t")[0].strip()
            current = FilePatch(old_path=old_path, new_path=new_path, hunks=[])
            patches.append(current)
            hunk = None
            continue
        if line.startswith("@@"):
            match = _HUNK_RE.match(line)
            if not match or current is None:
                hunk = None
                continue
            hunk = []
            current.hunks.append((int(match.group(1)), hunk))
            continue
        if hunk is not None:
            hunk.append(line)
    return [patch for patch in patches if patch.hunks]


def _strip_prefix(path: str) -> str:
    path = path.strip()
    if path.startswith('"') and path.endswith('"') and len(path) > 1:
        path = path[1:-1]
    if path in {"/dev/null"}:
        return ""
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _hunk_expectation(hunk_lines: list[str]) -> list[str]:
    """The lines that the original file must contain at the hunk position."""
    expected: list[str] = []
    for line in hunk_lines:
        if not line:
            continue
        marker, text = line[0], line[1:]
        if marker in {" ", "-"}:
            expected.append(text)
    return expected


def _find_anchor(original: list[str], index: int, expected: list[str], window: int = 8) -> int | None:
    """Find where the hunk belongs.

    The line number that the model wrote may be off by a few lines.  Like
    ``patch`` does, the anchor is searched in a small window around the
    stated position; the hunk content itself still has to match exactly.
    """
    if not expected:
        return index if 0 <= index <= len(original) else None
    low = max(0, index - window)
    high = min(len(original), index + window)
    for candidate in range(low, high + 1):
        if original[candidate:candidate + len(expected)] == expected:
            return candidate
    return None


def apply_unified_diff(root: Path, diff: str, dry_run: bool = False, window: int = 8) -> list[str]:
    """Apply ``diff`` to the files below ``root`` and return the touched paths."""
    root = Path(root).resolve()
    patches = _parse_hunks(diff.splitlines())
    if not patches:
        raise PatchError("the diff contains no file section")

    touched: list[str] = []
    for patch in patches:
        relative = _strip_prefix(patch.new_path) or _strip_prefix(patch.old_path)
        if not relative:
            continue
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PatchError(f"the diff points outside of the project: {relative}") from exc

        original = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
        result: list[str] = []
        cursor = 0
        for start, hunk_lines in patch.hunks:
            expected = _hunk_expectation(hunk_lines)
            index = _find_anchor(original, max(start - 1, 0), expected, window)
            if index is None:
                raise PatchError(
                    f"the hunk at line {start} of {relative} does not match the file; "
                    f"expected {expected[:3]}"
                )
            if index < cursor:
                raise PatchError(f"overlapping hunks in {relative}")
            result.extend(original[cursor:index])
            cursor = index
            for line in hunk_lines:
                if not line:
                    continue
                marker, text = line[0], line[1:]
                if marker == " ":
                    if cursor >= len(original) or original[cursor] != text:
                        actual = original[cursor] if cursor < len(original) else "<eof>"
                        raise PatchError(
                            f"context mismatch in {relative} at line {cursor + 1}: "
                            f"expected {text!r}, found {actual!r}"
                        )
                    result.append(text)
                    cursor += 1
                elif marker == "-":
                    if cursor >= len(original) or original[cursor] != text:
                        actual = original[cursor] if cursor < len(original) else "<eof>"
                        raise PatchError(
                            f"removal mismatch in {relative} at line {cursor + 1}: "
                            f"expected {text!r}, found {actual!r}"
                        )
                    cursor += 1
                elif marker == "+":
                    result.append(text)
                elif marker == "\\":
                    continue
                else:
                    raise PatchError(f"unexpected diff line in {relative}: {line!r}")
        result.extend(original[cursor:])
        touched.append(relative)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(result) + "\n", encoding="utf-8")
    if not touched:
        raise PatchError("the diff did not touch any file")
    return touched


def make_unified_diff(before: str, after: str, relative_path: str) -> str:
    """Return the unified diff between two texts."""
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        lineterm="",
        n=3,
    )
    return "\n".join(diff)


def diff_files(before_root: Path, after_root: Path, relative_paths: list[str]) -> str:
    """Return the unified diff of ``relative_paths`` between two trees."""
    chunks: list[str] = []
    for relative in relative_paths:
        before_path = Path(before_root) / relative
        after_path = Path(after_root) / relative
        before = before_path.read_text(encoding="utf-8") if before_path.exists() else ""
        after = after_path.read_text(encoding="utf-8") if after_path.exists() else ""
        chunk = make_unified_diff(before, after, relative)
        if chunk:
            chunks.append(chunk)
    return "\n".join(chunks)
