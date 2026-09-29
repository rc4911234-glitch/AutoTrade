"""Unit tests for AuditLogger event persistence."""

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import ChannelType, CommandType, PositionSide, SessionState
from trad_auto.core.events import (
    CommandReceivedEvent,
    KillSwitchActivatedEvent,
    PositionOpenedEvent,
    TradingSessionStateChangedEvent,
)
from trad_auto.persistence.audit_logger import AuditLogger
from trad_auto.persistence.database import DatabaseManager


class TestAuditLogger:
    """Audit trail verification tests."""

    def test_audit_logger_records_events(self, tmp_path: Path) -> None:
        db = DatabaseManager(db_path=str(tmp_path / "test_audit.db"))
        bus = EventBus()
        audit_logger = AuditLogger(event_bus=bus, db=db)

        # 1. Publish CommandReceivedEvent
        cmd_event = CommandReceivedEvent(
            command_type=CommandType.GET_STATUS,
            channel=ChannelType.CLI,
            sender_id="OWNER",
        )
        bus.publish(cmd_event)

        # 2. Publish TradingSessionStateChangedEvent
        state_event = TradingSessionStateChangedEvent(
            session_id=uuid4(),
            old_state=SessionState.IDLE,
            new_state=SessionState.TRADING,
            reason="Owner activated session",
        )
        bus.publish(state_event)

        # 3. Publish KillSwitchActivatedEvent
        kill_event = KillSwitchActivatedEvent(
            reason="Emergency stop requested",
            triggered_by="USER_COMMAND",
        )
        bus.publish(kill_event)

        # 4. Publish PositionOpenedEvent
        pos_event = PositionOpenedEvent(
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            quantity=Decimal("1.5"),
            entry_price=Decimal("45000.00"),
        )
        bus.publish(pos_event)

        # Verify all 4 events recorded in audit_events
        events = audit_logger.get_audit_events()
        assert len(events) == 4

        # Verify filtering by event type
        kill_events = audit_logger.get_audit_events(event_type="KillSwitchActivatedEvent")
        assert len(kill_events) == 1
        payload = kill_events[0]["payload"]
        assert payload["triggered_by"] == "USER_COMMAND"
        assert payload["reason"] == "Emergency stop requested"

        pos_events = audit_logger.get_audit_events(event_type="PositionOpenedEvent")
        assert len(pos_events) == 1
        pos_payload = pos_events[0]["payload"]
        assert pos_payload["symbol"] == "BTCUSDT"
        assert pos_payload["quantity"] == "1.5"
        assert pos_payload["entry_price"] == "45000.00"
