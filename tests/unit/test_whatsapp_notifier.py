"""Unit tests for WhatsAppNotifier event-driven alerts."""

from decimal import Decimal
from uuid import uuid4

import pytest

from trad_auto.communication.notifier import WhatsAppNotifier
from trad_auto.communication.twilio_client import MockWhatsAppClient
from trad_auto.core.bus import EventBus
from trad_auto.core.enums import PositionSide
from trad_auto.core.events import (
    DailyProfitTargetReachedEvent,
    KillSwitchActivatedEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    RiskLimitBreachedEvent,
)

OWNER_PHONE = "+919876543210"


def _make_notifier() -> tuple[WhatsAppNotifier, MockWhatsAppClient, EventBus]:
    """Factory creating a WhatsAppNotifier wired to a MockWhatsAppClient."""
    bus = EventBus()
    client = MockWhatsAppClient()
    notifier = WhatsAppNotifier(
        event_bus=bus,
        whatsapp_client=client,
        owner_phone=OWNER_PHONE,
    )
    return notifier, client, bus


class TestWhatsAppNotifierInit:
    """Construction validation."""

    def test_empty_owner_phone_raises(self) -> None:
        bus = EventBus()
        client = MockWhatsAppClient()
        with pytest.raises(ValueError, match="owner_phone must not be empty"):
            WhatsAppNotifier(event_bus=bus, whatsapp_client=client, owner_phone="")


class TestPositionOpenedAlert:
    """PositionOpenedEvent notification tests."""

    def test_position_opened_sends_alert(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            PositionOpenedEvent(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=Decimal("0.5"),
                entry_price=Decimal("42000.00"),
                lot_id=uuid4(),
            )
        )

        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert msg.to == OWNER_PHONE
        assert "BTCUSDT" in msg.body
        assert "LONG" in msg.body
        assert "0.5" in msg.body
        assert "42000.00" in msg.body
        assert "📈" in msg.body


class TestPositionClosedAlert:
    """PositionClosedEvent notification tests."""

    def test_position_closed_profit_sends_green_alert(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            PositionClosedEvent(
                symbol="ETHUSDT",
                side=PositionSide.LONG,
                closed_quantity=Decimal("2.0"),
                exit_price=Decimal("3200.00"),
                realized_pnl=Decimal("150.00"),
                total_fees=Decimal("3.20"),
            )
        )

        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert "ETHUSDT" in msg.body
        assert "150.00" in msg.body
        assert "🟢" in msg.body  # Profit → green
        assert "3.20" in msg.body  # Fees shown

    def test_position_closed_loss_sends_red_alert(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            PositionClosedEvent(
                symbol="BTCUSDT",
                side=PositionSide.SHORT,
                closed_quantity=Decimal("1.0"),
                exit_price=Decimal("44000.00"),
                realized_pnl=Decimal("-500.00"),
                total_fees=Decimal("10.00"),
            )
        )

        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert "🔴" in msg.body  # Loss → red


class TestRiskLimitBreachedAlert:
    """RiskLimitBreachedEvent notification tests."""

    def test_risk_breach_sends_alert(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            RiskLimitBreachedEvent(
                limit_type="MAX_DRAWDOWN",
                current_value=Decimal("1500.00"),
                limit_value=Decimal("1000.00"),
                action_taken="New entries blocked",
            )
        )

        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert "MAX_DRAWDOWN" in msg.body
        assert "1500.00" in msg.body
        assert "🚨" in msg.body


class TestDailyProfitTargetAlert:
    """DailyProfitTargetReachedEvent notification tests."""

    def test_profit_target_sends_alert(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            DailyProfitTargetReachedEvent(
                realized_profit=Decimal("5000.00"),
                target=Decimal("4500.00"),
            )
        )

        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert "5000.00" in msg.body
        assert "4500.00" in msg.body
        assert "🎯" in msg.body


class TestKillSwitchAlert:
    """KillSwitchActivatedEvent notification tests."""

    def test_kill_switch_sends_alert(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            KillSwitchActivatedEvent(
                reason="Max drawdown exceeded",
                triggered_by="RiskEngine",
            )
        )

        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert "KILL SWITCH" in msg.body
        assert "Max drawdown exceeded" in msg.body
        assert "RiskEngine" in msg.body
        assert "🚨" in msg.body


class TestMultipleEvents:
    """Verifies multiple events produce multiple independent alerts."""

    def test_multiple_events_send_multiple_alerts(self) -> None:
        _, client, bus = _make_notifier()

        bus.publish(
            PositionOpenedEvent(
                symbol="BTCUSDT",
                side=PositionSide.LONG,
                quantity=Decimal("1.0"),
                entry_price=Decimal("40000.00"),
            )
        )
        bus.publish(
            KillSwitchActivatedEvent(
                reason="Circuit breaker",
                triggered_by="System",
            )
        )

        assert len(client.sent_messages) == 2
        assert "BTCUSDT" in client.sent_messages[0].body
        assert "KILL SWITCH" in client.sent_messages[1].body
