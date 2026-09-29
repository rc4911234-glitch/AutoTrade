"""Simulated market data adapter for deterministic replay in backtests and tests."""

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataFeedStatus
from trad_auto.core.events import (
    BarCompletedEvent,
    DataFeedStatusChangedEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote, Trade
from trad_auto.core.time import SimulatedClock
from trad_auto.market_data.adapters.base import BaseMarketDataAdapter
from trad_auto.market_data.store import BarStore, QuoteStore


class SimulatedMarketDataAdapter(BaseMarketDataAdapter):
    """Feeds historical bars, quotes, and trades into the platform deterministically."""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        clock: SimulatedClock | None = None,
        bar_store: BarStore | None = None,
        quote_store: QuoteStore | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._clock = clock
        self._bar_store = bar_store
        self._quote_store = quote_store

        self._status = DataFeedStatus.DISCONNECTED
        self._subscribed_symbols: set[str] = set()
        self._subscribed_timeframes: set[str] = set()

    @property
    def status(self) -> DataFeedStatus:
        return self._status

    @property
    def subscribed_symbols(self) -> set[str]:
        return set(self._subscribed_symbols)

    @property
    def subscribed_timeframes(self) -> set[str]:
        return set(self._subscribed_timeframes)

    def connect(self) -> None:
        old_status = self._status
        self._status = DataFeedStatus.CONNECTED
        if self._event_bus:
            self._event_bus.publish(
                DataFeedStatusChangedEvent(
                    symbol="*",
                    old_status=old_status,
                    new_status=self._status,
                    reason="Simulated feed connected",
                )
            )

    def disconnect(self) -> None:
        old_status = self._status
        self._status = DataFeedStatus.DISCONNECTED
        if self._event_bus:
            self._event_bus.publish(
                DataFeedStatusChangedEvent(
                    symbol="*",
                    old_status=old_status,
                    new_status=self._status,
                    reason="Simulated feed disconnected",
                )
            )

    def subscribe(self, symbols: list[str], timeframes: list[str]) -> None:
        self._subscribed_symbols.update(symbols)
        self._subscribed_timeframes.update(timeframes)

    def unsubscribe(self, symbols: list[str]) -> None:
        for s in symbols:
            self._subscribed_symbols.discard(s)

    def feed_bar(self, bar: Bar) -> None:
        """Injects a completed bar, advancing simulated clock and updating stores/events."""
        if self._status != DataFeedStatus.CONNECTED:
            raise DomainValidationError("Cannot feed bar: simulated adapter is not connected")

        if self._clock:
            self._clock.set_time(bar.timestamp)

        if self._bar_store:
            self._bar_store.add_bar(bar)

        if self._event_bus:
            self._event_bus.publish(BarCompletedEvent(bar=bar))

    def feed_quote(self, quote: Quote) -> None:
        """Injects a market quote, advancing simulated clock and updating stores/events."""
        if self._status != DataFeedStatus.CONNECTED:
            raise DomainValidationError("Cannot feed quote: simulated adapter is not connected")

        if self._clock:
            self._clock.set_time(quote.timestamp)

        if self._quote_store:
            self._quote_store.update_quote(quote)

        if self._event_bus:
            self._event_bus.publish(QuoteUpdatedEvent(quote=quote))

    def feed_trade(self, trade: Trade) -> None:
        """Injects an executed trade tick, advancing simulated clock and notifying listeners."""
        if self._status != DataFeedStatus.CONNECTED:
            raise DomainValidationError("Cannot feed trade: simulated adapter is not connected")

        if self._clock:
            self._clock.set_time(trade.timestamp)

        if self._event_bus:
            self._event_bus.publish(TradeReceivedEvent(trade=trade))

    def replay_bars(self, bars: list[Bar]) -> int:
        """Plays back an entire list of bars in sequence.

        Returns total bars processed.
        """
        if not self.is_connected:
            self.connect()

        count = 0
        for bar in bars:
            self.feed_bar(bar)
            count += 1
        return count
