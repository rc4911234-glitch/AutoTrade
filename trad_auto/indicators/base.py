"""Base abstract contract for streaming technical indicators."""

from abc import ABC, abstractmethod
from decimal import Decimal


class BaseIndicator(ABC):
    """Abstract interface for all incremental streaming technical indicators."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Indicator display name."""
        pass

    @property
    @abstractmethod
    def is_ready(self) -> bool:
        """Returns True when enough data points have accumulated to produce a valid value."""
        pass

    @property
    @abstractmethod
    def value(self) -> Decimal | None:
        """Current calculated indicator value, or None if not ready."""
        pass

    @abstractmethod
    def update(self, value: Decimal) -> Decimal | None:
        """Ingests a new decimal data point and returns the updated indicator value."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Resets indicator state."""
        pass
