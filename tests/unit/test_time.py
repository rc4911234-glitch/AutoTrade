"""Tests for Clock abstractions and monotonicity enforcement."""

from datetime import UTC, datetime, timedelta

import pytest

from trad_auto.core.constants import SYSTEM_TIMEZONE
from trad_auto.core.exceptions import BackwardClockError, TimeError
from trad_auto.core.time import SimulatedClock, SystemClock


def test_system_clock_timezone_aware() -> None:
    """SystemClock returns timezone-aware UTC datetime."""
    clock = SystemClock()
    t1 = clock.now()
    assert t1.tzinfo is not None
    assert t1.tzinfo == SYSTEM_TIMEZONE


def test_simulated_clock_monotonic_progression() -> None:
    """SimulatedClock advances forward deterministically."""
    start = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    clock = SimulatedClock(start)
    assert clock.now() == start

    next_time = start + timedelta(minutes=5)
    clock.set_time(next_time)
    assert clock.now() == next_time


def test_simulated_clock_rejects_backward_time() -> None:
    """SimulatedClock raises BackwardClockError on backward time steps."""
    start = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    clock = SimulatedClock(start)

    backward_time = start - timedelta(seconds=1)
    with pytest.raises(BackwardClockError, match="Clock cannot move backwards"):
        clock.set_time(backward_time)


def test_simulated_clock_requires_timezone_aware() -> None:
    """SimulatedClock rejects naive datetimes."""
    naive = datetime(2026, 1, 1, 9, 15)
    with pytest.raises(TimeError, match="must be timezone-aware"):
        SimulatedClock(naive)

    clock = SimulatedClock(datetime(2026, 1, 1, 9, 15, tzinfo=UTC))
    with pytest.raises(TimeError, match="must be timezone-aware"):
        clock.set_time(naive)
