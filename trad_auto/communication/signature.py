"""Twilio webhook signature validation (HMAC-SHA1).

Twilio signs every webhook request with X-Twilio-Signature using HMAC-SHA1
over the request URL + sorted POST parameters. This module validates that
signature to reject forged webhook calls.

Reference:
    https://www.twilio.com/docs/usage/security#validating-requests
"""

import hashlib
import hmac
from base64 import b64encode


class TwilioSignatureValidator:
    """Validates X-Twilio-Signature on incoming webhook requests.

    The validator is stateless and thread-safe once constructed.
    """

    def __init__(self, auth_token: str) -> None:
        if not auth_token:
            raise ValueError("auth_token must not be empty")
        self._auth_token = auth_token

    def validate(
        self,
        url: str,
        params: dict[str, str],
        signature: str,
    ) -> bool:
        """Validates the X-Twilio-Signature header value.

        Args:
            url: The full webhook URL (scheme + host + path) that Twilio POSTed to.
            params: The POST body parameters as a flat str->str dict.
            signature: The value of the X-Twilio-Signature header.

        Returns:
            True if the signature matches, False otherwise.
        """
        if not signature:
            return False

        expected = self._compute_signature(url, params)
        return hmac.compare_digest(expected, signature)

    def _compute_signature(self, url: str, params: dict[str, str]) -> str:
        """Computes the expected Twilio HMAC-SHA1 signature.

        Algorithm (per Twilio docs):
        1. Take the full URL of the request.
        2. If the request is a POST, sort all POST parameters alphabetically
           by name and append each name and value (with no delimiters) to the URL.
        3. Sign the resulting string with HMAC-SHA1 using the auth token as the key.
        4. Base64-encode the resulting hash value.
        """
        # Ensure URL ends cleanly for consistent hashing
        data_to_sign = url

        # Sort params by key, append key+value to the URL string
        for key in sorted(params.keys()):
            data_to_sign += key + params[key]

        # HMAC-SHA1
        mac = hmac.new(
            self._auth_token.encode("utf-8"),
            data_to_sign.encode("utf-8"),
            hashlib.sha1,
        )
        return b64encode(mac.digest()).decode("utf-8")
