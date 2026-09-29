"""Market data quality and anomaly validation filters."""

from datetime import datetime
from decimal import Decimal

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import DataAnomalyType
from trad_auto.core.events import DataAnomalyDetectedEvent
from trad_auto.core.models.market_data import DataQualityIssue, Quote, Trade
from trad_auto.core.time import Clock


class DataQualityValidator:
    """Detects aberrant prints, stale feeds, clock drift, and inverted quotes.

    Provides automated safety guardrails to ensure algorithms do not act on
    corrupted exchange data or hung WebSocket connections.
    """

    def __init__(
        self,
        max_price_spike_pct: Decimal = Decimal("0.10"),  # 10% max single-tick spike
        max_clock_drift_seconds: float = 5.0,  # 5 seconds max lag/drift
        max_staleness_seconds: float = 15.0,  # 15 seconds without quotes = stale
        event_bus: EventBus | None = None,
    ) -> None:
        if max_price_spike_pct <= ZERO_DECIMAL:
            raise ValueError("max_price_spike_pct must be strictly positive")
        if max_clock_drift_seconds <= 0.0:
            raise ValueError("max_clock_drift_seconds must be strictly positive")
        if max_staleness_seconds <= 0.0:
            raise ValueError("max_staleness_seconds must be strictly positive")

        self.max_price_spike_pct = max_price_spike_pct
        self.max_clock_drift_seconds = max_clock_drift_seconds
        self.max_staleness_seconds = max_staleness_seconds
        self._event_bus = event_bus

        # Tracking state per symbol
        self._last_prices: dict[str, Decimal] = {}
        self._last_quote_timestamps: dict[str, datetime] = {}
        self._last_trade_timestamps: dict[str, datetime] = {}

    def validate_quote(self, quote: Quote, clock: Clock) -> list[DataQualityIssue]:
        """Validates incoming quote against clock drift and order-of-arrival."""
        issues: list[DataQualityIssue] = []
        now = clock.now()

        # 1. Clock drift check
        drift = abs((now - quote.timestamp).total_seconds())
        if drift > self.max_clock_drift_seconds:
            issues.append(
                DataQualityIssue(
                    timestamp=now,
                    symbol=quote.symbol,
                    anomaly_type=DataAnomalyType.TIMESTAMP_DRIFT,
                    severity="WARNING",
                    details=(
                        f"Quote timestamp {quote.timestamp} drifts by {drift:.2f}s "
                        f"from server clock {now} (threshold: {self.max_clock_drift_seconds}s)"
                    ),
                )
            )

        # 2. Sequence check (out of order)
        last_ts = self._last_quote_timestamps.get(quote.symbol)
        if last_ts and quote.timestamp < last_ts:
            issues.append(
                DataQualityIssue(
                    timestamp=now,
                    symbol=quote.symbol,
                    anomaly_type=DataAnomalyType.OUT_OF_ORDER,
                    severity="CRITICAL",
                    details=(
                        f"Out-of-order quote: received timestamp {quote.timestamp} "
                        f"is earlier than prior quote timestamp {last_ts}"
                    ),
                )
            )
        else:
            self._last_quote_timestamps[quote.symbol] = quote.timestamp

        self._emit_issues(issues)
        return issues

    def validate_trade(self, trade: Trade, clock: Clock) -> list[DataQualityIssue]:
        """Validates incoming trade against price spikes, clock drift, and sequencing."""
        issues: list[DataQualityIssue] = []
        now = clock.now()

        # 1. Clock drift check
        drift = abs((now - trade.timestamp).total_seconds())
        if drift > self.max_clock_drift_seconds:
            issues.append(
                DataQualityIssue(
                    timestamp=now,
                    symbol=trade.symbol,
                    anomaly_type=DataAnomalyType.TIMESTAMP_DRIFT,
                    severity="WARNING",
                    details=(
                        f"Trade timestamp {trade.timestamp} drifts by {drift:.2f}s "
                        f"from server clock {now} (threshold: {self.max_clock_drift_seconds}s)"
                    ),
                )
            )

        # 2. Sequence check
        last_ts = self._last_trade_timestamps.get(trade.symbol)
        if last_ts and trade.timestamp < last_ts:
            issues.append(
                DataQualityIssue(
                    timestamp=now,
                    symbol=trade.symbol,
                    anomaly_type=DataAnomalyType.OUT_OF_ORDER,
                    severity="CRITICAL",
                    details=(
                        f"Out-of-order trade: received timestamp {trade.timestamp} "
                        f"is earlier than prior trade timestamp {last_ts}"
                    ),
                )
            )
        else:
            self._last_trade_timestamps[trade.symbol] = trade.timestamp

        # 3. Price spike check
        last_price = self._last_prices.get(trade.symbol)
        if last_price and last_price > ZERO_DECIMAL:
            spike_pct = abs(trade.price - last_price) / last_price
            if spike_pct > self.max_price_spike_pct:
                issues.append(
                    DataQualityIssue(
                        timestamp=now,
                        symbol=trade.symbol,
                        anomaly_type=DataAnomalyType.PRICE_SPIKE,
                        severity="CRITICAL",
                        details=(
                            f"Price spike detected: trade price {trade.price} deviates by "
                            f"{spike_pct * 100:.2f}% from previous price {last_price} "
                            f"(threshold: {self.max_price_spike_pct * 100:.2f}%)"
                        ),
                    )
                )

        # If no critical spike, update last price
        if not any(i.anomaly_type == DataAnomalyType.PRICE_SPIKE for i in issues):
            self._last_prices[trade.symbol] = trade.price

        self._emit_issues(issues)
        return issues

    def check_feed_staleness(self, symbol: str, clock: Clock) -> DataQualityIssue | None:
        """Audits the elapsed time since the most recent quote or trade.

        Returns a DataQualityIssue if feed silence exceeds max_staleness_seconds.
        """
        now = clock.now()
        last_quote_ts = self._last_quote_timestamps.get(symbol)
        last_trade_ts = self._last_trade_timestamps.get(symbol)

        candidates = [ts for ts in (last_quote_ts, last_trade_ts) if ts is not None]
        if not candidates:
            # No data ever received for this symbol
            return None

        most_recent = max(candidates)
        elapsed = (now - most_recent).total_seconds()

        if elapsed > self.max_staleness_seconds:
            issue = DataQualityIssue(
                timestamp=now,
                symbol=symbol,
                anomaly_type=DataAnomalyType.STALE_DATA,
                severity="CRITICAL",
                details=(
                    f"Market data feed for {symbol} is stale: no updates for "
                    f"{elapsed:.1f}s (threshold: {self.max_staleness_seconds}s)"
                ),
            )
            self._emit_issues([issue])
            return issue

        return None

    def _emit_issues(self, issues: list[DataQualityIssue]) -> None:
        if self._event_bus and issues:
            for issue in issues:
                self._event_bus.publish(DataAnomalyDetectedEvent(issue=issue))
