"""Unit tests for Order domain model and lifecycle state machine."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide, OrderStatus, OrderType
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.order import Order


def create_sample_order(
    price: str = "100.00",
    qty: str = "1.0",
    order_type: OrderType = OrderType.LIMIT,
    side: OrderSide = OrderSide.BUY,
) -> Order:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return Order(
        symbol="BTCUSDT",
        side=side,
        order_type=order_type,
        price=Decimal(price),
        quantity=Decimal(qty),
        client_order_id="TEST-001",
        created_at=now,
        updated_at=now,
    )


def test_order_creation_and_properties() -> None:
    order = create_sample_order(price="100.00", qty="2.5")
    assert order.symbol == "BTCUSDT"
    assert order.status == OrderStatus.NEW
    assert order.is_active
    assert not order.is_terminal
    assert order.remaining_quantity == Decimal("2.5")
    assert order.notional_value == Decimal("250.00")


def test_order_invalid_parameters() -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with pytest.raises(DomainValidationError, match="symbol cannot be empty"):
        Order(
            symbol="",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("100"),
            quantity=Decimal("1"),
            client_order_id="1",
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(DomainValidationError, match="client_order_id cannot be empty"):
        Order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("100"),
            quantity=Decimal("1"),
            client_order_id="",
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(DomainValidationError, match="quantity must be strictly positive"):
        Order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=Decimal("100"),
            quantity=ZERO_DECIMAL,
            client_order_id="1",
            created_at=now,
            updated_at=now,
        )

    with pytest.raises(DomainValidationError, match="price must be strictly positive"):
        Order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=ZERO_DECIMAL,
            quantity=Decimal("1"),
            client_order_id="1",
            created_at=now,
            updated_at=now,
        )


def test_order_mark_submitted() -> None:
    order = create_sample_order(price="100.00", qty="2.0")
    t1 = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    order.mark_submitted(t1)
    assert order.status == OrderStatus.SUBMITTED
    assert order.is_active


def test_order_partial_fill() -> None:
    order = create_sample_order(price="100.00", qty="2.0")
    t1 = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 12, 2, tzinfo=UTC)
    order.mark_submitted(t1)
    order.apply_fill(Decimal("1.0"), t2)
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("1.0")
    assert order.remaining_quantity == Decimal("1.0")
    assert order.is_active
    assert not order.is_terminal


def test_order_full_fill() -> None:
    order = create_sample_order(price="100.00", qty="1.0")
    t1 = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 12, 2, tzinfo=UTC)
    order.mark_submitted(t1)
    order.apply_fill(Decimal("1.0"), t2)
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == Decimal("1.0")
    assert order.remaining_quantity == ZERO_DECIMAL
    assert not order.is_active
    assert order.is_terminal


def test_order_lifecycle_cancellation_flow() -> None:
    order = create_sample_order(price="100.00", qty="1.0")
    t1 = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 1, 12, 2, tzinfo=UTC)
    order.mark_submitted(t1)
    order.mark_canceled(t2)
    assert order.status == OrderStatus.CANCELED
    assert order.is_terminal
    assert not order.is_active


def test_order_invalid_state_transitions() -> None:
    order = create_sample_order(price="100.00", qty="1.0")
    t1 = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    order.mark_submitted(t1)

    # Cannot submit already submitted order
    with pytest.raises(DomainValidationError, match="expected NEW"):
        order.mark_submitted(t1)

    order.apply_fill(Decimal("1.0"), t1)
    # Cannot apply fill to already FILLED order
    with pytest.raises(DomainValidationError, match="inactive order"):
        order.apply_fill(Decimal("0.1"), t1)

    # Cannot cancel FILLED order
    with pytest.raises(DomainValidationError, match="Cannot cancel inactive order"):
        order.mark_canceled(t1)


def test_order_fill_exceeding_quantity_fails() -> None:
    order = create_sample_order(price="100.00", qty="1.0")
    t1 = datetime(2026, 1, 1, 12, 1, tzinfo=UTC)
    order.mark_submitted(t1)

    with pytest.raises(DomainValidationError, match="exceeds remaining order quantity"):
        order.apply_fill(Decimal("1.0001"), t1)
