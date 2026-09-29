"""Cryptographic request signing for Binance USD-M Futures REST API."""

import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from trad_auto.core.exceptions import DomainValidationError


class BinanceRequestSigner:
    """Signs outbound REST API requests using HMAC-SHA256.

    Guarantees:
    1. RFC 2104 compliant HMAC-SHA256 hash generation.
    2. Replay attack protection with millisecond UTC timestamps and strict recvWindow.
    3. Mandatory X-MBX-APIKEY header generation.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        recv_window: int = 5000,
        time_offset_ms: int = 0,
    ) -> None:
        if not api_key or not api_key.strip():
            raise DomainValidationError("Binance api_key must be non-empty")
        if not api_secret or not api_secret.strip():
            raise DomainValidationError("Binance api_secret must be non-empty")
        if recv_window <= 0 or recv_window > 60000:
            raise DomainValidationError("recv_window must be between 1 and 60000 ms")

        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.recv_window = recv_window
        self.time_offset_ms = time_offset_ms

    def get_headers(self) -> dict[str, str]:
        """Generates standard Binance REST request headers."""
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def sign_parameters(
        self,
        params: dict[str, Any],
        timestamp_ms: int | None = None,
    ) -> dict[str, Any]:
        """Appends timestamp, recvWindow, and computes cryptographic HMAC-SHA256 signature."""
        signed_params = dict(params)

        if timestamp_ms is None:
            now_ms = int(datetime.now(UTC).timestamp() * 1000) + self.time_offset_ms
            signed_params["timestamp"] = now_ms
        else:
            signed_params["timestamp"] = timestamp_ms

        signed_params["recvWindow"] = self.recv_window

        # Sort items so serialization order matches HMAC hashing order exactly
        ordered_items = sorted(signed_params.items())
        query_string = urllib.parse.urlencode(
            [(k, str(v).lower() if isinstance(v, bool) else v) for k, v in ordered_items]
        )

        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        result_params: dict[str, Any] = dict(ordered_items)
        result_params["signature"] = signature
        return result_params
