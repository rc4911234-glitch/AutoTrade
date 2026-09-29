"""Tests for CommandValidator parsing, validation, and fail-closed rules."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trad_auto.command.validator import CommandValidator
from trad_auto.core.enums import ChannelType, TradingMode
from trad_auto.core.exceptions import CommandAmbiguousError
from trad_auto.core.models.command import (
    ActivateKillSwitchCommand,
    ConfirmCommand,
    GetPositionsCommand,
    GetRiskStatusCommand,
    GetStatusCommand,
    GetTodayPnLCommand,
    PauseTradingCommand,
    ResetKillSwitchCommand,
    ResumeTradingCommand,
    StartTradingCommand,
    StopTradingCommand,
)


@pytest.fixture
def validator() -> CommandValidator:
    return CommandValidator(max_session_capital=Decimal("5000.00"))


def test_validator_parses_status_and_queries(validator: CommandValidator) -> None:
    """Validator parses read-only queries."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    c1 = validator.parse_text_command("status", "user1", ChannelType.CLI, "m1", now)
    assert isinstance(c1, GetStatusCommand)

    c2 = validator.parse_text_command("pnl today", "user1", ChannelType.CLI, "m2", now)
    assert isinstance(c2, GetTodayPnLCommand)

    c3 = validator.parse_text_command("aaj ka profit", "user1", ChannelType.CLI, "m3", now)
    assert isinstance(c3, GetTodayPnLCommand)

    c4 = validator.parse_text_command("positions", "user1", ChannelType.CLI, "m4", now)
    assert isinstance(c4, GetPositionsCommand)

    c5 = validator.parse_text_command("risk status", "user1", ChannelType.CLI, "m5", now)
    assert isinstance(c5, GetRiskStatusCommand)


def test_validator_parses_controls(validator: CommandValidator) -> None:
    """Validator parses stop, pause, resume, kill, and reset kill."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    assert isinstance(
        validator.parse_text_command("kill", "u1", ChannelType.CLI, "m1", now),
        ActivateKillSwitchCommand,
    )
    assert isinstance(
        validator.parse_text_command("reset kill", "u1", ChannelType.CLI, "m2", now),
        ResetKillSwitchCommand,
    )
    assert isinstance(
        validator.parse_text_command("stop", "u1", ChannelType.CLI, "m3", now), StopTradingCommand
    )
    assert isinstance(
        validator.parse_text_command("ruk jao", "u1", ChannelType.CLI, "m4", now),
        StopTradingCommand,
    )
    assert isinstance(
        validator.parse_text_command("pause", "u1", ChannelType.CLI, "m5", now), PauseTradingCommand
    )
    assert isinstance(
        validator.parse_text_command("resume", "u1", ChannelType.CLI, "m6", now),
        ResumeTradingCommand,
    )


def test_validator_parses_start_trading(validator: CommandValidator) -> None:
    """Validator parses start trading with amount and mode."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    # Defaults to PAPER
    cmd1 = validator.parse_text_command("start 500", "u1", ChannelType.CLI, "m1", now)
    assert isinstance(cmd1, StartTradingCommand)
    assert cmd1.mode == TradingMode.PAPER
    assert cmd1.authorized_capital == Decimal("500")

    # Explicit paper
    cmd2 = validator.parse_text_command("start paper 250.50", "u1", ChannelType.CLI, "m2", now)
    assert isinstance(cmd2, StartTradingCommand)
    assert cmd2.mode == TradingMode.PAPER
    assert cmd2.authorized_capital == Decimal("250.50")

    # Explicit live
    cmd3 = validator.parse_text_command("start live 1000", "u1", ChannelType.CLI, "m3", now)
    assert isinstance(cmd3, StartTradingCommand)
    assert cmd3.mode == TradingMode.LIVE
    assert cmd3.authorized_capital == Decimal("1000")


def test_validator_parses_confirmation(validator: CommandValidator) -> None:
    """Validator parses confirm codes."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    c1 = validator.parse_text_command("confirm start A7K2", "u1", ChannelType.CLI, "m1", now)
    assert isinstance(c1, ConfirmCommand)
    assert c1.confirmation_code == "A7K2"

    c2 = validator.parse_text_command("confirm B9X4", "u1", ChannelType.CLI, "m2", now)
    assert isinstance(c2, ConfirmCommand)
    assert c2.confirmation_code == "B9X4"


def test_validator_fails_closed_on_ambiguity(validator: CommandValidator) -> None:
    """Ambiguous or invalid inputs fail closed with CommandAmbiguousError."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(CommandAmbiguousError):
        validator.parse_text_command("", "u1", ChannelType.CLI, "m1", now)

    with pytest.raises(CommandAmbiguousError):
        validator.parse_text_command(
            "buy some bitcoin now please", "u1", ChannelType.CLI, "m2", now
        )

    # Exceeding capital ceiling
    with pytest.raises(CommandAmbiguousError, match="exceeds session ceiling"):
        validator.parse_text_command("start paper 6000", "u1", ChannelType.CLI, "m3", now)

    # Zero or negative capital
    with pytest.raises(CommandAmbiguousError, match="must be strictly positive"):
        validator.parse_text_command("start paper 0", "u1", ChannelType.CLI, "m4", now)


def test_validator_parses_extended_synonyms(validator: CommandValidator) -> None:
    """Validator parses newly supported shorthand synonyms."""
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    assert isinstance(
        validator.parse_text_command("pnl", "u1", ChannelType.CLI, "m1", now), GetTodayPnLCommand
    )
    assert isinstance(
        validator.parse_text_command("profit", "u1", ChannelType.CLI, "m2", now), GetTodayPnLCommand
    )
    assert isinstance(
        validator.parse_text_command("position", "u1", ChannelType.CLI, "m3", now),
        GetPositionsCommand,
    )
    assert isinstance(
        validator.parse_text_command("portfolio", "u1", ChannelType.CLI, "m4", now),
        GetPositionsCommand,
    )
    assert isinstance(
        validator.parse_text_command("trades", "u1", ChannelType.CLI, "m5", now),
        GetPositionsCommand,
    )


def test_gateway_dynamic_pnl_and_positions_with_ledger(tmp_path: Path) -> None:
    """CommandGateway computes live dynamic PnL and formats positions when ledger connected."""
    from config.settings import Settings
    from trad_auto.command.confirmation import ConfirmationManager
    from trad_auto.command.deduplication import MessageDeduplicator
    from trad_auto.command.gateway import CommandGateway
    from trad_auto.command.session_manager import TradingSessionManager
    from trad_auto.core.enums import CommandStatus, OrderSide
    from trad_auto.core.models.command import InboundMessage
    from trad_auto.core.time import SimulatedClock
    from trad_auto.portfolio.ledger import PositionLedger

    clock = SimulatedClock(datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    settings = Settings(
        max_authorized_capital_per_session=Decimal("5000"),
        owner_whatsapp_number="+919876543210",
    )
    session_mgr = TradingSessionManager()
    conf_mgr = ConfirmationManager()
    dedup = MessageDeduplicator(db_path=str(tmp_path / "dedup.db"))
    ledger = PositionLedger(clock=clock, default_quote_asset="USDT")
    ledger.set_initial_balance("USDT", Decimal("10000.00"))

    gateway = CommandGateway(
        settings=settings,
        clock=clock,
        session_manager=session_mgr,
        confirmation_manager=conf_mgr,
        deduplicator=dedup,
        position_ledger=ledger,
    )

    # 1. When flat
    pnl_res = gateway.handle_inbound_message(
        InboundMessage(
            channel=ChannelType.CLI,
            sender_id="owner",
            raw_text="pnl",
            received_at=clock.now(),
            external_message_id="m1",
        )
    )
    assert pnl_res.status == CommandStatus.EXECUTED
    assert "Today's P&L Summary" in pnl_res.message
    assert "Net P&L: $0.00" in pnl_res.message

    pos_res = gateway.handle_inbound_message(
        InboundMessage(
            channel=ChannelType.CLI,
            sender_id="owner",
            raw_text="positions",
            received_at=clock.now(),
            external_message_id="m2",
        )
    )
    assert pos_res.status == CommandStatus.EXECUTED
    assert "No open positions" in pos_res.message

    # 2. Open a position: BUY 0.1 BTC @ 50000
    ledger.record_fill(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        price=Decimal("50000.00"),
        quantity=Decimal("0.1"),
        fee=Decimal("1.00"),
        timestamp=clock.now(),
    )

    # Query positions
    pos_res2 = gateway.handle_inbound_message(
        InboundMessage(
            channel=ChannelType.CLI,
            sender_id="owner",
            raw_text="positions",
            received_at=clock.now(),
            external_message_id="m3",
        )
    )
    assert pos_res2.status == CommandStatus.EXECUTED
    assert "Open Positions (1):" in pos_res2.message
    assert "BTCUSDT LONG: 0.1" in pos_res2.message
    assert "@ $50000.00" in pos_res2.message
