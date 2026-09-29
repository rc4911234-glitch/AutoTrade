"""Feed watchdog monitoring WebSocket stream staleness and heartbeat health."""

from datetime import UTC, datetime

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataAnomalyType, DataFeedStatus
from trad_auto.core.events import DataAnomalyDetectedEvent, DataFeedStatusChangedEvent
from trad_auto.core.models.market_data import DataQualityIssue
from trad_auto.core.time import Clock, SystemClock


class FeedWatchdog:
    """Monitors real-time data feeds for inactivity, silent drops, and staleness.

    Guarantees 'Capital Protection First':
    If exchange feeds stall beyond the configured timeout threshold (default: 15s),
    this watchdog triggers a CRITICAL DataQualityIssue and transitions the feed
    status to STALE, halting downstream trade entries.
    """

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        event_bus: EventBus | None = None,
        clock: Clock | None = None,
    ) -> None:
        if timeout_seconds <= 0.0:
            raise ValueError("timeout_seconds must be strictly positive")

        self.timeout_seconds = timeout_seconds
        self._event_bus = event_bus
        self._clock = clock or SystemClock()

        self._tracked_symbols: set[str] = set()
        self._last_heartbeats: dict[str, datetime] = {}
        self._statuses: dict[str, DataFeedStatus] = {}

    @property
    def tracked_symbols(self) -> set[str]:
        """Returns the set of actively monitored symbols."""
        return set(self._tracked_symbols)

    def track_symbol(self, symbol: str) -> None:
        """Enrolls a symbol into heartbeat staleness tracking."""
        sym = symbol.upper()
        self._tracked_symbols.add(sym)
        if sym not in self._statuses:
            self._statuses[sym] = DataFeedStatus.CONNECTED

    def untrack_symbol(self, symbol: str) -> None:
        """Removes a symbol from staleness tracking."""
        sym = symbol.upper()
        self._tracked_symbols.discard(sym)
        self._last_heartbeats.pop(sym, None)
        self._statuses.pop(sym, None)

    def record_activity(self, symbol: str, timestamp: datetime | None = None) -> None:
        """Records an incoming message or tick timestamp for a symbol."""
        sym = symbol.upper()
        now = timestamp or self._clock.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        self._last_heartbeats[sym] = now

        old_status = self._statuses.get(sym, DataFeedStatus.CONNECTED)
        if old_status == DataFeedStatus.STALE:
            self._statuses[sym] = DataFeedStatus.CONNECTED
            if self._event_bus:
                self._event_bus.publish(
                    DataFeedStatusChangedEvent(
                        symbol=sym,
                        old_status=old_status,
                        new_status=DataFeedStatus.CONNECTED,
                        reason="Feed activity resumed, recovered from stale",
                    )
                )

    def last_heartbeat(self, symbol: str) -> datetime | None:
        """Returns the most recent activity timestamp for a symbol."""
        return self._last_heartbeats.get(symbol.upper())

    def get_status(self, symbol: str) -> DataFeedStatus:
        """Returns the current operational status for a symbol feed."""
        return self._statuses.get(symbol.upper(), DataFeedStatus.DISCONNECTED)

    def is_stale(self, symbol: str, now: datetime | None = None) -> bool:
        """Checks if a specific symbol's feed has exceeded the staleness timeout."""
        sym = symbol.upper()
        last = self._last_heartbeats.get(sym)
        if last is None:
            return False

        current_time = now or self._clock.now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)

        elapsed = (current_time - last).total_seconds()
        return elapsed > self.timeout_seconds

    def check_staleness(self, now: datetime | None = None) -> list[DataQualityIssue]:
        """Audits all tracked symbols for silence exceeding timeout_seconds.

        If a feed is found to be stale, emits a CRITICAL DataAnomalyDetectedEvent,
        transitions the feed status to STALE, and returns the issue list.
        """
        current_time = now or self._clock.now()
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=UTC)

        issues: list[DataQualityIssue] = []

        for sym in sorted(self._tracked_symbols):
            last = self._last_heartbeats.get(sym)
            if last is None:
                continue

            elapsed = (current_time - last).total_seconds()
            if elapsed > self.timeout_seconds:
                issue = DataQualityIssue(
                    timestamp=current_time,
                    symbol=sym,
                    anomaly_type=DataAnomalyType.STALE_DATA,
                    severity="CRITICAL",
                    details=(
                        f"Feed silence for {sym}: no updates received in "
                        f"{elapsed:.1f}s (threshold: {self.timeout_seconds:.1f}s)"
                    ),
                )
                issues.append(issue)

                old_status = self._statuses.get(sym, DataFeedStatus.CONNECTED)
                if old_status != DataFeedStatus.STALE:
                    self._statuses[sym] = DataFeedStatus.STALE
                    if self._event_bus:
                        self._event_bus.publish(DataAnomalyDetectedEvent(issue=issue))
                        self._event_bus.publish(
                            DataFeedStatusChangedEvent(
                                symbol=sym,
                                old_status=old_status,
                                new_status=DataFeedStatus.STALE,
                                reason=f"Heartbeat timeout exceeded ({elapsed:.1f}s)",
                            )
                        )

        return issues

    def reset(self) -> None:
        """Resets all recorded heartbeat timestamps and statuses."""
        self._last_heartbeats.clear()
        for sym in self._tracked_symbols:
            self._statuses[sym] = DataFeedStatus.CONNECTED
