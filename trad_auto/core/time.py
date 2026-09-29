"""Clock abstractions for deterministic time handling in live, paper, and backtest modes."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from trad_auto.core.constants import SYSTEM_TIMEZONE
from trad_auto.core.exceptions import BackwardClockError, TimeError


class Clock(ABC):
    """Abstract clock interface providing the authoritative internal datetime."""

    @abstractmethod
    def now(self) -> datetime:
        """Returns the current timezone-aware UTC datetime."""
        pass


class SystemClock(Clock):
    """Production clock consuming host system time converted to timezone-aware UTC."""

    def now(self) -> datetime:
        return datetime.now(SYSTEM_TIMEZONE)


class SimulatedClock(Clock):
    """Deterministic clock for backtesting and simulation with monotonic time enforcement."""

    def __init__(self, start_time: datetime) -> None:
        if not isinstance(start_time, datetime):
            raise TimeError("start_time must be a datetime instance")
        if start_time.tzinfo is None:
            raise TimeError("SimulatedClock start_time must be timezone-aware (UTC)")
        self._current_time = start_time.astimezone(SYSTEM_TIMEZONE)

    def now(self) -> datetime:
        return self._current_time

    def set_time(self, new_time: datetime) -> None:
        """Advances simulated time. Rejects backward time steps with BackwardClockError."""
        if not isinstance(new_time, datetime):
            raise TimeError("new_time must be a datetime instance")
        if new_time.tzinfo is None:
            raise TimeError("new_time must be timezone-aware (UTC)")

        utc_time = new_time.astimezone(SYSTEM_TIMEZONE)
        if utc_time < self._current_time:
            raise BackwardClockError(
                f"Clock cannot move backwards: current={self._current_time.isoformat()}, "
                f"requested={utc_time.isoformat()}"
            )
        self._current_time = utc_time

    def advance(self, duration: timedelta) -> None:
        """Advances simulated time by a given duration."""
        self.set_time(self._current_time + duration)
