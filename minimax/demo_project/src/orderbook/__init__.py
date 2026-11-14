"""Orderbook: a tiny order filtering library used as the SDD demo project.

The module is intentionally small: it is a real, runnable business project
with its own specifications and tests, and it is the target of the
MiniMax-driven code change demonstrated in this competition entry.
"""

from .filtering import filter_orders
from .models import Order, to_decimal

__all__ = ["Order", "filter_orders", "to_decimal"]
