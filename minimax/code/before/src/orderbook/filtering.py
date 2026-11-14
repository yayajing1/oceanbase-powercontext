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
) -> List[Order]:
    """Return the orders that match ``status`` and ``category``.

    ``status`` and ``category`` are optional; when one of them is ``None``
    the corresponding condition is not applied.  When both are given the
    conditions are combined with ``AND``.  The relative order of the
    returned orders always follows the input order.
    """
    matched: List[Order] = []
    for order in orders:
        if status is not None and order.status != status:
            continue
        if category is not None and order.category != category:
            continue
        matched.append(order)
    return matched
