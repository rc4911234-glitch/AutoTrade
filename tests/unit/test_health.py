"""Unit tests for HealthMonitor, SystemHealthReport, and component vitals."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import TradingMode
from trad_auto.core.models.session import FinancialLimits
from trad_auto.core.time import SimulatedClock
from trad_auto.health import (
    ComponentStatus,
    HealthMonitor,
    get_process_memory_bytes,
)
from trad_auto.market_data.streaming.watchdog import FeedWatchdog
from trad_auto.persistence.database import DatabaseManager


class TestHealthMonitor:
    """Test suite verifying subsystem telemetry and health status determination."""

    def test_health_monitor_initial_state_and_defaults(self) -> None:
        clock = SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))
        monitor = HealthMonitor(clock=clock)
        report = monitor.get_health_report()

        assert report.status == ComponentStatus.HEALTHY
        assert report.is_healthy is True
        assert report.uptime_seconds == 0.0
        assert "database" in report.components
        assert "session" in report.components
        assert "market_data" in report.components
        assert "execution" in report.components
        assert "system" in report.components

    def test_database_health_check_healthy(self, tmp_path: pytest.TempPathFactory) -> None:
        db_path = str(tmp_path) + "/health_test.db"
        db_manager = DatabaseManager(db_path=db_path)
        monitor = HealthMonitor(db_manager=db_manager)

        comp = monitor.check_database()
        assert comp.status == ComponentStatus.HEALTHY
        assert "verified" in comp.message.lower()

    def test_database_health_check_unhealthy(self) -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.get_connection.side_effect = RuntimeError("Disk I/O failure")
        mock_db.db_path = "/bad/path/test.db"

        monitor = HealthMonitor(db_manager=mock_db)
        comp = monitor.check_database()

        assert comp.status == ComponentStatus.UNHEALTHY
        assert "Disk I/O failure" in comp.message

    def test_session_health_states(self) -> None:
        clock = SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))
        event_bus = EventBus()
        session_mgr = TradingSessionManager(event_bus=event_bus)
        monitor = HealthMonitor(session_manager=session_mgr, clock=clock)

        # 1. Initial IDLE state
        comp = monitor.check_session()
        assert comp.status == ComponentStatus.HEALTHY
        assert comp.details["session_state"] == "IDLE"

        # 2. Active TRADING state
        limits = FinancialLimits(authorized_capital=Decimal("5000.00"))
        session_mgr.activate_session(
            mode=TradingMode.PAPER,
            limits=limits,
            owner_command_id=uuid4(),
            current_time=clock.now(),
        )
        comp = monitor.check_session()
        assert comp.status == ComponentStatus.HEALTHY
        assert comp.details["session_state"] == "TRADING"

        # 3. RISK_LOCKED state -> DEGRADED
        session_mgr.lock_risk(reason="Daily loss limit hit")
        comp = monitor.check_session()
        assert comp.status == ComponentStatus.DEGRADED
        assert "RISK_LOCKED" in comp.message

        # 4. EMERGENCY_STOP state -> UNHEALTHY
        session_mgr.activate_kill_switch(reason="Manual trigger", triggered_by="owner")
        comp = monitor.check_session()
        assert comp.status == ComponentStatus.UNHEALTHY
        assert "EMERGENCY_STOP" in comp.message

    def test_market_data_feed_freshness(self) -> None:
        clock = SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))
        watchdog = FeedWatchdog(timeout_seconds=15.0, clock=clock)
        watchdog.track_symbol("BTCUSDT")
        monitor = HealthMonitor(feed_watchdog=watchdog, clock=clock)

        # Fresh activity
        watchdog.record_activity("BTCUSDT", timestamp=clock.now())
        comp = monitor.check_market_data()
        assert comp.status == ComponentStatus.HEALTHY

        # Advance clock by 20s -> Stale
        clock.advance(timedelta(seconds=20))
        comp = monitor.check_market_data()
        assert comp.status == ComponentStatus.DEGRADED
        assert "BTCUSDT" in comp.message

    def test_execution_adapter_rate_limit_health(self) -> None:
        clock = SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))

        # Mock adapter with normal client
        mock_adapter = MagicMock()
        mock_client = MagicMock()
        mock_client.used_weight_1m = 150
        mock_adapter.rest_client = mock_client

        monitor = HealthMonitor(execution_adapter=mock_adapter, clock=clock)
        comp = monitor.check_execution()
        assert comp.status == ComponentStatus.HEALTHY

        # Rate limit weight elevated (>1000)
        mock_client.used_weight_1m = 1120
        comp = monitor.check_execution()
        assert comp.status == ComponentStatus.DEGRADED
        assert "elevated" in comp.message.lower()

    def test_system_vitals_and_memory_helper(self) -> None:
        clock = SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))
        monitor = HealthMonitor(clock=clock)

        # Advance uptime
        clock.advance(timedelta(seconds=120))
        comp = monitor.check_system()
        assert comp.status == ComponentStatus.HEALTHY
        assert comp.details["uptime_seconds"] == 120.0

        mem = get_process_memory_bytes()
        assert isinstance(mem, int)
        assert mem >= 0

    def test_overall_status_aggregation_and_serialization(self) -> None:
        clock = SimulatedClock(datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC))
        session_mgr = TradingSessionManager()
        monitor = HealthMonitor(session_manager=session_mgr, clock=clock)

        # All healthy
        report = monitor.get_health_report()
        assert report.status == ComponentStatus.HEALTHY
        assert report.is_healthy is True

        # Activate and then lock session
        session_mgr.activate_session(
            mode=TradingMode.PAPER,
            limits=FinancialLimits(authorized_capital=Decimal("1000.00")),
            owner_command_id=uuid4(),
            current_time=clock.now(),
        )
        session_mgr.lock_risk(reason="Drawdown breaker")
        report = monitor.get_health_report()
        assert report.status == ComponentStatus.DEGRADED
        assert report.is_healthy is True

        # Session kill switch -> overall unhealthy, is_healthy=False
        session_mgr.activate_kill_switch(reason="Kill", triggered_by="owner")
        report = monitor.get_health_report()
        assert report.status == ComponentStatus.UNHEALTHY
        assert report.is_healthy is False

        # Serialization to dict
        report_dict = report.to_dict()
        assert report_dict["status"] == "UNHEALTHY"
        assert report_dict["is_healthy"] is False
        assert "session" in report_dict["components"]
        assert report_dict["components"]["session"]["status"] == "UNHEALTHY"
