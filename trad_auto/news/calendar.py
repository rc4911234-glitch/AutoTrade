"""Macroeconomic calendar manager tracking scheduled volatility release windows."""

from datetime import UTC, datetime
from uuid import UUID

from trad_auto.core.models.news import MacroEconomicEvent


class MacroCalendarManager:
    """Manages scheduled economic releases and determines volatility blackout status."""

    def __init__(
        self,
        events: list[MacroEconomicEvent] | None = None,
        default_pre_buffer_minutes: int | None = None,
        default_post_buffer_minutes: int | None = None,
    ) -> None:
        self.default_pre_buffer_minutes = default_pre_buffer_minutes
        self.default_post_buffer_minutes = default_post_buffer_minutes
        self._events: dict[UUID, MacroEconomicEvent] = {}
        if events:
            for ev in events:
                self.register_event(ev)

    @property
    def event_count(self) -> int:
        return len(self._events)

    def register_event(self, event: MacroEconomicEvent) -> None:
        """Enrolls a scheduled release into the calendar."""
        self._events[event.event_id] = event

    def unregister_event(self, event_id: UUID) -> bool:
        """Removes a scheduled event."""
        return self._events.pop(event_id, None) is not None

    def list_events(self) -> list[MacroEconomicEvent]:
        """Returns all registered events sorted by scheduled release timestamp."""
        return sorted(self._events.values(), key=lambda e: e.scheduled_at)

    def get_active_blackout_events(
        self,
        now: datetime,
        symbol: str | None = None,
        pre_override: int | None = None,
        post_override: int | None = None,
    ) -> list[MacroEconomicEvent]:
        """Returns all events currently imposing an active entry blackout."""
        current_time = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        pre = pre_override if pre_override is not None else self.default_pre_buffer_minutes
        post = post_override if post_override is not None else self.default_post_buffer_minutes
        active: list[MacroEconomicEvent] = []

        for event in self._events.values():
            if symbol and not event.affects_symbol(symbol):
                continue
            if event.is_in_blackout_window(
                current_time,
                pre_override=pre,
                post_override=post,
            ):
                active.append(event)

        return sorted(active, key=lambda e: e.scheduled_at)

    def is_in_blackout(
        self,
        now: datetime,
        symbol: str | None = None,
        pre_override: int | None = None,
        post_override: int | None = None,
    ) -> bool:
        """Returns True if any active blackout window is currently in effect."""
        return (
            len(
                self.get_active_blackout_events(
                    now,
                    symbol=symbol,
                    pre_override=pre_override,
                    post_override=post_override,
                )
            )
            > 0
        )

    def next_upcoming_event(self, now: datetime) -> MacroEconomicEvent | None:
        """Returns the earliest upcoming event scheduled after the given time."""
        current_time = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        future_events = [e for e in self._events.values() if e.scheduled_at > current_time]
        if not future_events:
            return None
        return min(future_events, key=lambda e: e.scheduled_at)

    def clear(self) -> None:
        """Clears all calendar events."""
        self._events.clear()
