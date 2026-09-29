"""Portfolio and ledger bridge protocol providing live exposure and P&L metrics."""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import TYPE_CHECKING

from trad_auto.core.constants import ZERO_DECIMAL

if TYPE_CHECKING:
    from trad_auto.portfolio.ledger import PositionLedger


class PortfolioRiskBridge(ABC):
    """Abstract interface providing portfolio exposure and P&L metrics for risk gates."""

    @abstractmethod
    def get_total_deployed_capital(self) -> Decimal:
        """Total capital currently locked in active positions/orders."""
        pass

    @abstractmethod
    def get_current_exposure(self, symbol: str | None = None) -> Decimal:
        """Total gross notional value of open positions (overall or per symbol)."""
        pass

    @abstractmethod
    def get_today_realized_pnl(self) -> Decimal:
        """Cumulative realized profit and loss for today."""
        pass

    @abstractmethod
    def get_current_unrealized_pnl(self) -> Decimal:
        """Mark-to-market unrealized profit and loss across all open positions."""
        pass

    @abstractmethod
    def get_open_positions_count(self) -> int:
        """Number of currently active open positions."""
        pass


class InMemoryPortfolioRiskBridge(PortfolioRiskBridge):
    """In-memory portfolio bridge for testing and risk gate validation."""

    def __init__(self) -> None:
        self._deployed_capital = ZERO_DECIMAL
        self._exposures: dict[str, Decimal] = {}
        self._today_realized_pnl = ZERO_DECIMAL
        self._current_unrealized_pnl = ZERO_DECIMAL
        self._open_positions_count = 0

    def get_total_deployed_capital(self) -> Decimal:
        return self._deployed_capital

    def get_current_exposure(self, symbol: str | None = None) -> Decimal:
        if symbol:
            return self._exposures.get(symbol, ZERO_DECIMAL)
        return sum(self._exposures.values(), ZERO_DECIMAL)

    def get_today_realized_pnl(self) -> Decimal:
        return self._today_realized_pnl

    def get_current_unrealized_pnl(self) -> Decimal:
        return self._current_unrealized_pnl

    def get_open_positions_count(self) -> int:
        return self._open_positions_count

    # State update mutators
    def set_deployed_capital(self, amount: Decimal) -> None:
        self._deployed_capital = amount

    def set_exposure(self, symbol: str, amount: Decimal) -> None:
        self._exposures[symbol] = amount

    def set_today_realized_pnl(self, pnl: Decimal) -> None:
        self._today_realized_pnl = pnl

    def set_current_unrealized_pnl(self, pnl: Decimal) -> None:
        self._current_unrealized_pnl = pnl

    def set_open_positions_count(self, count: int) -> None:
        self._open_positions_count = count

    def reset(self) -> None:
        self._deployed_capital = ZERO_DECIMAL
        self._exposures.clear()
        self._today_realized_pnl = ZERO_DECIMAL
        self._current_unrealized_pnl = ZERO_DECIMAL
        self._open_positions_count = 0


class PositionLedgerRiskBridge(PortfolioRiskBridge):
    """Bridges live PositionLedger state into PortfolioRiskBridge protocol for RiskGatekeeper."""

    def __init__(self, ledger: "PositionLedger") -> None:
        self._ledger = ledger

    def get_total_deployed_capital(self) -> Decimal:
        return self._ledger.get_total_deployed_capital()

    def get_current_exposure(self, symbol: str | None = None) -> Decimal:
        return self._ledger.get_current_exposure(symbol)

    def get_today_realized_pnl(self) -> Decimal:
        return self._ledger.get_today_realized_pnl()

    def get_current_unrealized_pnl(self) -> Decimal:
        return self._ledger.get_current_unrealized_pnl()

    def get_open_positions_count(self) -> int:
        return self._ledger.get_open_positions_count()
