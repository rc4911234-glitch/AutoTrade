"""Unit tests for SimulatedExecutionAdapter."""

from datetime import UTC, datetime
from decimal import Decimal

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import OrderSide, OrderStatus, OrderType
from trad_auto.core.events import (
    OrderCanceledEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.models.market_data import Quote, Trade
from trad_auto.core.models.order import Order
from trad_auto.core.time import SimulatedClock
from trad_auto.execution.simulated_adapter import SimulatedExecutionAdapter


def make_test_quote(
    symbol: str = "BTCUSDT",
    bid: str = "99.00",
    ask: str = "101.00",
    ts: datetime | None = None,
) -> Quote:
    now = ts or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return Quote(
        symbol=symbol,
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("10.0"),
        ask_size=Decimal("10.0"),
        timestamp=now,
    )


def test_market_order_immediate_execution_with_slippage() -> None:
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(
        event_bus=bus,
        clock=clock,
        taker_fee_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.01"),  # 1% slippage
    )

    filled_events: list[OrderFilledEvent] = []
    bus.subscribe(OrderFilledEvent, lambda e: filled_events.append(e))

    # Publish quote: Bid 100, Ask 100 (mid 100)
    bus.publish(QuoteUpdatedEvent(quote=make_test_quote(bid="100.00", ask="100.00")))

    # Market BUY 2.0 BTC: Fills at ask * 1.01 = 101.00
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        price=Decimal("100.00"),
        quantity=Decimal("2.0"),
        client_order_id="MKT-BUY",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    adapter.submit_order(order)

    assert order.status == OrderStatus.FILLED
    assert len(filled_events) == 1
    assert filled_events[0].fill_price == Decimal("101.00")
    assert filled_events[0].fill_quantity == Decimal("2.0")
    # Fee: 101.00 * 2.0 * 0.001 = 0.202
    assert filled_events[0].fee == Decimal("0.202")
    assert not filled_events[0].is_maker


def test_limit_maker_spread_crossing_rejection() -> None:
    """Verifies that post-only orders crossing the spread are rejected immediately."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)

    rejected_events: list[OrderRejectedEvent] = []
    bus.subscribe(OrderRejectedEvent, lambda e: rejected_events.append(e))

    bus.publish(QuoteUpdatedEvent(quote=make_test_quote(bid="99.00", ask="101.00")))

    # Limit Maker BUY at 101.00 (crosses ask 101.00 as taker) -> REJECTED
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        price=Decimal("101.00"),
        quantity=Decimal("1.0"),
        client_order_id="POST-FAIL",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    adapter.submit_order(order)

    assert order.status == OrderStatus.REJECTED
    assert len(rejected_events) == 1
    assert "cross spread" in rejected_events[0].reason


def test_limit_maker_order_book_entry_and_resting_fill() -> None:
    """Verifies limit maker rests on the book and fills as maker upon market price movement."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(
        event_bus=bus,
        clock=clock,
        maker_fee_rate=Decimal("0.0002"),
    )

    submitted_events: list[OrderSubmittedEvent] = []
    filled_events: list[OrderFilledEvent] = []
    bus.subscribe(OrderSubmittedEvent, lambda e: submitted_events.append(e))
    bus.subscribe(OrderFilledEvent, lambda e: filled_events.append(e))

    # Spread is 99.00 / 101.00
    bus.publish(QuoteUpdatedEvent(quote=make_test_quote(bid="99.00", ask="101.00")))

    # Submit BUY LIMIT_MAKER at 99.50 (valid inside spread, post-only)
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        price=Decimal("99.50"),
        quantity=Decimal("1.0"),
        client_order_id="POST-OK",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    adapter.submit_order(order)

    assert order.status == OrderStatus.SUBMITTED
    assert len(submitted_events) == 1
    assert len(filled_events) == 0

    # Market sellers push ask down to 99.50 -> Order fills!
    bus.publish(QuoteUpdatedEvent(quote=make_test_quote(bid="99.00", ask="99.50")))

    fetched = adapter.get_order(order.order_id)
    assert fetched is not None
    assert fetched.status == OrderStatus.FILLED
    assert len(filled_events) == 1
    assert filled_events[0].fill_price == Decimal("99.50")
    assert filled_events[0].is_maker  # Captured maker fee!
    assert filled_events[0].fee == Decimal("99.50") * Decimal("0.0002")


def test_stop_loss_trigger_and_fill() -> None:
    """Verifies that STOP orders trigger when market trades across the stop price."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)

    filled_events: list[OrderFilledEvent] = []
    bus.subscribe(OrderFilledEvent, lambda e: filled_events.append(e))

    # Market at 100.00
    bus.publish(QuoteUpdatedEvent(quote=make_test_quote(bid="99.90", ask="100.10")))

    # Protective STOP SELL at 95.00
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        price=Decimal("95.00"),
        quantity=Decimal("1.0"),
        client_order_id="STOP-01",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    adapter.submit_order(order)
    assert order.status == OrderStatus.SUBMITTED

    # Market crashes to 94.00
    trade = Trade(
        symbol="BTCUSDT",
        price=Decimal("94.50"),
        quantity=Decimal("5.0"),
        side=OrderSide.SELL,
        trade_id="T-01",
        timestamp=clock.now(),
    )
    bus.publish(TradeReceivedEvent(trade=trade))

    fetched = adapter.get_order(order.order_id)
    assert fetched is not None
    assert fetched.status == OrderStatus.FILLED
    assert len(filled_events) == 1
    assert not filled_events[0].is_maker


def test_order_cancellation_and_cancel_all() -> None:
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)

    canceled_events: list[OrderCanceledEvent] = []
    bus.subscribe(OrderCanceledEvent, lambda e: canceled_events.append(e))

    order1 = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("50.00"),
        quantity=Decimal("1.0"),
        client_order_id="ORD-1",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    order2 = Order(
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        price=Decimal("1000.00"),
        quantity=Decimal("2.0"),
        client_order_id="ORD-2",
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    adapter.submit_order(order1)
    adapter.submit_order(order2)

    assert len(adapter.get_active_orders()) == 2

    # Cancel order 1
    res = adapter.cancel_order(order1.order_id)
    assert res is True
    assert order1.status == OrderStatus.CANCELED
    assert len(canceled_events) == 1
    assert len(adapter.get_active_orders()) == 1

    # Cancel all remaining
    count = adapter.cancel_all_orders()
    assert count == 1
    assert len(adapter.get_active_orders()) == 0
