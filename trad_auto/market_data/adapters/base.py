"""Base abstract adapter protocol for market data providers."""

from abc import ABC, abstractmethod

from trad_auto.core.enums import DataFeedStatus


class BaseMarketDataAdapter(ABC):
    """Abstract interface defining the market data adapter contract."""

    @property
    @abstractmethod
    def status(self) -> DataFeedStatus:
        """Returns the current operational status of the data feed."""
        pass

    @property
    def is_connected(self) -> bool:
        """Returns True if the feed is actively connected."""
        return self.status == DataFeedStatus.CONNECTED

    @property
    @abstractmethod
    def subscribed_symbols(self) -> set[str]:
        """Returns the set of actively subscribed instrument symbols."""
        pass

    @property
    @abstractmethod
    def subscribed_timeframes(self) -> set[str]:
        """Returns the set of actively subscribed candle timeframes."""
        pass

    @abstractmethod
    def connect(self) -> None:
        """Establishes connection to the data provider."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Terminates connection to the data provider."""
        pass

    @abstractmethod
    def subscribe(self, symbols: list[str], timeframes: list[str]) -> None:
        """Subscribes to market updates for symbols and timeframes."""
        pass

    @abstractmethod
    def unsubscribe(self, symbols: list[str]) -> None:
        """Cancels subscriptions for the given symbols."""
        pass
