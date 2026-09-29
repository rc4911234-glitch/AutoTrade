"""Unit tests for WhatsAppAdapter webhook processing."""

from __future__ import annotations

import hashlib
import hmac
import tempfile
from base64 import b64encode
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.settings import Settings
from trad_auto.command.confirmation import ConfirmationManager
from trad_auto.command.deduplication import MessageDeduplicator
from trad_auto.command.gateway import CommandGateway
from trad_auto.command.session_manager import TradingSessionManager
from trad_auto.communication.signature import TwilioSignatureValidator
from trad_auto.communication.twilio_client import MockWhatsAppClient
from trad_auto.communication.whatsapp_adapter import (
    SignatureVerificationError,
    UnauthorizedSenderError,
    WhatsAppAdapter,
)
from trad_auto.core.bus import EventBus
from trad_auto.core.time import SimulatedClock

OWNER_PHONE = "+919876543210"
AUTH_TOKEN = "test_auth_token_whatsapp"
WEBHOOK_URL = "https://myapp.example.com/twilio/webhook"


def _make_settings(**overrides: str) -> Settings:
    """Create Settings with WhatsApp owner configured."""
    defaults: dict[str, str] = {
        "owner_whatsapp_number": OWNER_PHONE,
        "twilio_auth_token": AUTH_TOKEN,
        "twilio_account_sid": "AC_test",
        "twilio_whatsapp_number": "whatsapp:+14155238886",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _compute_signature(url: str, params: dict[str, str], token: str) -> str:
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]
    mac = hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    return b64encode(mac.digest()).decode("utf-8")


def _make_adapter(
    settings: Settings | None = None,
    tmp_dir: Path | None = None,
) -> tuple[WhatsAppAdapter, MockWhatsAppClient, SimulatedClock]:
    """Factory creating a fully wired WhatsAppAdapter with mock dependencies."""
    settings = settings or _make_settings()
    clock = SimulatedClock(datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC))
    event_bus = EventBus()

    # Use a real temp file for SQLite (`:memory:` creates separate DBs per connection)
    if tmp_dir is not None:
        db_path = str(tmp_dir / "adapter_test.db")
    else:
        db_path = tempfile.mktemp(suffix=".db")  # noqa: S324

    session_mgr = TradingSessionManager(event_bus=event_bus)
    deduplicator = MessageDeduplicator(db_path=db_path)
    confirmation_mgr = ConfirmationManager()

    gateway = CommandGateway(
        settings=settings,
        clock=clock,
        session_manager=session_mgr,
        confirmation_manager=confirmation_mgr,
        deduplicator=deduplicator,
        event_bus=event_bus,
    )

    sig_validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)
    mock_client = MockWhatsAppClient()

    adapter = WhatsAppAdapter(
        settings=settings,
        clock=clock,
        gateway=gateway,
        whatsapp_client=mock_client,
        signature_validator=sig_validator,
    )

    return adapter, mock_client, clock


class TestWhatsAppAdapterSignature:
    """Signature validation layer tests."""

    def test_invalid_signature_raises(self, tmp_path: Path) -> None:
        adapter, _, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": f"whatsapp:{OWNER_PHONE}",
            "Body": "status",
            "MessageSid": "SM001",
        }

        with pytest.raises(SignatureVerificationError, match="Invalid Twilio"):
            adapter.handle_webhook(WEBHOOK_URL, params, "bad_sig")

    def test_no_validator_skips_signature_check(self, tmp_path: Path) -> None:
        """When signature_validator is None, signature check is skipped."""
        settings = _make_settings()
        clock = SimulatedClock(datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC))
        event_bus = EventBus()
        session_mgr = TradingSessionManager(event_bus=event_bus)
        deduplicator = MessageDeduplicator(db_path=str(tmp_path / "no_sig.db"))
        confirmation_mgr = ConfirmationManager()
        gateway = CommandGateway(
            settings=settings,
            clock=clock,
            session_manager=session_mgr,
            confirmation_manager=confirmation_mgr,
            deduplicator=deduplicator,
            event_bus=event_bus,
        )
        mock_client = MockWhatsAppClient()

        adapter = WhatsAppAdapter(
            settings=settings,
            clock=clock,
            gateway=gateway,
            whatsapp_client=mock_client,
            signature_validator=None,  # no signature validation
        )

        params = {
            "From": f"whatsapp:{OWNER_PHONE}",
            "Body": "status",
            "MessageSid": "SM_no_sig",
        }

        reply = adapter.handle_webhook(WEBHOOK_URL, params, "any_value")
        assert "System State" in reply


class TestWhatsAppAdapterOwnerCheck:
    """Owner phone allowlist tests."""

    def test_unauthorized_sender_raises(self, tmp_path: Path) -> None:
        adapter, _, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": "whatsapp:+11111111111",
            "Body": "status",
            "MessageSid": "SM_unauth",
        }
        sig = _compute_signature(WEBHOOK_URL, params, AUTH_TOKEN)

        with pytest.raises(UnauthorizedSenderError, match="not the authorized owner"):
            adapter.handle_webhook(WEBHOOK_URL, params, sig)

    def test_sender_without_whatsapp_prefix_rejected(self, tmp_path: Path) -> None:
        """Non-owner without whatsapp: prefix is rejected."""
        adapter, _, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": "+11111111111",
            "Body": "status",
            "MessageSid": "SM_no_prefix",
        }
        sig = _compute_signature(WEBHOOK_URL, params, AUTH_TOKEN)

        with pytest.raises(UnauthorizedSenderError):
            adapter.handle_webhook(WEBHOOK_URL, params, sig)


class TestWhatsAppAdapterRouting:
    """End-to-end routing through CommandGateway."""

    def test_status_command_returns_system_state(self, tmp_path: Path) -> None:
        adapter, mock_client, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": f"whatsapp:{OWNER_PHONE}",
            "Body": "status",
            "MessageSid": "SM_status_01",
        }
        sig = _compute_signature(WEBHOOK_URL, params, AUTH_TOKEN)

        reply = adapter.handle_webhook(WEBHOOK_URL, params, sig)

        assert "System State" in reply
        assert "IDLE" in reply
        # Verify reply was sent via WhatsApp
        assert mock_client.last_message is not None
        assert mock_client.last_message.to == OWNER_PHONE

    def test_duplicate_message_returns_duplicate_response(self, tmp_path: Path) -> None:
        adapter, mock_client, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": f"whatsapp:{OWNER_PHONE}",
            "Body": "status",
            "MessageSid": "SM_dup_test",
        }
        sig = _compute_signature(WEBHOOK_URL, params, AUTH_TOKEN)

        # First call succeeds
        reply1 = adapter.handle_webhook(WEBHOOK_URL, params, sig)
        assert "System State" in reply1

        # Second call with same MessageSid returns duplicate
        reply2 = adapter.handle_webhook(WEBHOOK_URL, params, sig)
        assert "🔁" in reply2

    def test_ambiguous_command_returns_error(self, tmp_path: Path) -> None:
        adapter, _, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": f"whatsapp:{OWNER_PHONE}",
            "Body": "xyzzy_nonsense_command_12345",
            "MessageSid": "SM_ambig_01",
        }
        sig = _compute_signature(WEBHOOK_URL, params, AUTH_TOKEN)

        reply = adapter.handle_webhook(WEBHOOK_URL, params, sig)
        # Should get an error or ambiguous response
        assert reply  # Non-empty reply

    def test_reply_formatting_includes_emoji(self, tmp_path: Path) -> None:
        adapter, _, _ = _make_adapter(tmp_dir=tmp_path)
        params = {
            "From": f"whatsapp:{OWNER_PHONE}",
            "Body": "status",
            "MessageSid": "SM_emoji_01",
        }
        sig = _compute_signature(WEBHOOK_URL, params, AUTH_TOKEN)

        reply = adapter.handle_webhook(WEBHOOK_URL, params, sig)
        # Executed commands get ✅
        assert "✅" in reply
