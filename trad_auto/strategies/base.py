"""Base abstract contract for automated trading strategies."""

from abc import ABC, abstractmethod

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.market_data.store import BarStore


class BaseStrategy(ABC):
    """Abstract base class for all quantitative trading strategies.

    Fundamental Rules:
    1. Strategies only evaluate and emit TradeProposal contracts.
    2. Strategies NEVER execute orders or interact directly with brokers.
    3. Primary signal evaluation runs exclusively on closed candles (on_bar_completed).
    """

    def __init__(
        self,
        strategy_id: str,
        symbols: list[str],
        timeframes: list[str],
    ) -> None:
        if not strategy_id:
            raise DomainValidationError("Strategy strategy_id must be non-empty")
        if not symbols or not timeframes:
            raise DomainValidationError("Strategy symbols and timeframes must be non-empty")

        self._strategy_id = strategy_id
        self._symbols = list(symbols)
        self._timeframes = list(timeframes)
        self._is_enabled = True

    @property
    def strategy_id(self) -> str:
        return self._strategy_id

    @property
    def symbols(self) -> list[str]:
        return list(self._symbols)

    @property
    def timeframes(self) -> list[str]:
        return list(self._timeframes)

    @property
    def is_enabled(self) -> bool:
        return self._is_enabled

    def enable(self) -> None:
        self._is_enabled = True

    def disable(self) -> None:
        self._is_enabled = False

    def handles(self, symbol: str, timeframe: str) -> bool:
        """Returns True if this strategy is subscribed to the given symbol and timeframe."""
        return self._is_enabled and (symbol in self._symbols) and (timeframe in self._timeframes)

    @abstractmethod
    def on_bar_completed(self, bar: Bar, store: BarStore) -> list[TradeProposal]:
        """Primary strategy hook called exclusively when a candle closes."""
        pass

    def on_quote(self, quote: Quote) -> list[TradeProposal]:
        """Intrabar top-of-book quote update hook (e.g. for trailing stop triggers)."""
        return []

    @abstractmethod
    def reset(self) -> None:
        """Resets internal indicator states."""
        pass
