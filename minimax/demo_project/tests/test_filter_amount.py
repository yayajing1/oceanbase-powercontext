"""Target tests for ``SPEC-ORD-003`` amount range filtering.

These tests describe the behaviour that the MiniMax-driven change has to
implement.  Before the change ``filter_orders`` does not accept the amount
arguments at all, so every test in this file fails with a
``TypeError: filter_orders() got an unexpected keyword argument``.  That is
a genuine "target behaviour not implemented" failure, not an environment
error: the module imports and the other test files pass.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from orderbook.filtering import filter_orders
from orderbook.models import Order


def _order(
    order_id: str,
    status: str = "paid",
    category: str = "book",
    amount: str = "10.00",
) -> Order:
    return Order(
        order_id=order_id,
        status=status,
        category=category,
        amount=Decimal(amount),
    )


def test_min_amount_keeps_orders_at_or_above_the_bound():
    orders = [
        _order("A", amount="10.00"),
        _order("B", amount="20.00"),
        _order("C", amount="9.99"),
    ]

    kept = filter_orders(orders, min_amount=Decimal("10.00"))

    assert [order.order_id for order in kept] == ["A", "B"]


def test_max_amount_keeps_orders_at_or_below_the_bound():
    orders = [
        _order("A", amount="10.00"),
        _order("B", amount="20.00"),
        _order("C", amount="20.01"),
    ]

    kept = filter_orders(orders, max_amount=Decimal("20.00"))

    assert [order.order_id for order in kept] == ["A", "B"]


def test_amount_bounds_default_to_no_filtering():
    orders = [_order("A", amount="10.00"), _order("B", amount="99.00")]

    assert filter_orders(orders) == orders


def test_amount_range_is_inclusive_on_both_ends():
    orders = [_order("A", amount="10.00"), _order("B", amount="20.00")]

    kept = filter_orders(orders, min_amount=Decimal("10.00"), max_amount=Decimal("20.00"))

    assert [order.order_id for order in kept] == ["A", "B"]


def test_amount_bounds_accept_decimal_strings():
    orders = [_order("A", amount="10.00"), _order("B", amount="5.00")]

    kept = filter_orders(orders, min_amount="10.00")

    assert [order.order_id for order in kept] == ["A"]


def test_amount_bounds_combine_with_status_and_category():
    orders = [
        _order("A", status="paid", category="book", amount="10.00"),
        _order("B", status="paid", category="grocery", amount="30.00"),
        _order("C", status="pending", category="book", amount="30.00"),
    ]

    kept = filter_orders(
        orders,
        status="paid",
        category="book",
        min_amount=Decimal("5.00"),
        max_amount=Decimal("20.00"),
    )

    assert [order.order_id for order in kept] == ["A"]


def test_min_amount_greater_than_max_amount_raises_value_error():
    orders = [_order("A", amount="10.00")]

    with pytest.raises(ValueError):
        filter_orders(orders, min_amount=Decimal("20.00"), max_amount=Decimal("10.00"))


def test_amount_filter_keeps_the_input_order():
    orders = [
        _order("C", amount="30.00"),
        _order("A", amount="30.00"),
        _order("B", amount="5.00"),
    ]

    kept = filter_orders(orders, min_amount=Decimal("10.00"))

    assert [order.order_id for order in kept] == ["C", "A"]
