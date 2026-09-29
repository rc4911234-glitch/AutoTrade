"""Unit and integration tests for OrderManager and bracket stop-loss coordination."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SessionState,
)
from trad_auto.core.events import (
    QuoteUpdatedEvent,
    TradeIntentCreatedEvent,
    TradingSessionStateChangedEvent,
)
from trad_auto.core.models.intent import TradeIntent
from trad_auto.core.models.market_data import Quote
from trad_auto.core.models.order import Order
from trad_auto.core.time import SimulatedClock
from trad_auto.execution.order_manager import OrderManager
from trad_auto.execution.simulated_adapter import SimulatedExecutionAdapter
from trad_auto.portfolio.ledger import PositionLedger


def make_quote(symbol: str, bid: str, ask: str, ts: datetime) -> Quote:
    return Quote(
        symbol=symbol,
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("10.0"),
        ask_size=Decimal("10.0"),
        timestamp=ts,
    )


def test_order_manager_entry_fill_triggers_bracket_stop_and_take_profit() -> None:
    """Verifies that entry fill spawns linked bracket stop-loss and take-profit orders."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    _manager = OrderManager(event_bus=bus, adapter=adapter, ledger=ledger, clock=clock)
    assert _manager is not None

    # Top-of-book market quote
    t0 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "99.00", "101.00", t0)))

    # Approved TradeIntent arrives: Entry 99.50, Stop 90.00, TP 119.00 (Risk:Reward = 1:2)
    intent = TradeIntent(
        proposal_id=uuid4(),
        strategy_id="breakout_v1",
        symbol="BTCUSDT",
        direction=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        entry_price=Decimal("99.50"),
        quantity=Decimal("1.0"),
        stop_loss=Decimal("90.00"),
        take_profit=Decimal("119.00"),
        timeframe="1h",
        created_at=t0,
        expires_at=t0 + timedelta(minutes=5),
    )
    bus.publish(TradeIntentCreatedEvent(intent=intent))

    # Entry order is now resting on the book
    active_orders = adapter.get_active_orders()
    assert len(active_orders) == 1
    entry_order = active_orders[0]
    assert entry_order.order_type == OrderType.LIMIT_MAKER
    assert entry_order.price == Decimal("99.50")
    assert entry_order.action_purpose == OrderActionPurpose.NEW_ENTRY

    # No position yet in ledger
    assert ledger.get_position("BTCUSDT") is None

    # Market sellers push ask down to 99.50 -> Entry order fills!
    clock.advance(timedelta(seconds=30))
    t1 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "99.00", "99.50", t1)))

    # 1. Entry order is FILLED
    assert entry_order.status == OrderStatus.FILLED

    # 2. Position is OPEN in ledger
    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.is_open
    assert pos.side == PositionSide.LONG
    assert pos.quantity == Decimal("1.0")
    assert pos.average_entry_price == Decimal("99.50")

    # 3. Exactly 2 bracket orders are now active: STOP at 90.00 and TP at 119.00
    active_bracket_orders = adapter.get_active_orders()
    assert len(active_bracket_orders) == 2

    stop_order = next(o for o in active_bracket_orders if o.order_type == OrderType.STOP)
    tp_order = next(o for o in active_bracket_orders if o.order_type == OrderType.LIMIT_MAKER)

    assert stop_order.price == Decimal("90.00")
    assert stop_order.side == OrderSide.SELL
    assert stop_order.action_purpose == OrderActionPurpose.EXIT

    assert tp_order.price == Decimal("119.00")
    assert tp_order.side == OrderSide.SELL
    assert tp_order.action_purpose == OrderActionPurpose.EXIT

    # 4. Take-Profit fills at 119.00 -> Bracket OCO cancels the STOP order!
    clock.advance(timedelta(minutes=10))
    t2 = clock.now()
    # Market bid rises to 119.00
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "119.00", "119.50", t2)))

    # TP order is FILLED
    assert tp_order.status == OrderStatus.FILLED

    # Stop order must be automatically CANCELED via OCO!
    assert stop_order.status == OrderStatus.CANCELED
    assert len(adapter.get_active_orders()) == 0

    # Position in ledger is fully closed with realized profit!
    assert ledger.get_position("BTCUSDT") is None
    assert ledger.get_today_realized_pnl(current_date=t2.date()) > ZERO_DECIMAL


def test_order_manager_emergency_stop_cancels_entries_and_flattens() -> None:
    """Verifies that EMERGENCY_STOP cancels speculative orders and flattens open positions."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("1000.00"))

    _manager = OrderManager(event_bus=bus, adapter=adapter, ledger=ledger, clock=clock)
    assert _manager is not None

    t0 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "100.00", "100.00", t0)))

    # 1. Have an open position in ledger
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("100.00"),
        quantity=Decimal("1.5"),
        fee=ZERO_DECIMAL,
        timestamp=t0,
    )
    assert ledger.get_position("BTCUSDT") is not None

    # 2. Have a resting speculative buy order
    spec_order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        price=Decimal("95.00"),
        quantity=Decimal("1.0"),
        client_order_id="SPEC-1",
        action_purpose=OrderActionPurpose.NEW_ENTRY,
        created_at=t0,
        updated_at=t0,
    )
    adapter.submit_order(spec_order)
    assert spec_order.status == OrderStatus.SUBMITTED

    # 3. Emergency stop triggered!
    bus.publish(
        TradingSessionStateChangedEvent(
            old_state=SessionState.TRADING,
            new_state=SessionState.EMERGENCY_STOP,
            reason="Kill switch pressed",
        )
    )

    # Speculative order must be CANCELED
    fetched = adapter.get_order(spec_order.order_id)
    assert fetched is not None
    assert fetched.status == OrderStatus.CANCELED

    # Open position must be FLATTENED (closed) via market exit order!
    assert ledger.get_position("BTCUSDT") is None


def test_order_manager_cancels_stale_unfilled_entry_order() -> None:
    """Verifies that unfilled LIMIT_MAKER entry orders are canceled after entry_timeout_seconds."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)
    ledger = PositionLedger(event_bus=bus, clock=clock)

    manager = OrderManager(
        event_bus=bus,
        adapter=adapter,
        ledger=ledger,
        clock=clock,
        entry_timeout_seconds=45.0,
    )
    assert manager is not None

    t0 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "49900.0", "50100.0", t0)))

    # Submit an entry order
    order = Order(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        price=Decimal("49950.00"),
        quantity=Decimal("1.0"),
        client_order_id="ENTRY-STALE-1",
        action_purpose=OrderActionPurpose.NEW_ENTRY,
        created_at=t0,
        updated_at=t0,
    )
    adapter.submit_order(order)
    assert order.status == OrderStatus.SUBMITTED

    # Advance clock by 30 seconds (not stale yet)
    clock.advance(timedelta(seconds=30))
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "49920.0", "50100.0", clock.now())))
    active_entry = adapter.get_order(order.order_id)
    assert active_entry is not None
    assert active_entry.status == OrderStatus.SUBMITTED

    # Advance clock past 45s timeout (total 50s elapsed) -> Adverse selection reaper cancels it!
    clock.advance(timedelta(seconds=20))
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "49930.0", "50100.0", clock.now())))

    fetched = adapter.get_order(order.order_id)
    assert fetched is not None
    assert fetched.status == OrderStatus.CANCELED


def test_order_manager_orphan_watchdog_flattens_unshielded_position() -> None:
    """Verifies that orphan positions without protective stop loss are automatically flattened."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("10000.00"))

    manager = OrderManager(
        event_bus=bus,
        adapter=adapter,
        ledger=ledger,
        clock=clock,
        enable_orphan_watchdog=True,
    )
    assert manager is not None

    t0 = clock.now()
    # Unshielded position opened directly (e.g. following network drop before bracket placement)
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("50000.00"),
        quantity=Decimal("1.0"),
        fee=ZERO_DECIMAL,
        timestamp=t0,
    )
    assert ledger.get_position("BTCUSDT") is not None
    assert len(adapter.get_active_orders()) == 0  # Zero stop-loss orders exist!

    # Quote update triggers orphan watchdog check
    clock.advance(timedelta(seconds=5))
    bus.publish(QuoteUpdatedEvent(quote=make_quote("BTCUSDT", "50000.0", "50010.0", clock.now())))

    # Watchdog immediately submitted a MARKET exit order and flattened the unshielded position!
    assert ledger.get_position("BTCUSDT") is None

