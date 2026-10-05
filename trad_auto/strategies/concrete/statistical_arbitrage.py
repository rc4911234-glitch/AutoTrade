"""Statistical Arbitrage (Pairs Trading) Strategy using Engle-Granger Cointegration.

Mathematical Framework:
1. Engle-Granger Two-Step Cointegration:
   Tests for stationary linear combination between two crypto assets (e.g. BTC and ETH):
   Spread: S_t = Y_t - (beta * X_t + alpha) ~ I(0)
2. Ornstein-Uhlenbeck Mean-Reversion Process:
   Computes mean-reversion speed (half-life of mean-reversion).
3. Dynamic Z-score Divergence Trigger:
   When |Z-score| > 2.0, asset Y is statistically mispriced relative to asset X.
   - Z < -2.0 -> LONG_SPREAD (Target asset underpriced, expect upward reversion)
   - Z > +2.0 -> SHORT_SPREAD (Target asset overpriced, expect downward reversion)
"""

from decimal import Decimal
import logging
from typing import Any

import numpy as np

from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import Bar
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.market_data.store import BarStore
from trad_auto.math.cointegration import CointegrationEngine, PairsSpreadResult, StatArbSignal
from trad_auto.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class StatisticalArbitrageStrategy(BaseStrategy):
    """Statistical Arbitrage Pairs Trading Strategy for cointegrated crypto pairs."""

    def __init__(
        self,
        strategy_id: str = "stat_arb_btc_eth",
        symbol_x: str = "BTCUSDT",
        symbol_y: str = "ETHUSDT",
        timeframes: list[str] | None = None,
        z_entry_threshold: float = 2.0,
        z_exit_threshold: float = 0.5,
        min_correlation: float = 0.75,
        max_half_life_bars: float = 60.0,
        lookback_bars: int = 50,
        risk_per_trade_pct: Decimal = Decimal("0.005"),  # 0.5% risk distance
        take_profit_ratio: Decimal = Decimal("2.0"),     # 1:2 R:R target
    ) -> None:
        tfs = timeframes or ["1m", "5m"]
        super().__init__(
            strategy_id=strategy_id,
            symbols=[symbol_x, symbol_y],
            timeframes=tfs,
        )
        self.symbol_x = symbol_x
        self.symbol_y = symbol_y
        self.z_entry_threshold = z_entry_threshold
        self.z_exit_threshold = z_exit_threshold
        self.min_correlation = min_correlation
        self.max_half_life_bars = max_half_life_bars
        self.lookback_bars = lookback_bars
        self.risk_per_trade_pct = risk_per_trade_pct
        self.take_profit_ratio = take_profit_ratio

        self.cointegration_engine = CointegrationEngine(
            z_entry_threshold=z_entry_threshold,
            z_exit_threshold=z_exit_threshold,
            min_bars_required=30,
        )

        self._last_result: PairsSpreadResult | None = None
        self._last_signal_time: Any = None

    def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
        """Evaluates closed candle for statistical arbitrage cointegration divergence."""
        if not self.handles(bar.symbol, bar.timeframe):
            return []

        # Only evaluate on the target trading asset (symbol_y, e.g. ETHUSDT)
        if bar.symbol != self.symbol_y:
            return []

        bars_x = store.get_bars(self.symbol_x, bar.timeframe, count=self.lookback_bars)
        bars_y = store.get_bars(self.symbol_y, bar.timeframe, count=self.lookback_bars)

        if len(bars_x) < 30 or len(bars_y) < 30:
            return []

        # Align lengths
        min_len = min(len(bars_x), len(bars_y))
        prices_x = [float(b.close) for b in bars_x[-min_len:]]
        prices_y = [float(b.close) for b in bars_y[-min_len:]]

        result = self.cointegration_engine.analyze_pair(
            self.symbol_x, prices_x, self.symbol_y, prices_y
        )
        if result is None:
            return []

        self._last_result = result

        # Guard: Check minimum correlation and mean-reversion half-life
        if result.correlation < self.min_correlation:
            logger.debug(
                "[StatArb] Correlation below threshold: %.3f < %.3f",
                result.correlation,
                self.min_correlation,
            )
            return []

        if result.half_life_bars > self.max_half_life_bars:
            logger.debug(
                "[StatArb] Half-life exceeds threshold: %.1f > %.1f",
                result.half_life_bars,
                self.max_half_life_bars,
            )
            return []

        entry_price = bar.close
        risk_dist = entry_price * self.risk_per_trade_pct
        if risk_dist <= Decimal("0"):
            return []

        # Generate Divergence Reversion Proposals
        if result.signal == StatArbSignal.LONG_SPREAD:
            # Target asset Y is underpriced relative to X -> Long Y
            sl = entry_price - risk_dist
            tp = entry_price + (risk_dist * self.take_profit_ratio)
            confidence = Decimal(str(min(0.95, round(abs(result.z_score) / 3.0, 2))))
            reason = (
                f"Stat-Arb Long Spread: Z-score {result.z_score:+.2f} < -{self.z_entry_threshold:.1f} "
                f"| Hedge Beta: {result.hedge_ratio:.5f} | Half-Life: {result.half_life_bars:.1f}b"
            )
            return [
                TradeProposal(
                    strategy_id=self.strategy_id,
                    symbol=self.symbol_y,
                    timeframe=bar.timeframe,
                    direction=OrderSide.BUY,
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    timestamp=bar.timestamp,
                    reason=reason,
                    confidence=confidence,
                )
            ]

        if result.signal == StatArbSignal.SHORT_SPREAD:
            # Target asset Y is overpriced relative to X -> Short Y
            sl = entry_price + risk_dist
            tp = entry_price - (risk_dist * self.take_profit_ratio)
            confidence = Decimal(str(min(0.95, round(abs(result.z_score) / 3.0, 2))))
            reason = (
                f"Stat-Arb Short Spread: Z-score {result.z_score:+.2f} > +{self.z_entry_threshold:.1f} "
                f"| Hedge Beta: {result.hedge_ratio:.5f} | Half-Life: {result.half_life_bars:.1f}b"
            )
            return [
                TradeProposal(
                    strategy_id=self.strategy_id,
                    symbol=self.symbol_y,
                    timeframe=bar.timeframe,
                    direction=OrderSide.SELL,
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    timestamp=bar.timestamp,
                    reason=reason,
                    confidence=confidence,
                )
            ]

        return []

    def get_telemetry(self) -> dict[str, Any]:
        """Provides real-time telemetry for the Web Dashboard and Risk Engine."""
        if self._last_result is None:
            return {
                "status": "WARMING_UP",
                "symbol_x": self.symbol_x,
                "symbol_y": self.symbol_y,
                "z_score": 0.0,
                "beta": 0.0,
                "half_life": 0.0,
                "correlation": 0.0,
                "signal": "NEUTRAL",
            }
        return {
            "status": "ACTIVE",
            "symbol_x": self.symbol_x,
            "symbol_y": self.symbol_y,
            "z_score": round(self._last_result.z_score, 2),
            "beta": round(self._last_result.hedge_ratio, 5),
            "half_life": round(self._last_result.half_life_bars, 1),
            "correlation": round(self._last_result.correlation, 3),
            "signal": self._last_result.signal.value,
            "current_spread": round(self._last_result.current_spread, 2),
        }

    def reset(self) -> None:
        """Resets cached spread states."""
        self._last_result = None
        self._last_signal_time = None
