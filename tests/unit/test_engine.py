"""Integration and unit tests for unified TradingEngine."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from config.settings import Settings
from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import (
    OrderActionPurpose,
    OrderSide,
    OrderType,
    PositionSide,
    SessionState,
    TradingMode,
)
from trad_auto.core.events import BarCompletedEvent, QuoteUpdatedEvent
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.core.models.portfolio import Balance, PositionLot
from trad_auto.core.models.session import FinancialLimits, TradingSession
from trad_auto.core.models.strategy import TradeProposal
from trad_auto.core.time import SimulatedClock
from trad_auto.engine import TradingEngine
from trad_auto.market_data.store import BarStore
from trad_auto.persistence.database import DatabaseManager
from trad_auto.strategies.base import BaseStrategy


class _MockTriggerStrategy(BaseStrategy):
    """Deterministic strategy proposing a trade on designated bar."""

    def __init__(self, proposal: TradeProposal) -> None:
        super().__init__("mock_test_strat", [proposal.symbol], ["1h"])
        self.proposal = proposal
        self.triggered = False

    def on_bar_completed(self, bar: Bar, bar_store: BarStore) -> list[TradeProposal]:
        if not self.triggered:
            self.triggered = True
            return [self.proposal]
        return []

    def reset(self) -> None:
        self.triggered = False


class TestTradingEngineLifecycle:
    """Tests lifecycle, rehydration, and event flow of TradingEngine."""

    @pytest.fixture
    def clock(self) -> SimulatedClock:
        return SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))

    @pytest.fixture
    def test_settings(self, tmp_path: pytest.TempPathFactory) -> Settings:
        db_file = str(tmp_path) + "/engine_test.db"
        return Settings(
            trading_mode="PAPER",
            sqlite_db_path=db_file,
            max_authorized_capital_per_session=Decimal("10000.00"),
            enable_web_dashboard=False,
        )

    def test_engine_initialization_and_defaults(
        self, test_settings: Settings, clock: SimulatedClock
    ) -> None:
        engine = TradingEngine(settings=test_settings, clock=clock)
        assert engine.is_initialized is False
        assert engine.is_running is False

        engine.initialize(rehydrate=False)
        assert engine.is_initialized is True
        assert engine.is_running is False

        # Instruments registered
        assert "BTCUSDT" in engine.risk_gatekeeper._instruments
        assert "ETHUSDT" in engine.risk_gatekeeper._instruments

        # Default quote balance initialized
        usdt_bal = engine.ledger.get_balance("USDT")
        assert usdt_bal.total == Decimal("10000.00")

    def test_engine_start_stop_lifecycle(
        self, test_settings: Settings, clock: SimulatedClock
    ) -> None:
        engine = TradingEngine(settings=test_settings, clock=clock)
        engine.initialize(rehydrate=False)

        engine.start()
        assert engine.is_running is True
        assert engine.market_data_adapter.is_connected is True

        engine.stop(reason="Test completed")
        assert engine.is_running is False
        assert engine.market_data_adapter.is_connected is False

    def test_engine_context_manager(self, test_settings: Settings, clock: SimulatedClock) -> None:
        with TradingEngine(settings=test_settings, clock=clock) as engine:
            assert engine.is_initialized is True
            assert engine.is_running is True

        assert engine.is_running is False

    def test_engine_rehydration_restores_active_session_and_lots(
        self, test_settings: Settings, clock: SimulatedClock
    ) -> None:
        db = DatabaseManager(db_path=test_settings.sqlite_db_path)
        engine = TradingEngine(settings=test_settings, clock=clock, db_manager=db)

        # Pre-seed DB with an active session and open lot
        now = clock.now()
        session = TradingSession(
            session_id=uuid4(),
            session_number=1,
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("5000.00")),
            deployed_capital=Decimal("1000.00"),
            authorized_at=now,
            expires_at=now + timedelta(hours=4),
            status=SessionState.TRADING,
            owner_command_id=uuid4(),
        )
        engine.session_repo.save_session(session)

        # Seed balance and open lot
        engine.portfolio_repo.save_balance(
            Balance(asset="USDT", free=Decimal("4000.00"), locked=Decimal("1000.00"))
        )
        lot = PositionLot(
            lot_id=uuid4(),
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000.00"),
            initial_quantity=Decimal("0.02"),
            remaining_quantity=Decimal("0.02"),
            timestamp=now,
        )
        engine.portfolio_repo.save_lot(lot)

        # Initialize with rehydrate=True
        engine.initialize(rehydrate=True)

        assert engine.session_manager.state == SessionState.TRADING
        assert engine.session_manager.current_session is not None
        assert engine.session_manager.current_session.session_id == session.session_id

        # Position restored into ledger
        pos = engine.ledger.get_position("BTCUSDT")
        assert pos is not None
        assert pos.is_open is True
        assert pos.quantity == Decimal("0.02")

    def test_end_to_end_proposal_to_bracket_orders(
        self, test_settings: Settings, clock: SimulatedClock
    ) -> None:
        """End-to-end integration test:

        Bar received -> Strategy proposal -> Risk Gatekeeper sizes ->
        OrderManager submits entry -> ExecutionAdapter fills ->
        Ledger opens position -> Bracket Stop-Loss & Take-Profit placed!
        """
        engine = TradingEngine(settings=test_settings, clock=clock)
        engine.initialize(rehydrate=False)
        engine.start()

        # 1. Activate session
        now = clock.now()
        engine.session_manager.activate_session(
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("10000.00")),
            owner_command_id=uuid4(),
            current_time=now,
        )
        assert engine.session_manager.state == SessionState.TRADING

        # 2. Provide top-of-book quote so resting limit maker enters book cleanly
        quote_initial = Quote(
            symbol="BTCUSDT",
            timestamp=now,
            bid_price=Decimal("49990.00"),
            ask_price=Decimal("50010.00"),
            bid_size=Decimal("10.0"),
            ask_size=Decimal("10.0"),
        )
        engine.quote_store.update_quote(quote_initial)
        engine.event_bus.publish(QuoteUpdatedEvent(quote=quote_initial))

        # 3. Create proposal with 1:2 R:R
        proposal = TradeProposal(
            symbol="BTCUSDT",
            direction=OrderSide.BUY,
            entry_price=Decimal("50000.00"),
            stop_loss=Decimal("49000.00"),  # Risk = 1000
            take_profit=Decimal("52000.00"),  # Reward = 2000 (1:2 R:R)
            timeframe="1h",
            strategy_id="test_strat",
            timestamp=now,
            reason="Breakout signal",
        )
        strat = _MockTriggerStrategy(proposal)
        engine.register_strategy(strat)

        # 4. Trigger bar completed event -> proposal produced -> risk approved
        # Resting entry order is placed on the book
        bar = Bar(
            symbol="BTCUSDT",
            timeframe="1h",
            open=Decimal("49500.00"),
            high=Decimal("50100.00"),
            low=Decimal("49400.00"),
            close=Decimal("50000.00"),
            volume=Decimal("100.0"),
            timestamp=now,
        )
        engine.strategy_manager._handle_bar_completed_event(BarCompletedEvent(bar=bar))

        # 5. Market moves: Ask drops to 50000.00, filling the resting maker order
        quote_fill = Quote(
            symbol="BTCUSDT",
            timestamp=now + timedelta(seconds=1),
            bid_price=Decimal("49995.00"),
            ask_price=Decimal("50000.00"),
            bid_size=Decimal("10.0"),
            ask_size=Decimal("10.0"),
        )
        engine.event_bus.publish(QuoteUpdatedEvent(quote=quote_fill))

        # 6. Verify position is now open on the ledger
        pos = engine.ledger.get_position("BTCUSDT")
        assert pos is not None
        assert pos.is_open is True
        assert pos.side == PositionSide.LONG
        assert pos.quantity > ZERO_DECIMAL

        # 7. Verify protective bracket orders were placed
        active_orders = engine.execution_adapter.get_active_orders("BTCUSDT")
        assert len(active_orders) == 2  # Stop-Loss + Take-Profit
        purposes = [o.action_purpose for o in active_orders]
        assert all(p == OrderActionPurpose.EXIT for p in purposes)
        order_types = [o.order_type for o in active_orders]
        assert OrderType.STOP in order_types
        assert OrderType.LIMIT_MAKER in order_types

        # 8. Check status diagnostics snapshot
        status = engine.get_status()
        assert status["session_state"] == "TRADING"
        assert status["open_positions_count"] == 1
        assert len(status["open_positions"]) == 1
        assert status["open_positions"][0]["symbol"] == "BTCUSDT"

    def test_engine_emergency_kill_switch_flattens_and_cancels(
        self, test_settings: Settings, clock: SimulatedClock
    ) -> None:
        engine = TradingEngine(settings=test_settings, clock=clock)
        engine.initialize(rehydrate=False)
        engine.start()

        # Activate and trigger kill switch
        now = clock.now()
        engine.session_manager.activate_session(
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("5000.00")),
            owner_command_id=uuid4(),
            current_time=now,
        )

        engine.emergency_stop(reason="Unit test kill", triggered_by="Tester")
        assert engine.session_manager.state == SessionState.EMERGENCY_STOP

        # Health monitor reflects emergency stop
        health = engine.get_health()
        assert health.status.value == "UNHEALTHY"
        assert health.is_healthy is False

    def test_engine_live_mode_wiring(
        self, tmp_path: pytest.TempPathFactory, clock: SimulatedClock
    ) -> None:
        """Verifies that LIVE mode wires Binance adapters, Streaming WS, and FeedWatchdog."""
        from trad_auto.execution.binance.adapter import BinanceFuturesExecutionAdapter
        from trad_auto.market_data.streaming.adapter import StreamingMarketDataAdapter

        live_settings = Settings(
            trading_mode="LIVE",
            enable_live_trading=True,
            confirm_real_money_trading="I_UNDERSTAND_AND_ACCEPT_CAPITAL_RISK",
            binance_api_key="mock_key",
            binance_api_secret="mock_secret",
            sqlite_db_path=str(tmp_path) + "/live_engine.db",
            enable_web_dashboard=False,
        )
        engine = TradingEngine(settings=live_settings, clock=clock)

        assert isinstance(engine.execution_adapter, BinanceFuturesExecutionAdapter)
        assert isinstance(engine.market_data_adapter, StreamingMarketDataAdapter)
        assert engine.feed_watchdog is not None
        assert engine.risk_gatekeeper.feed_watchdog == engine.feed_watchdog

        # Initialize without rehydration
        engine.initialize(rehydrate=False)
        assert "BTCUSDT" in engine.execution_adapter._instruments
        assert engine.execution_adapter._instruments["BTCUSDT"].tick_size == Decimal("0.10")

    def test_engine_dashboard_snapshot_and_command(
        self, test_settings: Settings, clock: SimulatedClock
    ) -> None:
        """Verifies get_dashboard_snapshot and execute_dashboard_command functionality."""
        engine = TradingEngine(settings=test_settings, clock=clock)
        engine.initialize(rehydrate=False)

        snapshot = engine.get_dashboard_snapshot()
        assert snapshot["is_initialized"] is True
        assert "active_orders" in snapshot
        assert "trailing_info" in snapshot
        assert "news_info" in snapshot
        assert "strategy_info" in snapshot
        assert "smart_money_scalper" in snapshot["strategy_info"]

        # Test command execution
        res_status = engine.execute_dashboard_command("status")
        assert res_status["success"] is True
        assert "IDLE" in res_status["message"]

        # Test empty command
        res_empty = engine.execute_dashboard_command("  ")
        assert res_empty["success"] is False

        # Test kill switch via dashboard
        res_kill = engine.execute_dashboard_command("kill")
        assert res_kill["success"] is True
        assert engine.session_manager.state == SessionState.EMERGENCY_STOP
