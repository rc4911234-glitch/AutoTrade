"""Bollinger Bands and RSI Mean Reversion Strategy with mandatory 1:2+ R:R."""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import Bar
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.indicators.bollinger import BollingerBands
from trad_auto.indicators.rsi import RSI
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.base import BaseStrategy


class BollingerMeanReversionStrategy(BaseStrategy):
    """Mean reversion strategy for ranging/consolidating crypto markets.

    Trades extreme mean-deviation exhaustion:
    1. Long Setup: Bar closes below Lower Bollinger Band with RSI < oversold_threshold.
       Target: Middle Band (SMA).
       Stop-Loss: Enforces mathematical 1:2+ R:R.
    2. Short Setup: Bar closes above Upper Bollinger Band with RSI > overbought_threshold.
       Target: Middle Band (SMA).
       Stop-Loss: Enforces mathematical 1:2+ R:R.
    """

    def __init__(
        self,
        strategy_id: str = "bollinger_mean_reversion_v1",
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        bb_period: int = 20,
        bb_std: Decimal = Decimal("2.0"),
        rsi_period: int = 14,
        rsi_oversold: Decimal = Decimal("30.0"),
        rsi_overbought: Decimal = Decimal("70.0"),
        min_rrr: Decimal = Decimal("2.0"),
    ) -> None:
        target_symbols = symbols or ["BTCUSDT", "ETHUSDT"]
        target_timeframes = timeframes or ["15m"]
        super().__init__(strategy_id, target_symbols, target_timeframes)

        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.min_rrr = min_rrr

        # Indicators per (symbol, timeframe)
        self._bbs: dict[tuple[str, str], BollingerBands] = {}
        self._rsis: dict[tuple[str, str], RSI] = {}

    def _get_or_create_indicators(self, symbol: str, timeframe: str) -> tuple[BollingerBands, RSI]:
        key = (symbol, timeframe)
        if key not in self._bbs:
            self._bbs[key] = BollingerBands(self.bb_period, self.bb_std)
            self._rsis[key] = RSI(self.rsi_period)
        return self._bbs[key], self._rsis[key]

    def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
        if not self.handles(bar.symbol, bar.timeframe):
            return []

        bb, rsi = self._get_or_create_indicators(bar.symbol, bar.timeframe)
        bb.update(bar.close)
        rsi.update(bar.close)

        if not (bb.is_ready and rsi.is_ready):
            return []

        upper = bb.upper
        middle = bb.middle
        lower = bb.lower
        rsi_val = rsi.value

        if upper is None or middle is None or lower is None or rsi_val is None:
            return []

        proposals: list[TradeProposal] = []

        # 1. Oversold Dip Below Lower Band -> Long Reversion to Middle
        if bar.close < lower and rsi_val < self.rsi_oversold:
            target = middle
            reward = target - bar.close
            if reward > ZERO_DECIMAL:
                # Enforce at least 1:2 R:R -> Risk <= Reward / 2.0
                max_risk = reward / self.min_rrr
                stop_loss = bar.close - max_risk
                if stop_loss > ZERO_DECIMAL:
                    proposals.append(
                        TradeProposal(
                            strategy_id=self.strategy_id,
                            symbol=bar.symbol,
                            timeframe=bar.timeframe,
                            direction=OrderSide.BUY,
                            entry_price=bar.close,
                            stop_loss=stop_loss,
                            take_profit=target,
                            timestamp=bar.timestamp,
                            min_rrr=self.min_rrr,
                            reason=(
                                f"Oversold reversion: Close {bar.close} < Lower {lower:.2f}, "
                                f"RSI {rsi_val:.1f} < {self.rsi_oversold}, Middle {middle:.2f}"
                            ),
                        )
                    )

        # 2. Overbought Spike Above Upper Band -> Short Reversion to Middle
        elif bar.close > upper and rsi_val > self.rsi_overbought:
            target = middle
            reward = bar.close - target
            if reward > ZERO_DECIMAL:
                max_risk = reward / self.min_rrr
                stop_loss = bar.close + max_risk
                if target > ZERO_DECIMAL:
                    proposals.append(
                        TradeProposal(
                            strategy_id=self.strategy_id,
                            symbol=bar.symbol,
                            timeframe=bar.timeframe,
                            direction=OrderSide.SELL,
                            entry_price=bar.close,
                            stop_loss=stop_loss,
                            take_profit=target,
                            timestamp=bar.timestamp,
                            min_rrr=self.min_rrr,
                            reason=(
                                f"Overbought reversion: Close {bar.close} > Upper {upper:.2f}, "
                                f"RSI {rsi_val:.1f} > {self.rsi_overbought}, Middle {middle:.2f}"
                            ),
                        )
                    )

        return proposals

    def reset(self) -> None:
        for bb in self._bbs.values():
            bb.reset()
        for r in self._rsis.values():
            r.reset()
