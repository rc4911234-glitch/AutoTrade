"""Unit tests for StreamingMarketDataAdapter."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataFeedStatus, OrderSide
from trad_auto.core.events import (
    BarCompletedEvent,
    DataAnomalyDetectedEvent,
    DataFeedStatusChangedEvent,
    FundingRateEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.time import SimulatedClock
from trad_auto.market_data.bar_builder import BarBuilder
from trad_auto.market_data.streaming.adapter import StreamingMarketDataAdapter
from trad_auto.market_data.streaming.protocol import (
    MockWebSocketClient,
    WebSocketClientState,
)
from trad_auto.market_data.streaming.watchdog import FeedWatchdog
from trad_auto.market_data.validator import DataQualityValidator


def test_streaming_adapter_lifecycle_and_status() -> None:
    event_bus = EventBus()
    status_events: list[DataFeedStatusChangedEvent] = []
    event_bus.subscribe(DataFeedStatusChangedEvent, lambda e: status_events.append(e))

    ws_client = MockWebSocketClient()
    adapter = StreamingMarketDataAdapter(ws_client=ws_client, event_bus=event_bus)

    assert adapter.status.value == DataFeedStatus.DISCONNECTED.value
    assert not adapter.is_connected

    adapter.connect()
    assert adapter.status.value == DataFeedStatus.CONNECTED.value
    assert adapter.is_connected
    assert len(status_events) == 1
    assert status_events[0].new_status.value == DataFeedStatus.CONNECTED.value

    adapter.disconnect()
    assert adapter.status.value == DataFeedStatus.DISCONNECTED.value
    assert not adapter.is_connected
    assert len(status_events) == 2
    assert status_events[1].new_status.value == DataFeedStatus.DISCONNECTED.value


def test_streaming_adapter_reconnecting_status() -> None:
    ws_client = MockWebSocketClient()
    adapter = StreamingMarketDataAdapter(ws_client=ws_client)

    ws_client._state = WebSocketClientState.CONNECTING
    assert adapter.status.value == DataFeedStatus.RECONNECTING.value


def test_streaming_adapter_subscribe_unsubscribe() -> None:
    ws_client = MockWebSocketClient()
    watchdog = FeedWatchdog(timeout_seconds=15.0)
    adapter = StreamingMarketDataAdapter(ws_client=ws_client, watchdog=watchdog)

    adapter.subscribe(["BTCUSDT"], ["1m", "5m"])
    assert adapter.subscribed_symbols == {"BTCUSDT"}
    assert adapter.subscribed_timeframes == {"1m", "5m"}
    assert "BTCUSDT" in watchdog.tracked_symbols

    # Verify WebSocket stream topics
    assert ws_client.subscribed_streams == {
        "btcusdt@trade",
        "btcusdt@bookTicker",
        "btcusdt@markPrice@1s",
    }

    # Empty subscribe does nothing
    adapter.subscribe([], [])

    # Unsubscribe
    adapter.unsubscribe(["BTCUSDT"])
    assert adapter.subscribed_symbols == set()
    assert "BTCUSDT" not in watchdog.tracked_symbols
    assert ws_client.subscribed_streams == set()

    # Empty unsubscribe does nothing
    adapter.unsubscribe([])


def test_streaming_adapter_trade_ingestion_and_bar_building() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    event_bus = EventBus()

    trade_events: list[TradeReceivedEvent] = []
    bar_events: list[BarCompletedEvent] = []
    event_bus.subscribe(TradeReceivedEvent, lambda e: trade_events.append(e))
    event_bus.subscribe(BarCompletedEvent, lambda e: bar_events.append(e))

    ws_client = MockWebSocketClient()
    watchdog = FeedWatchdog(timeout_seconds=15.0, clock=clock)
    bar_builder = BarBuilder(timeframes=["1m"], event_bus=event_bus)

    adapter = StreamingMarketDataAdapter(
        ws_client=ws_client,
        watchdog=watchdog,
        bar_builder=bar_builder,
        event_bus=event_bus,
        clock=clock,
    )
    adapter.connect()
    adapter.subscribe(["BTCUSDT"], ["1m"])

    # Ingest tick at 12:00:10
    trade_frame_1 = {
        "e": "trade",
        "E": 1672574410000,
        "s": "BTCUSDT",
        "t": 1001,
        "p": "30000.00",
        "q": "0.5",
        "T": 1672574410000,
        "m": False,
    }
    ws_client.inject_message(trade_frame_1)

    assert len(trade_events) == 1
    assert trade_events[0].trade is not None
    assert trade_events[0].trade.price == Decimal("30000.00")
    assert trade_events[0].trade.side == OrderSide.BUY
    assert watchdog.last_heartbeat("BTCUSDT") is not None
    assert len(bar_events) == 0  # 1m bar not closed yet

    # Ingest tick at 12:01:05 (next minute, closes the previous 1m bar)
    trade_frame_2 = {
        "e": "trade",
        "E": 1672574465000,
        "s": "BTCUSDT",
        "t": 1002,
        "p": "30050.00",
        "q": "1.0",
        "T": 1672574465000,
        "m": True,
    }
    ws_client.inject_message(trade_frame_2)

    assert len(trade_events) == 2
    assert len(bar_events) == 1
    closed_bar = bar_events[0].bar
    assert closed_bar is not None
    assert closed_bar.symbol == "BTCUSDT"
    assert closed_bar.open == Decimal("30000.00")
    assert closed_bar.close == Decimal("30000.00")


def test_streaming_adapter_quote_and_funding_ingestion() -> None:
    event_bus = EventBus()
    quote_events: list[QuoteUpdatedEvent] = []
    funding_events: list[FundingRateEvent] = []

    event_bus.subscribe(QuoteUpdatedEvent, lambda e: quote_events.append(e))
    event_bus.subscribe(FundingRateEvent, lambda e: funding_events.append(e))

    ws_client = MockWebSocketClient()
    adapter = StreamingMarketDataAdapter(ws_client=ws_client, event_bus=event_bus)
    adapter.connect()

    # Ingest book ticker
    book_frame = {
        "e": "bookTicker",
        "s": "BTCUSDT",
        "b": "30000.10",
        "B": "5.0",
        "a": "30000.20",
        "A": "8.0",
        "T": 1672574410000,
    }
    ws_client.inject_message(book_frame)

    assert len(quote_events) == 1
    assert quote_events[0].quote is not None
    assert quote_events[0].quote.bid_price == Decimal("30000.10")
    assert quote_events[0].quote.ask_price == Decimal("30000.20")

    # Ingest mark price
    mark_frame = {
        "e": "markPriceUpdate",
        "s": "BTCUSDT",
        "p": "30005.00",
        "r": "0.00010000",
        "T": 1672574410000,
    }
    ws_client.inject_message(mark_frame)

    assert len(funding_events) == 1
    assert funding_events[0].funding_rate is not None
    assert funding_events[0].funding_rate.rate == Decimal("0.00010000")


def test_streaming_adapter_drops_aberrant_trade_spike() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    event_bus = EventBus()

    anomalies: list[DataAnomalyDetectedEvent] = []
    trade_events: list[TradeReceivedEvent] = []
    event_bus.subscribe(DataAnomalyDetectedEvent, lambda e: anomalies.append(e))
    event_bus.subscribe(TradeReceivedEvent, lambda e: trade_events.append(e))

    validator = DataQualityValidator(
        max_price_spike_pct=Decimal("0.10"),  # 10% max jump
        max_clock_drift_seconds=10.0,
        event_bus=event_bus,
    )
    ws_client = MockWebSocketClient()
    bar_builder = BarBuilder(timeframes=["1m"])

    adapter = StreamingMarketDataAdapter(
        ws_client=ws_client,
        validator=validator,
        bar_builder=bar_builder,
        event_bus=event_bus,
        clock=clock,
    )
    adapter.connect()

    # Initial valid tick at $30,000
    ws_client.inject_message(
        {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 1,
            "p": "30000.00",
            "q": "1.0",
            "T": int(t0.timestamp() * 1000),
            "m": False,
        }
    )
    assert len(trade_events) == 1
    assert len(anomalies) == 0

    # Aberrant print: +50% spike to $45,000
    ws_client.inject_message(
        {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 2,
            "p": "45000.00",
            "q": "1.0",
            "T": int(t0.timestamp() * 1000),
            "m": False,
        }
    )

    # Anomaly was published and trade was dropped fail-closed
    assert len(anomalies) == 1
    assert len(trade_events) == 1  # Not incremented


def test_streaming_adapter_status_reflects_watchdog_staleness() -> None:
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    clock = SimulatedClock(t0)
    ws_client = MockWebSocketClient()
    watchdog = FeedWatchdog(timeout_seconds=15.0, clock=clock)

    adapter = StreamingMarketDataAdapter(ws_client=ws_client, watchdog=watchdog, clock=clock)
    adapter.connect()
    adapter.subscribe(["BTCUSDT"], ["1m"])

    assert adapter.status.value == DataFeedStatus.CONNECTED.value

    # Inject message to set heartbeat
    ws_client.inject_message(
        {
            "e": "trade",
            "s": "BTCUSDT",
            "t": 1,
            "p": "30000.00",
            "q": "1.0",
            "T": int(t0.timestamp() * 1000),
        }
    )
    assert adapter.status.value == DataFeedStatus.CONNECTED.value

    # Fast forward clock beyond timeout
    clock.advance(timedelta(seconds=20))
    watchdog.check_staleness()

    # Feed status should now be STALE
    stale_status: str = adapter.status.value
    assert stale_status == DataFeedStatus.STALE.value


def test_streaming_adapter_ignores_unparseable_message() -> None:
    ws_client = MockWebSocketClient()
    adapter = StreamingMarketDataAdapter(ws_client=ws_client)
    adapter.connect()

    # Ingest unparseable message
    ws_client.inject_message({"unknown": "data"})
    assert adapter.status.value == DataFeedStatus.CONNECTED.value
