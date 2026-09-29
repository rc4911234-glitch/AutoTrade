"""Bollinger Bands indicator with standard deviation bands and bandwidth."""

import math
from collections import deque
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.indicators.base import BaseIndicator
from trad_auto.indicators.moving_averages import SMA


class BollingerBands(BaseIndicator):
    """Bollinger Bands volatility and range indicator.

    Calculates:
    - Middle Band = SMA(period)
    - Upper Band = Middle + (num_std * standard_deviation)
    - Lower Band = Middle - (num_std * standard_deviation)
    - Bandwidth = (Upper - Lower) / Middle
    """

    def __init__(self, period: int = 20, num_std: Decimal = Decimal("2.0")) -> None:
        if period <= 0:
            raise DomainValidationError("Bollinger Bands period must be strictly positive")
        if num_std <= ZERO_DECIMAL:
            raise DomainValidationError("num_std must be strictly positive")

        self._period = period
        self._num_std = num_std
        self._sma = SMA(period)
        self._window: deque[Decimal] = deque(maxlen=period)

    @property
    def name(self) -> str:
        return f"BollingerBands({self._period}, {self._num_std})"

    @property
    def period(self) -> int:
        return self._period

    @property
    def is_ready(self) -> bool:
        return self._sma.is_ready and len(self._window) == self._period

    @property
    def value(self) -> Decimal | None:
        """Returns middle band as the primary value."""
        return self.middle

    @property
    def middle(self) -> Decimal | None:
        return self._sma.value

    @property
    def upper(self) -> Decimal | None:
        if not self.is_ready or self.middle is None:
            return None
        return self.middle + (self._num_std * self.std_dev)  # type: ignore[operator]

    @property
    def lower(self) -> Decimal | None:
        if not self.is_ready or self.middle is None:
            return None
        return self.middle - (self._num_std * self.std_dev)  # type: ignore[operator]

    @property
    def std_dev(self) -> Decimal | None:
        if not self.is_ready or self.middle is None:
            return None

        mean = float(self.middle)
        variance = sum((float(x) - mean) ** 2 for x in self._window) / self._period
        return Decimal(str(math.sqrt(variance)))

    @property
    def bandwidth(self) -> Decimal | None:
        if not self.is_ready or self.middle is None or self.upper is None or self.lower is None:
            return None
        if self.middle == ZERO_DECIMAL:
            return None
        return (self.upper - self.lower) / self.middle

    def update(self, value: Decimal) -> Decimal | None:
        self._window.append(value)
        self._sma.update(value)
        return self.value

    def reset(self) -> None:
        self._window.clear()
        self._sma.reset()
