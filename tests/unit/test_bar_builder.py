"""Unit tests for BarBuilder multi-timeframe candle aggregator."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import OrderSide
from trad_auto.core.events import BarCompletedEvent, BarUpdatedEvent
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Trade
from trad_auto.market_data.bar_builder import (
    BarBuilder,
    calculate_bar_window,
    timeframe_to_seconds,
)
from trad_auto.market_data.store import BarStore


def make_trade(
    price: str,
    quantity: str,
    timestamp: datetime,
    symbol: str = "BTCUSDT",
    trade_id: str = "1",
) -> Trade:
    return Trade(
        timestamp=timestamp,
        symbol=symbol,
        price=Decimal(price),
        quantity=Decimal(quantity),
        side=OrderSide.BUY,
        trade_id=trade_id,
    )


def test_timeframe_helpers() -> None:
    """Verifies timeframe parsing and window calculation."""
    assert timeframe_to_seconds("1m") == 60
    assert timeframe_to_seconds("5m") == 300
    assert timeframe_to_seconds("1h") == 3600
    assert timeframe_to_seconds("1d") == 86400

    with pytest.raises(DomainValidationError, match="Unsupported timeframe"):
        timeframe_to_seconds("2m")

    # Align 12:03:45 to 1-minute window -> [12:03:00, 12:04:00)
    t = datetime(2026, 1, 1, 12, 3, 45, tzinfo=UTC)
    start_1m, end_1m = calculate_bar_window(t, 60)
    assert start_1m == datetime(2026, 1, 1, 12, 3, 0, tzinfo=UTC)
    assert end_1m == datetime(2026, 1, 1, 12, 4, 0, tzinfo=UTC)

    # Align 12:03:45 to 5-minute window -> [12:00:00, 12:05:00)
    start_5m, end_5m = calculate_bar_window(t, 300)
    assert start_5m == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert end_5m == datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)


def test_intrabar_tick_aggregation() -> None:
    """Verifies multi-tick accumulation within a single candle window."""
    builder = BarBuilder(timeframes=["1m"])
    base_t = datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC)

    # Tick 1 (Opens bar)
    builder.on_trade(make_trade("100.0", "1.0", base_t, trade_id="1"))
    active = builder.get_active_bar("BTCUSDT", "1m")
    assert active is not None
    assert active.open == Decimal("100.0")
    assert active.high == Decimal("100.0")
    assert active.low == Decimal("100.0")
    assert active.close == Decimal("100.0")
    assert active.volume == Decimal("1.0")

    # Tick 2 (Higher price)
    builder.on_trade(make_trade("105.0", "0.5", base_t + timedelta(seconds=10), trade_id="2"))
    active = builder.get_active_bar("BTCUSDT", "1m")
    assert active is not None
    assert active.high == Decimal("105.0")
    assert active.low == Decimal("100.0")
    assert active.close == Decimal("105.0")
    assert active.volume == Decimal("1.5")

    # Tick 3 (Lower price)
    builder.on_trade(make_trade("95.0", "2.0", base_t + timedelta(seconds=20), trade_id="3"))
    active = builder.get_active_bar("BTCUSDT", "1m")
    assert active is not None
    assert active.open == Decimal("100.0")
    assert active.high == Decimal("105.0")
    assert active.low == Decimal("95.0")
    assert active.close == Decimal("95.0")
    assert active.volume == Decimal("3.5")


def test_bar_closure_and_event_dispatch() -> None:
    """Verifies that crossing period boundary finalizes the bar and dispatches events."""
    bus = EventBus()
    store = BarStore()
    completed_events: list[Bar] = []
    updated_events: list[Bar] = []

    bus.subscribe(
        BarCompletedEvent,
        lambda e: completed_events.append(e.bar) if e.bar else None,
    )
    bus.subscribe(
        BarUpdatedEvent,
        lambda e: updated_events.append(e.bar) if e.bar else None,
    )

    builder = BarBuilder(timeframes=["1m"], event_bus=bus, bar_store=store)
    t0 = datetime(2026, 1, 1, 12, 0, 15, tzinfo=UTC)

    # Ingest ticks in minute 12:00
    builder.on_trade(make_trade("50000", "1.0", t0, trade_id="1"))
    builder.on_trade(make_trade("50100", "2.0", t0 + timedelta(seconds=15), trade_id="2"))
    assert len(completed_events) == 0
    assert len(updated_events) == 2

    # Tick in minute 12:01 -> triggers closure of 12:00 candle
    t1 = datetime(2026, 1, 1, 12, 1, 5, tzinfo=UTC)
    completed = builder.on_trade(make_trade("50200", "0.5", t1, trade_id="3"))

    assert len(completed) == 1
    assert completed[0].open == Decimal("50000")
    assert completed[0].high == Decimal("50100")
    assert completed[0].low == Decimal("50000")
    assert completed[0].close == Decimal("50100")
    assert completed[0].volume == Decimal("3.0")
    assert completed[0].timestamp == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # Event dispatch and store verification
    assert len(completed_events) == 1
    assert completed_events[0] == completed[0]
    assert store.bar_count("BTCUSDT", "1m") == 1
    assert store.get_latest_bar("BTCUSDT", "1m") == completed[0]

    # Active bar is now the 12:01 candle
    active = builder.get_active_bar("BTCUSDT", "1m")
    assert active is not None
    assert active.timestamp == datetime(2026, 1, 1, 12, 1, 0, tzinfo=UTC)
    assert active.open == Decimal("50200")


def test_multi_timeframe_synthesis() -> None:
    """Verifies that a single stream of trades simultaneously updates 1m and 5m bars."""
    builder = BarBuilder(timeframes=["1m", "5m"])
    base_t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # Feed 6 ticks spaced 1 minute apart
    all_completed: list[Bar] = []
    for m in range(6):
        trade = make_trade(f"{100 + m}", "1.0", base_t + timedelta(minutes=m, seconds=10))
        completed = builder.on_trade(trade)
        all_completed.extend(completed)

    # At minute 5:
    # - 1m bars completed for minutes 0, 1, 2, 3, 4 (5 bars)
    # - 5m bar completed for interval 12:00-12:05 (1 bar)
    one_min_bars = [b for b in all_completed if b.timeframe == "1m"]
    five_min_bars = [b for b in all_completed if b.timeframe == "5m"]

    assert len(one_min_bars) == 5
    assert len(five_min_bars) == 1

    closed_5m = five_min_bars[0]
    assert closed_5m.timestamp == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert closed_5m.open == Decimal("100")
    assert closed_5m.close == Decimal("104")
    assert closed_5m.volume == Decimal("5.0")  # Minutes 0, 1, 2, 3, 4


def test_flush_active_bars() -> None:
    """Verifies flush closes and returns in-progress bars."""
    builder = BarBuilder(timeframes=["1m"])
    t = datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC)
    builder.on_trade(make_trade("100", "1.0", t))

    assert builder.get_active_bar("BTCUSDT", "1m") is not None
    flushed = builder.flush()
    assert len(flushed) == 1
    assert flushed[0].close == Decimal("100")
    assert builder.get_active_bar("BTCUSDT", "1m") is None


def test_out_of_order_trade_rejection() -> None:
    """Verifies trade tick arriving earlier than current bar start raises error."""
    builder = BarBuilder(timeframes=["1m"])
    t0 = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
    builder.on_trade(make_trade("100", "1.0", t0))

    # Older tick arriving (e.g. from 12:04:30)
    t_old = datetime(2026, 1, 1, 12, 4, 30, tzinfo=UTC)
    with pytest.raises(DomainValidationError, match="Out-of-order trade tick"):
        builder.on_trade(make_trade("99", "1.0", t_old))
