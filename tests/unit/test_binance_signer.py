"""Unit tests for BinanceRequestSigner."""

import hashlib
import hmac
import urllib.parse
from typing import Any

import pytest

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.execution.binance.signer import BinanceRequestSigner


def test_signer_init_validation() -> None:
    # Empty api_key
    with pytest.raises(DomainValidationError, match="api_key must be non-empty"):
        BinanceRequestSigner(api_key="", api_secret="secret")

    # Empty api_secret
    with pytest.raises(DomainValidationError, match="api_secret must be non-empty"):
        BinanceRequestSigner(api_key="key", api_secret="")

    # Invalid recv_window
    with pytest.raises(DomainValidationError, match="recv_window must be between"):
        BinanceRequestSigner(api_key="key", api_secret="secret", recv_window=0)
    with pytest.raises(DomainValidationError, match="recv_window must be between"):
        BinanceRequestSigner(api_key="key", api_secret="secret", recv_window=70000)


def test_signer_headers() -> None:
    signer = BinanceRequestSigner(api_key="my_api_key", api_secret="my_secret")
    headers = signer.get_headers()
    assert headers["X-MBX-APIKEY"] == "my_api_key"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_signer_deterministic_signature() -> None:
    api_key = "test_key"
    api_secret = "test_secret_123"
    signer = BinanceRequestSigner(api_key=api_key, api_secret=api_secret, recv_window=5000)

    params: dict[str, Any] = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": "1.0",
        "price": "50000.00",
    }
    ts_ms = 1672531200000

    signed = signer.sign_parameters(params, timestamp_ms=ts_ms)

    assert signed["timestamp"] == ts_ms
    assert signed["recvWindow"] == 5000
    assert "signature" in signed

    # Calculate expected signature manually to verify RFC 2104 compliance
    expected_params = dict(params)
    expected_params["timestamp"] = ts_ms
    expected_params["recvWindow"] = 5000
    expected_query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in expected_params.items()))
    expected_sig = hmac.new(
        api_secret.encode("utf-8"),
        expected_query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert signed["signature"] == expected_sig


def test_signer_boolean_conversion() -> None:
    signer = BinanceRequestSigner(api_key="key", api_secret="secret")
    params: dict[str, Any] = {"reduceOnly": True, "closePosition": False}
    signed = signer.sign_parameters(params, timestamp_ms=1000)

    # Boolean parameters should be converted to lowercase strings in query string
    expected_query = "closePosition=false&recvWindow=5000&reduceOnly=true&timestamp=1000"
    expected_sig = hmac.new(
        b"secret",
        expected_query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert signed["signature"] == expected_sig


def test_signer_default_timestamp() -> None:
    signer = BinanceRequestSigner(api_key="key", api_secret="secret")
    params: dict[str, Any] = {"symbol": "ETHUSDT"}
    signed = signer.sign_parameters(params)
    assert isinstance(signed["timestamp"], int)
    assert signed["timestamp"] > 0
