"""Unit tests for Freqtrade-inspired dynamic breakeven and trailing stop ratchet."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import (
    OrderSide,
    OrderType,
    PositionSide,
)
from trad_auto.core.events import (
    QuoteUpdatedEvent,
    TradeIntentCreatedEvent,
)
from trad_auto.core.models.intent import TradeIntent
from trad_auto.core.models.market_data import Quote
from trad_auto.core.time import SimulatedClock
from trad_auto.execution.order_manager import OrderManager
from trad_auto.execution.simulated_adapter import SimulatedExecutionAdapter
from trad_auto.portfolio.ledger import PositionLedger


def _make_quote(symbol: str, bid: str, ask: str, ts: datetime) -> Quote:
    return Quote(
        symbol=symbol,
        bid_price=Decimal(bid),
        ask_price=Decimal(ask),
        bid_size=Decimal("10.0"),
        ask_size=Decimal("10.0"),
        timestamp=ts,
    )


def test_trailing_stop_breakeven_lock_and_trailing_ratchet_long() -> None:
    """Verifies that Long position ratchets to Breakeven (+0.75%) and Trails behind Peak (+1.2%)."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    adapter = SimulatedExecutionAdapter(event_bus=bus, clock=clock)
    ledger = PositionLedger(event_bus=bus, clock=clock)
    ledger.set_initial_balance("USDT", Decimal("10000.00"))

    # Initialize OrderManager with dynamic trailing stop enabled
    manager = OrderManager(
        event_bus=bus,
        adapter=adapter,
        ledger=ledger,
        clock=clock,
        enable_trailing_stop=True,
        breakeven_offset_ratio=Decimal("0.0075"),  # +0.75% activates Breakeven
        breakeven_fee_buffer=Decimal("0.0008"),  # +0.08% fee cushion
        trailing_offset_ratio=Decimal("0.012"),  # +1.20% activates trailing stop
        trailing_delta_ratio=Decimal("0.005"),  # 0.50% trail behind peak
    )
    assert manager is not None

    t0 = clock.now()
    # Initial market price: Bid 49900, Ask 50100 (resting book)
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49900.0", "50100.0", t0)))

    # Entry at 50,000, initial Stop Loss at 49,000 (2% risk), TP at 53,000
    intent = TradeIntent(
        proposal_id=uuid4(),
        strategy_id="scalper_btc",
        symbol="BTCUSDT",
        direction=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        entry_price=Decimal("50000.00"),
        quantity=Decimal("1.0"),
        stop_loss=Decimal("49000.00"),
        take_profit=Decimal("53000.00"),
        timeframe="1m",
        created_at=t0,
        expires_at=t0 + timedelta(minutes=5),
    )
    bus.publish(TradeIntentCreatedEvent(intent=intent))

    # Fill entry order
    clock.advance(timedelta(seconds=10))
    t1 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49990.0", "50000.0", t1)))

    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.is_open

    # Check active stop loss order
    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    assert active_stops[0].price == Decimal("49000.00")

    # 1. Price rises to $50,400 (+0.80% profit >= +0.75% Breakeven threshold)
    clock.advance(timedelta(minutes=1))
    t2 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "50400.0", "50400.0", t2)))

    # Stop Loss MUST ratchet to Breakeven + fee buffer ($50,000 * 1.0008 = $50,040)
    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    be_stop = active_stops[0]
    expected_be_price = Decimal("50000.00") * (Decimal("1.0") + Decimal("0.0008"))
    assert be_stop.price == expected_be_price
    assert be_stop.price > Decimal("50000.00")  # Position is now 100% risk-free!

    # 2. Price surges to $51,000 (+2.0% profit >= +1.20% Trailing threshold)
    clock.advance(timedelta(minutes=2))
    t3 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "51000.0", "51000.0", t3)))

    # Stop Loss MUST trail 0.5% behind peak ($51,000 * 0.995 = $50,745)
    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    trailing_stop = active_stops[0]
    expected_trailing_price = Decimal("51000.00") * (Decimal("1.0") - Decimal("0.005"))
    assert trailing_stop.price == expected_trailing_price
    assert trailing_stop.price == Decimal("50745.000")

    # 3. Price pulls back slightly to $50,850. Stop Loss MUST NOT move down (one-way ratchet)!
    clock.advance(timedelta(minutes=1))
    t4 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "50850.0", "50850.0", t4)))

    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    assert active_stops[0].price == expected_trailing_price  # Preserved at peak trail!


def test_trailing_stop_breakeven_lock_and_trailing_ratchet_short() -> None:
    """Verifies that Short position ratchets to Breakeven (+0.75%)
    and Trails behind Peak (+1.2%).
    """
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
        enable_trailing_stop=True,
        breakeven_offset_ratio=Decimal("0.0075"),
        breakeven_fee_buffer=Decimal("0.0008"),
        trailing_offset_ratio=Decimal("0.012"),
        trailing_delta_ratio=Decimal("0.005"),
    )
    assert manager is not None

    t0 = clock.now()
    # Initial market price: Bid 49900, Ask 50100
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49900.0", "50100.0", t0)))

    # Short entry at 50,000, initial Stop Loss at 51,000 (2% risk), TP at 47,000
    intent = TradeIntent(
        proposal_id=uuid4(),
        strategy_id="scalper_btc",
        symbol="BTCUSDT",
        direction=OrderSide.SELL,
        order_type=OrderType.LIMIT_MAKER,
        entry_price=Decimal("50000.00"),
        quantity=Decimal("1.0"),
        stop_loss=Decimal("51000.00"),
        take_profit=Decimal("47000.00"),
        timeframe="1m",
        created_at=t0,
        expires_at=t0 + timedelta(minutes=5),
    )
    bus.publish(TradeIntentCreatedEvent(intent=intent))

    clock.advance(timedelta(seconds=10))
    t1 = clock.now()
    # Bid reaches 50000 -> short entry fills!
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "50000.0", "50100.0", t1)))

    pos = ledger.get_position("BTCUSDT")
    assert pos is not None
    assert pos.side == PositionSide.SHORT

    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    assert active_stops[0].price == Decimal("51000.00")

    # 1. Price drops to $49,600 (+0.80% profit for short >= +0.75% Breakeven threshold)
    clock.advance(timedelta(minutes=1))
    t2 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49600.0", "49600.0", t2)))

    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    expected_be_price = Decimal("50000.00") * (Decimal("1.0") - Decimal("0.0008"))
    assert active_stops[0].price == expected_be_price
    assert active_stops[0].price < Decimal("50000.00")

    # 2. Price plunges to $49,000 (+2.0% profit for short >= +1.20% Trailing threshold)
    clock.advance(timedelta(minutes=2))
    t3 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49000.0", "49000.0", t3)))

    active_stops = [
        o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP
    ]
    assert len(active_stops) == 1
    expected_trailing_price = Decimal("49000.00") * (Decimal("1.0") + Decimal("0.005"))
    assert active_stops[0].price == expected_trailing_price
    assert active_stops[0].price == Decimal("49245.000")


def test_trailing_stop_throttles_micro_ticks_rate_limit_protection() -> None:
    """Verifies that micro price ticks do NOT trigger excessive order cancel/replaces."""
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
        enable_trailing_stop=True,
        trailing_offset_ratio=Decimal("0.010"),  # +1.0% activates trail
        trailing_delta_ratio=Decimal("0.005"),  # 0.5% behind peak
        trailing_min_ratchet_pct=Decimal("0.001"),  # Require >= 0.10% improvement to ratchet
    )
    assert manager is not None

    t0 = clock.now()
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49900.0", "50100.0", t0)))

    intent = TradeIntent(
        proposal_id=uuid4(),
        strategy_id="scalper_btc",
        symbol="BTCUSDT",
        direction=OrderSide.BUY,
        order_type=OrderType.LIMIT_MAKER,
        entry_price=Decimal("50000.00"),
        quantity=Decimal("1.0"),
        stop_loss=Decimal("49000.00"),
        take_profit=Decimal("55000.00"),
        timeframe="1m",
        created_at=t0,
        expires_at=t0 + timedelta(minutes=5),
    )
    bus.publish(TradeIntentCreatedEvent(intent=intent))

    # Fill entry
    clock.advance(timedelta(seconds=5))
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "49990.0", "50000.0", clock.now())))

    # Price jumps to 51000 (+2.0% profit -> initial trail ratchet: 51000 * 0.995 = 50745)
    clock.advance(timedelta(seconds=10))
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "51000.0", "51000.0", clock.now())))
    stop1 = [o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP][0]
    assert stop1.price == Decimal("50745.000")

    # Now a tiny micro-tick occurs: price rises by $5 to 51005 (0.0098% change < 0.10% min ratchet)
    clock.advance(timedelta(seconds=1))
    bus.publish(QuoteUpdatedEvent(quote=_make_quote("BTCUSDT", "51005.0", "51005.0", clock.now())))
    stop2 = [o for o in adapter.get_active_orders() if o.order_type == OrderType.STOP][0]

    # Stop order must NOT have been cancelled or replaced!
    assert stop2.order_id == stop1.order_id
    assert stop2.price == Decimal("50745.000")
