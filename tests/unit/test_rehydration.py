"""Unit tests for StateRehydrator (reboot and crash recovery)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.enums import PositionSide, SessionState, TradingMode
from trad_auto.core.models.portfolio import Balance, PositionLot
from trad_auto.core.models.session import FinancialLimits, TradingSession
from trad_auto.persistence.database import DatabaseManager
from trad_auto.persistence.rehydration import StateRehydrator
from trad_auto.persistence.repositories.portfolio_repo import PortfolioRepository
from trad_auto.persistence.repositories.session_repo import SessionRepository
from trad_auto.portfolio.ledger import PositionLedger


def _setup_repos(tmp_path: Path) -> tuple[DatabaseManager, SessionRepository, PortfolioRepository]:
    db = DatabaseManager(db_path=str(tmp_path / "test_rehydrate.db"))
    session_repo = SessionRepository(db)
    portfolio_repo = PortfolioRepository(db)
    return db, session_repo, portfolio_repo


class TestStateRehydrator:
    """State recovery and rehydration tests."""

    def test_rehydrate_active_session(self, tmp_path: Path) -> None:
        _, session_repo, portfolio_repo = _setup_repos(tmp_path)
        rehydrator = StateRehydrator(session_repo, portfolio_repo)

        now = datetime.now(UTC)
        session = TradingSession(
            session_id=uuid4(),
            session_number=5,
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("20000.00")),
            deployed_capital=Decimal("4000.00"),
            authorized_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=7),
            status=SessionState.TRADING,
            owner_command_id=uuid4(),
        )
        session_repo.save_session(session)

        # Fresh empty session manager
        new_mgr = TradingSessionManager()
        assert new_mgr.state.value == SessionState.IDLE.value

        restored = rehydrator.rehydrate_session(new_mgr, now=now)

        assert restored is not None
        assert restored.session_id == session.session_id
        assert new_mgr.state.value == SessionState.TRADING.value
        assert new_mgr.current_session is not None
        assert new_mgr.current_session.session_number == 5

    def test_expired_session_not_rehydrated(self, tmp_path: Path) -> None:
        _, session_repo, portfolio_repo = _setup_repos(tmp_path)
        rehydrator = StateRehydrator(session_repo, portfolio_repo)

        now = datetime.now(UTC)
        expired_session = TradingSession(
            session_id=uuid4(),
            session_number=1,
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("5000.00")),
            deployed_capital=Decimal("0.00"),
            authorized_at=now - timedelta(hours=10),
            expires_at=now - timedelta(hours=2),  # Expired 2 hours ago
            status=SessionState.TRADING,
            owner_command_id=uuid4(),
        )
        session_repo.save_session(expired_session)

        new_mgr = TradingSessionManager()
        restored = rehydrator.rehydrate_session(new_mgr, now=now)

        assert restored is None
        assert new_mgr.state == SessionState.IDLE
        assert new_mgr.current_session is None

    def test_rehydrate_ledger_balances_and_lots(self, tmp_path: Path) -> None:
        _, session_repo, portfolio_repo = _setup_repos(tmp_path)
        rehydrator = StateRehydrator(session_repo, portfolio_repo)

        # 1. Save Balances
        portfolio_repo.save_balance(Balance(asset="USDT", free=Decimal("10000.00")))
        portfolio_repo.save_balance(Balance(asset="ETH", free=Decimal("2.0")))

        # 2. Save 2 Open Lots for ETHUSDT: 1.0 @ 2500 and 1.0 @ 2600 -> VWAP 2550
        now = datetime.now(UTC)
        lot1 = PositionLot(
            symbol="ETHUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("2500.00"),
            initial_quantity=Decimal("1.0"),
            remaining_quantity=Decimal("1.0"),
            timestamp=now - timedelta(minutes=10),
        )
        lot2 = PositionLot(
            symbol="ETHUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("2600.00"),
            initial_quantity=Decimal("1.0"),
            remaining_quantity=Decimal("1.0"),
            timestamp=now - timedelta(minutes=5),
        )
        portfolio_repo.save_lot(lot1)
        portfolio_repo.save_lot(lot2)

        # 3. Fresh empty ledger
        new_ledger = PositionLedger()
        assert new_ledger.get_open_positions_count() == 0

        restored_positions = rehydrator.rehydrate_ledger(new_ledger)

        # 4. Verify balances and positions restored accurately
        assert restored_positions == 1
        assert new_ledger.get_balance("USDT").total == Decimal("10000.00")
        assert new_ledger.get_balance("ETH").total == Decimal("2.0")

        pos = new_ledger.get_position("ETHUSDT")
        assert pos is not None
        assert pos.symbol == "ETHUSDT"
        assert pos.side == PositionSide.LONG
        assert pos.quantity == Decimal("2.0")
        assert pos.average_entry_price == Decimal("2550.00")
        assert len(pos.lots) == 2
