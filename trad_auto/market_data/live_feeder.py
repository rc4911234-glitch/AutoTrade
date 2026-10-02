"""Continuous live market data feeder streaming real Binance Futures candles into EventBus."""

import logging
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trad_auto.core.bus import EventBus
from trad_auto.core.events import BarCompletedEvent, QuoteUpdatedEvent
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.training.data_downloader import BinanceDataDownloader

logger = logging.getLogger(__name__)


class BinanceLiveFeeder:
    """Streams live market data from Binance Futures public REST/Websocket into EventBus.

    Responsibilities:
    1. Instant Warmup: Pre-populates indicators (EMA9, EMA21, EMA50, RSI, ATR, ADX) with
       historical 1m bars so indicators are ready immediately on startup across all symbols.
    2. Continuous Heartbeat: Polls latest klines every 5-10 seconds, detecting newly closed
       candles and streaming them as BarCompletedEvent to trigger active strategies.
    3. Price Feed: Continuously emits QuoteUpdatedEvent so mark-to-market valuations and
       real-time PnL reflect live prices.
    """

    def __init__(
        self,
        event_bus: EventBus,
        symbols: list[str] | str = "BTCUSDT",
        timeframe: str = "1m",
        poll_interval_sec: float = 5.0,
    ) -> None:
        self._event_bus = event_bus
        if isinstance(symbols, str):
            self.symbols = [symbols.upper()]
        else:
            self.symbols = [s.upper() for s in symbols]
        self.symbol = self.symbols[0]  # backward compatibility
        self.timeframe = timeframe
        self.poll_interval_sec = poll_interval_sec
        self._downloader = BinanceDataDownloader(timeout_seconds=8.0)
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_completed_timestamps_ms: dict[str, int] = {s: 0 for s in self.symbols}

    def start(self, warmup_bars: int = 100) -> None:
        """Warms up indicators and launches continuous polling thread."""
        if self._running:
            return
        self._running = True

        # Initial warmup pass (synchronous so indicators are hot before loop)
        for sym in self.symbols:
            try:
                self._warmup_symbol(sym, limit=warmup_bars)
            except Exception as exc:
                logger.warning("[LiveFeeder] Warmup pass failed for %s (continuing): %s", sym, exc)

        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BinanceLiveFeeder")
        self._thread.start()
        logger.info("[LiveFeeder] Started continuous market data feed for %s", self.symbols)

    def stop(self) -> None:
        """Stops the feeder thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("[LiveFeeder] Stopped")

    def _warmup_symbol(self, symbol: str, limit: int = 60) -> None:
        """Fetches historical bars and feeds them into EventBus to warm up indicators."""
        raw_klines = self._downloader.fetch_klines(symbol=symbol, interval=self.timeframe, limit=limit)
        if not raw_klines:
            logger.warning("[LiveFeeder] No warmup klines returned from Binance for %s", symbol)
            return

        for k in raw_klines:
            bar = self._dict_to_bar(k, symbol=symbol)
            self._event_bus.publish(BarCompletedEvent(bar=bar))
            self._last_completed_timestamps_ms[symbol] = max(
                self._last_completed_timestamps_ms.get(symbol, 0),
                k.get("timestamp_ms", 0),
            )

        logger.info("[LiveFeeder] Successfully warmed up indicators with %d bars for %s", len(raw_klines), symbol)

    def _run_loop(self) -> None:
        """Continuous background loop pulling new ticks and candles for all symbols."""
        while self._running:
            for sym in self.symbols:
                try:
                    # Fetch recent 2 candles (one closed, one forming)
                    recent = self._downloader.fetch_klines(symbol=sym, interval=self.timeframe, limit=2)
                    if recent:
                        # Latest candle quote update
                        latest = recent[-1]
                        price = Decimal(str(latest["close"]))
                        half_spread = max(price * Decimal("0.00005"), Decimal("0.05"))
                        quote = Quote(
                            timestamp=latest.get("timestamp") or datetime.now(tz=UTC),
                            symbol=sym,
                            bid_price=price - half_spread,
                            ask_price=price + half_spread,
                            bid_size=Decimal("1.0"),
                            ask_size=Decimal("1.0"),
                        )
                        self._event_bus.publish(QuoteUpdatedEvent(quote=quote))

                        # Completed candle check
                        if len(recent) >= 2:
                            closed_kline = recent[-2]
                            ts = closed_kline.get("timestamp_ms", 0)
                            last_ts = self._last_completed_timestamps_ms.get(sym, 0)
                            if ts > last_ts:
                                self._last_completed_timestamps_ms[sym] = ts
                                bar = self._dict_to_bar(closed_kline, symbol=sym)
                                self._event_bus.publish(BarCompletedEvent(bar=bar))
                                logger.info("[LiveFeeder] New %s candle closed at %s: close=%s", sym, bar.timestamp, bar.close)

                except Exception as exc:
                    logger.debug("[LiveFeeder] Polling transient error for %s: %s", sym, exc)

            time.sleep(self.poll_interval_sec)

    def _dict_to_bar(self, k: dict[str, Any], symbol: str | None = None) -> Bar:
        sym = symbol or self.symbol
        ts = k.get("timestamp")
        if ts is None or getattr(ts, "tzinfo", None) is None:
            ts = datetime.now(tz=UTC)
        return Bar(
            timestamp=ts,
            symbol=sym,
            timeframe=self.timeframe,
            open=Decimal(str(k["open"])),
            high=Decimal(str(k["high"])),
            low=Decimal(str(k["low"])),
            close=Decimal(str(k["close"])),
            volume=Decimal(str(k["volume"])),
        )
