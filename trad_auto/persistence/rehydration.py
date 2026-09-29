"""System state rehydration for crash recovery and reboot resilience."""

from collections import defaultdict
from datetime import UTC, datetime

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import PositionStatus
from trad_auto.core.models.portfolio import Position, PositionLot
from trad_auto.core.models.session import TradingSession
from trad_auto.persistence.repositories.portfolio_repo import PortfolioRepository
from trad_auto.persistence.repositories.session_repo import SessionRepository
from trad_auto.portfolio.fifo_tracker import FifoPositionTracker
from trad_auto.portfolio.ledger import PositionLedger


class StateRehydrator:
    """Restores trading sessions, balances, and open position lots from database."""

    def __init__(
        self,
        session_repo: SessionRepository,
        portfolio_repo: PortfolioRepository,
    ) -> None:
        self._session_repo = session_repo
        self._portfolio_repo = portfolio_repo

    def rehydrate_session(
        self,
        session_manager: TradingSessionManager,
        now: datetime | None = None,
    ) -> TradingSession | None:
        """Restores the most recent active session into TradingSessionManager if unexpired."""
        current_time = now or datetime.now(UTC)
        session = self._session_repo.get_active_session()
        if session is None:
            return None

        # Expired sessions cannot be rehydrated as active
        if session.expires_at <= current_time:
            return None

        # Restore state into session manager
        session_manager._current_session = session
        session_manager._system_state = session.status
        session_manager._session_counter = session.session_number
        return session

    def rehydrate_ledger(self, ledger: PositionLedger) -> int:
        """Restores all asset balances and open FIFO position lots into PositionLedger.

        Returns:
            The number of active open positions restored.
        """
        # 1. Restore Balances
        balances = self._portfolio_repo.load_all_balances()
        for b in balances:
            ledger._balances[b.asset] = b

        # 2. Restore Open Lots grouped by symbol
        open_lots = self._portfolio_repo.load_open_lots()
        if not open_lots:
            return 0

        lots_by_symbol: dict[str, list[PositionLot]] = defaultdict(list)
        for lot in open_lots:
            lots_by_symbol[lot.symbol].append(lot)

        rehydrated_positions_count = 0
        now = datetime.now(UTC)

        for symbol, lots in lots_by_symbol.items():
            if not lots:
                continue

            target_side = lots[0].side
            tracker = FifoPositionTracker(symbol=symbol, side=target_side)
            for lot in lots:
                tracker.add_lot(lot)

            ledger._trackers[symbol] = tracker

            position = Position(
                symbol=symbol,
                side=target_side,
                quantity=tracker.total_quantity,
                average_entry_price=tracker.average_entry_price,
                mark_price=tracker.average_entry_price,
                lots=list(tracker.open_lots),
                unrealized_pnl=ZERO_DECIMAL,
                realized_pnl=ZERO_DECIMAL,
                status=PositionStatus.OPEN,
                updated_at=now,
            )
            ledger._positions[symbol] = position
            rehydrated_positions_count += 1

        return rehydrated_positions_count
