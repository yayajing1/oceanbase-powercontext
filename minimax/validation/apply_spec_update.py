"""Apply and revert the specification change of the validation sample.

The validation sample has to prove that the context module does not return a
fixed answer: after the specification of the sample is changed and imported
again, the context must contain the **new** content.

The change applied here is:

* ``version`` of ``SPEC-ORD-003`` goes from ``1`` to ``2``,
* rule ``R2`` is reworded,
* a new rule ``R8`` is added.

``R8`` only states something the implementation already guarantees (the
amount filter and the status / category filter are combined), so the test
set stays valid before and after the change.

Usage::

    python validation/apply_spec_update.py --apply
    python validation/apply_spec_update.py --revert
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MINIMAX_ROOT = Path(__file__).resolve().parents[1]
SPEC = MINIMAX_ROOT / "demo_project" / "specs" / "spec_amount_filter.md"

VERSION_1 = "version: 1"
VERSION_2 = "version: 2"

R2_V1 = "- R2: `min_amount` 生效时，只保留 `amount >= min_amount` 的订单（边界值包含在内）。"
R2_V2 = (
    "- R2: `min_amount` 生效时，只保留 `amount >= min_amount` 的订单（边界值包含在内）。"
    "金额以人民币元为单位，不做汇率换算。"
)

R8_V2 = (
    "- R8: 金额筛选必须与 `status` / `category` 条件同时生效，两个条件不允许互相覆盖。"
)


def apply_update(text: str) -> str:
    if R8_V2 in text:
        return text
    if R2_V1 not in text:
        raise SystemExit(f"the original rule R2 was not found in {SPEC}")
    text = text.replace(VERSION_1, VERSION_2, 1)
    text = text.replace(R2_V1, R2_V2, 1)
    text = text.rstrip("\n") + "\n" + R8_V2 + "\n"
    return text


def revert_update(text: str) -> str:
    if R8_V2 not in text and VERSION_2 not in text:
        return text
    text = text.replace(R8_V2 + "\n", "")
    text = text.replace(R2_V2, R2_V1, 1)
    text = text.replace(VERSION_2, VERSION_1, 1)
    return text


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="apply or revert the specification update")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply", action="store_true")
    group.add_argument("--revert", action="store_true")
    args = parser.parse_args(argv)

    original = SPEC.read_text(encoding="utf-8")
    updated = apply_update(original) if args.apply else revert_update(original)
    if updated != original:
        SPEC.write_text(updated, encoding="utf-8")
    print(f"{'applied' if args.apply else 'reverted'}: {SPEC}")
    for line in updated.splitlines():
        if line.startswith("version:") or line.startswith("- R2:") or line.startswith("- R8:"):
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
