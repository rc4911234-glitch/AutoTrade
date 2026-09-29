"""Generic PendingConfirmation domain model with correlation codes."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from trad_auto.core.enums import CommandType
from trad_auto.core.exceptions import ConfirmationExpiredError, DomainValidationError


@dataclass(frozen=True)
class PendingConfirmation:
    """Generic envelope holding an unconfirmed high-impact command payload."""

    pending_confirmation_id: UUID
    command_id: UUID
    owner_identity: str
    command_type: CommandType
    command_payload: dict[str, Any]
    confirmation_code: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise DomainValidationError("Confirmation timestamps must be timezone-aware (UTC)")
        if self.expires_at <= self.created_at:
            raise DomainValidationError("Confirmation expires_at must be strictly after created_at")
        if not self.confirmation_code:
            raise DomainValidationError("confirmation_code must be non-empty")

    @property
    def is_consumed(self) -> bool:
        """Derived property: True if confirmation has been consumed."""
        return self.consumed_at is not None

    def is_expired(self, current_time: datetime) -> bool:
        """Checks if current time exceeds expiration timestamp."""
        return current_time >= self.expires_at

    def consume(self, consumed_time: datetime) -> "PendingConfirmation":
        """Consumes the confirmation code once. Raises if already consumed or expired."""
        if self.is_consumed:
            raise ConfirmationExpiredError("Confirmation code has already been consumed")
        if self.is_expired(consumed_time):
            raise ConfirmationExpiredError("Confirmation code has expired")
        return replace(self, consumed_at=consumed_time)
