"""Unit tests for macroeconomic event calendar and blackout window calculations."""

from datetime import UTC, datetime, timedelta

import pytest

from trad_auto.core.enums import NewsImpactLevel
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.news import MacroEconomicEvent
from trad_auto.news.calendar import MacroCalendarManager


def test_macro_event_validation_rules() -> None:
    """Verifies MacroEconomicEvent model validation constraints."""
    release_time = datetime(2026, 9, 23, 12, 30, tzinfo=UTC)

    # Valid event
    ev = MacroEconomicEvent(
        title="US Consumer Price Index (CPI)",
        scheduled_at=release_time,
        impact=NewsImpactLevel.HIGH,
        cool_off_pre_minutes=15,
        cool_off_post_minutes=15,
        affected_symbols=["*"],
    )
    assert ev.title == "US Consumer Price Index (CPI)"
    assert ev.cool_off_pre_minutes == 15
    assert ev.cool_off_post_minutes == 15

    # Empty title
    with pytest.raises(DomainValidationError, match="title must be non-empty"):
        MacroEconomicEvent(title="  ", scheduled_at=release_time)

    # Naive timestamp
    naive_time = datetime(2026, 9, 23, 12, 30)
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        MacroEconomicEvent(title="US CPI", scheduled_at=naive_time)

    # Negative cool-off minutes
    with pytest.raises(DomainValidationError, match="non-negative"):
        MacroEconomicEvent(title="US CPI", scheduled_at=release_time, cool_off_pre_minutes=-5)


def test_blackout_window_time_bounds() -> None:
    """Tests exact boundary conditions for blackout window start and end."""
    release_time = datetime(2026, 9, 23, 12, 30, tzinfo=UTC)
    event = MacroEconomicEvent(
        title="Federal Reserve FOMC Rate Decision",
        scheduled_at=release_time,
        cool_off_pre_minutes=15,
        cool_off_post_minutes=15,
    )

    # Start: 12:15, End: 12:45
    assert event.blackout_start_time() == datetime(2026, 9, 23, 12, 15, tzinfo=UTC)
    assert event.blackout_end_time() == datetime(2026, 9, 23, 12, 45, tzinfo=UTC)

    # Before window (12:14:59)
    assert not event.is_in_blackout_window(datetime(2026, 9, 23, 12, 14, 59, tzinfo=UTC))

    # At exact start (12:15:00)
    assert event.is_in_blackout_window(datetime(2026, 9, 23, 12, 15, 0, tzinfo=UTC))

    # In middle at release (12:30:00)
    assert event.is_in_blackout_window(datetime(2026, 9, 23, 12, 30, 0, tzinfo=UTC))

    # At exact end (12:45:00)
    assert event.is_in_blackout_window(datetime(2026, 9, 23, 12, 45, 0, tzinfo=UTC))

    # After window (12:45:01)
    assert not event.is_in_blackout_window(datetime(2026, 9, 23, 12, 45, 1, tzinfo=UTC))


def test_symbol_impact_targeting() -> None:
    """Verifies wildcard and specific symbol impact routing."""
    release_time = datetime(2026, 9, 23, 12, 30, tzinfo=UTC)

    # Wildcard impacts all
    global_event = MacroEconomicEvent(
        title="Non-Farm Payrolls (NFP)",
        scheduled_at=release_time,
        affected_symbols=["*"],
    )
    assert global_event.affects_symbol("BTCUSDT")
    assert global_event.affects_symbol("ETHUSDT")

    # Specific symbol
    sol_event = MacroEconomicEvent(
        title="Solana Foundation Ecosystem Token Unlock",
        scheduled_at=release_time,
        affected_symbols=["SOLUSDT"],
    )
    assert sol_event.affects_symbol("SOLUSDT")
    assert sol_event.affects_symbol("solusdt")  # Case insensitive
    assert not sol_event.affects_symbol("BTCUSDT")


def test_calendar_manager_crud_and_sorting() -> None:
    """Tests registering, sorting, and unregistering events in calendar."""
    t1 = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    ev1 = MacroEconomicEvent(title="Later Event", scheduled_at=t1)
    ev2 = MacroEconomicEvent(title="Earlier Event", scheduled_at=t2)

    cal = MacroCalendarManager()
    cal.register_event(ev1)
    cal.register_event(ev2)

    assert cal.event_count == 2
    events = cal.list_events()
    assert events[0].title == "Earlier Event"
    assert events[1].title == "Later Event"

    # Unregister
    assert cal.unregister_event(ev1.event_id) is True
    assert cal.event_count == 1
    assert cal.unregister_event(ev1.event_id) is False


def test_calendar_manager_blackout_queries() -> None:
    """Tests querying active blackouts and upcoming events across time."""
    now = datetime(2026, 9, 23, 13, 0, tzinfo=UTC)
    fomc_time = now + timedelta(minutes=10)  # 13:10 (pre-buffer 15m means blackout starts 12:55)
    cpi_time = now + timedelta(hours=3)  # 16:00

    fomc = MacroEconomicEvent(
        title="FOMC Interest Rate Decision",
        scheduled_at=fomc_time,
        cool_off_pre_minutes=15,
        cool_off_post_minutes=15,
    )
    cpi = MacroEconomicEvent(
        title="CPI Release",
        scheduled_at=cpi_time,
        cool_off_pre_minutes=15,
        cool_off_post_minutes=15,
    )

    cal = MacroCalendarManager(events=[fomc, cpi])

    # At 13:00, FOMC blackout is active (12:55 to 13:25)
    assert cal.is_in_blackout(now) is True
    active = cal.get_active_blackout_events(now)
    assert len(active) == 1
    assert active[0].title == "FOMC Interest Rate Decision"

    # Next upcoming after 13:00 is FOMC at 13:10
    next_ev = cal.next_upcoming_event(now)
    assert next_ev is not None
    assert next_ev.title == "FOMC Interest Rate Decision"

    # At 13:30, FOMC blackout is over, CPI is upcoming
    after_fomc = now + timedelta(minutes=30)
    assert cal.is_in_blackout(after_fomc) is False
    next_after = cal.next_upcoming_event(after_fomc)
    assert next_after is not None
    assert next_after.title == "CPI Release"

    # Clear calendar
    cal.clear()
    assert cal.event_count == 0
    assert cal.is_in_blackout(now) is False
    assert cal.next_upcoming_event(now) is None
