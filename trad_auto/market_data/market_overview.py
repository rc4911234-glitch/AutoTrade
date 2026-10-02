"""Institutional Market-Wide live telemetry module for Trad-Auto dashboard."""

import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Cache to avoid hammering APIs on every 5s Streamlit rerun
_MARKET_CACHE: dict[str, Any] = {}
_LAST_FETCH_TIME = 0.0
_CACHE_TTL_SEC = 6.0


def get_market_overview() -> dict[str, Any]:
    """Fetches broad market tickers, funding rates, and sentiment index."""
    global _MARKET_CACHE, _LAST_FETCH_TIME
    now = time.time()
    if _MARKET_CACHE and (now - _LAST_FETCH_TIME < _CACHE_TTL_SEC):
        return _MARKET_CACHE

    result: dict[str, Any] = {
        "tickers": {},
        "fear_greed": {"value": "—", "classification": "Neutral"},
        "funding_rates": {},
    }

    # 1. Fetch Binance 24hr Tickers
    try:
        url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
        req = urllib.request.Request(url, headers={"User-Agent": "TradAuto/1.0"})
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tracked = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"}
            for item in data:
                sym = item.get("symbol")
                if sym in tracked:
                    price = float(item.get("lastPrice", 0.0))
                    change = float(item.get("priceChangePercent", 0.0))
                    vol_m = round(float(item.get("quoteVolume", 0.0)) / 1_000_000, 1)
                    high = float(item.get("highPrice", 0.0))
                    low = float(item.get("lowPrice", 0.0))
                    result["tickers"][sym] = {
                        "price": price,
                        "change": change,
                        "volume_m": vol_m,
                        "high": high,
                        "low": low,
                    }
    except Exception as exc:
        logger.debug("[MarketOverview] Failed to fetch 24hr tickers: %s", exc)

    # 2. Fetch Binance Funding Rates
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        req = urllib.request.Request(url, headers={"User-Agent": "TradAuto/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tracked = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
            for item in data:
                sym = item.get("symbol")
                if sym in tracked:
                    rate_pct = round(float(item.get("lastFundingRate", 0.0)) * 100, 4)
                    result["funding_rates"][sym] = rate_pct
    except Exception as exc:
        logger.debug("[MarketOverview] Failed to fetch funding rates: %s", exc)

    # 3. Fetch Alternative.me Crypto Fear & Greed Index
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "TradAuto/1.0"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("data"):
                fng = data["data"][0]
                result["fear_greed"] = {
                    "value": fng.get("value", "—"),
                    "classification": fng.get("value_classification", "Neutral"),
                }
    except Exception as exc:
        logger.debug("[MarketOverview] Failed to fetch Fear & Greed: %s", exc)

    _MARKET_CACHE = result
    _LAST_FETCH_TIME = now
    return result
