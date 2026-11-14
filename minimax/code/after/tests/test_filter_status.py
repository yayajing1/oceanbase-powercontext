"""Regression tests for ``SPEC-ORD-001`` status filtering.

These tests describe behaviour that already exists and must keep working
after the amount-range change.
"""

from __future__ import annotations

from decimal import Decimal

from orderbook.filtering import filter_orders
from orderbook.models import Order


def _order(order_id: str, status: str = "paid", category: str = "book") -> Order:
    return Order(
        order_id=order_id,
        status=status,
        category=category,
        amount=Decimal("10.00"),
    )


def test_status_none_keeps_every_order():
    orders = [_order("A"), _order("B", status="pending")]

    assert filter_orders(orders) == orders


def test_status_filters_to_matching_orders():
    orders = [_order("A"), _order("B", status="pending"), _order("C")]

    kept = filter_orders(orders, status="paid")

    assert [order.order_id for order in kept] == ["A", "C"]


def test_status_keeps_input_order():
    orders = [_order("C"), _order("A"), _order("B")]

    kept = filter_orders(orders, status="paid")

    assert [order.order_id for order in kept] == ["C", "A", "B"]


def test_unknown_status_returns_empty_list():
    orders = [_order("A"), _order("B", status="pending")]

    assert filter_orders(orders, status="refunded") == []


def test_status_and_category_are_combined_with_and():
    orders = [
        _order("A", status="paid", category="book"),
        _order("B", status="paid", category="grocery"),
        _order("C", status="pending", category="book"),
    ]

    kept = filter_orders(orders, status="paid", category="book")

    assert [order.order_id for order in kept] == ["A"]
