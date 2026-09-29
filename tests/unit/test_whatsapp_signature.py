"""Unit tests for TwilioSignatureValidator (HMAC-SHA1)."""

import hashlib
import hmac
from base64 import b64encode

import pytest

from trad_auto.communication.signature import TwilioSignatureValidator

AUTH_TOKEN = "test_auth_token_12345"
WEBHOOK_URL = "https://myapp.example.com/twilio/webhook"


def _compute_expected_signature(url: str, params: dict[str, str], token: str) -> str:
    """Reference implementation of the Twilio signature algorithm."""
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]
    mac = hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1)
    return b64encode(mac.digest()).decode("utf-8")


class TestTwilioSignatureValidatorInit:
    """Construction validation."""

    def test_empty_auth_token_raises(self) -> None:
        with pytest.raises(ValueError, match="auth_token must not be empty"):
            TwilioSignatureValidator(auth_token="")


class TestTwilioSignatureValidation:
    """Functional HMAC-SHA1 validation tests."""

    def test_valid_signature_accepted(self) -> None:
        params = {
            "From": "whatsapp:+919876543210",
            "Body": "status",
            "MessageSid": "SM123456",
        }
        sig = _compute_expected_signature(WEBHOOK_URL, params, AUTH_TOKEN)
        validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)

        assert validator.validate(WEBHOOK_URL, params, sig) is True

    def test_invalid_signature_rejected(self) -> None:
        params = {"From": "whatsapp:+919876543210", "Body": "status"}
        validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)

        assert validator.validate(WEBHOOK_URL, params, "bad_signature") is False

    def test_empty_signature_rejected(self) -> None:
        params = {"From": "whatsapp:+919876543210", "Body": "status"}
        validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)

        assert validator.validate(WEBHOOK_URL, params, "") is False

    def test_wrong_auth_token_rejects(self) -> None:
        params = {"From": "whatsapp:+919876543210", "Body": "hello"}
        correct_sig = _compute_expected_signature(WEBHOOK_URL, params, AUTH_TOKEN)
        wrong_validator = TwilioSignatureValidator(auth_token="wrong_token")

        assert wrong_validator.validate(WEBHOOK_URL, params, correct_sig) is False

    def test_tampered_params_rejected(self) -> None:
        params = {"From": "whatsapp:+919876543210", "Body": "status"}
        sig = _compute_expected_signature(WEBHOOK_URL, params, AUTH_TOKEN)
        validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)

        # Tamper with the body
        tampered = {**params, "Body": "kill switch"}
        assert validator.validate(WEBHOOK_URL, tampered, sig) is False

    def test_empty_params_valid(self) -> None:
        params: dict[str, str] = {}
        sig = _compute_expected_signature(WEBHOOK_URL, params, AUTH_TOKEN)
        validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)

        assert validator.validate(WEBHOOK_URL, params, sig) is True

    def test_param_sort_order_matters(self) -> None:
        """Verifies that parameter ordering is handled correctly."""
        params = {
            "Zebra": "last",
            "Alpha": "first",
            "Middle": "center",
        }
        sig = _compute_expected_signature(WEBHOOK_URL, params, AUTH_TOKEN)
        validator = TwilioSignatureValidator(auth_token=AUTH_TOKEN)

        assert validator.validate(WEBHOOK_URL, params, sig) is True
