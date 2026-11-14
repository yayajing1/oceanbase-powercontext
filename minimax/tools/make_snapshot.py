"""Create the ``code/before`` snapshot of the demo project.

Run once, before the MiniMax driven change is applied::

    python tools/make_snapshot.py before
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
PROJECT = MINIMAX_ROOT / "demo_project"
SNAPSHOT_ROOT = MINIMAX_ROOT / "code"

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}


def copy_tree(source: Path, target: Path) -> list[str]:
    copied: list[str] = []
    for path in sorted(source.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        copied.append(relative.as_posix())
    return copied


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in {"before", "after"}:
        print("usage: python tools/make_snapshot.py before|after", file=sys.stderr)
        return 2
    which = argv[1]
    target = SNAPSHOT_ROOT / which
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = copy_tree(PROJECT, target)
    print(f"{which}: {len(copied)} files -> {target}")
    for name in copied:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
