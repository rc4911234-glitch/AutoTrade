"""Relative Strength Index (RSI) indicator with Wilder's smoothing."""

from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.indicators.base import BaseIndicator


class RSI(BaseIndicator):
    """Relative Strength Index (RSI) oscillator bounded in [0, 100].

    Uses classic Wilder's smoothing of upward and downward price changes.
    """

    def __init__(self, period: int = 14) -> None:
        if period <= 0:
            raise DomainValidationError("RSI period must be strictly positive")
        self._period = period
        self._prev_price: Decimal | None = None
        self._change_count = 0
        self._initial_gain_sum = ZERO_DECIMAL
        self._initial_loss_sum = ZERO_DECIMAL
        self._avg_gain: Decimal | None = None
        self._avg_loss: Decimal | None = None
        self._current_rsi: Decimal | None = None

    @property
    def name(self) -> str:
        return f"RSI({self._period})"

    @property
    def period(self) -> int:
        return self._period

    @property
    def is_ready(self) -> bool:
        return self._current_rsi is not None

    @property
    def value(self) -> Decimal | None:
        return self._current_rsi

    def update(self, value: Decimal) -> Decimal | None:
        if self._prev_price is None:
            self._prev_price = value
            return None

        change = value - self._prev_price
        self._prev_price = value

        gain = change if change > ZERO_DECIMAL else ZERO_DECIMAL
        loss = -change if change < ZERO_DECIMAL else ZERO_DECIMAL

        if self._avg_gain is None or self._avg_loss is None:
            self._initial_gain_sum += gain
            self._initial_loss_sum += loss
            self._change_count += 1

            if self._change_count == self._period:
                n = Decimal(self._period)
                self._avg_gain = self._initial_gain_sum / n
                self._avg_loss = self._initial_loss_sum / n
                self._current_rsi = self._calculate_rsi(self._avg_gain, self._avg_loss)
        else:
            n = Decimal(self._period)
            n_minus_1 = n - Decimal("1")
            self._avg_gain = (self._avg_gain * n_minus_1 + gain) / n
            self._avg_loss = (self._avg_loss * n_minus_1 + loss) / n
            self._current_rsi = self._calculate_rsi(self._avg_gain, self._avg_loss)

        return self._current_rsi

    @staticmethod
    def _calculate_rsi(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
        if avg_loss == ZERO_DECIMAL:
            return Decimal("100.0") if avg_gain > ZERO_DECIMAL else Decimal("50.0")
        if avg_gain == ZERO_DECIMAL:
            return Decimal("0.0")

        rs = avg_gain / avg_loss
        return Decimal("100.0") - (Decimal("100.0") / (Decimal("1.0") + rs))

    def reset(self) -> None:
        self._prev_price = None
        self._change_count = 0
        self._initial_gain_sum = ZERO_DECIMAL
        self._initial_loss_sum = ZERO_DECIMAL
        self._avg_gain = None
        self._avg_loss = None
        self._current_rsi = None
