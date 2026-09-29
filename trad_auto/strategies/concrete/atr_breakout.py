"""Trend-following breakout strategy with dynamic ATR stop-loss and 1:2+ R:R."""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import OrderSide
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.indicators.atr import ATR
from trad_auto.indicators.moving_averages import EMA, SMA
from trad_auto.market_data.store import BarStore
from trad_auto.strategies.base import BaseStrategy


class ATRBreakoutStrategy(BaseStrategy):
    """Institutional-grade trend volatility breakout strategy for crypto.

    Key Rules:
    1. Trend Direction: Fast EMA vs Slow EMA determines overall directional bias.
    2. Donchian Breakout: Bar closes beyond the Highest High (Long) or Lowest Low (Short)
       of the preceding N bars.
    3. Volume Confirmation: Breakout bar volume must exceed average volume.
    4. Volatility Stop-Loss: Placed at (Entry +/- (ATR * atr_multiplier)).
    5. Mathematical 1:2 R:R: Take Profit is placed at exactly (Entry +/- 2 * Risk).
    """

    def __init__(
        self,
        strategy_id: str = "atr_breakout_v1",
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        fast_ema_period: int = 20,
        slow_ema_period: int = 50,
        donchian_period: int = 20,
        atr_period: int = 14,
        atr_multiplier: Decimal = Decimal("2.0"),
        rr_target_multiplier: Decimal = Decimal("2.0"),
        volume_filter_multiplier: Decimal = Decimal("1.0"),
    ) -> None:
        target_symbols = symbols or ["BTCUSDT", "ETHUSDT"]
        target_timeframes = timeframes or ["1h"]
        super().__init__(strategy_id, target_symbols, target_timeframes)

        if rr_target_multiplier < Decimal("2.0"):
            raise DomainValidationError("rr_target_multiplier cannot be lower than 2.0 (1:2 R:R)")

        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.donchian_period = donchian_period
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.rr_target_multiplier = rr_target_multiplier
        self.volume_filter_multiplier = volume_filter_multiplier

        # Indicators per (symbol, timeframe)
        self._fast_emas: dict[tuple[str, str], EMA] = {}
        self._slow_emas: dict[tuple[str, str], EMA] = {}
        self._atrs: dict[tuple[str, str], ATR] = {}
        self._vol_smas: dict[tuple[str, str], SMA] = {}

    def _get_or_create_indicators(self, symbol: str, timeframe: str) -> tuple[EMA, EMA, ATR, SMA]:
        key = (symbol, timeframe)
        if key not in self._fast_emas:
            self._fast_emas[key] = EMA(self.fast_ema_period)
            self._slow_emas[key] = EMA(self.slow_ema_period)
            self._atrs[key] = ATR(self.atr_period)
            self._vol_smas[key] = SMA(self.donchian_period)
        return (
            self._fast_emas[key],
            self._slow_emas[key],
            self._atrs[key],
            self._vol_smas[key],
        )

    def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
        """Evaluates closed candle bar for trend breakout signals."""
        if not self.handles(bar.symbol, bar.timeframe):
            return []

        fast_ema, slow_ema, atr, vol_sma = self._get_or_create_indicators(bar.symbol, bar.timeframe)

        # Update streaming indicators
        fast_ema.update(bar.close)
        slow_ema.update(bar.close)
        atr.update_bar(bar)
        vol_sma.update(bar.volume)

        # Ensure all indicators are warmed up
        if not (fast_ema.is_ready and slow_ema.is_ready and atr.is_ready and vol_sma.is_ready):
            return []

        # Ensure we have enough historical bars for Donchian channel
        total_stored = store.bar_count(bar.symbol, bar.timeframe)
        if total_stored < self.donchian_period:
            return []

        # Retrieve previous closed bars (excluding current bar)
        latest_stored = store.get_latest_bar(bar.symbol, bar.timeframe)
        if latest_stored is not None and latest_stored.timestamp == bar.timestamp:
            # Bar was already appended to store
            prior_bars = store.get_bars(bar.symbol, bar.timeframe, count=self.donchian_period + 1)[
                :-1
            ]
        else:
            prior_bars = store.get_bars(bar.symbol, bar.timeframe, count=self.donchian_period)

        if len(prior_bars) < self.donchian_period:
            return []

        highest_high = max(b.high for b in prior_bars)
        lowest_low = min(b.low for b in prior_bars)

        # Volume threshold
        avg_vol = vol_sma.value or ZERO_DECIMAL
        vol_threshold = avg_vol * self.volume_filter_multiplier
        if bar.volume < vol_threshold:
            return []

        atr_val = atr.value
        fast_val = fast_ema.value
        slow_val = slow_ema.value
        if atr_val is None or fast_val is None or slow_val is None or atr_val <= ZERO_DECIMAL:
            return []

        risk_distance = atr_val * self.atr_multiplier
        proposals: list[TradeProposal] = []

        # 1. Bullish Trend Breakout
        if fast_val > slow_val and bar.close > highest_high:
            stop_loss = bar.close - risk_distance
            if stop_loss > ZERO_DECIMAL:
                take_profit = bar.close + (risk_distance * self.rr_target_multiplier)
                proposals.append(
                    TradeProposal(
                        strategy_id=self.strategy_id,
                        symbol=bar.symbol,
                        timeframe=bar.timeframe,
                        direction=OrderSide.BUY,
                        entry_price=bar.close,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=bar.timestamp,
                        reason=(
                            f"Bullish Donchian breakout above {highest_high} with EMA uptrend "
                            f"(Fast {fast_val:.2f} > Slow {slow_val:.2f}) and ATR {atr_val:.2f}"
                        ),
                    )
                )

        # 2. Bearish Trend Breakdown
        elif fast_val < slow_val and bar.close < lowest_low:
            stop_loss = bar.close + risk_distance
            take_profit = bar.close - (risk_distance * self.rr_target_multiplier)
            if take_profit > ZERO_DECIMAL:
                proposals.append(
                    TradeProposal(
                        strategy_id=self.strategy_id,
                        symbol=bar.symbol,
                        timeframe=bar.timeframe,
                        direction=OrderSide.SELL,
                        entry_price=bar.close,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        timestamp=bar.timestamp,
                        reason=(
                            f"Bearish Donchian breakdown below {lowest_low} with EMA downtrend "
                            f"(Fast {fast_val:.2f} < Slow {slow_val:.2f}) and ATR {atr_val:.2f}"
                        ),
                    )
                )

        return proposals

    def reset(self) -> None:
        for ema in self._fast_emas.values():
            ema.reset()
        for ema in self._slow_emas.values():
            ema.reset()
        for a in self._atrs.values():
            a.reset()
        for sma in self._vol_smas.values():
            sma.reset()
