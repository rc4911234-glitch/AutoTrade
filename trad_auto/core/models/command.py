"""Strongly typed immutable command models and execution results."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import ChannelType, CommandStatus, CommandType, TradingMode


class BaseCommand(BaseModel):
    """Base model for all inbound user/system commands."""

    model_config = ConfigDict(frozen=True)

    command_id: UUID = Field(default_factory=uuid4)
    sender_id: str
    channel: ChannelType
    external_message_id: str
    received_at: datetime  # Internal server clock timestamp (UTC)

    @field_validator("received_at")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("received_at must be timezone-aware (UTC)")
        return v


class StartTradingCommand(BaseCommand):
    """Command requesting activation of a new trading session."""

    model_config = ConfigDict(frozen=True)

    mode: TradingMode = TradingMode.PAPER  # Default is PAPER; LIVE must never be inferred
    authorized_capital: Decimal
    duration_hours: int | None = None

    @field_validator("authorized_capital")
    @classmethod
    def validate_positive_capital(cls, v: Decimal) -> Decimal:
        if v <= ZERO_DECIMAL:
            raise ValueError("authorized_capital must be strictly positive")
        return v


class ConfirmCommand(BaseCommand):
    """Command confirming a previously issued high-impact action."""

    model_config = ConfigDict(frozen=True)

    confirmation_code: str = Field(min_length=1, max_length=32)


class StopTradingCommand(BaseCommand):
    """Stops new trades. If open positions exist -> PAUSED; if flat -> IDLE."""

    model_config = ConfigDict(frozen=True)

    reason: str = "Owner manual stop"


class PauseTradingCommand(BaseCommand):
    """Pauses new entries while preserving protective exit management."""

    model_config = ConfigDict(frozen=True)


class ResumeTradingCommand(BaseCommand):
    """Resumes new entry evaluations from a PAUSED state."""

    model_config = ConfigDict(frozen=True)


class CloseAllPositionsCommand(BaseCommand):
    """Explicitly requests liquidation of all open positions."""

    model_config = ConfigDict(frozen=True)

    reason: str = "Owner liquidation request"
    emergency: bool = False


class ActivateKillSwitchCommand(BaseCommand):
    """Immediate safety action. Halts all new entries and cancels pending entries."""

    model_config = ConfigDict(frozen=True)

    reason: str = "Emergency stop initiated"


class ResetKillSwitchCommand(BaseCommand):
    """Requests reset of the emergency kill switch (requires confirmation)."""

    model_config = ConfigDict(frozen=True)


class GetStatusCommand(BaseCommand):
    """Read-only query for system, session, and connection status."""

    model_config = ConfigDict(frozen=True)


class GetTodayPnLCommand(BaseCommand):
    """Read-only query for today's realized and unrealized P&L."""

    model_config = ConfigDict(frozen=True)


class GetPositionsCommand(BaseCommand):
    """Read-only query for active open positions."""

    model_config = ConfigDict(frozen=True)


class GetRiskStatusCommand(BaseCommand):
    """Read-only query for risk budget and loss utilization."""

    model_config = ConfigDict(frozen=True)


class InboundMessage(BaseModel):
    """Raw envelope received from an I/O channel adapter."""

    model_config = ConfigDict(frozen=True)

    external_message_id: str
    channel: ChannelType
    sender_id: str
    raw_text: str | None = None
    received_at: datetime


class CommandResult(BaseModel):
    """Deterministic outcome returned by command handling."""

    model_config = ConfigDict(frozen=True)

    command_id: UUID
    command_type: CommandType
    status: CommandStatus
    message: str
    data: dict[str, Any] | None = None
    requires_confirmation: bool = False
    pending_confirmation_id: UUID | None = None
    confirmation_code: str | None = None
