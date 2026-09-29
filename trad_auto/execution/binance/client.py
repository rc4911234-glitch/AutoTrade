"""Binance USD-M Futures REST API client with request signing and rate limit monitoring."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from trad_auto.core.exceptions import ExchangeApiError, RateLimitExceededError
from trad_auto.execution.binance.signer import BinanceRequestSigner

logger = logging.getLogger(__name__)

BINANCE_FUTURES_LIVE_URL = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"
DEFAULT_MAX_WEIGHT_PER_MINUTE = 1200


class HttpTransport(ABC):
    """Abstract HTTP transport interface for executing network requests."""

    @abstractmethod
    def send_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        """Sends an HTTP request returning (status_code, json_payload, response_headers)."""
        pass


class UrllibHttpTransport(HttpTransport):
    """Production HTTP transport utilizing Python's standard library urllib."""

    def send_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        query_string = urllib.parse.urlencode(params or {})
        full_url = f"{url}?{query_string}" if query_string and method in ("GET", "DELETE") else url
        data = query_string.encode("utf-8") if method in ("POST", "PUT") else None

        req = urllib.request.Request(full_url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310
                status_code = resp.status
                raw_body = resp.read().decode("utf-8")
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                parsed: Any = json.loads(raw_body) if raw_body else {}
                return status_code, parsed, resp_headers
        except urllib.error.HTTPError as exc:
            raw_err = exc.read().decode("utf-8")
            err_headers = {k.lower(): v for k, v in exc.headers.items()}
            try:
                parsed_err: Any = json.loads(raw_err)
            except Exception:
                parsed_err = {"code": exc.code, "msg": raw_err}
            return exc.code, parsed_err, err_headers


class MockHttpTransport(HttpTransport):
    """Deterministic in-memory HTTP transport test double."""

    def __init__(self) -> None:
        self.sent_requests: list[dict[str, Any]] = []
        self._canned_responses: list[tuple[int, Any, dict[str, str]]] = []
        self.default_response: tuple[int, Any, dict[str, str]] = (200, {}, {})

    def queue_response(
        self,
        status_code: int,
        payload: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Queues a canned response to be returned sequentially."""
        self._canned_responses.append((status_code, payload, headers or {}))

    def send_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        self.sent_requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "params": params or {},
            }
        )
        if self._canned_responses:
            return self._canned_responses.pop(0)
        return self.default_response


class BinanceFuturesRestClient:
    """REST API client for Binance USD-M Futures.

    Features:
    - Cryptographic HMAC-SHA256 request signing via BinanceRequestSigner.
    - Rate limit weight tracking (`x-mbx-used-weight-1m`).
    - Fail-closed handling for HTTP 429 (Rate Limit) and HTTP 418 (IP Ban).
    """

    def __init__(
        self,
        signer: BinanceRequestSigner,
        base_url: str = BINANCE_FUTURES_TESTNET_URL,
        transport: HttpTransport | None = None,
        max_weight_per_min: int = DEFAULT_MAX_WEIGHT_PER_MINUTE,
    ) -> None:
        self.signer = signer
        self.base_url = base_url.rstrip("/")
        self.transport = transport or UrllibHttpTransport()
        self.max_weight_per_min = max_weight_per_min
        self.used_weight_1m: int = 0

    def sync_server_time(self) -> int:
        """Synchronizes client clock with Binance server time to eliminate clock drift."""
        try:
            status_code, body, _ = self.transport.send_request(
                "GET", f"{self.base_url}/fapi/v1/time", {}
            )
            if status_code == 200 and isinstance(body, dict) and "serverTime" in body:
                server_time = int(body["serverTime"])
                local_time = int(datetime.now(UTC).timestamp() * 1000)
                offset = server_time - local_time
                self.signer.time_offset_ms = offset
                logger.info("Synchronized Binance server time offset: %d ms", offset)
                return offset
        except Exception as exc:
            logger.warning("Failed to synchronize Binance server time: %s", exc)
        return 0

    def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        """Places a new order (POST /fapi/v1/order)."""
        result = self._request("POST", "/fapi/v1/order", params, signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def cancel_order(
        self,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancels an active order (DELETE /fapi/v1/order)."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id

        result = self._request("DELETE", "/fapi/v1/order", params, signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def cancel_all_open_orders(self, symbol: str) -> dict[str, Any]:
        """Cancels all active open orders for a symbol (DELETE /fapi/v1/allOpenOrders)."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        result = self._request("DELETE", "/fapi/v1/allOpenOrders", params, signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def get_order(
        self,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Queries order execution status (GET /fapi/v1/order)."""
        params: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id

        result = self._request("GET", "/fapi/v1/order", params, signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Queries current open orders (GET /fapi/v1/openOrders)."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()

        result = self._request("GET", "/fapi/v1/openOrders", params, signed=True)
        return list(result) if isinstance(result, list) else []

    def get_account(self) -> dict[str, Any]:
        """Queries account margin balances and assets (GET /fapi/v2/account)."""
        result = self._request("GET", "/fapi/v2/account", signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def get_position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Queries position risk and leverage stats (GET /fapi/v2/positionRisk)."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()

        result = self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        return list(result) if isinstance(result, list) else []

    def set_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        """Adjusts initial leverage for a symbol (POST /fapi/v1/leverage)."""
        params: dict[str, Any] = {"symbol": symbol.upper(), "leverage": leverage}
        result = self._request("POST", "/fapi/v1/leverage", params, signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def set_margin_type(self, symbol: str, margin_type: str) -> dict[str, Any]:
        """Sets margin mode (ISOLATED or CROSSED) (POST /fapi/v1/marginType)."""
        params: dict[str, Any] = {"symbol": symbol.upper(), "marginType": margin_type.upper()}
        result = self._request("POST", "/fapi/v1/marginType", params, signed=True)
        return dict(result) if isinstance(result, dict) else {}

    def get_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        """Queries trading rules and symbol filters (GET /fapi/v1/exchangeInfo)."""
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol.upper()
        result = self._request("GET", "/fapi/v1/exchangeInfo", params, signed=False)
        return dict(result) if isinstance(result, dict) else {}

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
        retry_on_timestamp_drift: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"
        req_params = dict(params or {})

        if signed:
            req_params = self.signer.sign_parameters(req_params)

        headers = self.signer.get_headers()
        status_code, body, resp_headers = self.transport.send_request(
            method=method,
            url=url,
            headers=headers,
            params=req_params,
        )

        # Rate limit weight tracking
        weight_str = resp_headers.get("x-mbx-used-weight-1m")
        if weight_str and weight_str.isdigit():
            self.used_weight_1m = int(weight_str)
            if self.used_weight_1m >= int(0.80 * self.max_weight_per_min):
                logger.warning(
                    "Binance API rate limit approaching: used weight %d / %d",
                    self.used_weight_1m,
                    self.max_weight_per_min,
                )

        # Fail-closed error handling
        if status_code == 429:
            raise RateLimitExceededError(
                f"Binance rate limit exceeded (HTTP 429): used_weight={self.used_weight_1m}"
            )
        if status_code == 418:
            raise RateLimitExceededError("Binance IP auto-banned (HTTP 418)")

        # NTP clock drift recovery: if -1021, resync time and retry once
        if (
            signed
            and retry_on_timestamp_drift
            and isinstance(body, dict)
            and body.get("code") == -1021
        ):
            logger.warning(
                "Binance code -1021 detected (timestamp outside recvWindow). "
                "Resynchronizing server time and retrying request..."
            )
            self.sync_server_time()
            return self._request(
                method=method,
                path=path,
                params=params,
                signed=signed,
                retry_on_timestamp_drift=False,
            )

        if status_code >= 400:
            raise ExchangeApiError(f"Binance API error (HTTP {status_code}): {body}")

        return body
