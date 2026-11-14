"""Domain models of the orderbook project."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

__all__ = ["Order", "to_decimal"]


def to_decimal(value: object) -> Decimal:
    """Convert ``value`` to :class:`decimal.Decimal`.

    ``str`` inputs are parsed as decimal strings so that ``"10.50"`` and
    ``Decimal("10.50")`` compare equal.  Anything that cannot be parsed
    raises :class:`TypeError`.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TypeError(f"amount is not a valid decimal: {value!r}") from exc


@dataclass(frozen=True)
class Order:
    """A single order row."""

    order_id: str
    status: str
    category: str
    amount: Decimal

    @classmethod
    def from_mapping(cls, row: Mapping[str, object]) -> "Order":
        """Build an order from a plain mapping (e.g. a database row)."""
        return cls(
            order_id=str(row["order_id"]),
            status=str(row["status"]),
            category=str(row["category"]),
            amount=to_decimal(row["amount"]),
        )
