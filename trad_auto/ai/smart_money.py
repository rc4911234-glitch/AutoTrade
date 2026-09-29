"""Binance Smart Money AI & On-Chain Quantitative Signal Analyzer.

Uses Binance Futures public institutional metrics (Top Trader Long/Short ratio,
Taker Buy/Sell volume, Open Interest accumulation) to evaluate smart money sentiment
without requiring paid LLMs, third-party subscriptions, or elevated API privileges.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trad_auto.core.enums import OrderSide

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmartMoneyMetrics:
    """Normalized institutional smart money snapshot for a specific symbol."""

    symbol: str
    top_trader_position_ratio: Decimal  # Whale positions L/S
    top_trader_account_ratio: Decimal  # Whale accounts L/S
    taker_buy_sell_ratio: Decimal  # Aggressive taker market flow
    sentiment_score: Decimal  # Normalized -1.0 (Extreme Bearish) to +1.0 (Extreme Bullish)
    analysis_timestamp: datetime
    raw_whale_long_pct: Decimal
    raw_whale_short_pct: Decimal


class BinanceSmartMoneyAnalyzer:
    """Fetches and evaluates Binance institutional sentiment and smart money flow."""

    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        timeout_seconds: float = 6.0,
        user_agent: str = "TradAuto-SmartMoney/1.0",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def fetch_smart_money_metrics(self, symbol: str) -> SmartMoneyMetrics | None:
        """Fetches top-trader ratios and taker flow for a given crypto futures pair."""
        try:
            # 1. Top Trader Long/Short Position Ratio
            pos_ratio_data = self._get_json(
                f"{self.base_url}/futures/data/topLongShortPositionRatio?symbol={symbol}&period=1h&limit=1"
            )
            # 2. Top Trader Long/Short Account Ratio
            acc_ratio_data = self._get_json(
                f"{self.base_url}/futures/data/topLongShortAccountRatio?symbol={symbol}&period=1h&limit=1"
            )
            # 3. Taker Buy/Sell Volume Ratio
            taker_data = self._get_json(
                f"{self.base_url}/futures/data/takerlongshortRatio?symbol={symbol}&period=1h&limit=1"
            )

            if not pos_ratio_data or not acc_ratio_data or not taker_data:
                logger.warning("Empty smart money metrics returned for %s", symbol)
                return None

            latest_pos = pos_ratio_data[0]
            latest_acc = acc_ratio_data[0]
            latest_taker = taker_data[0]

            pos_ls = Decimal(str(latest_pos.get("longShortRatio", "1.0")))
            acc_ls = Decimal(str(latest_acc.get("longShortRatio", "1.0")))
            taker_bs = Decimal(str(latest_taker.get("buySellRatio", "1.0")))
            long_pct = Decimal(str(latest_pos.get("longAccount", "0.50")))
            short_pct = Decimal(str(latest_pos.get("shortAccount", "0.50")))

            # Calculate composite sentiment score between -1.0 and +1.0
            # Neutral baseline: pos_ls = 1.0, taker_bs = 1.0
            # Higher long/short and taker buy indicates institutional accumulation
            pos_delta = (pos_ls - Decimal("1.0")) / Decimal("2.0")
            taker_delta = (taker_bs - Decimal("1.0")) / Decimal("1.5")
            raw_score = (pos_delta * Decimal("0.6")) + (taker_delta * Decimal("0.4"))
            sentiment_score = max(
                Decimal("-1.0"), min(Decimal("1.0"), raw_score.quantize(Decimal("0.01")))
            )

            return SmartMoneyMetrics(
                symbol=symbol,
                top_trader_position_ratio=pos_ls,
                top_trader_account_ratio=acc_ls,
                taker_buy_sell_ratio=taker_bs,
                sentiment_score=sentiment_score,
                analysis_timestamp=datetime.now(UTC),
                raw_whale_long_pct=long_pct,
                raw_whale_short_pct=short_pct,
            )

        except Exception as exc:
            logger.error("Failed to fetch Binance Smart Money metrics for %s: %s", symbol, exc)
            return None

    def validate_proposal_alignment(
        self,
        symbol: str,
        side: OrderSide,
        max_opposing_threshold: Decimal = Decimal("2.0"),
    ) -> tuple[bool, str]:
        """Validates if a proposed trade aligns with or does not fight extreme whale sentiment.

        Returns (is_approved, explanation).
        Fails open (returns True, neutral note) on network issues to prevent false-rejection.
        """
        metrics = self.fetch_smart_money_metrics(symbol)
        if metrics is None:
            return True, "Smart money data temporarily unavailable (Permitted fail-open)"

        if side == OrderSide.BUY:
            # Check if whales are overwhelmingly short (e.g. L/S < 0.50 -> 2:1 short dominance)
            if metrics.top_trader_position_ratio < (Decimal("1.0") / max_opposing_threshold):
                short_ratio = (Decimal("1.0") / metrics.top_trader_position_ratio).quantize(
                    Decimal("0.01")
                )
                msg = (
                    f"Blocked by Smart Money Shield: Top traders are {short_ratio}x net SHORT "
                    f"on {symbol} (Whale Long/Short ratio={metrics.top_trader_position_ratio})"
                )
                return False, msg

            align_msg = (
                f"Smart Money Aligned (Whale Long: {metrics.raw_whale_long_pct * 100:.1f}%, "
                f"Score={metrics.sentiment_score:+})"
            )
            return True, align_msg

        if side == OrderSide.SELL:
            # Check if whales are overwhelmingly long (L/S ratio > 2.0 -> 2:1 long dominance)
            if metrics.top_trader_position_ratio > max_opposing_threshold:
                msg = (
                    f"Blocked by Smart Money Shield: Top traders are "
                    f"{metrics.top_trader_position_ratio}x net LONG on {symbol} "
                    f"(Whale Long/Short ratio={metrics.top_trader_position_ratio})"
                )
                return False, msg

            align_msg = (
                f"Smart Money Aligned (Whale Short: {metrics.raw_whale_short_pct * 100:.1f}%, "
                f"Score={metrics.sentiment_score:+})"
            )
            return True, align_msg

        return True, "Permitted"

    def _get_json(self, url: str) -> list[dict[str, object]] | None:
        """Executes HTTP GET and returns parsed JSON array."""
        req = Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                data: object = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
                return None
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.debug("Smart money API request failed for %s: %s", url, exc)
            return None
