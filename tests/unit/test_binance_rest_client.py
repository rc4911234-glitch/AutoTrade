"""Unit tests for BinanceFuturesRestClient and HTTP transports."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from trad_auto.core.exceptions import ExchangeApiError, RateLimitExceededError
from trad_auto.execution.binance.client import (
    BINANCE_FUTURES_TESTNET_URL,
    BinanceFuturesRestClient,
    MockHttpTransport,
    UrllibHttpTransport,
)
from trad_auto.execution.binance.signer import BinanceRequestSigner


@pytest.fixture
def signer() -> BinanceRequestSigner:
    return BinanceRequestSigner(api_key="test_api_key", api_secret="test_api_secret")


def test_rest_client_init(signer: BinanceRequestSigner) -> None:
    client = BinanceFuturesRestClient(signer=signer)
    assert client.base_url == BINANCE_FUTURES_TESTNET_URL
    assert client.used_weight_1m == 0


def test_rest_client_place_order(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    canned_resp = {
        "orderId": 12345,
        "symbol": "BTCUSDT",
        "status": "NEW",
        "clientOrderId": "cid_1",
    }
    transport.queue_response(200, canned_resp, {"x-mbx-used-weight-1m": "10"})

    params = {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "quantity": "1.0"}
    resp = client.place_order(params)

    assert resp["orderId"] == 12345
    assert client.used_weight_1m == 10
    assert len(transport.sent_requests) == 1
    req = transport.sent_requests[0]
    assert req["method"] == "POST"
    assert req["url"].endswith("/fapi/v1/order")
    assert req["params"]["symbol"] == "BTCUSDT"
    assert "signature" in req["params"]


def test_rest_client_cancel_order(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    transport.queue_response(200, {"orderId": 12345, "status": "CANCELED"})
    resp = client.cancel_order(symbol="BTCUSDT", order_id=12345, client_order_id="cid_1")

    assert resp["status"] == "CANCELED"
    req = transport.sent_requests[0]
    assert req["method"] == "DELETE"
    assert req["url"].endswith("/fapi/v1/order")
    assert req["params"]["orderId"] == 12345
    assert req["params"]["origClientOrderId"] == "cid_1"


def test_rest_client_cancel_all_open_orders(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    transport.queue_response(200, {"code": 200, "msg": "Success"})
    resp = client.cancel_all_open_orders(symbol="ETHUSDT")

    assert resp["code"] == 200
    req = transport.sent_requests[0]
    assert req["method"] == "DELETE"
    assert req["url"].endswith("/fapi/v1/allOpenOrders")
    assert req["params"]["symbol"] == "ETHUSDT"


def test_rest_client_get_order_and_open_orders(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    # get_order
    transport.queue_response(200, {"orderId": 100, "status": "FILLED"})
    order_resp = client.get_order(symbol="BTCUSDT", client_order_id="cid_99")
    assert order_resp["orderId"] == 100

    # get_open_orders
    transport.queue_response(200, [{"orderId": 101}, {"orderId": 102}])
    open_orders = client.get_open_orders(symbol="BTCUSDT")
    assert len(open_orders) == 2


def test_rest_client_account_and_position_risk(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    # get_account
    transport.queue_response(200, {"totalWalletBalance": "5000.00"})
    acct = client.get_account()
    assert acct["totalWalletBalance"] == "5000.00"

    # get_position_risk
    transport.queue_response(200, [{"symbol": "BTCUSDT", "positionAmt": "0.5"}])
    pos_risk = client.get_position_risk(symbol="BTCUSDT")
    assert len(pos_risk) == 1
    assert pos_risk[0]["positionAmt"] == "0.5"


def test_rest_client_leverage_and_margin_type(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    # set_leverage
    transport.queue_response(200, {"symbol": "BTCUSDT", "leverage": 10})
    lev_resp = client.set_leverage("BTCUSDT", 10)
    assert lev_resp["leverage"] == 10

    # set_margin_type
    transport.queue_response(200, {"code": 200, "msg": "success"})
    margin_resp = client.set_margin_type("BTCUSDT", "ISOLATED")
    assert margin_resp["code"] == 200


def test_rest_client_rate_limit_warning_and_errors(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport, max_weight_per_min=1000)

    # 1. Used weight reaching 80%
    transport.queue_response(200, {}, {"x-mbx-used-weight-1m": "850"})
    client.get_account()
    assert client.used_weight_1m == 850

    # 2. HTTP 429
    transport.queue_response(429, {"msg": "Too Many Requests"})
    with pytest.raises(RateLimitExceededError, match="HTTP 429"):
        client.get_account()

    # 3. HTTP 418 (IP ban)
    transport.queue_response(418, {"msg": "IP Banned"})
    with pytest.raises(RateLimitExceededError, match="HTTP 418"):
        client.get_account()

    # 4. HTTP 400 (API error)
    transport.queue_response(400, {"code": -1102, "msg": "Mandatory parameter missing"})
    with pytest.raises(ExchangeApiError, match="HTTP 400"):
        client.get_account()


def test_urllib_http_transport_success_and_error() -> None:
    transport = UrllibHttpTransport()

    # Test success path with mocked urlopen
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({"status": "ok"}).encode("utf-8")
    mock_resp.headers = {"x-mbx-used-weight-1m": "5"}
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        code, body, headers = transport.send_request(
            method="GET",
            url="https://test.binance.com/api",
            headers={"X-MBX-APIKEY": "k"},
            params={"q": "1"},
        )
        assert code == 200
        assert body == {"status": "ok"}
        assert headers["x-mbx-used-weight-1m"] == "5"

    # Test HTTPError path
    mock_http_err = urllib.error.HTTPError(
        url="https://test.binance.com/api",
        code=400,
        msg="Bad Request",
        hdrs=MagicMock(items=lambda: [("content-type", "application/json")]),
        fp=MagicMock(read=lambda: json.dumps({"code": -1000, "msg": "fail"}).encode("utf-8")),
    )

    with patch("urllib.request.urlopen", side_effect=mock_http_err):
        code, body, _ = transport.send_request(
            method="POST",
            url="https://test.binance.com/api",
            headers={},
            params={"p": "test"},
        )
        assert code == 400
        assert body["code"] == -1000


def test_rest_client_get_exchange_info(signer: BinanceRequestSigner) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    canned_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                ],
            }
        ]
    }
    transport.queue_response(200, canned_info)
    info = client.get_exchange_info(symbol="BTCUSDT")

    assert "symbols" in info
    assert len(info["symbols"]) == 1
    assert transport.sent_requests[0]["params"]["symbol"] == "BTCUSDT"


def test_rest_client_auto_recovers_and_retries_on_timestamp_code_1021(
    signer: BinanceRequestSigner,
) -> None:
    transport = MockHttpTransport()
    client = BinanceFuturesRestClient(signer=signer, transport=transport)

    # 1st call: Binance returns code -1021 (timestamp outside recvWindow)
    transport.queue_response(
        400, {"code": -1021, "msg": "Timestamp for this request was 1000ms ahead."}
    )
    # 2nd call: sync_server_time response
    transport.queue_response(200, {"serverTime": 1700000005000})
    # 3rd call: retried order response succeeds
    transport.queue_response(200, {"orderId": 99999, "status": "FILLED"})

    resp = client.place_order(
        {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "0.01"}
    )

    assert resp["orderId"] == 99999
    assert resp["status"] == "FILLED"
    # Three requests must have occurred: failed request -> time sync -> retried request
    assert len(transport.sent_requests) == 3
    assert transport.sent_requests[1]["url"].endswith("/fapi/v1/time")
    assert transport.sent_requests[2]["url"].endswith("/fapi/v1/order")
