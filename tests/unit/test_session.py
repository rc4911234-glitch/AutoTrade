"""Tests for TradingSessionManager state machine and transitions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import SessionState, TradingMode
from trad_auto.core.events import KillSwitchActivatedEvent, TradingSessionStateChangedEvent
from trad_auto.core.exceptions import InvalidSessionTransitionError
from trad_auto.core.models.session import FinancialLimits


@pytest.fixture
def sample_limits() -> FinancialLimits:
    return FinancialLimits(authorized_capital=Decimal("500.00"))


def test_session_manager_initial_state() -> None:
    """Manager starts in IDLE with no active session."""
    mgr = TradingSessionManager()
    assert mgr.state == SessionState.IDLE
    assert mgr.current_session is None


def test_session_activation_and_lifecycle(sample_limits: FinancialLimits) -> None:
    """Happy path: IDLE -> TRADING -> PAUSED -> TRADING -> IDLE."""
    bus = EventBus()
    state_changes: list[TradingSessionStateChangedEvent] = []
    bus.subscribe(TradingSessionStateChangedEvent, lambda e: state_changes.append(e))

    mgr = TradingSessionManager(event_bus=bus)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Activate
    session = mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now)
    assert mgr.get_state() == SessionState.TRADING
    assert session.status == SessionState.TRADING
    assert mgr.is_trading_allowed(now) is True

    # Pause
    paused = mgr.pause_session("Testing pause")
    assert mgr.get_state() == SessionState.PAUSED
    assert paused.status == SessionState.PAUSED
    assert mgr.is_trading_allowed(now) is False

    # Resume
    resumed = mgr.resume_session("Testing resume")
    assert mgr.get_state() == SessionState.TRADING
    assert resumed.status == SessionState.TRADING

    # Stop with zero positions -> IDLE
    final_state = mgr.stop_session(open_positions_count=0)
    assert final_state == SessionState.IDLE
    assert mgr.get_state() == SessionState.IDLE
    assert mgr.current_session is None

    assert len(state_changes) == 4


def test_stop_trading_with_open_positions(sample_limits: FinancialLimits) -> None:
    """Stopping trading while positions remain open must transition to PAUSED."""
    mgr = TradingSessionManager()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now)

    # Stop while 2 positions are open
    state = mgr.stop_session(open_positions_count=2, reason="Owner requested stop")
    assert state == SessionState.PAUSED
    assert mgr.state == SessionState.PAUSED


def test_risk_lock_and_reset(sample_limits: FinancialLimits) -> None:
    """Risk breach transitions TRADING -> RISK_LOCKED -> IDLE on verified reset."""
    mgr = TradingSessionManager()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now)

    locked = mgr.lock_risk("Max daily loss hit")
    assert mgr.get_state() == SessionState.RISK_LOCKED
    assert locked.status == SessionState.RISK_LOCKED
    assert mgr.is_trading_allowed(now) is False

    # Cannot activate new session while locked
    with pytest.raises(InvalidSessionTransitionError):
        mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now)

    # Explicit reset
    state = mgr.reset_risk_lock(policy_verified=True)
    assert state == SessionState.IDLE
    assert mgr.get_state() == SessionState.IDLE


def test_kill_switch_immediate_activation_from_any_state(sample_limits: FinancialLimits) -> None:
    """Kill switch activates immediately from ANY state and requires explicit reset."""
    bus = EventBus()
    kills: list[KillSwitchActivatedEvent] = []
    bus.subscribe(KillSwitchActivatedEvent, lambda e: kills.append(e))

    mgr = TradingSessionManager(event_bus=bus)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # 1. Kill from IDLE
    mgr.activate_kill_switch(reason="Panic from IDLE", triggered_by="Owner")
    assert mgr.get_state() == SessionState.EMERGENCY_STOP
    assert len(kills) == 1

    # Reset
    mgr.reset_kill_switch()
    assert mgr.get_state() == SessionState.IDLE

    # 2. Kill from TRADING
    mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now)
    mgr.activate_kill_switch(reason="Panic during trade", triggered_by="Owner")
    assert mgr.get_state() == SessionState.EMERGENCY_STOP
    assert len(kills) == 2

    # Cannot trade while in EMERGENCY_STOP
    assert mgr.is_trading_allowed(now) is False
    with pytest.raises(InvalidSessionTransitionError):
        mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now)

    mgr.reset_kill_switch()
    assert mgr.get_state() == SessionState.IDLE


def test_session_expiration_check(sample_limits: FinancialLimits) -> None:
    """is_trading_allowed returns False once session expires."""
    mgr = TradingSessionManager()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    mgr.activate_session(TradingMode.PAPER, sample_limits, uuid4(), now, duration_hours=2)

    assert mgr.is_trading_allowed(now + timedelta(hours=1)) is True
    assert mgr.is_trading_allowed(now + timedelta(hours=2, seconds=1)) is False
