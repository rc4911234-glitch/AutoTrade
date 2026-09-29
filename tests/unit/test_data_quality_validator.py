"""Unit tests for DataQualityValidator market data safety filters."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataAnomalyType, OrderSide
from trad_auto.core.events import DataAnomalyDetectedEvent
from trad_auto.core.models.market_data import DataQualityIssue, Quote, Trade
from trad_auto.core.time import SimulatedClock
from trad_auto.market_data.validator import DataQualityValidator


def test_quote_clock_drift_and_out_of_order() -> None:
    """Verifies that quotes exceeding clock drift threshold or arriving out of order are flagged."""
    bus = EventBus()
    events: list[DataQualityIssue] = []
    bus.subscribe(
        DataAnomalyDetectedEvent,
        lambda e: events.append(e.issue) if e.issue else None,
    )

    validator = DataQualityValidator(
        max_clock_drift_seconds=5.0,
        event_bus=bus,
    )
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)

    # 1. Normal quote (exact time)
    q1 = Quote(
        timestamp=t0,
        symbol="BTCUSDT",
        bid_price=Decimal("60000"),
        ask_price=Decimal("60001"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    issues = validator.validate_quote(q1, clock)
    assert len(issues) == 0
    assert len(events) == 0

    # 2. Clock drift: server clock is at 12:00:10, quote is at 12:00:00 (10s drift > 5s)
    clock.set_time(t0 + timedelta(seconds=10))
    q2 = Quote(
        timestamp=t0,
        symbol="BTCUSDT",
        bid_price=Decimal("60002"),
        ask_price=Decimal("60003"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    issues2 = validator.validate_quote(q2, clock)
    assert len(issues2) == 1
    assert issues2[0].anomaly_type == DataAnomalyType.TIMESTAMP_DRIFT
    assert len(events) == 1

    # 3. Out-of-order quote: received timestamp earlier than prior timestamp
    # prior was t0 (12:00:00), now send t0 - 1s (11:59:59)
    q_retro = Quote(
        timestamp=t0 - timedelta(seconds=1),
        symbol="BTCUSDT",
        bid_price=Decimal("60000"),
        ask_price=Decimal("60001"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    issues3 = validator.validate_quote(q_retro, clock)
    types = [i.anomaly_type for i in issues3]
    assert DataAnomalyType.OUT_OF_ORDER in types


def test_trade_price_spike_filter() -> None:
    """Verifies that aberrant trade prints deviating > 10% are detected as PRICE_SPIKE."""
    validator = DataQualityValidator(max_price_spike_pct=Decimal("0.10"))
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)

    # Base trade at $100
    t1 = Trade(
        timestamp=t0,
        symbol="SOLUSDT",
        price=Decimal("100.00"),
        quantity=Decimal("1.0"),
        side=OrderSide.BUY,
        trade_id="1",
    )
    assert len(validator.validate_trade(t1, clock)) == 0

    # Small 2% movement -> normal
    t2 = Trade(
        timestamp=t0 + timedelta(seconds=1),
        symbol="SOLUSDT",
        price=Decimal("102.00"),
        quantity=Decimal("1.0"),
        side=OrderSide.BUY,
        trade_id="2",
    )
    assert len(validator.validate_trade(t2, clock)) == 0

    # Aberrant 15% flash wick to $118 (> 10% threshold)
    t3 = Trade(
        timestamp=t0 + timedelta(seconds=2),
        symbol="SOLUSDT",
        price=Decimal("118.00"),
        quantity=Decimal("1.0"),
        side=OrderSide.BUY,
        trade_id="3",
    )
    issues = validator.validate_trade(t3, clock)
    assert len(issues) == 1
    assert issues[0].anomaly_type == DataAnomalyType.PRICE_SPIKE
    assert issues[0].severity == "CRITICAL"


def test_feed_staleness_watchdog() -> None:
    """Verifies that silence exceeding max_staleness_seconds triggers STALE_DATA alert."""
    validator = DataQualityValidator(max_staleness_seconds=15.0)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)

    # Initial quote arrives at t0
    q = Quote(
        timestamp=t0,
        symbol="ETHUSDT",
        bid_price=Decimal("3000"),
        ask_price=Decimal("3001"),
        bid_size=Decimal("1"),
        ask_size=Decimal("1"),
    )
    validator.validate_quote(q, clock)

    # Check staleness at 10 seconds (threshold: 15s) -> Healthy
    clock.set_time(t0 + timedelta(seconds=10))
    assert validator.check_feed_staleness("ETHUSDT", clock) is None

    # Check staleness at 20 seconds (> 15s) -> STALE_DATA
    clock.set_time(t0 + timedelta(seconds=20))
    stale_issue = validator.check_feed_staleness("ETHUSDT", clock)
    assert stale_issue is not None
    assert stale_issue.anomaly_type == DataAnomalyType.STALE_DATA
    assert stale_issue.severity == "CRITICAL"

    # Querying unknown symbol returns None
    assert validator.check_feed_staleness("UNKNOWN", clock) is None


def test_validator_parameter_validations() -> None:
    """Verifies that validator rejects non-positive thresholds."""
    with pytest.raises(ValueError, match="max_price_spike_pct"):
        DataQualityValidator(max_price_spike_pct=Decimal("0"))

    with pytest.raises(ValueError, match="max_clock_drift_seconds"):
        DataQualityValidator(max_clock_drift_seconds=-1.0)

    with pytest.raises(ValueError, match="max_staleness_seconds"):
        DataQualityValidator(max_staleness_seconds=0.0)
