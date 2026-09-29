"""Average True Range (ATR) indicator with Wilder's RMA smoothing."""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar
from trad_auto.indicators.base import BaseIndicator


class ATR(BaseIndicator):
    """Average True Range (ATR) indicator.

    Calculates volatility using Wilder's smoothed moving average (RMA) of True Range:
    TR = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
    """

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise DomainValidationError("ATR period must be strictly positive")
        self._period = period
        self._prev_close: Decimal | None = None
        self._count = 0
        self._initial_tr_sum = ZERO_DECIMAL
        self._current_atr: Decimal | None = None

    @property
    def name(self) -> str:
        return f"ATR({self._period})"

    @property
    def period(self) -> int:
        return self._period

    @property
    def is_ready(self) -> bool:
        return self._current_atr is not None

    @property
    def value(self) -> Decimal | None:
        return self._current_atr

    def update(self, value: Decimal) -> Decimal | None:
        """Fallback update using single price (High = Low = Close)."""
        return self.update_hlc(value, value, value)

    def update_bar(self, bar: Bar) -> Decimal | None:
        """Updates ATR with a completed OHLCV candle Bar."""
        return self.update_hlc(bar.high, bar.low, bar.close)

    def update_hlc(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal | None:
        """Updates ATR with High, Low, Close prices."""
        if high < low:
            raise DomainValidationError(f"High ({high}) cannot be lower than Low ({low})")

        # Calculate True Range
        if self._prev_close is None:
            tr = high - low
        else:
            diff1 = high - low
            diff2 = abs(high - self._prev_close)
            diff3 = abs(low - self._prev_close)
            tr = max(diff1, diff2, diff3)

        self._prev_close = close

        # Smoothing using Wilder's method (RMA)
        if self._current_atr is None:
            self._initial_tr_sum += tr
            self._count += 1
            if self._count == self._period:
                self._current_atr = self._initial_tr_sum / Decimal(self._period)
        else:
            # Wilder's formula: ATR_t = (ATR_{t-1} * (N - 1) + TR_t) / N
            n = Decimal(self._period)
            self._current_atr = (self._current_atr * (n - Decimal("1")) + tr) / n

        return self._current_atr

    def reset(self) -> None:
        self._prev_close = None
        self._count = 0
        self._initial_tr_sum = ZERO_DECIMAL
        self._current_atr = None
