"""Real-time market data streaming components."""

from trad_auto.market_data.streaming.adapter import StreamingMarketDataAdapter
from trad_auto.market_data.streaming.binance_ws import (
    DEFAULT_BINANCE_FUTURES_WS_URL,
    BinanceWebSocketClient,
)
from trad_auto.market_data.streaming.normalizer import BinancePayloadNormalizer
from trad_auto.market_data.streaming.protocol import (
    MockWebSocketClient,
    WebSocketClient,
    WebSocketClientState,
    WebSocketMessageCallback,
)
from trad_auto.market_data.streaming.watchdog import FeedWatchdog

__all__ = [
    "DEFAULT_BINANCE_FUTURES_WS_URL",
    "BinancePayloadNormalizer",
    "BinanceWebSocketClient",
    "FeedWatchdog",
    "MockWebSocketClient",
    "StreamingMarketDataAdapter",
    "WebSocketClient",
    "WebSocketClientState",
    "WebSocketMessageCallback",
]
