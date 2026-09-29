"""Deterministic command parser and validator enforcing fail-closed ambiguity rules."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import ChannelType, TradingMode
from trad_auto.core.exceptions import CommandAmbiguousError
from trad_auto.core.models.command import (
    ActivateKillSwitchCommand,
    BaseCommand,
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


class CommandValidator:
    """Parses natural text into typed command schemas or fails closed."""

    def __init__(self, max_session_capital: Decimal = Decimal("10000.00")) -> None:
        self._max_session_capital = max_session_capital

    def parse_text_command(
        self,
        raw_text: str,
        sender_id: str,
        channel: ChannelType,
        external_message_id: str,
        received_at: datetime,
    ) -> BaseCommand:
        """Parses a text string into a strongly-typed command.

        Raises CommandAmbiguousError if ambiguous or invalid.
        """
        text = raw_text.strip().lower()
        if not text:
            raise CommandAmbiguousError("Empty command received")

        # 1. Status queries
        if text in ("status", "get status", "system status"):
            return GetStatusCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in (
            "pnl",
            "pnl today",
            "today pnl",
            "today's pnl",
            "profit",
            "profit today",
            "aaj ka profit",
            "aaj ka pnl",
        ):
            return GetTodayPnLCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in (
            "position",
            "positions",
            "open positions",
            "get positions",
            "portfolio",
            "trades",
        ):
            return GetPositionsCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in ("risk status", "risk", "get risk status"):
            return GetRiskStatusCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        # 2. Control actions
        if text in ("kill", "activate kill switch", "emergency stop", "kill switch"):
            return ActivateKillSwitchCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in ("reset kill", "reset kill switch", "reset emergency stop", "reset"):
            return ResetKillSwitchCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in ("stop", "stop trading", "stop trade", "ruk jao"):
            return StopTradingCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in ("pause", "pause trading", "pause trade"):
            return PauseTradingCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        if text in ("resume", "resume trading", "resume trade"):
            return ResumeTradingCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
            )

        # 3. Confirmations
        confirm_match = re.match(r"^confirm(?:\s+start|\s+reset)?\s+([a-zA-Z0-9]{1,32})$", text)
        if confirm_match:
            code = confirm_match.group(1).upper()
            return ConfirmCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
                confirmation_code=code,
            )

        # 4. Start Trading
        start_match = re.match(
            r"^start\s+(paper|live)?\s*(?:trading\s*)?(?:with\s*)?(?:₹|\$)?\s*([0-9]+(?:\.[0-9]+)?)$",
            text,
        )
        if start_match:
            mode_str = start_match.group(1)
            capital_str = start_match.group(2)

            mode = TradingMode.LIVE if mode_str == "live" else TradingMode.PAPER

            try:
                capital = Decimal(capital_str)
            except InvalidOperation:
                raise CommandAmbiguousError(f"Invalid monetary format: {capital_str}") from None

            if capital <= ZERO_DECIMAL:
                raise CommandAmbiguousError("Trading capital must be strictly positive")

            if capital > self._max_session_capital:
                ceiling_msg = (
                    f"Requested capital ({capital}) exceeds session ceiling "
                    f"({self._max_session_capital})"
                )
                raise CommandAmbiguousError(ceiling_msg)

            return StartTradingCommand(
                command_id=uuid4(),
                sender_id=sender_id,
                channel=channel,
                external_message_id=external_message_id,
                received_at=received_at,
                mode=mode,
                authorized_capital=capital,
            )

        # If no pattern cleanly matched: Fail-closed
        raise CommandAmbiguousError(
            f"Ambiguous or unrecognized command: '{raw_text}'. No action taken."
        )
