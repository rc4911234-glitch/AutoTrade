"""Deterministic Trade to Bar aggregator with zero look-ahead bias guarantees."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from trad_auto.core.bus import EventBus
from trad_auto.core.events import BarCompletedEvent, BarUpdatedEvent
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Trade
from trad_auto.market_data.store import BarStore

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_to_seconds(timeframe: str) -> int:
    """Converts a timeframe string into total seconds."""
    seconds = TIMEFRAME_SECONDS.get(timeframe)
    if seconds is None:
        valid_options = list(TIMEFRAME_SECONDS.keys())
        raise DomainValidationError(
            f"Unsupported timeframe '{timeframe}'. Supported: {valid_options}"
        )
    return seconds


def calculate_bar_window(timestamp: datetime, interval_seconds: int) -> tuple[datetime, datetime]:
    """Calculates UTC midnight-aligned start and end datetimes for a candle interval."""
    if timestamp.tzinfo is None:
        raise DomainValidationError("Timestamp must be timezone-aware (UTC)")

    epoch_seconds = int(timestamp.timestamp())
    start_epoch = (epoch_seconds // interval_seconds) * interval_seconds
    end_epoch = start_epoch + interval_seconds

    start_dt = datetime.fromtimestamp(start_epoch, tz=UTC)
    end_dt = datetime.fromtimestamp(end_epoch, tz=UTC)
    return start_dt, end_dt


@dataclass
class _ActiveBar:
    symbol: str
    timeframe: str
    start_time: datetime
    end_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int

    def to_bar(self) -> Bar:
        return Bar(
            timestamp=self.start_time,
            symbol=self.symbol,
            timeframe=self.timeframe,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class BarBuilder:
    """Aggregates raw market trade ticks into normalized OHLCV candle bars.

    Guarantees:
    1. Zero Look-Ahead Bias: Closed candles are published only when the time interval
       closes, ensuring strategy evaluation uses only finalized data.
    2. Exact Time Alignment: Candles align strictly with UTC period boundaries.
    3. Multi-Timeframe Fan-out: A single trade tick can simultaneously update 1m, 5m, 1h bars.
    """

    def __init__(
        self,
        timeframes: list[str],
        event_bus: EventBus | None = None,
        bar_store: BarStore | None = None,
        on_bar_completed: Callable[[Bar], None] | None = None,
    ) -> None:
        if not timeframes:
            raise DomainValidationError("timeframes cannot be empty")
        for tf in timeframes:
            timeframe_to_seconds(tf)  # Validates all timeframes upfront

        self._timeframes = list(timeframes)
        self._event_bus = event_bus
        self._bar_store = bar_store
        self._on_bar_completed = on_bar_completed

        # Key: (symbol, timeframe) -> _ActiveBar
        self._active_bars: dict[tuple[str, str], _ActiveBar] = {}

    @property
    def timeframes(self) -> list[str]:
        return list(self._timeframes)

    def on_trade(self, trade: Trade) -> list[Bar]:
        """Processes an incoming Trade tick across all configured timeframes.

        Returns a list of any Bars that were completed and closed by this tick.
        """
        completed_bars: list[Bar] = []

        for tf in self._timeframes:
            interval_sec = timeframe_to_seconds(tf)
            key = (trade.symbol, tf)
            active = self._active_bars.get(key)

            if active is None:
                # Start new candle
                start_dt, end_dt = calculate_bar_window(trade.timestamp, interval_sec)
                new_active = _ActiveBar(
                    symbol=trade.symbol,
                    timeframe=tf,
                    start_time=start_dt,
                    end_time=end_dt,
                    open=trade.price,
                    high=trade.price,
                    low=trade.price,
                    close=trade.price,
                    volume=trade.quantity,
                    trade_count=1,
                )
                self._active_bars[key] = new_active
                if self._event_bus:
                    self._event_bus.publish(BarUpdatedEvent(bar=new_active.to_bar()))
            elif trade.timestamp >= active.end_time:
                # Candle period closed! Finalize previous bar
                closed_bar = active.to_bar()
                completed_bars.append(closed_bar)
                self._emit_completed_bar(closed_bar)

                # Initialize next candle
                start_dt, end_dt = calculate_bar_window(trade.timestamp, interval_sec)
                new_active = _ActiveBar(
                    symbol=trade.symbol,
                    timeframe=tf,
                    start_time=start_dt,
                    end_time=end_dt,
                    open=trade.price,
                    high=trade.price,
                    low=trade.price,
                    close=trade.price,
                    volume=trade.quantity,
                    trade_count=1,
                )
                self._active_bars[key] = new_active
                if self._event_bus:
                    self._event_bus.publish(BarUpdatedEvent(bar=new_active.to_bar()))
            elif trade.timestamp < active.start_time:
                # Out-of-order tick prior to current window start
                raise DomainValidationError(
                    f"Out-of-order trade tick: timestamp {trade.timestamp} "
                    f"is earlier than active bar start {active.start_time}"
                )
            else:
                # Tick falls within current bar
                active.high = max(active.high, trade.price)
                active.low = min(active.low, trade.price)
                active.close = trade.price
                active.volume += trade.quantity
                active.trade_count += 1
                if self._event_bus:
                    self._event_bus.publish(BarUpdatedEvent(bar=active.to_bar()))

        return completed_bars

    def flush(self, symbol: str | None = None, timeframe: str | None = None) -> list[Bar]:
        """Closes and returns currently active bars."""
        closed: list[Bar] = []
        keys_to_close = [
            k
            for k in self._active_bars
            if (symbol is None or k[0] == symbol) and (timeframe is None or k[1] == timeframe)
        ]

        for k in keys_to_close:
            active = self._active_bars.pop(k)
            bar = active.to_bar()
            closed.append(bar)
            self._emit_completed_bar(bar)

        return closed

    def get_active_bar(self, symbol: str, timeframe: str) -> Bar | None:
        """Returns the in-progress forming bar, if one exists."""
        active = self._active_bars.get((symbol, timeframe))
        return active.to_bar() if active else None

    def _emit_completed_bar(self, bar: Bar) -> None:
        if self._bar_store:
            self._bar_store.add_bar(bar)
        if self._event_bus:
            self._event_bus.publish(BarCompletedEvent(bar=bar))
        if self._on_bar_completed:
            self._on_bar_completed(bar)
