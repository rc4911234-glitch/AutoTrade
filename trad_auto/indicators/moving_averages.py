"""Simple Moving Average (SMA) and Exponential Moving Average (EMA) indicators."""

from collections import deque
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.indicators.base import BaseIndicator


class SMA(BaseIndicator):
    """Simple Moving Average calculated incrementally over a rolling window."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise DomainValidationError("SMA period must be strictly positive")
        self._period = period
        self._window: deque[Decimal] = deque(maxlen=period)
        self._sum = ZERO_DECIMAL

    @property
    def name(self) -> str:
        return f"SMA({self._period})"

    @property
    def period(self) -> int:
        return self._period

    @property
    def is_ready(self) -> bool:
        return len(self._window) == self._period

    @property
    def value(self) -> Decimal | None:
        if not self.is_ready:
            return None
        return self._sum / Decimal(self._period)

    def update(self, value: Decimal) -> Decimal | None:
        if len(self._window) == self._period:
            oldest = self._window[0]
            self._sum -= oldest

        self._window.append(value)
        self._sum += value
        return self.value

    def reset(self) -> None:
        self._window.clear()
        self._sum = ZERO_DECIMAL


class EMA(BaseIndicator):
    """Exponential Moving Average with SMA seed initialization."""

    def __init__(self, period: int) -> None:
        if period <= 0:
            raise DomainValidationError("EMA period must be strictly positive")
        self._period = period
        self._multiplier = Decimal("2") / Decimal(period + 1)
        self._one_minus_mult = Decimal("1") - self._multiplier
        self._sma_seed = SMA(period)
        self._current_value: Decimal | None = None

    @property
    def name(self) -> str:
        return f"EMA({self._period})"

    @property
    def period(self) -> int:
        return self._period

    @property
    def is_ready(self) -> bool:
        return self._current_value is not None

    @property
    def value(self) -> Decimal | None:
        return self._current_value

    def update(self, value: Decimal) -> Decimal | None:
        if self._current_value is None:
            self._sma_seed.update(value)
            if self._sma_seed.is_ready:
                self._current_value = self._sma_seed.value
        else:
            self._current_value = (value * self._multiplier) + (
                self._current_value * self._one_minus_mult
            )
        return self._current_value

    def reset(self) -> None:
        self._sma_seed.reset()
        self._current_value = None
