"""Real-time streaming market data adapter bridging WebSockets to the domain event bus."""

import logging
from typing import Any

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataFeedStatus
from trad_auto.core.events import (
    DataFeedStatusChangedEvent,
    FundingRateEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.models.market_data import FundingRate, Quote, Trade
from trad_auto.core.time import Clock, SystemClock
from trad_auto.market_data.adapters.base import BaseMarketDataAdapter
from trad_auto.market_data.bar_builder import BarBuilder
from trad_auto.market_data.streaming.binance_ws import BinanceWebSocketClient
from trad_auto.market_data.streaming.normalizer import BinancePayloadNormalizer
from trad_auto.market_data.streaming.protocol import WebSocketClient, WebSocketClientState
from trad_auto.market_data.streaming.watchdog import FeedWatchdog
from trad_auto.market_data.validator import DataQualityValidator

logger = logging.getLogger(__name__)


class StreamingMarketDataAdapter(BaseMarketDataAdapter):
    """Production-grade market data adapter consuming real-time exchange WebSocket streams.

    Architectural Responsibilities:
    1. Normalization: Translates exchange-specific JSON frames into typed domain objects
       (Trade, Quote, FundingRate) via BinancePayloadNormalizer.
    2. Quality Control: Validates timestamps, sequence order, and price spikes before
       forwarding to BarBuilder. Aberrant ticks are dropped fail-closed.
    3. Feed Watchdog: Continuously tracks per-symbol heartbeat freshness. Silences exceeding
       15 seconds automatically transition feed status to STALE.
    4. Aggregation: Passes validated ticks to BarBuilder for zero look-ahead bias candle creation.
    5. Event Publishing: Broadcasts QuoteUpdatedEvent, TradeReceivedEvent, and FundingRateEvent.
    """

    def __init__(
        self,
        ws_client: WebSocketClient,
        normalizer: BinancePayloadNormalizer | None = None,
        watchdog: FeedWatchdog | None = None,
        validator: DataQualityValidator | None = None,
        bar_builder: BarBuilder | None = None,
        event_bus: EventBus | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._ws_client = ws_client
        self._normalizer = normalizer or BinancePayloadNormalizer()
        self._watchdog = watchdog
        self._validator = validator
        self._bar_builder = bar_builder
        self._event_bus = event_bus
        self._clock = clock or SystemClock()

        self._subscribed_symbols: set[str] = set()
        self._subscribed_timeframes: set[str] = set()

        # Register callback with client to consume inbound JSON frames
        self._ws_client.register_callback(self._on_ws_message)

    @property
    def status(self) -> DataFeedStatus:
        """Computes the real-time operational status of the feed."""
        ws_state = self._ws_client.state
        if ws_state == WebSocketClientState.CONNECTING:
            return DataFeedStatus.RECONNECTING
        if ws_state in (WebSocketClientState.DISCONNECTED, WebSocketClientState.FAILED):
            return DataFeedStatus.DISCONNECTED

        # If connected, audit watchdog for any stale subscribed symbols
        if self._watchdog and self._subscribed_symbols:
            for sym in self._subscribed_symbols:
                if self._watchdog.get_status(sym) == DataFeedStatus.STALE:
                    return DataFeedStatus.STALE

        return DataFeedStatus.CONNECTED

    @property
    def subscribed_symbols(self) -> set[str]:
        return set(self._subscribed_symbols)

    @property
    def subscribed_timeframes(self) -> set[str]:
        return set(self._subscribed_timeframes)

    def connect(self) -> None:
        """Initiates WebSocket connection and updates status."""
        old_status = self.status
        self._ws_client.connect()
        new_status = self.status
        if self._event_bus:
            self._event_bus.publish(
                DataFeedStatusChangedEvent(
                    symbol="*",
                    old_status=old_status,
                    new_status=new_status,
                    reason="Streaming market data adapter connected",
                )
            )

    def disconnect(self) -> None:
        """Gracefully disconnects WebSocket client and updates status."""
        old_status = self.status
        self._ws_client.disconnect()
        if self._event_bus:
            self._event_bus.publish(
                DataFeedStatusChangedEvent(
                    symbol="*",
                    old_status=old_status,
                    new_status=DataFeedStatus.DISCONNECTED,
                    reason="Streaming market data adapter disconnected",
                )
            )

    def subscribe(self, symbols: list[str], timeframes: list[str]) -> None:
        """Subscribes to market updates for symbols across timeframes."""
        if not symbols:
            return

        clean_symbols = [s.strip().upper() for s in symbols if s.strip()]
        self._subscribed_symbols.update(clean_symbols)
        self._subscribed_timeframes.update(timeframes)

        stream_topics: list[str] = []
        for sym in clean_symbols:
            if self._watchdog:
                self._watchdog.track_symbol(sym)

            stream_topics.append(BinanceWebSocketClient.build_trade_stream(sym))
            stream_topics.append(BinanceWebSocketClient.build_book_ticker_stream(sym))
            stream_topics.append(BinanceWebSocketClient.build_mark_price_stream(sym))

        self._ws_client.subscribe_streams(stream_topics)

    def unsubscribe(self, symbols: list[str]) -> None:
        """Cancels subscriptions for the given symbols."""
        if not symbols:
            return

        clean_symbols = [s.strip().upper() for s in symbols if s.strip()]
        self._subscribed_symbols.difference_update(clean_symbols)

        stream_topics: list[str] = []
        for sym in clean_symbols:
            if self._watchdog:
                self._watchdog.untrack_symbol(sym)

            stream_topics.append(BinanceWebSocketClient.build_trade_stream(sym))
            stream_topics.append(BinanceWebSocketClient.build_book_ticker_stream(sym))
            stream_topics.append(BinanceWebSocketClient.build_mark_price_stream(sym))

        self._ws_client.unsubscribe_streams(stream_topics)

    def _on_ws_message(self, message: dict[str, Any]) -> None:
        """Processes an incoming JSON message frame dispatched by the WebSocket client."""
        normalized = self._normalizer.normalize(message)
        if normalized is None:
            return

        if isinstance(normalized, Trade):
            self._handle_trade(normalized)
        elif isinstance(normalized, Quote):
            self._handle_quote(normalized)
        elif isinstance(normalized, FundingRate):
            self._handle_funding_rate(normalized)

    def _handle_trade(self, trade: Trade) -> None:
        """Handles a normalized incoming Trade tick."""
        if self._watchdog:
            self._watchdog.record_activity(trade.symbol, trade.timestamp)

        # Safety Check: Validate trade print quality
        if self._validator:
            issues = self._validator.validate_trade(trade, self._clock)
            # If critical anomaly (e.g. price spike, out-of-order), drop tick
            # to prevent corrupting bars
            if any(issue.severity == "CRITICAL" for issue in issues):
                logger.warning(
                    "Dropping aberrant trade tick for %s at %s due to critical anomaly: %s",
                    trade.symbol,
                    trade.price,
                    issues,
                )
                return

        # Forward to bar builder for zero look-ahead bias candle aggregation
        if self._bar_builder:
            self._bar_builder.on_trade(trade)

        if self._event_bus:
            self._event_bus.publish(TradeReceivedEvent(trade=trade))

    def _handle_quote(self, quote: Quote) -> None:
        """Handles a normalized incoming Quote (top-of-book best bid/ask)."""
        if self._watchdog:
            self._watchdog.record_activity(quote.symbol, quote.timestamp)

        if self._validator:
            self._validator.validate_quote(quote, self._clock)

        if self._event_bus:
            self._event_bus.publish(QuoteUpdatedEvent(quote=quote))

    def _handle_funding_rate(self, funding_rate: FundingRate) -> None:
        """Handles a normalized incoming FundingRate update."""
        if self._watchdog:
            self._watchdog.record_activity(funding_rate.symbol, funding_rate.timestamp)

        if self._event_bus:
            self._event_bus.publish(FundingRateEvent(funding_rate=funding_rate))
