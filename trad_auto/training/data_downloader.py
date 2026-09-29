"""Historical market data ingestion engine for Binance BTCUSDT futures."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class BinanceDataDownloader:
    """Fetches historical OHLCV klines from Binance Public Futures REST API.

    Features:
    - 100% Free: Uses Binance public endpoint (zero API keys required for public klines).
    - Rate limit aware: Uses batch pagination (1000 candles per request).
    - Synthetic generator: Fallback generator for offline backtesting and CI pipelines.
    """

    BASE_URL = "https://fapi.binance.com/fapi/v1/klines"

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Fetches raw klines from Binance Futures public endpoint.

        Supports pagination when limit > 1000 by walking backward in time.
        """
        all_candles: list[dict[str, Any]] = []
        current_end = end_time_ms

        while len(all_candles) < limit:
            remaining = limit - len(all_candles)
            batch_limit = min(remaining, 1000)
            batch = self._fetch_batch(
                symbol=symbol,
                interval=interval,
                start_time_ms=start_time_ms,
                end_time_ms=current_end,
                limit=batch_limit,
            )
            if not batch:
                break

            all_candles = batch + all_candles
            if len(batch) < batch_limit:
                break
            current_end = batch[0]["timestamp_ms"] - 1

        if not all_candles:
            logger.warning("No klines returned from Binance API. Using synthetic fallback.")
            return self.generate_synthetic_klines(symbol=symbol, count=limit)

        return all_candles

    def _fetch_batch(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None,
        end_time_ms: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fetches a single batch of klines from Binance API."""
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        query_str = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}?{query_str}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TradAuto/1.0 (Institutional Quant Engine)"},
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning(
                "Failed to fetch kline batch from Binance API: %s.", exc
            )
            return []

        candles: list[dict[str, Any]] = []
        for raw in data:
            candles.append(
                {
                    "timestamp_ms": int(raw[0]),
                    "timestamp": datetime.fromtimestamp(raw[0] / 1000.0, tz=UTC),
                    "open": float(raw[1]),
                    "high": float(raw[2]),
                    "low": float(raw[3]),
                    "close": float(raw[4]),
                    "volume": float(raw[5]),
                    "taker_buy_volume": float(raw[9]),
                    "trades_count": int(raw[8]),
                }
            )
        return candles

    def generate_synthetic_klines(
        self,
        symbol: str = "BTCUSDT",
        count: int = 1000,
        start_price: float = 64000.0,
        volatility: float = 0.002,
        seed: int = 42,
    ) -> list[dict[str, Any]]:
        """Generates realistic geometric Brownian motion klines with regimes.

        Used for offline training, deterministic CI testing, and stress-tests.
        """
        rng = np.random.default_rng(seed)
        candles: list[dict[str, Any]] = []
        current_price = start_price
        base_time = int(datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)

        for i in range(count):
            # Price jump with drift
            drift = 0.00005 if (i // 200) % 2 == 0 else -0.00005
            shock = rng.normal(drift, volatility)
            next_close = max(100.0, current_price * (1.0 + shock))

            high_shock = abs(rng.normal(0, volatility * 0.5))
            low_shock = abs(rng.normal(0, volatility * 0.5))
            candle_high = max(current_price, next_close) * (1.0 + high_shock)
            candle_low = min(current_price, next_close) * (1.0 - low_shock)

            volume = abs(rng.normal(50.0, 15.0))
            taker_ratio = rng.uniform(0.35, 0.65)
            taker_buy_volume = volume * taker_ratio

            candle_time = base_time + (i * 60 * 1000)

            candles.append(
                {
                    "timestamp_ms": candle_time,
                    "timestamp": datetime.fromtimestamp(candle_time / 1000.0, tz=UTC),
                    "open": float(current_price),
                    "high": float(candle_high),
                    "low": float(candle_low),
                    "close": float(next_close),
                    "volume": float(volume),
                    "taker_buy_volume": float(taker_buy_volume),
                    "trades_count": int(abs(rng.normal(120, 30))),
                }
            )
            current_price = next_close

        return candles
