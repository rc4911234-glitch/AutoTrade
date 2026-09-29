"""WebSocket client abstraction and test mock."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from typing import Any


class WebSocketClientState(StrEnum):
    """Lifecycle states of a WebSocket client."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"


WebSocketMessageCallback = Callable[[dict[str, Any]], None]


class WebSocketClient(ABC):
    """Abstract interface for real-time WebSocket clients."""

    @property
    @abstractmethod
    def state(self) -> WebSocketClientState:
        """Returns the current connection state."""
        pass

    @property
    def is_connected(self) -> bool:
        """Returns True if the socket is actively connected."""
        return self.state == WebSocketClientState.CONNECTED

    @abstractmethod
    def connect(self) -> None:
        """Initiates connection to the remote WebSocket endpoint."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Gracefully disconnects from the remote WebSocket endpoint."""
        pass

    @abstractmethod
    def subscribe_streams(self, streams: list[str]) -> None:
        """Subscribes to one or more topic streams."""
        pass

    @abstractmethod
    def unsubscribe_streams(self, streams: list[str]) -> None:
        """Unsubscribes from topic streams."""
        pass

    @abstractmethod
    def register_callback(self, callback: WebSocketMessageCallback) -> None:
        """Registers a callback to receive parsed JSON message frames."""
        pass


class MockWebSocketClient(WebSocketClient):
    """Deterministic in-memory WebSocket client for automated testing."""

    def __init__(self) -> None:
        self._state: WebSocketClientState = WebSocketClientState.DISCONNECTED
        self._subscribed_streams: set[str] = set()
        self._callbacks: list[WebSocketMessageCallback] = []
        self.sent_messages: list[dict[str, Any]] = []

    @property
    def state(self) -> WebSocketClientState:
        return self._state

    @property
    def subscribed_streams(self) -> set[str]:
        return set(self._subscribed_streams)

    def connect(self) -> None:
        self._state = WebSocketClientState.CONNECTED

    def disconnect(self) -> None:
        self._state = WebSocketClientState.DISCONNECTED

    def subscribe_streams(self, streams: list[str]) -> None:
        self._subscribed_streams.update(streams)

    def unsubscribe_streams(self, streams: list[str]) -> None:
        self._subscribed_streams.difference_update(streams)

    def register_callback(self, callback: WebSocketMessageCallback) -> None:
        self._callbacks.append(callback)

    def inject_message(self, data: dict[str, Any]) -> None:
        """Simulates receiving a message frame from the exchange."""
        for cb in self._callbacks:
            cb(data)

    def simulate_disconnect(self) -> None:
        """Simulates an unexpected socket disconnect."""
        self._state = WebSocketClientState.DISCONNECTED

    def simulate_reconnect(self) -> None:
        """Simulates socket reconnection."""
        self._state = WebSocketClientState.CONNECTED
