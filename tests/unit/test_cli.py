"""End-to-end integration tests for CLICommandAdapter and CommandGateway."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from config.settings import Settings
from trad_auto.command.adapters.cli import CLICommandAdapter
from trad_auto.command.confirmation import ConfirmationManager
from trad_auto.command.deduplication import MessageDeduplicator
from trad_auto.command.gateway import CommandGateway
from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import ChannelType, CommandStatus, SessionState
from trad_auto.core.models.command import InboundMessage
from trad_auto.core.time import SimulatedClock


@pytest.fixture
def cli_stack(tmp_path: Path) -> tuple[CLICommandAdapter, TradingSessionManager, SimulatedClock]:
    """Fixture assembling a complete Phase 1 CLI and Gateway stack."""
    settings = Settings(
        trading_mode="PAPER",
        owner_cli_enabled=True,
        owner_whatsapp_number="+919876543210",
        max_authorized_capital_per_session=Decimal("5000.00"),
        confirmation_timeout_seconds=120,
    )
    clock = SimulatedClock(datetime(2026, 1, 1, 10, 0, tzinfo=UTC))
    bus = EventBus()
    dedup = MessageDeduplicator(db_path=str(tmp_path / "cli_test.db"))
    session_mgr = TradingSessionManager(event_bus=bus)
    conf_mgr = ConfirmationManager(event_bus=bus)

    gateway = CommandGateway(
        settings=settings,
        clock=clock,
        session_manager=session_mgr,
        confirmation_manager=conf_mgr,
        deduplicator=dedup,
        event_bus=bus,
    )

    adapter = CLICommandAdapter(gateway=gateway, clock=clock, sender_id="owner_cli")
    return adapter, session_mgr, clock


def test_cli_queries(
    cli_stack: tuple[CLICommandAdapter, TradingSessionManager, SimulatedClock],
) -> None:
    """CLI processes read-only deterministic queries."""
    cli, session_mgr, _ = cli_stack

    # Status
    r1 = cli.execute_string("status")
    assert r1.status == CommandStatus.EXECUTED
    assert "System State: IDLE" in r1.message

    # P&L - Phase 1 deterministic response
    r2 = cli.execute_string("pnl today")
    assert r2.status == CommandStatus.EXECUTED
    assert "P&L unavailable — portfolio/ledger module not implemented yet" in r2.message

    # Positions
    r3 = cli.execute_string("positions")
    assert r3.status == CommandStatus.EXECUTED
    assert "No open positions" in r3.message

    # Risk Status
    r4 = cli.execute_string("risk status")
    assert r4.status == CommandStatus.EXECUTED
    assert "Risk Status: Normal" in r4.message


def test_cli_session_activation_flow(
    cli_stack: tuple[CLICommandAdapter, TradingSessionManager, SimulatedClock],
) -> None:
    """CLI full two-step confirmation flow: start paper 500 -> confirm -> activated."""
    cli, session_mgr, _ = cli_stack

    # 1. Request start trading
    r1 = cli.execute_string("start paper 500")
    assert r1.status == CommandStatus.PENDING_CONFIRMATION
    assert r1.requires_confirmation is True
    assert r1.confirmation_code is not None
    code = r1.confirmation_code
    assert session_mgr.get_state() == SessionState.IDLE

    # 2. Confirm with code
    r2 = cli.execute_string(f"confirm start {code}")
    assert r2.status == CommandStatus.EXECUTED
    assert "Trading session activated with ₹500" in r2.message
    assert session_mgr.get_state() == SessionState.TRADING
    assert session_mgr.current_session is not None
    assert session_mgr.current_session.limits.authorized_capital == Decimal("500")

    # 3. Stop trading (zero positions -> IDLE)
    r3 = cli.execute_string("stop")
    assert r3.status == CommandStatus.EXECUTED
    assert session_mgr.get_state() == SessionState.IDLE


def test_cli_kill_switch_and_confirmed_reset(
    cli_stack: tuple[CLICommandAdapter, TradingSessionManager, SimulatedClock],
) -> None:
    """CLI immediate kill switch and confirmed reset flow."""
    cli, session_mgr, _ = cli_stack

    # Kill switch is immediate
    r1 = cli.execute_string("kill")
    assert r1.status == CommandStatus.EXECUTED
    assert "KILL SWITCH ACTIVATED" in r1.message
    assert session_mgr.get_state() == SessionState.EMERGENCY_STOP

    # Reset kill switch requires confirmation
    r2 = cli.execute_string("reset kill")
    assert r2.status == CommandStatus.PENDING_CONFIRMATION
    assert r2.confirmation_code is not None
    code = r2.confirmation_code

    # Confirm reset
    r3 = cli.execute_string(f"confirm reset {code}")
    assert r3.status == CommandStatus.EXECUTED
    assert "Kill switch reset successfully" in r3.message
    assert session_mgr.get_state() == SessionState.IDLE


def test_cli_duplicate_message_deduplication(
    cli_stack: tuple[CLICommandAdapter, TradingSessionManager, SimulatedClock],
) -> None:
    """Submitting the identical external_message_id returns DUPLICATE status."""
    cli, _, _ = cli_stack
    msg_id = "cli_fixed_123"

    r1 = cli.execute_string("status", external_message_id=msg_id)
    assert r1.status == CommandStatus.EXECUTED

    r2 = cli.execute_string("status", external_message_id=msg_id)
    assert r2.status == CommandStatus.DUPLICATE
    assert r1.message in r2.message


def test_unauthorized_sender_rejection(
    cli_stack: tuple[CLICommandAdapter, TradingSessionManager, SimulatedClock],
) -> None:
    """Messages from unauthorized senders are rejected."""
    cli, _, clock = cli_stack

    unauthorized_msg = InboundMessage(
        external_message_id="msg_unauth",
        channel=ChannelType.WHATSAPP,
        sender_id="+910000000000",  # Not owner number
        raw_text="status",
        received_at=clock.now(),
    )

    result = cli.process_message(unauthorized_msg)
    assert result.status == CommandStatus.REJECTED
    assert "Unauthorized sender" in result.message
