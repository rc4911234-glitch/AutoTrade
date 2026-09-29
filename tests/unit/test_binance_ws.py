"""Unit tests for BinanceWebSocketClient."""

import json
from typing import Any

from trad_auto.market_data.streaming.binance_ws import (
    DEFAULT_BINANCE_FUTURES_WS_URL,
    BinanceWebSocketClient,
)
from trad_auto.market_data.streaming.protocol import WebSocketClientState


def test_binance_ws_initialization() -> None:
    client = BinanceWebSocketClient()
    assert client.base_url == DEFAULT_BINANCE_FUTURES_WS_URL
    assert client.state.value == WebSocketClientState.DISCONNECTED.value
    assert not client.is_connected
    assert client.subscribed_streams == set()


def test_binance_ws_connect_disconnect() -> None:
    client = BinanceWebSocketClient()
    client.connect()
    assert client.state.value == WebSocketClientState.CONNECTED.value
    assert client.is_connected

    client.disconnect()
    assert client.state.value == WebSocketClientState.DISCONNECTED.value
    assert not client.is_connected


def test_binance_ws_subscribe_unsubscribe() -> None:
    client = BinanceWebSocketClient()
    client.connect()

    # Subscribing
    client.subscribe_streams(["BTCUSDT@trade", "btcusdt@bookTicker"])
    assert client.subscribed_streams == {"btcusdt@trade", "btcusdt@bookTicker"}
    assert len(client.sent_frames) == 1
    assert client.sent_frames[0]["method"] == "SUBSCRIBE"
    assert client.sent_frames[0]["params"] == ["btcusdt@trade", "btcusdt@bookTicker"]
    assert client.sent_frames[0]["id"] == 1

    # Empty subscription does nothing
    client.subscribe_streams([])
    assert len(client.sent_frames) == 1

    # Unsubscribing
    client.unsubscribe_streams(["btcusdt@trade"])
    assert client.subscribed_streams == {"btcusdt@bookTicker"}
    assert len(client.sent_frames) == 2
    assert client.sent_frames[1]["method"] == "UNSUBSCRIBE"
    assert client.sent_frames[1]["params"] == ["btcusdt@trade"]
    assert client.sent_frames[1]["id"] == 2


def test_binance_ws_stream_topic_builders() -> None:
    assert BinanceWebSocketClient.build_trade_stream("BTCUSDT") == "btcusdt@trade"
    assert BinanceWebSocketClient.build_book_ticker_stream("ETHUSDT") == "ethusdt@bookTicker"
    assert BinanceWebSocketClient.build_mark_price_stream("SOLUSDT", 1000) == "solusdt@markPrice@1s"
    assert BinanceWebSocketClient.build_mark_price_stream("SOLUSDT", 3000) == "solusdt@markPrice"


def test_binance_ws_backoff_calculation() -> None:
    client = BinanceWebSocketClient(initial_backoff=1.0, max_backoff=10.0, backoff_multiplier=2.0)

    # Deterministic backoff without jitter
    b0 = client.calculate_backoff(0, jitter=False)
    b1 = client.calculate_backoff(1, jitter=False)
    b2 = client.calculate_backoff(2, jitter=False)
    b3 = client.calculate_backoff(3, jitter=False)
    b4 = client.calculate_backoff(4, jitter=False)

    assert b0 == 1.0
    assert b1 == 2.0
    assert b2 == 4.0
    assert b3 == 8.0
    assert b4 == 10.0  # capped at max_backoff

    # Negative attempt clamped to 0
    assert client.calculate_backoff(-1, jitter=False) == 1.0

    # With jitter
    b_jitter = client.calculate_backoff(1, jitter=True)
    assert 2.0 <= b_jitter <= 2.0 + (0.25 * 2.0)


def test_binance_ws_raw_frame_handling() -> None:
    client = BinanceWebSocketClient()
    dispatched: list[dict[str, Any]] = []

    client.register_callback(lambda msg: dispatched.append(msg))

    # Standard JSON payload as string
    trade_frame = json.dumps({"e": "trade", "s": "BTCUSDT", "p": "50000.00"})
    client.handle_raw_frame(trade_frame)
    assert len(dispatched) == 1
    assert dispatched[0]["s"] == "BTCUSDT"

    # Combined stream wrapper as bytes
    combined_frame = json.dumps(
        {"stream": "btcusdt@bookTicker", "data": {"s": "BTCUSDT", "b": "50000"}}
    )
    client.handle_raw_frame(combined_frame.encode("utf-8"))
    assert len(dispatched) == 2
    assert dispatched[1]["b"] == "50000"

    # Malformed frame handling (does not crash)
    client.handle_raw_frame("invalid json {{")
    client.handle_raw_frame(b"\xff\xfe\x00")
    client.handle_raw_frame(json.dumps(["not a dict"]))
    assert len(dispatched) == 2
