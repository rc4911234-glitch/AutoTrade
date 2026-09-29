"""Unit tests for FeedWatchdog and staleness circuit breaker."""

from datetime import UTC, datetime, timedelta

import pytest

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataAnomalyType, DataFeedStatus
from trad_auto.core.events import DataAnomalyDetectedEvent, DataFeedStatusChangedEvent
from trad_auto.core.time import SimulatedClock
from trad_auto.market_data.streaming.watchdog import FeedWatchdog


def test_watchdog_init_validation() -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        FeedWatchdog(timeout_seconds=0.0)

    with pytest.raises(ValueError, match="strictly positive"):
        FeedWatchdog(timeout_seconds=-5.0)


def test_watchdog_tracking_and_activity() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    event_bus = EventBus()
    watchdog = FeedWatchdog(timeout_seconds=15.0, event_bus=event_bus, clock=clock)

    watchdog.track_symbol("BTCUSDT")
    assert "BTCUSDT" in watchdog.tracked_symbols
    assert watchdog.get_status("BTCUSDT").value == DataFeedStatus.CONNECTED.value
    assert watchdog.last_heartbeat("BTCUSDT") is None

    # Record activity
    watchdog.record_activity("BTCUSDT", t0)
    assert watchdog.last_heartbeat("BTCUSDT") == t0
    assert not watchdog.is_stale("BTCUSDT")


def test_watchdog_detects_staleness_and_emits_events() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    event_bus = EventBus()

    anomalies: list[DataAnomalyDetectedEvent] = []
    feed_status_events: list[DataFeedStatusChangedEvent] = []

    event_bus.subscribe(DataAnomalyDetectedEvent, lambda e: anomalies.append(e))
    event_bus.subscribe(DataFeedStatusChangedEvent, lambda e: feed_status_events.append(e))

    watchdog = FeedWatchdog(timeout_seconds=15.0, event_bus=event_bus, clock=clock)
    watchdog.track_symbol("BTCUSDT")
    watchdog.record_activity("BTCUSDT", t0)

    # Advance clock by 10s (not stale)
    clock.advance(timedelta(seconds=10))
    assert not watchdog.is_stale("BTCUSDT")
    issues = watchdog.check_staleness()
    assert len(issues) == 0
    assert watchdog.get_status("BTCUSDT").value == DataFeedStatus.CONNECTED.value

    # Advance clock by another 6s (total 16s > 15s timeout -> STALE)
    clock.advance(timedelta(seconds=6))
    assert watchdog.is_stale("BTCUSDT")
    issues = watchdog.check_staleness()

    assert len(issues) == 1
    assert issues[0].symbol == "BTCUSDT"
    assert issues[0].anomaly_type.value == DataAnomalyType.STALE_DATA.value
    assert issues[0].severity == "CRITICAL"
    assert watchdog.get_status("BTCUSDT").value == DataFeedStatus.STALE.value

    # Verify event bus notifications
    assert len(anomalies) == 1
    assert anomalies[0].issue is not None
    assert anomalies[0].issue.anomaly_type.value == DataAnomalyType.STALE_DATA.value

    assert len(feed_status_events) == 1
    assert feed_status_events[0].symbol == "BTCUSDT"
    assert feed_status_events[0].new_status.value == DataFeedStatus.STALE.value

    # Subsequent check while still stale should return issue but not duplicate feed status event
    issues_repeat = watchdog.check_staleness()
    assert len(issues_repeat) == 1
    assert len(feed_status_events) == 1


def test_watchdog_recovers_when_activity_resumes() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    event_bus = EventBus()

    feed_status_events: list[DataFeedStatusChangedEvent] = []
    event_bus.subscribe(DataFeedStatusChangedEvent, lambda e: feed_status_events.append(e))

    watchdog = FeedWatchdog(timeout_seconds=15.0, event_bus=event_bus, clock=clock)
    watchdog.track_symbol("ETHUSDT")
    watchdog.record_activity("ETHUSDT", t0)

    # Stale trigger
    clock.advance(timedelta(seconds=20))
    watchdog.check_staleness()
    assert watchdog.get_status("ETHUSDT").value == DataFeedStatus.STALE.value
    assert len(feed_status_events) == 1
    assert feed_status_events[0].new_status.value == DataFeedStatus.STALE.value

    # Now activity resumes
    t_recovered = t0 + timedelta(seconds=21)
    watchdog.record_activity("ETHUSDT", t_recovered)

    assert watchdog.get_status("ETHUSDT").value == DataFeedStatus.CONNECTED.value
    assert not watchdog.is_stale("ETHUSDT", now=t_recovered)
    assert len(feed_status_events) == 2
    assert feed_status_events[1].new_status.value == DataFeedStatus.CONNECTED.value
    assert "recovered" in feed_status_events[1].reason


def test_watchdog_untrack_and_reset() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    watchdog = FeedWatchdog(timeout_seconds=15.0, clock=clock)

    watchdog.track_symbol("SOLUSDT")
    watchdog.record_activity("SOLUSDT", t0)
    assert watchdog.last_heartbeat("SOLUSDT") is not None

    watchdog.reset()
    assert watchdog.last_heartbeat("SOLUSDT") is None
    assert watchdog.get_status("SOLUSDT").value == DataFeedStatus.CONNECTED.value

    watchdog.untrack_symbol("SOLUSDT")
    assert "SOLUSDT" not in watchdog.tracked_symbols
    assert watchdog.get_status("SOLUSDT").value == DataFeedStatus.DISCONNECTED.value
