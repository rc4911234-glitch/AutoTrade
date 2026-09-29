"""Average Directional Index (ADX) and Directional Movement Index (DMI).

Implements J. Welles Wilder's canonical trend strength indicator:
- True Range (TR)
- Positive & Negative Directional Movement (+DM, -DM)
- Directional Indicators (+DI, -DI)
- Directional Movement Index (DX)
- Average Directional Index (ADX) via Wilder's RMA smoothing

Values:
- ADX < 20: Weak/Absent Trend (Choppy, Sideways consolidation)
- 20 <= ADX < 25: Trend Forming
- ADX >= 25: Strong Trending Market (Favorable for Momentum/Scalping)
- ADX >= 50: Extreme / Blowoff Trend
"""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar
from trad_auto.indicators.base import BaseIndicator

DECIMAL_100 = Decimal("100.0")


class ADX(BaseIndicator):
    """Average Directional Index (ADX) indicator for regime classification."""

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise DomainValidationError("ADX period must be strictly positive")
        self._period = period

        # Historical bar state
        self._prev_high: Decimal | None = None
        self._prev_low: Decimal | None = None
        self._prev_close: Decimal | None = None

        # Cumulative initial sums for Wilder's first period
        self._count = 0
        self._initial_tr_sum = ZERO_DECIMAL
        self._initial_plus_dm_sum = ZERO_DECIMAL
        self._initial_minus_dm_sum = ZERO_DECIMAL

        # Smoothed Wilder's values
        self._smoothed_tr: Decimal | None = None
        self._smoothed_plus_dm: Decimal | None = None
        self._smoothed_minus_dm: Decimal | None = None

        # DI values
        self._plus_di: Decimal | None = None
        self._minus_di: Decimal | None = None

        # DX collection for first ADX calculation
        self._dx_history: list[Decimal] = []
        self._current_adx: Decimal | None = None

    @property
    def name(self) -> str:
        return f"ADX({self._period})"

    @property
    def period(self) -> int:
        return self._period

    @property
    def is_ready(self) -> bool:
        """True once enough bars have elapsed to produce a valid ADX."""
        return self._current_adx is not None

    @property
    def value(self) -> Decimal | None:
        """Returns the current ADX trend strength value (0 to 100)."""
        return self._current_adx

    @property
    def adx(self) -> Decimal | None:
        """Alias for value."""
        return self._current_adx

    @property
    def plus_di(self) -> Decimal | None:
        """Returns current +DI (Positive Directional Indicator)."""
        return self._plus_di

    @property
    def minus_di(self) -> Decimal | None:
        """Returns current -DI (Negative Directional Indicator)."""
        return self._minus_di

    def is_trending(self, threshold: Decimal = Decimal("25.0")) -> bool:
        """Returns True if current market is in a confirmed strong trend regime."""
        if self._current_adx is None:
            return False
        return self._current_adx >= threshold

    def update(self, value: Decimal) -> Decimal | None:
        """Fallback update using single price point."""
        return self.update_hlc(value, value, value)

    def update_bar(self, bar: Bar) -> Decimal | None:
        """Updates ADX with completed candle bar."""
        return self.update_hlc(bar.high, bar.low, bar.close)

    def update_hlc(self, high: Decimal, low: Decimal, close: Decimal) -> Decimal | None:
        """Ingests high, low, close prices and recalculates ADX."""
        if high < low:
            raise DomainValidationError(f"High ({high}) cannot be lower than Low ({low})")

        # First bar warmup: store baseline price references
        if self._prev_high is None or self._prev_low is None or self._prev_close is None:
            self._prev_high = high
            self._prev_low = low
            self._prev_close = close
            return None

        # 1. True Range (TR)
        diff1 = high - low
        diff2 = abs(high - self._prev_close)
        diff3 = abs(low - self._prev_close)
        tr = max(diff1, diff2, diff3)

        # 2. Directional Movement (+DM and -DM)
        up_move = high - self._prev_high
        down_move = self._prev_low - low

        plus_dm = ZERO_DECIMAL
        minus_dm = ZERO_DECIMAL

        if up_move > down_move and up_move > ZERO_DECIMAL:
            plus_dm = up_move
        if down_move > up_move and down_move > ZERO_DECIMAL:
            minus_dm = down_move

        self._prev_high = high
        self._prev_low = low
        self._prev_close = close
        self._count += 1

        period_dec = Decimal(str(self._period))

        # Initial accumulation phase
        if self._count < self._period:
            self._initial_tr_sum += tr
            self._initial_plus_dm_sum += plus_dm
            self._initial_minus_dm_sum += minus_dm
            return None

        if self._count == self._period:
            # First initialization of Wilder's smoothing
            self._initial_tr_sum += tr
            self._initial_plus_dm_sum += plus_dm
            self._initial_minus_dm_sum += minus_dm

            self._smoothed_tr = self._initial_tr_sum
            self._smoothed_plus_dm = self._initial_plus_dm_sum
            self._smoothed_minus_dm = self._initial_minus_dm_sum
        else:
            # Subsequent Wilder's smoothing
            assert self._smoothed_tr is not None
            assert self._smoothed_plus_dm is not None
            assert self._smoothed_minus_dm is not None

            self._smoothed_tr = self._smoothed_tr - (self._smoothed_tr / period_dec) + tr
            self._smoothed_plus_dm = (
                self._smoothed_plus_dm - (self._smoothed_plus_dm / period_dec) + plus_dm
            )
            self._smoothed_minus_dm = (
                self._smoothed_minus_dm - (self._smoothed_minus_dm / period_dec) + minus_dm
            )

        # 3. Calculate +DI and -DI
        if self._smoothed_tr > ZERO_DECIMAL:
            self._plus_di = (self._smoothed_plus_dm / self._smoothed_tr) * DECIMAL_100
            self._minus_di = (self._smoothed_minus_dm / self._smoothed_tr) * DECIMAL_100
        else:
            self._plus_di = ZERO_DECIMAL
            self._minus_di = ZERO_DECIMAL

        # 4. Calculate DX (Directional Movement Index)
        di_sum = self._plus_di + self._minus_di
        di_diff = abs(self._plus_di - self._minus_di)

        if di_sum > ZERO_DECIMAL:
            dx = (di_diff / di_sum) * DECIMAL_100
        else:
            dx = ZERO_DECIMAL

        # 5. Smooth DX to produce ADX
        if len(self._dx_history) < self._period - 1:
            self._dx_history.append(dx)
            return None

        if len(self._dx_history) == self._period - 1:
            self._dx_history.append(dx)
            initial_adx = sum(self._dx_history) / period_dec
            self._current_adx = initial_adx
            return self._current_adx

        # Subsequent ADX values: Wilder's smoothing of DX
        assert self._current_adx is not None
        self._current_adx = ((self._current_adx * (period_dec - Decimal("1.0"))) + dx) / period_dec
        return self._current_adx

    def reset(self) -> None:
        """Resets all streaming indicator states."""
        self._prev_high = None
        self._prev_low = None
        self._prev_close = None
        self._count = 0
        self._initial_tr_sum = ZERO_DECIMAL
        self._initial_plus_dm_sum = ZERO_DECIMAL
        self._initial_minus_dm_sum = ZERO_DECIMAL
        self._smoothed_tr = None
        self._smoothed_plus_dm = None
        self._smoothed_minus_dm = None
        self._plus_di = None
        self._minus_di = None
        self._dx_history.clear()
        self._current_adx = None
