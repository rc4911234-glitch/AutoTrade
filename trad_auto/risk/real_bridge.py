"""Live PositionLedger-backed portfolio risk bridge."""

from decimal import Decimal

from trad_auto.portfolio.ledger import PositionLedger
from trad_auto.risk.ledger_bridge import PortfolioRiskBridge


class LedgerPortfolioRiskBridge(PortfolioRiskBridge):
    """Production bridge routing risk engine metric checks to the live PositionLedger."""

    def __init__(self, ledger: PositionLedger) -> None:
        self._ledger = ledger

    def get_total_deployed_capital(self) -> Decimal:
        """Calculates total cost basis deployed in active positions."""
        return self._ledger.get_total_deployed_capital()

    def get_current_exposure(self, symbol: str | None = None) -> Decimal:
        """Calculates gross mark-to-market exposure."""
        return self._ledger.get_current_exposure(symbol)

    def get_today_realized_pnl(self) -> Decimal:
        """Calculates today's cumulative realized P&L."""
        return self._ledger.get_today_realized_pnl()

    def get_current_unrealized_pnl(self) -> Decimal:
        """Calculates total open mark-to-market unrealized P&L."""
        return self._ledger.get_current_unrealized_pnl()

    def get_open_positions_count(self) -> int:
        """Returns the number of active open positions."""
        return self._ledger.get_open_positions_count()
