"""WhatsApp client abstraction for outbound messaging via Twilio.

Production implementation uses the Twilio REST API to send WhatsApp
messages. A mock implementation captures sent messages for testing.
"""

import json
import logging
from abc import ABC, abstractmethod
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from trad_auto.core.constants import SYSTEM_TIMEZONE

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SentMessage:
    """Record of an outbound WhatsApp message (for test inspection)."""

    to: str
    body: str
    sent_at: datetime = field(default_factory=lambda: datetime.now(SYSTEM_TIMEZONE))


class WhatsAppClient(ABC):
    """Abstract interface for sending outbound WhatsApp messages."""

    @abstractmethod
    def send_message(self, to: str, body: str) -> str:
        """Sends a WhatsApp text message.

        Args:
            to: Recipient phone in E.164 format (e.g. "+919876543210").
            body: Message text content.

        Returns:
            External message SID (Twilio MessageSid or mock equivalent).
        """


class TwilioWhatsAppClient(WhatsAppClient):
    """Production WhatsApp client wrapping the Twilio Messages API.

    Sends outbound WhatsApp messages via the Twilio REST API using
    HTTP Basic Auth over ``urllib.request`` (stdlib, zero extra deps).
    """

    _API_URL_TEMPLATE = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
    ) -> None:
        if not account_sid:
            raise ValueError("account_sid must not be empty")
        if not auth_token:
            raise ValueError("auth_token must not be empty")
        if not from_number:
            raise ValueError("from_number must not be empty")

        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._api_url = self._API_URL_TEMPLATE.format(account_sid=account_sid)

    def _build_auth_header(self) -> str:
        """Builds HTTP Basic Auth header value."""
        credentials = f"{self._account_sid}:{self._auth_token}"
        encoded = b64encode(credentials.encode("utf-8")).decode("utf-8")
        return f"Basic {encoded}"

    def send_message(self, to: str, body: str) -> str:
        """Sends a WhatsApp message via the Twilio REST API.

        Args:
            to: Recipient phone in E.164 format (e.g. "+917856917258").
            body: Message text content.

        Returns:
            Twilio MessageSid string (e.g. "SMxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").

        Raises:
            RuntimeError: If the Twilio API call fails.
        """
        whatsapp_to = f"whatsapp:{to}" if not to.startswith("whatsapp:") else to
        whatsapp_from = (
            f"whatsapp:{self._from_number}"
            if not self._from_number.startswith("whatsapp:")
            else self._from_number
        )

        payload = urlencode({"From": whatsapp_from, "To": whatsapp_to, "Body": body})
        request = Request(
            self._api_url,
            data=payload.encode("utf-8"),
            method="POST",
            headers={
                "Authorization": self._build_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            with urlopen(request, timeout=15) as response:
                response_data: dict[str, object] = json.loads(response.read().decode("utf-8"))
                sid = str(response_data.get("sid", ""))
                logger.info(
                    "Twilio WhatsApp sent: sid=%s from=%s to=%s body_len=%d",
                    sid,
                    whatsapp_from,
                    whatsapp_to,
                    len(body),
                )
                return sid
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            logger.error("Twilio API error %d: %s", exc.code, error_body[:500])
            raise RuntimeError(f"Twilio API error {exc.code}: {error_body[:200]}") from exc
        except URLError as exc:
            logger.error("Twilio network error: %s", exc.reason)
            raise RuntimeError(f"Twilio network error: {exc.reason}") from exc


class MockWhatsAppClient(WhatsAppClient):
    """In-memory mock client that records all sent messages for test assertions."""

    def __init__(self) -> None:
        self._sent: list[SentMessage] = []
        self._next_sid_counter: int = 0

    def send_message(self, to: str, body: str) -> str:
        """Records the message and returns a deterministic mock SID."""
        self._next_sid_counter += 1
        sid = f"SM_mock_{self._next_sid_counter:04d}"
        self._sent.append(SentMessage(to=to, body=body))
        return sid

    @property
    def sent_messages(self) -> list[SentMessage]:
        """Returns all messages sent through this mock client."""
        return list(self._sent)

    @property
    def last_message(self) -> SentMessage | None:
        """Returns the most recently sent message, or None."""
        return self._sent[-1] if self._sent else None

    def clear(self) -> None:
        """Resets the recorded messages."""
        self._sent.clear()
        self._next_sid_counter = 0
