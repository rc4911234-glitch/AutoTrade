"""Tests for ConfirmationManager and single-active confirmation rule."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from trad_auto.command.confirmation import ConfirmationManager
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import CommandType
from trad_auto.core.events import ConfirmationConsumedEvent, ConfirmationCreatedEvent
from trad_auto.core.exceptions import ConfirmationExpiredError


def test_confirmation_manager_creation_and_consumption() -> None:
    """Happy path: create, validate with code, consume."""
    bus = EventBus()
    created: list[ConfirmationCreatedEvent] = []
    consumed: list[ConfirmationConsumedEvent] = []
    bus.subscribe(ConfirmationCreatedEvent, lambda e: created.append(e))
    bus.subscribe(ConfirmationConsumedEvent, lambda e: consumed.append(e))

    mgr = ConfirmationManager(event_bus=bus)
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    cmd_id = uuid4()

    pending = mgr.create_confirmation(
        command_id=cmd_id,
        owner_identity="owner_123",
        command_type=CommandType.START_TRADING,
        command_payload={"mode": "PAPER", "capital": "500"},
        current_time=now,
        timeout_seconds=120,
    )

    assert len(pending.confirmation_code) == 4
    assert mgr.get_pending("owner_123") == pending
    assert len(created) == 1

    # Validate and consume using full string format "CONFIRM START <CODE>"
    code_input = f"CONFIRM START {pending.confirmation_code}"
    result = mgr.validate_and_consume("owner_123", code_input, now + timedelta(seconds=10))

    assert result.is_consumed is True
    assert mgr.get_pending("owner_123") is None
    assert len(consumed) == 1


def test_single_active_confirmation_rule() -> None:
    """Creating a second confirmation for the same owner supersedes the first."""
    mgr = ConfirmationManager()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    first = mgr.create_confirmation(
        command_id=uuid4(),
        owner_identity="owner_1",
        command_type=CommandType.START_TRADING,
        command_payload={"capital": "500"},
        current_time=now,
    )

    second = mgr.create_confirmation(
        command_id=uuid4(),
        owner_identity="owner_1",
        command_type=CommandType.RESET_KILL_SWITCH,
        command_payload={},
        current_time=now + timedelta(seconds=10),
    )

    assert mgr.get_pending("owner_1") == second

    # Attempting to use first code fails
    with pytest.raises(ConfirmationExpiredError, match="Invalid confirmation code"):
        mgr.validate_and_consume("owner_1", first.confirmation_code, now + timedelta(seconds=20))

    # Second code succeeds
    consumed = mgr.validate_and_consume(
        "owner_1", second.confirmation_code, now + timedelta(seconds=20)
    )
    assert consumed.command_type == CommandType.RESET_KILL_SWITCH


def test_confirmation_expiration() -> None:
    """Confirmation code expires after timeout window."""
    mgr = ConfirmationManager()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    pending = mgr.create_confirmation(
        command_id=uuid4(),
        owner_identity="owner_1",
        command_type=CommandType.START_TRADING,
        command_payload={},
        current_time=now,
        timeout_seconds=60,
    )

    # Attempt after 61 seconds
    with pytest.raises(ConfirmationExpiredError, match="has expired"):
        mgr.validate_and_consume("owner_1", pending.confirmation_code, now + timedelta(seconds=61))


def test_invalid_or_missing_confirmation() -> None:
    """Validation raises if no confirmation exists or code is wrong."""
    mgr = ConfirmationManager()
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    with pytest.raises(ConfirmationExpiredError, match="No pending confirmation"):
        mgr.validate_and_consume("unknown_owner", "A7K2", now)

    mgr.create_confirmation(
        command_id=uuid4(),
        owner_identity="owner_1",
        command_type=CommandType.START_TRADING,
        command_payload={},
        current_time=now,
    )

    with pytest.raises(ConfirmationExpiredError, match="Invalid confirmation code"):
        mgr.validate_and_consume("owner_1", "WRONG", now)
