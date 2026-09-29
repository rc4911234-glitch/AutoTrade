"""Confirmation manager enforcing correlation codes and single-active confirmation rule."""

import random
import string
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from trad_auto.core.bus import EventBus
from trad_auto.core.constants import CONFIRMATION_CODE_LENGTH, DEFAULT_CONFIRMATION_TIMEOUT_SECONDS
from trad_auto.core.enums import CommandType
from trad_auto.core.events import ConfirmationConsumedEvent, ConfirmationCreatedEvent
from trad_auto.core.exceptions import ConfirmationExpiredError
from trad_auto.core.models.confirmation import PendingConfirmation


class ConfirmationManager:
    """Manages high-impact command confirmations with correlated short codes."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._pending: dict[str, PendingConfirmation] = {}  # Key: owner_identity
        self._event_bus = event_bus

    @staticmethod
    def generate_code(length: int = CONFIRMATION_CODE_LENGTH) -> str:
        """Generates a random human-readable alphanumeric confirmation code."""
        chars = string.ascii_uppercase + string.digits
        # Avoid ambiguous characters 0, O, 1, I
        unambiguous = [c for c in chars if c not in ("0", "O", "1", "I")]
        return "".join(random.choices(unambiguous, k=length))

    def create_confirmation(
        self,
        command_id: UUID,
        owner_identity: str,
        command_type: CommandType,
        command_payload: dict[str, Any],
        current_time: datetime,
        timeout_seconds: int = DEFAULT_CONFIRMATION_TIMEOUT_SECONDS,
    ) -> PendingConfirmation:
        """Creates a pending confirmation envelope.

        Enforces Single-Active Rule: Any existing pending confirmation for this owner
        is automatically cancelled/overwritten.
        """
        code = self.generate_code()
        expires_at = current_time + timedelta(seconds=timeout_seconds)

        confirmation = PendingConfirmation(
            pending_confirmation_id=uuid4(),
            command_id=command_id,
            owner_identity=owner_identity,
            command_type=command_type,
            command_payload=command_payload,
            confirmation_code=code,
            created_at=current_time,
            expires_at=expires_at,
        )

        self._pending[owner_identity] = confirmation

        if self._event_bus:
            self._event_bus.publish(
                ConfirmationCreatedEvent(
                    pending_confirmation_id=confirmation.pending_confirmation_id,
                    command_id=command_id,
                    confirmation_code=code,
                    expires_at=expires_at,
                )
            )

        return confirmation

    def get_pending(self, owner_identity: str) -> PendingConfirmation | None:
        """Retrieves the active pending confirmation for the owner, if any."""
        return self._pending.get(owner_identity)

    def validate_and_consume(
        self,
        owner_identity: str,
        code: str,
        current_time: datetime,
    ) -> PendingConfirmation:
        """Validates confirmation code and consumes it once.

        Raises ConfirmationExpiredError on mismatch, expiration, or already consumed.
        """
        pending = self._pending.get(owner_identity)
        if pending is None:
            raise ConfirmationExpiredError("No pending confirmation found for this owner")

        if pending.is_consumed:
            raise ConfirmationExpiredError("Confirmation code has already been consumed")

        if pending.is_expired(current_time):
            # Clean up expired
            del self._pending[owner_identity]
            raise ConfirmationExpiredError("Confirmation code has expired")

        # Support matching exact code or case-insensitive code
        clean_code = code.strip().upper()
        # If user typed "CONFIRM START A7K2", extract code
        parts = clean_code.split()
        candidate = parts[-1] if parts else clean_code

        if candidate != pending.confirmation_code:
            raise ConfirmationExpiredError(
                f"Invalid confirmation code: expected {pending.confirmation_code}"
            )

        consumed = pending.consume(current_time)
        del self._pending[owner_identity]

        if self._event_bus:
            self._event_bus.publish(
                ConfirmationConsumedEvent(
                    pending_confirmation_id=consumed.pending_confirmation_id,
                    consumed_at=current_time,
                )
            )

        return consumed
