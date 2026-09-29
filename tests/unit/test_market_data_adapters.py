"""Unit tests for SimulatedMarketDataAdapter and CryptoExchangeMarketDataAdapter."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.bus import EventBus
from trad_auto.core.enums import DataFeedStatus, OrderSide
from trad_auto.core.events import (
    BarCompletedEvent,
    BarUpdatedEvent,
    DataFeedStatusChangedEvent,
    QuoteUpdatedEvent,
    TradeReceivedEvent,
)
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote, Trade
from trad_auto.core.time import SimulatedClock
from trad_auto.market_data.adapters.crypto import CryptoExchangeMarketDataAdapter
from trad_auto.market_data.adapters.simulated import SimulatedMarketDataAdapter
from trad_auto.market_data.store import BarStore, QuoteStore


def test_simulated_adapter_lifecycle_and_subscriptions() -> None:
    """Verifies connection state, subscriptions, and status events on simulated adapter."""
    bus = EventBus()
    status_events: list[DataFeedStatus] = []
    bus.subscribe(
        DataFeedStatusChangedEvent,
        lambda e: status_events.append(e.new_status),
    )

    adapter = SimulatedMarketDataAdapter(event_bus=bus)
    assert not adapter.is_connected
    assert adapter.status == DataFeedStatus.DISCONNECTED

    adapter.connect()
    assert adapter.is_connected
    assert adapter.status == DataFeedStatus.CONNECTED
    assert status_events == [DataFeedStatus.CONNECTED]

    adapter.subscribe(["BTCUSDT", "ETHUSDT"], ["1m", "5m"])
    assert adapter.subscribed_symbols == {"BTCUSDT", "ETHUSDT"}
    assert adapter.subscribed_timeframes == {"1m", "5m"}

    adapter.unsubscribe(["ETHUSDT"])
    assert adapter.subscribed_symbols == {"BTCUSDT"}

    adapter.disconnect()
    assert not adapter.is_connected
    assert status_events == [DataFeedStatus.CONNECTED, DataFeedStatus.DISCONNECTED]


def test_simulated_adapter_feed_and_clock_advancement() -> None:
    """Verifies that feeding bars/quotes/trades advances clock and populates stores."""
    bus = EventBus()
    clock = SimulatedClock(datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC))
    bar_store = BarStore()
    quote_store = QuoteStore()

    bars_received: list[Bar] = []
    quotes_received: list[Quote] = []
    trades_received: list[Trade] = []

    bus.subscribe(
        BarCompletedEvent,
        lambda e: bars_received.append(e.bar) if e.bar else None,
    )
    bus.subscribe(
        QuoteUpdatedEvent,
        lambda e: quotes_received.append(e.quote) if e.quote else None,
    )
    bus.subscribe(
        TradeReceivedEvent,
        lambda e: trades_received.append(e.trade) if e.trade else None,
    )

    adapter = SimulatedMarketDataAdapter(
        event_bus=bus,
        clock=clock,
        bar_store=bar_store,
        quote_store=quote_store,
    )

    # Disconnected feed raises DomainValidationError
    bar1 = Bar(
        timestamp=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        symbol="BTCUSDT",
        timeframe="1m",
        open=Decimal("60000"),
        high=Decimal("60100"),
        low=Decimal("59900"),
        close=Decimal("60050"),
        volume=Decimal("10"),
    )
    with pytest.raises(DomainValidationError, match="not connected"):
        adapter.feed_bar(bar1)

    adapter.connect()

    # Feed bar
    adapter.feed_bar(bar1)
    assert clock.now() == bar1.timestamp
    assert bar_store.bar_count("BTCUSDT", "1m") == 1
    assert bars_received == [bar1]

    # Feed quote
    quote1 = Quote(
        timestamp=datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC),
        symbol="BTCUSDT",
        bid_price=Decimal("60040"),
        ask_price=Decimal("60045"),
        bid_size=Decimal("1.2"),
        ask_size=Decimal("1.5"),
    )
    adapter.feed_quote(quote1)
    assert clock.now() == quote1.timestamp
    assert quote_store.get_latest_quote("BTCUSDT") == quote1
    assert quotes_received == [quote1]

    # Feed trade
    trade1 = Trade(
        timestamp=datetime(2026, 1, 1, 12, 0, 45, tzinfo=UTC),
        symbol="BTCUSDT",
        price=Decimal("60045"),
        quantity=Decimal("0.5"),
        side=OrderSide.BUY,
        trade_id="tr_100",
    )
    adapter.feed_trade(trade1)
    assert clock.now() == trade1.timestamp
    assert trades_received == [trade1]


def test_simulated_adapter_replay_bars() -> None:
    """Verifies replay_bars batch playback."""
    adapter = SimulatedMarketDataAdapter(bar_store=BarStore())
    bars = [
        Bar(
            timestamp=datetime(2026, 1, 1, 12, i, 0, tzinfo=UTC),
            symbol="BTCUSDT",
            timeframe="1m",
            open=Decimal("60000"),
            high=Decimal("60100"),
            low=Decimal("59900"),
            close=Decimal("60050"),
            volume=Decimal("10"),
        )
        for i in range(3)
    ]
    replayed = adapter.replay_bars(bars)
    assert replayed == 3


def test_crypto_adapter_kline_normalization() -> None:
    """Verifies REST array and WebSocket kline payload normalization."""
    # 1. REST kline list format
    rest_kline = [1704110400000, "60000", "60100", "59900", "60050", "25.0"]
    bar, is_closed = CryptoExchangeMarketDataAdapter.normalize_kline("BTCUSDT", "1m", rest_kline)
    assert is_closed is True
    assert bar.close == Decimal("60050")
    assert bar.volume == Decimal("25.0")

    # 2. WebSocket forming (open) kline
    ws_kline_open = {
        "e": "kline",
        "k": {
            "t": 1704110400000,
            "s": "BTCUSDT",
            "i": "1m",
            "o": "60000",
            "h": "60100",
            "l": "59900",
            "c": "60020",
            "v": "10.0",
            "x": False,
        },
    }
    bar_open, is_closed_ws = CryptoExchangeMarketDataAdapter.normalize_kline(
        "BTCUSDT", "1m", ws_kline_open
    )
    assert is_closed_ws is False
    assert bar_open.close == Decimal("60020")

    # 3. WebSocket closed kline
    ws_kline_open["k"]["x"] = True  # type: ignore[index]
    _, is_closed_final = CryptoExchangeMarketDataAdapter.normalize_kline(
        "BTCUSDT", "1m", ws_kline_open
    )
    assert is_closed_final is True


def test_crypto_adapter_ticker_trade_and_funding_rate() -> None:
    """Verifies bookTicker, trade (buyer maker), and funding rate normalization."""
    # 1. BookTicker
    ticker_payload = {
        "s": "ETHUSDT",
        "b": "3100.50",
        "a": "3100.60",
        "B": "10.5",
        "A": "15.2",
        "E": 1704110400000,
    }
    quote = CryptoExchangeMarketDataAdapter.normalize_book_ticker(ticker_payload)
    assert quote.symbol == "ETHUSDT"
    assert quote.bid_price == Decimal("3100.50")
    assert quote.ask_price == Decimal("3100.60")

    # 2. Trade - buyer is maker (m=True -> SELL market order)
    trade_sell_payload = {
        "s": "ETHUSDT",
        "p": "3100.50",
        "q": "2.5",
        "t": 98765,
        "m": True,
        "T": 1704110400000,
    }
    trade_sell = CryptoExchangeMarketDataAdapter.normalize_trade(trade_sell_payload)
    assert trade_sell.side == OrderSide.SELL
    assert trade_sell.price == Decimal("3100.50")

    # Trade - buyer is taker (m=False -> BUY market order)
    trade_buy_payload = {
        "s": "ETHUSDT",
        "p": "3100.60",
        "q": "1.0",
        "t": 98766,
        "m": False,
        "T": 1704110400000,
    }
    trade_buy = CryptoExchangeMarketDataAdapter.normalize_trade(trade_buy_payload)
    assert trade_buy.side == OrderSide.BUY

    # 3. Funding rate
    funding_payload = {
        "symbol": "ETHUSDT",
        "fundingRate": "0.00010000",
        "fundingTime": 1704139200000,
        "time": 1704110400000,
    }
    fr = CryptoExchangeMarketDataAdapter.normalize_funding_rate(funding_payload)
    assert fr.symbol == "ETHUSDT"
    assert fr.rate == Decimal("0.00010000")


def test_crypto_adapter_ws_handlers_dispatch_events() -> None:
    """Verifies that handle_ws_* helper methods dispatch typed events through the bus."""
    bus = EventBus()
    kline_closed_events: list[Bar] = []
    kline_updated_events: list[Bar] = []
    quote_events: list[Quote] = []
    trade_events: list[Trade] = []

    bus.subscribe(BarCompletedEvent, lambda e: kline_closed_events.append(e.bar) if e.bar else None)
    bus.subscribe(BarUpdatedEvent, lambda e: kline_updated_events.append(e.bar) if e.bar else None)
    bus.subscribe(QuoteUpdatedEvent, lambda e: quote_events.append(e.quote) if e.quote else None)
    bus.subscribe(TradeReceivedEvent, lambda e: trade_events.append(e.trade) if e.trade else None)

    adapter = CryptoExchangeMarketDataAdapter(event_bus=bus)

    # Intrabar kline message -> BarUpdatedEvent
    adapter.handle_ws_kline_message(
        "BTCUSDT",
        "1m",
        {
            "k": {
                "t": 1704110400000,
                "s": "BTCUSDT",
                "i": "1m",
                "o": "60000",
                "h": "60010",
                "l": "59990",
                "c": "60005",
                "v": "5",
                "x": False,
            }
        },
    )
    assert len(kline_updated_events) == 1
    assert len(kline_closed_events) == 0

    # Closed kline message -> BarCompletedEvent
    adapter.handle_ws_kline_message(
        "BTCUSDT",
        "1m",
        {
            "k": {
                "t": 1704110400000,
                "s": "BTCUSDT",
                "i": "1m",
                "o": "60000",
                "h": "60010",
                "l": "59990",
                "c": "60008",
                "v": "8",
                "x": True,
            }
        },
    )
    assert len(kline_closed_events) == 1

    # Book ticker message -> QuoteUpdatedEvent
    adapter.handle_ws_ticker_message(
        {
            "s": "BTCUSDT",
            "b": "60008",
            "a": "60009",
            "B": "1",
            "A": "1",
            "E": 1704110400000,
        }
    )
    assert len(quote_events) == 1

    # Trade message -> TradeReceivedEvent
    adapter.handle_ws_trade_message(
        {
            "s": "BTCUSDT",
            "p": "60009",
            "q": "0.1",
            "t": 1,
            "m": False,
            "T": 1704110400000,
        }
    )
    assert len(trade_events) == 1
