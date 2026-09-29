"""Unit tests for BinancePayloadNormalizer."""

from decimal import Decimal

from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import FundingRate, Quote, Trade
from trad_auto.market_data.streaming.normalizer import BinancePayloadNormalizer


def test_normalize_binance_trade_taker_sell() -> None:
    normalizer = BinancePayloadNormalizer()
    payload = {
        "e": "trade",
        "E": 1672531200000,
        "s": "BTCUSDT",
        "t": 1234567,
        "p": "20000.50",
        "q": "0.150",
        "b": 88,
        "a": 89,
        "T": 1672531200000,
        "m": True,  # buyer is maker -> taker is seller (SELL)
    }

    result = normalizer.normalize(payload)
    assert isinstance(result, Trade)
    assert result.symbol == "BTCUSDT"
    assert result.price == Decimal("20000.50")
    assert result.quantity == Decimal("0.150")
    assert result.side == OrderSide.SELL
    assert result.trade_id == "1234567"
    assert result.timestamp.tzinfo is not None


def test_normalize_binance_trade_taker_buy() -> None:
    normalizer = BinancePayloadNormalizer()
    payload = {
        "e": "trade",
        "E": 1672531200000,
        "s": "btcusdt",
        "t": 1234568,
        "p": "20001.00",
        "q": "1.000",
        "T": 1672531200000,
        "m": False,  # buyer is taker (BUY)
    }

    result = normalizer.normalize(payload)
    assert isinstance(result, Trade)
    assert result.symbol == "BTCUSDT"
    assert result.side == OrderSide.BUY
    assert result.price == Decimal("20001.00")
    assert result.quantity == Decimal("1.000")


def test_normalize_binance_book_ticker() -> None:
    normalizer = BinancePayloadNormalizer()
    payload = {
        "e": "bookTicker",
        "u": 400900217,
        "s": "ETHUSDT",
        "b": "1200.10",
        "B": "10.5",
        "a": "1200.20",
        "A": "15.2",
        "T": 1672531200000,
        "E": 1672531200001,
    }

    result = normalizer.normalize(payload)
    assert isinstance(result, Quote)
    assert result.symbol == "ETHUSDT"
    assert result.bid_price == Decimal("1200.10")
    assert result.ask_price == Decimal("1200.20")
    assert result.bid_size == Decimal("10.5")
    assert result.ask_size == Decimal("15.2")
    assert result.timestamp.tzinfo is not None


def test_normalize_binance_mark_price() -> None:
    normalizer = BinancePayloadNormalizer()
    payload = {
        "e": "markPriceUpdate",
        "E": 1672531200000,
        "s": "BTCUSDT",
        "p": "20010.00",
        "P": "20015.00",
        "r": "0.00010000",
        "T": 1672531200000,
    }

    result = normalizer.normalize(payload)
    assert isinstance(result, FundingRate)
    assert result.symbol == "BTCUSDT"
    assert result.rate == Decimal("0.00010000")
    assert result.timestamp.tzinfo is not None
    assert result.next_funding_time.tzinfo is not None


def test_normalize_fallback_heuristics() -> None:
    normalizer = BinancePayloadNormalizer()

    # Book ticker without "e"
    book_payload = {
        "s": "SOLUSDT",
        "b": "25.50",
        "B": "100.0",
        "a": "25.55",
        "A": "150.0",
    }
    result_book = normalizer.normalize(book_payload)
    assert isinstance(result_book, Quote)
    assert result_book.symbol == "SOLUSDT"

    # Trade without "e"
    trade_payload = {
        "s": "SOLUSDT",
        "p": "25.52",
        "q": "5.0",
        "t": 999,
        "T": 1672531200000,
    }
    result_trade = normalizer.normalize(trade_payload)
    assert isinstance(result_trade, Trade)
    assert result_trade.symbol == "SOLUSDT"


def test_normalize_malformed_and_unknown_payloads() -> None:
    normalizer = BinancePayloadNormalizer()

    # Unknown event type
    assert normalizer.normalize({"e": "unknown_event", "s": "BTCUSDT"}) is None

    # Missing mandatory trade fields
    assert normalizer.normalize({"e": "trade", "s": "BTCUSDT"}) is None

    # Invalid decimal numbers
    assert (
        normalizer.normalize({"e": "trade", "s": "BTCUSDT", "p": "NaN_price", "q": "1.0", "t": 1})
        is None
    )
    assert (
        normalizer.normalize(
            {
                "e": "bookTicker",
                "s": "BTCUSDT",
                "b": "bad",
                "B": "1",
                "a": "10",
                "A": "1",
            }
        )
        is None
    )
    assert (
        normalizer.normalize(
            {
                "e": "markPriceUpdate",
                "s": "BTCUSDT",
                "r": "invalid_rate",
                "T": 1672531200000,
            }
        )
        is None
    )

    # Empty payload
    assert normalizer.normalize({}) is None
