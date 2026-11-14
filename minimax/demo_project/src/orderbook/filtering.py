"""Order filtering helpers.

The behaviour of this module is defined by the specifications under
``specs/``: ``SPEC-ORD-001`` (status / category filtering) and
``SPEC-ORD-003`` (amount range filtering).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, List, Optional

from .models import Order

__all__ = ["filter_orders"]


def filter_orders(
    orders: Iterable[Order],
    *,
    status: Optional[str] = None,
    category: Optional[str] = None,
    min_amount: Optional[object] = None,
    max_amount: Optional[object] = None,
) -> List[Order]:
    """Return the orders that match ``status``, ``category``, and amount bounds.

    ``status`` and ``category`` are optional; when one of them is ``None``
    the corresponding condition is not applied.  When both are given the
    conditions are combined with ``AND``.  ``min_amount`` and
    ``max_amount`` are inclusive bounds; ``None`` disables the corresponding
    condition.  The relative order of the returned orders always follows
    the input order.
    """
    min_bound = Decimal(str(min_amount)) if min_amount is not None else None
    max_bound = Decimal(str(max_amount)) if max_amount is not None else None
    if (
        min_bound is not None
        and max_bound is not None
        and min_bound > max_bound
    ):
        raise ValueError("min_amount must not be greater than max_amount")

    matched: List[Order] = []
    for order in orders:
        if status is not None and order.status != status:
            continue
        if category is not None and order.category != category:
            continue
        if min_bound is not None and order.amount < min_bound:
            continue
        if max_bound is not None and order.amount > max_bound:
            continue
        matched.append(order)
    return matched
