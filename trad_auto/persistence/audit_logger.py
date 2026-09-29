"""Immutable append-only audit logger capturing all domain events."""

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from trad_auto.core.bus import EventBus
from trad_auto.core.events import (
    BalanceUpdatedEvent,
    BaseEvent,
    CommandExecutedEvent,
    CommandReceivedEvent,
    ConfirmationConsumedEvent,
    ConfirmationCreatedEvent,
    DailyProfitTargetReachedEvent,
    KillSwitchActivatedEvent,
    OrderCanceledEvent,
    OrderExpiredEvent,
    OrderFilledEvent,
    OrderRejectedEvent,
    OrderSubmittedEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    PositionUpdatedEvent,
    RiskLimitBreachedEvent,
    TradeIntentCreatedEvent,
    TradeProposalEvent,
    TradeProposalRejectedEvent,
    TradingSessionStateChangedEvent,
)
from trad_auto.persistence.database import DatabaseManager

logger = logging.getLogger(__name__)


def _to_json_serializable(obj: Any) -> Any:
    """Recursively converts domain types to JSON-serializable primitives.

    Handles Decimal, UUID, datetime, Enum, and dataclass objects.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_json_serializable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_json_serializable(i) for i in obj]
    return obj


class AuditLogger:
    """Subscribes to domain events on the EventBus and writes an immutable audit trail."""

    EVENT_TYPES: tuple[type[BaseEvent], ...] = (
        CommandReceivedEvent,
        CommandExecutedEvent,
        ConfirmationCreatedEvent,
        ConfirmationConsumedEvent,
        TradingSessionStateChangedEvent,
        KillSwitchActivatedEvent,
        TradeProposalEvent,
        TradeProposalRejectedEvent,
        TradeIntentCreatedEvent,
        RiskLimitBreachedEvent,
        DailyProfitTargetReachedEvent,
        OrderSubmittedEvent,
        OrderFilledEvent,
        OrderCanceledEvent,
        OrderRejectedEvent,
        OrderExpiredEvent,
        PositionOpenedEvent,
        PositionUpdatedEvent,
        PositionClosedEvent,
        BalanceUpdatedEvent,
    )

    def __init__(self, event_bus: EventBus, db: DatabaseManager) -> None:
        self._event_bus = event_bus
        self._db = db

        for event_type in self.EVENT_TYPES:
            self._event_bus.subscribe(event_type, self._record_event)

    def _record_event(self, event: BaseEvent) -> None:
        """Captures a domain event into the audit_events table."""
        try:
            event_id = str(event.event_id)
            event_type = type(event).__name__
            timestamp = event.timestamp.isoformat()

            raw_dict = asdict(event)
            payload_data = _to_json_serializable(raw_dict)
            payload_json = json.dumps(payload_data)

            with self._db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_events (event_id, event_type, timestamp, payload_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(event_id) DO NOTHING
                    """,
                    (event_id, event_type, timestamp, payload_json),
                )
        except Exception:
            logger.exception("Failed to write audit event %s", type(event).__name__)

    def get_audit_events(
        self, event_type: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Retrieves historical audit events."""
        with self._db.get_connection() as conn:
            if event_type is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM audit_events
                    WHERE event_type = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM audit_events
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()

            results: list[dict[str, Any]] = []
            for r in rows:
                results.append(
                    {
                        "event_id": r["event_id"],
                        "event_type": r["event_type"],
                        "timestamp": r["timestamp"],
                        "payload": json.loads(r["payload_json"]),
                    }
                )
            return results
