"""In-memory rolling stores for candle bars and top-of-book quotes."""

from collections import deque
from decimal import Decimal
from typing import Literal

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote

PriceField = Literal["open", "high", "low", "close", "volume"]


class BarStore:
    """In-memory circular buffer storing fixed capacity historical candle bars.

    Maintains bars in strictly ascending chronological order per (symbol, timeframe).
    Provides O(1) appends and fast series extraction for technical indicators.
    """

    def __init__(self, max_bars: int = 1000) -> None:
        if max_bars <= 0:
            raise DomainValidationError("max_bars must be strictly positive")
        self._max_bars = max_bars
        # Key: (symbol, timeframe) -> deque of Bar
        self._storage: dict[tuple[str, str], deque[Bar]] = {}

    @property
    def max_bars(self) -> int:
        return self._max_bars

    def add_bar(self, bar: Bar) -> None:
        """Appends a closed candle bar to the store in strict chronological order."""
        key = (bar.symbol, bar.timeframe)
        if key not in self._storage:
            self._storage[key] = deque(maxlen=self._max_bars)

        queue = self._storage[key]
        if queue:
            last_bar = queue[-1]
            if bar.timestamp <= last_bar.timestamp:
                raise DomainValidationError(
                    f"Bar timestamp ({bar.timestamp}) must be strictly later than "
                    f"previous bar timestamp ({last_bar.timestamp}) for {key}"
                )

        queue.append(bar)

    def get_latest_bar(self, symbol: str, timeframe: str) -> Bar | None:
        """Returns the most recently closed bar for the given symbol and timeframe."""
        key = (symbol, timeframe)
        queue = self._storage.get(key)
        if not queue:
            return None
        return queue[-1]

    def get_bars(self, symbol: str, timeframe: str, count: int | None = None) -> list[Bar]:
        """Returns historical bars in ascending chronological order (oldest to newest).

        If count is specified, returns the most recent `count` bars.
        """
        key = (symbol, timeframe)
        queue = self._storage.get(key)
        if not queue:
            return []

        if count is None or count >= len(queue):
            return list(queue)
        if count <= 0:
            return []

        # Slice the most recent `count` items
        return list(queue)[-count:]

    def get_series(
        self,
        symbol: str,
        timeframe: str,
        field_name: PriceField = "close",
        count: int | None = None,
    ) -> list[Decimal]:
        """Extracts a continuous list of Decimals for the specified candle field."""
        valid_fields = ("open", "high", "low", "close", "volume")
        if field_name not in valid_fields:
            raise DomainValidationError(
                f"Invalid field_name '{field_name}'. Must be one of {valid_fields}"
            )

        bars = self.get_bars(symbol, timeframe, count=count)
        return [getattr(b, field_name) for b in bars]

    def bar_count(self, symbol: str, timeframe: str) -> int:
        """Returns the total number of bars currently stored for (symbol, timeframe)."""
        key = (symbol, timeframe)
        queue = self._storage.get(key)
        return len(queue) if queue else 0

    def clear(self, symbol: str | None = None, timeframe: str | None = None) -> None:
        """Clears stored bars for a specific pair or all pairs."""
        if symbol is not None and timeframe is not None:
            self._storage.pop((symbol, timeframe), None)
        else:
            self._storage.clear()


class QuoteStore:
    """In-memory store caching the latest top-of-book market quote per symbol."""

    def __init__(self) -> None:
        self._quotes: dict[str, Quote] = {}

    def update_quote(self, quote: Quote) -> None:
        """Updates the cached quote if the incoming timestamp is not older."""
        existing = self._quotes.get(quote.symbol)
        if existing and quote.timestamp < existing.timestamp:
            raise DomainValidationError(
                f"Quote timestamp ({quote.timestamp}) cannot precede existing "
                f"quote timestamp ({existing.timestamp}) for {quote.symbol}"
            )
        self._quotes[quote.symbol] = quote

    def get_latest_quote(self, symbol: str) -> Quote | None:
        """Returns the most recent Quote for the given symbol, if available."""
        return self._quotes.get(symbol)

    def get_mid_price(self, symbol: str) -> Decimal | None:
        """Calculates (bid + ask) / 2 for the given symbol."""
        quote = self._quotes.get(symbol)
        if not quote:
            return None
        return (quote.bid_price + quote.ask_price) / Decimal("2")

    def get_spread(self, symbol: str) -> Decimal | None:
        """Calculates ask - bid spread for the given symbol."""
        quote = self._quotes.get(symbol)
        if not quote:
            return None
        return quote.ask_price - quote.bid_price

    def get_spread_pct(self, symbol: str) -> Decimal | None:
        """Calculates (ask - bid) / bid_price for the given symbol."""
        quote = self._quotes.get(symbol)
        if not quote:
            return None
        return (quote.ask_price - quote.bid_price) / quote.bid_price

    def clear(self, symbol: str | None = None) -> None:
        """Clears quote cache."""
        if symbol:
            self._quotes.pop(symbol, None)
        else:
            self._quotes.clear()
