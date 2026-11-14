"""Regression tests for ``SPEC-ORD-001`` category filtering."""

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


def test_category_none_keeps_every_order():
    orders = [_order("A"), _order("B", category="grocery")]

    assert filter_orders(orders) == orders


def test_category_filters_to_matching_orders():
    orders = [_order("A"), _order("B", category="grocery"), _order("C", category="book")]

    kept = filter_orders(orders, category="book")

    assert [order.order_id for order in kept] == ["A", "C"]


def test_category_is_case_sensitive():
    orders = [_order("A", category="book")]

    assert filter_orders(orders, category="Book") == []


def test_filter_does_not_mutate_the_input_orders():
    orders = [_order("A"), _order("B", category="grocery")]

    filter_orders(orders, category="book")

    assert [order.order_id for order in orders] == ["A", "B"]
