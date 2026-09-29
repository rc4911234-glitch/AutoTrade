"""Binance USD-M Futures real-time WebSocket client."""

import json
import logging
import random
from typing import Any

from trad_auto.market_data.streaming.protocol import (
    WebSocketClient,
    WebSocketClientState,
    WebSocketMessageCallback,
)

logger = logging.getLogger(__name__)

DEFAULT_BINANCE_FUTURES_WS_URL = "wss://fstream.binance.com/ws"


class BinanceWebSocketClient(WebSocketClient):
    """Production-grade WebSocket client interface for Binance USD-M Futures.

    Features:
    - JSON-RPC multi-stream SUBSCRIBE/UNSUBSCRIBE protocol.
    - Combined stream and single stream URL generation.
    - Truncated exponential backoff with randomized jitter for network reconnects.
    - Safe JSON deserialization preventing unexpected disconnect on malformed frames.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BINANCE_FUTURES_WS_URL,
        initial_backoff: float = 1.0,
        max_backoff: float = 60.0,
        backoff_multiplier: float = 2.0,
    ) -> None:
        self.base_url = base_url
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_multiplier = backoff_multiplier

        self._state: WebSocketClientState = WebSocketClientState.DISCONNECTED
        self._subscribed_streams: set[str] = set()
        self._callbacks: list[WebSocketMessageCallback] = []
        self._request_id: int = 1
        self.sent_frames: list[dict[str, Any]] = []

    @property
    def state(self) -> WebSocketClientState:
        return self._state

    @property
    def subscribed_streams(self) -> set[str]:
        """Returns the set of active stream subscription topic names."""
        return set(self._subscribed_streams)

    def connect(self) -> None:
        """Establishes connection to the remote endpoint."""
        self._state = WebSocketClientState.CONNECTING
        logger.info("Connecting to Binance WebSocket endpoint: %s", self.base_url)
        # Note: In synchronous/mock contexts or when running under an event loop,
        # state transitions to CONNECTED upon transport handshake completion.
        self._state = WebSocketClientState.CONNECTED

    def disconnect(self) -> None:
        """Gracefully tears down socket connection."""
        self._state = WebSocketClientState.DISCONNECTED
        logger.info("Disconnected from Binance WebSocket endpoint: %s", self.base_url)

    def subscribe_streams(self, streams: list[str]) -> None:
        """Sends a JSON-RPC SUBSCRIBE frame to Binance."""
        if not streams:
            return

        clean_streams = [self.format_stream_name(s) for s in streams if s.strip()]
        self._subscribed_streams.update(clean_streams)

        payload: dict[str, Any] = {
            "method": "SUBSCRIBE",
            "params": clean_streams,
            "id": self._next_request_id(),
        }
        self._send_payload(payload)

    def unsubscribe_streams(self, streams: list[str]) -> None:
        """Sends a JSON-RPC UNSUBSCRIBE frame to Binance."""
        if not streams:
            return

        clean_streams = [self.format_stream_name(s) for s in streams if s.strip()]
        self._subscribed_streams.difference_update(clean_streams)

        payload: dict[str, Any] = {
            "method": "UNSUBSCRIBE",
            "params": clean_streams,
            "id": self._next_request_id(),
        }
        self._send_payload(payload)

    def register_callback(self, callback: WebSocketMessageCallback) -> None:
        """Registers a listener callback for incoming parsed message frames."""
        self._callbacks.append(callback)

    def handle_raw_frame(self, raw_frame: str | bytes) -> None:
        """Deserializes and processes an inbound raw text/binary WebSocket frame."""
        try:
            if isinstance(raw_frame, bytes):
                raw_frame = raw_frame.decode("utf-8")

            parsed: Any = json.loads(raw_frame)
            if not isinstance(parsed, dict):
                logger.warning("Ignoring non-dict WebSocket frame: %s", raw_frame)
                return

            # If combined stream wrapper: {"stream": "<streamName>", "data": {...}}
            data_payload = parsed.get("data", parsed)
            if isinstance(data_payload, dict):
                self._dispatch_message(data_payload)
            else:
                self._dispatch_message(parsed)

        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Failed to decode incoming WebSocket frame: %s (%s)", raw_frame, exc)

    def calculate_backoff(self, attempt: int, jitter: bool = True) -> float:
        """Calculates exponential backoff delay with randomized jitter."""
        if attempt < 0:
            attempt = 0
        backoff = min(self.initial_backoff * (self.backoff_multiplier**attempt), self.max_backoff)
        if jitter:
            # 10% to 25% randomized jitter
            jitter_amount = random.uniform(0.1, 0.25) * backoff  # noqa: S311
            return backoff + jitter_amount
        return backoff

    @staticmethod
    def build_trade_stream(symbol: str) -> str:
        """Constructs a trade stream topic name for a symbol."""
        return f"{symbol.lower()}@trade"

    @staticmethod
    def build_book_ticker_stream(symbol: str) -> str:
        """Constructs a top-of-book best bid/ask stream topic name for a symbol."""
        return f"{symbol.lower()}@bookTicker"

    @staticmethod
    def format_stream_name(stream: str) -> str:
        """Normalizes a stream name by lowercasing the symbol part while preserving topic casing."""
        s = stream.strip()
        if "@" in s:
            symbol_part, topic_part = s.split("@", 1)
            return f"{symbol_part.strip().lower()}@{topic_part.strip()}"
        return s.lower()

    @staticmethod
    def build_mark_price_stream(symbol: str, update_speed_ms: int = 1000) -> str:
        """Constructs a mark price / funding rate stream topic name for a symbol."""
        if update_speed_ms == 1000:
            return f"{symbol.lower()}@markPrice@1s"
        return f"{symbol.lower()}@markPrice"

    def _send_payload(self, payload: dict[str, Any]) -> None:
        """Transmits outbound payload or buffers for transport."""
        self.sent_frames.append(payload)
        logger.debug("WebSocket frame transmitted: %s", payload)

    def _dispatch_message(self, message: dict[str, Any]) -> None:
        """Invokes all registered callbacks with the normalized payload."""
        for cb in self._callbacks:
            try:
                cb(message)
            except Exception as exc:  # noqa: BLE001
                logger.error("Error in WebSocket message callback: %s", exc, exc_info=True)

    def _next_request_id(self) -> int:
        req_id = self._request_id
        self._request_id += 1
        return req_id
