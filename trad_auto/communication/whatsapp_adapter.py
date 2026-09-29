"""WhatsApp adapter bridging Twilio webhook payloads to CommandGateway.

Responsibilities:
1. Validate X-Twilio-Signature via ``TwilioSignatureValidator``.
2. Enforce owner phone allowlist (only owner_whatsapp_number is accepted).
3. Parse Twilio POST body (``From``, ``Body``, ``MessageSid``) into ``InboundMessage``.
4. Delegate to ``CommandGateway.handle_inbound_message()``.
5. Format ``CommandResult`` into a human-readable WhatsApp reply string.
"""

import logging

from config.settings import Settings
from trad_auto.command.gateway import CommandGateway
from trad_auto.communication.signature import TwilioSignatureValidator
from trad_auto.communication.twilio_client import WhatsAppClient
from trad_auto.core.enums import ChannelType, CommandStatus
from trad_auto.core.models.command import CommandResult, InboundMessage
from trad_auto.core.time import Clock

logger = logging.getLogger(__name__)


class WhatsAppAdapterError(Exception):
    """Base exception for WhatsApp adapter failures."""


class SignatureVerificationError(WhatsAppAdapterError):
    """Raised when Twilio signature validation fails."""


class UnauthorizedSenderError(WhatsAppAdapterError):
    """Raised when the sender is not the authorized owner."""


class WhatsAppAdapter:
    """Converts Twilio webhook payloads into ``InboundMessage`` and routes to the gateway.

    The adapter performs three-layer security validation:
    1. HMAC-SHA1 signature check (Twilio auth token).
    2. Owner phone allowlist check (settings.owner_whatsapp_number).
    3. CommandGateway's own owner authentication + deduplication.
    """

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        gateway: CommandGateway,
        whatsapp_client: WhatsAppClient,
        signature_validator: TwilioSignatureValidator | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._gateway = gateway
        self._whatsapp_client = whatsapp_client
        self._signature_validator = signature_validator

    def handle_webhook(
        self,
        url: str,
        params: dict[str, str],
        signature: str,
    ) -> str:
        """Processes a Twilio webhook POST request.

        Args:
            url: Full webhook URL that Twilio POSTed to.
            params: POST body key-value pairs (From, Body, MessageSid, etc.).
            signature: X-Twilio-Signature header value.

        Returns:
            Human-readable reply text to send back via WhatsApp.

        Raises:
            SignatureVerificationError: If the Twilio signature is invalid.
            UnauthorizedSenderError: If the sender is not the authorized owner.
        """
        # 1. Validate signature (if validator is configured)
        if self._signature_validator is not None:
            if not self._signature_validator.validate(url, params, signature):
                logger.warning("Twilio signature validation failed for URL=%s", url)
                raise SignatureVerificationError("Invalid Twilio webhook signature")

        # 2. Extract fields from Twilio POST body
        from_number = params.get("From", "")
        body = params.get("Body", "")
        message_sid = params.get("MessageSid", "")

        # Strip "whatsapp:" prefix for consistent E.164 comparison
        sender_phone = from_number.removeprefix("whatsapp:")
        if not from_number.startswith("whatsapp:"):
            sender_phone = from_number

        # 3. Owner allowlist check (pre-gateway)
        owner_number = self._settings.owner_whatsapp_number.strip()
        if not owner_number or sender_phone.strip() != owner_number:
            logger.warning(
                "Unauthorized WhatsApp sender: %s (expected %s)",
                sender_phone,
                owner_number,
            )
            raise UnauthorizedSenderError(f"Sender {sender_phone} is not the authorized owner")

        # 4. Construct InboundMessage and delegate to CommandGateway
        now = self._clock.now()
        inbound = InboundMessage(
            external_message_id=message_sid,
            channel=ChannelType.WHATSAPP,
            sender_id=sender_phone,
            raw_text=body,
            received_at=now,
        )

        result = self._gateway.handle_inbound_message(inbound)

        # 5. Format reply and send back via WhatsApp (REST or fallback to TwiML)
        reply_text = self._format_reply(result)
        try:
            self._whatsapp_client.send_message(to=sender_phone, body=reply_text)
        except Exception as exc:
            logger.warning(
                "Direct outbound REST send failed (%s). Webhook will reply via TwiML.",
                exc,
            )

        return reply_text

    @staticmethod
    def _format_reply(result: CommandResult) -> str:
        """Formats a ``CommandResult`` into a human-readable WhatsApp reply."""
        status_emoji: dict[CommandStatus, str] = {
            CommandStatus.EXECUTED: "✅",
            CommandStatus.PENDING_CONFIRMATION: "⏳",
            CommandStatus.REJECTED: "❌",
            CommandStatus.COMMAND_AMBIGUOUS: "❓",
            CommandStatus.FAILED: "⚠️",
            CommandStatus.DUPLICATE: "🔁",
        }
        emoji = status_emoji.get(result.status, "ℹ️")
        return f"{emoji} {result.message}"
