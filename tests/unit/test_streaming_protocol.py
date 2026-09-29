"""Unit tests for WebSocket streaming protocol and MockWebSocketClient."""

from typing import Any

from trad_auto.market_data.streaming.protocol import (
    MockWebSocketClient,
    WebSocketClientState,
)


def test_mock_websocket_client_lifecycle() -> None:
    client = MockWebSocketClient()
    assert client.state.value == WebSocketClientState.DISCONNECTED.value
    assert not client.is_connected

    client.connect()
    assert client.state.value == WebSocketClientState.CONNECTED.value
    assert client.is_connected

    client.disconnect()
    assert client.state.value == WebSocketClientState.DISCONNECTED.value
    assert not client.is_connected


def test_mock_websocket_client_subscriptions() -> None:
    client = MockWebSocketClient()
    client.subscribe_streams(["btcusdt@trade", "btcusdt@bookTicker"])
    assert client.subscribed_streams == {"btcusdt@trade", "btcusdt@bookTicker"}

    client.unsubscribe_streams(["btcusdt@trade"])
    assert client.subscribed_streams == {"btcusdt@bookTicker"}


def test_mock_websocket_client_message_injection() -> None:
    client = MockWebSocketClient()
    received: list[dict[str, Any]] = []

    def callback(data: dict[str, Any]) -> None:
        received.append(data)

    client.register_callback(callback)
    msg = {"e": "trade", "s": "BTCUSDT", "p": "50000.00"}
    client.inject_message(msg)

    assert len(received) == 1
    assert received[0] == msg


def test_mock_websocket_client_simulate_disconnect_reconnect() -> None:
    client = MockWebSocketClient()
    client.connect()
    assert client.is_connected

    client.simulate_disconnect()
    assert not client.is_connected
    assert client.state.value == WebSocketClientState.DISCONNECTED.value

    client.simulate_reconnect()
    assert client.is_connected
    assert client.state.value == WebSocketClientState.CONNECTED.value
