"""WhatsApp notifier subscribing to domain events and pushing alerts.

Listens on the ``EventBus`` for critical domain events and formats
concise WhatsApp notifications for the system owner.
"""

import logging
from decimal import Decimal

from trad_auto.communication.twilio_client import WhatsAppClient
from trad_auto.core.bus import EventBus
from trad_auto.core.events import (
    DailyProfitTargetReachedEvent,
    KillSwitchActivatedEvent,
    PositionClosedEvent,
    PositionOpenedEvent,
    RiskLimitBreachedEvent,
)

logger = logging.getLogger(__name__)


class WhatsAppNotifier:
    """Pushes formatted WhatsApp alerts for critical trading domain events.

    Subscribes to:
    - ``PositionOpenedEvent`` — New position entry alert.
    - ``PositionClosedEvent`` — Position exit with realized P&L.
    - ``RiskLimitBreachedEvent`` — Risk limit breach notification.
    - ``DailyProfitTargetReachedEvent`` — Gain protection trigger.
    - ``KillSwitchActivatedEvent`` — Emergency stop notification.
    """

    def __init__(
        self,
        event_bus: EventBus,
        whatsapp_client: WhatsAppClient,
        owner_phone: str,
    ) -> None:
        if not owner_phone:
            raise ValueError("owner_phone must not be empty")

        self._event_bus = event_bus
        self._client = whatsapp_client
        self._owner_phone = owner_phone

        # Register all subscriptions
        self._event_bus.subscribe(PositionOpenedEvent, self._on_position_opened)
        self._event_bus.subscribe(PositionClosedEvent, self._on_position_closed)
        self._event_bus.subscribe(RiskLimitBreachedEvent, self._on_risk_limit_breached)
        self._event_bus.subscribe(
            DailyProfitTargetReachedEvent, self._on_daily_profit_target_reached
        )
        self._event_bus.subscribe(KillSwitchActivatedEvent, self._on_kill_switch_activated)

    def _send_alert(self, body: str) -> None:
        """Sends a WhatsApp alert to the owner, swallowing send failures."""
        try:
            self._client.send_message(to=self._owner_phone, body=body)
        except Exception as exc:
            logger.warning("WhatsApp alert delivery skipped: %s (Reason: %s)", body.splitlines()[0] if body else "empty", exc)

    def _on_position_opened(self, event: PositionOpenedEvent) -> None:
        """Formats and sends a new position entry alert."""
        body = (
            f"📈 Position Opened\n"
            f"Symbol: {event.symbol}\n"
            f"Side: {event.side.value}\n"
            f"Qty: {event.quantity}\n"
            f"Entry: {event.entry_price}"
        )
        self._send_alert(body)

    def _on_position_closed(self, event: PositionClosedEvent) -> None:
        """Formats and sends a position exit alert with P&L."""
        pnl_emoji = "🟢" if event.realized_pnl >= Decimal("0") else "🔴"
        body = (
            f"📉 Position Closed\n"
            f"Symbol: {event.symbol}\n"
            f"Side: {event.side.value}\n"
            f"Qty: {event.closed_quantity}\n"
            f"Exit: {event.exit_price}\n"
            f"P&L: {pnl_emoji} {event.realized_pnl}\n"
            f"Fees: {event.total_fees}"
        )
        self._send_alert(body)

    def _on_risk_limit_breached(self, event: RiskLimitBreachedEvent) -> None:
        """Formats and sends a risk limit breach alert."""
        body = (
            f"🚨 Risk Limit Breached\n"
            f"Limit: {event.limit_type}\n"
            f"Current: {event.current_value}\n"
            f"Threshold: {event.limit_value}\n"
            f"Action: {event.action_taken}"
        )
        self._send_alert(body)

    def _on_daily_profit_target_reached(self, event: DailyProfitTargetReachedEvent) -> None:
        """Formats and sends a daily profit target alert."""
        body = (
            f"🎯 Daily Profit Target Reached\n"
            f"Realized: {event.realized_profit}\n"
            f"Target: {event.target}"
        )
        self._send_alert(body)

    def _on_kill_switch_activated(self, event: KillSwitchActivatedEvent) -> None:
        """Formats and sends an emergency kill switch alert."""
        body = (
            f"🚨 KILL SWITCH ACTIVATED\nReason: {event.reason}\nTriggered by: {event.triggered_by}"
        )
        self._send_alert(body)
