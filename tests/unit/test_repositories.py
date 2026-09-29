"""Unit tests for persistence repositories (Session, Portfolio, Order)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    SessionState,
    TimeInForce,
    TradingMode,
)
from trad_auto.core.models.order import Order
from trad_auto.core.models.portfolio import Balance, PositionLot
from trad_auto.core.models.session import FinancialLimits, TradingSession
from trad_auto.persistence.database import DatabaseManager
from trad_auto.persistence.repositories.order_repo import OrderRepository
from trad_auto.persistence.repositories.portfolio_repo import PortfolioRepository
from trad_auto.persistence.repositories.session_repo import SessionRepository


def _make_db(tmp_path: Path) -> DatabaseManager:
    return DatabaseManager(db_path=str(tmp_path / "test_repo.db"))


class TestSessionRepository:
    """SessionRepository tests."""

    def test_save_and_retrieve_session(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        repo = SessionRepository(db)

        session_id = uuid4()
        now = datetime.now(UTC)
        limits = FinancialLimits(
            authorized_capital=Decimal("10000.00"),
            max_allowed_loss=Decimal("200.00"),
        )
        session = TradingSession(
            session_id=session_id,
            session_number=1,
            mode=TradingMode.PAPER,
            limits=limits,
            deployed_capital=Decimal("1500.00"),
            authorized_at=now,
            expires_at=now + timedelta(hours=8),
            status=SessionState.TRADING,
            owner_command_id=uuid4(),
        )

        repo.save_session(session)
        loaded = repo.get_session(session_id)

        assert loaded is not None
        assert loaded.session_id == session_id
        assert loaded.mode == TradingMode.PAPER
        assert loaded.status == SessionState.TRADING
        assert loaded.limits.authorized_capital == Decimal("10000.00")
        assert loaded.limits.max_allowed_loss == Decimal("200.00")
        assert loaded.deployed_capital == Decimal("1500.00")

    def test_update_session_status(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        repo = SessionRepository(db)
        session_id = uuid4()
        now = datetime.now(UTC)
        session = TradingSession(
            session_id=session_id,
            session_number=1,
            mode=TradingMode.BACKTEST,
            limits=FinancialLimits(authorized_capital=Decimal("5000.00")),
            deployed_capital=Decimal("0.00"),
            authorized_at=now,
            expires_at=now + timedelta(hours=8),
            status=SessionState.ARMED,
            owner_command_id=uuid4(),
        )
        repo.save_session(session)

        updated = repo.update_session_status(session_id, SessionState.TRADING)
        assert updated is True

        loaded = repo.get_session(session_id)
        assert loaded is not None
        assert loaded.status == SessionState.TRADING

    def test_get_active_session(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        repo = SessionRepository(db)
        now = datetime.now(UTC)

        # Idle / closed session
        s1 = TradingSession(
            session_id=uuid4(),
            session_number=1,
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("1000")),
            deployed_capital=Decimal("0"),
            authorized_at=now,
            expires_at=now + timedelta(hours=1),
            status=SessionState.IDLE,
            owner_command_id=uuid4(),
        )
        repo.save_session(s1)
        assert repo.get_active_session() is None

        # Active session
        s2 = TradingSession(
            session_id=uuid4(),
            session_number=2,
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("2000")),
            deployed_capital=Decimal("0"),
            authorized_at=now,
            expires_at=now + timedelta(hours=4),
            status=SessionState.TRADING,
            owner_command_id=uuid4(),
        )
        repo.save_session(s2)

        active = repo.get_active_session()
        assert active is not None
        assert active.session_id == s2.session_id


class TestPortfolioRepository:
    """PortfolioRepository tests for balances and position lots."""

    def test_save_and_load_balances(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        repo = PortfolioRepository(db)

        b1 = Balance(asset="USDT", free=Decimal("5000.00"), locked=Decimal("1000.00"))
        b2 = Balance(asset="BTC", free=Decimal("0.25"), locked=Decimal("0.00"))

        repo.save_balance(b1)
        repo.save_balance(b2)

        loaded_b1 = repo.get_balance("USDT")
        assert loaded_b1 is not None
        assert loaded_b1.free == Decimal("5000.00")
        assert loaded_b1.locked == Decimal("1000.00")
        assert loaded_b1.total == Decimal("6000.00")

        all_balances = repo.load_all_balances()
        assert len(all_balances) == 2

    def test_save_and_update_lots(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        repo = PortfolioRepository(db)

        now = datetime.now(UTC)
        lot1 = PositionLot(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("40000.00"),
            initial_quantity=Decimal("1.0"),
            remaining_quantity=Decimal("1.0"),
            timestamp=now,
        )
        lot2 = PositionLot(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("41000.00"),
            initial_quantity=Decimal("0.5"),
            remaining_quantity=Decimal("0.5"),
            timestamp=now + timedelta(minutes=5),
        )

        repo.save_lot(lot1)
        repo.save_lot(lot2)

        open_lots = repo.load_open_lots("BTCUSDT")
        assert len(open_lots) == 2

        # Consume lot1 completely
        repo.update_lot_remaining_quantity(lot1.lot_id, Decimal("0.0"))

        open_lots_after = repo.load_open_lots("BTCUSDT")
        assert len(open_lots_after) == 1
        assert open_lots_after[0].lot_id == lot2.lot_id

        all_lots = repo.load_all_lots("BTCUSDT")
        assert len(all_lots) == 2


class TestOrderRepository:
    """OrderRepository tests."""

    def test_save_and_get_order(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path)
        repo = OrderRepository(db)

        order_id = uuid4()
        now = datetime.now(UTC)
        order = Order(
            order_id=order_id,
            client_order_id="TEST-001",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT_MAKER,
            price=Decimal("42000.00"),
            quantity=Decimal("0.5"),
            filled_quantity=Decimal("0.0"),
            status=OrderStatus.SUBMITTED,
            action_purpose=OrderActionPurpose.NEW_ENTRY,
            time_in_force=TimeInForce.GTC,
            created_at=now,
            updated_at=now,
        )

        repo.save_order(order)
        loaded = repo.get_order(order_id)

        assert loaded is not None
        assert loaded.client_order_id == "TEST-001"
        assert loaded.price == Decimal("42000.00")
        assert loaded.status == OrderStatus.SUBMITTED

        active_orders = repo.get_active_orders()
        assert len(active_orders) == 1

        # Fill order
        order.apply_fill(Decimal("0.5"), now + timedelta(seconds=1))
        repo.save_order(order)

        assert len(repo.get_active_orders()) == 0
