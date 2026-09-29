"""Audit log domain model for financial command accountability."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from trad_auto.core.enums import ChannelType, CommandStatus, CommandType


@dataclass(frozen=True)
class CommandAuditLog:
    """Structured audit trail entry for inbound commands and outcomes."""

    audit_id: UUID
    external_message_id: str
    channel: ChannelType
    sender_id: str
    command_type: CommandType
    command_payload: dict[str, Any]
    execution_status: CommandStatus
    response_summary: str
    received_at: datetime
    completed_at: datetime
