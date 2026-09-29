"""Crypto exchange market data adapter and message normalizers."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

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
from trad_auto.core.models.market_data import Bar, FundingRate, Quote, Trade
from trad_auto.market_data.adapters.base import BaseMarketDataAdapter
from trad_auto.market_data.loader import parse_timestamp_value


class CryptoExchangeMarketDataAdapter(BaseMarketDataAdapter):
    """Base crypto market data adapter providing normalization for exchange feeds.

    Supports Binance/Bybit style REST klines, WebSocket kline streams,
    top-of-book tickers, trade streams, and perpetual funding rates.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._event_bus = event_bus
        self._status = DataFeedStatus.DISCONNECTED
        self._subscribed_symbols: set[str] = set()
        self._subscribed_timeframes: set[str] = set()

    @property
    def status(self) -> DataFeedStatus:
        return self._status

    @property
    def subscribed_symbols(self) -> set[str]:
        return set(self._subscribed_symbols)

    @property
    def subscribed_timeframes(self) -> set[str]:
        return set(self._subscribed_timeframes)

    def connect(self) -> None:
        old = self._status
        self._status = DataFeedStatus.CONNECTED
        if self._event_bus:
            self._event_bus.publish(
                DataFeedStatusChangedEvent(
                    symbol="*",
                    old_status=old,
                    new_status=self._status,
                    reason="Crypto exchange adapter connected",
                )
            )

    def disconnect(self) -> None:
        old = self._status
        self._status = DataFeedStatus.DISCONNECTED
        if self._event_bus:
            self._event_bus.publish(
                DataFeedStatusChangedEvent(
                    symbol="*",
                    old_status=old,
                    new_status=self._status,
                    reason="Crypto exchange adapter disconnected",
                )
            )

    def subscribe(self, symbols: list[str], timeframes: list[str]) -> None:
        self._subscribed_symbols.update(symbols)
        self._subscribed_timeframes.update(timeframes)

    def unsubscribe(self, symbols: list[str]) -> None:
        for s in symbols:
            self._subscribed_symbols.discard(s)

    @staticmethod
    def normalize_kline(
        symbol: str,
        timeframe: str,
        payload: list[Any] | dict[str, Any],
    ) -> tuple[Bar, bool]:
        """Normalizes REST or WebSocket kline payload into a Bar.

        Returns tuple of (Bar, is_closed: bool).
        """
        try:
            if isinstance(payload, list):
                # REST API format: [open_time, open, high, low, close, volume, ...]
                ts = parse_timestamp_value(payload[0])
                bar = Bar(
                    timestamp=ts,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=Decimal(str(payload[1])),
                    high=Decimal(str(payload[2])),
                    low=Decimal(str(payload[3])),
                    close=Decimal(str(payload[4])),
                    volume=Decimal(str(payload[5])),
                )
                return bar, True  # REST historical klines are completed bars

            if isinstance(payload, dict):
                # WebSocket format: {"e": "kline", "k": {...}} or direct dict
                k_data = payload.get("k", payload)
                ts = parse_timestamp_value(k_data["t"])
                sym = k_data.get("s", symbol)
                tf = k_data.get("i", timeframe)
                is_closed = bool(k_data.get("x", True))
                bar = Bar(
                    timestamp=ts,
                    symbol=sym,
                    timeframe=tf,
                    open=Decimal(str(k_data["o"])),
                    high=Decimal(str(k_data["h"])),
                    low=Decimal(str(k_data["l"])),
                    close=Decimal(str(k_data["c"])),
                    volume=Decimal(str(k_data["v"])),
                )
                return bar, is_closed

            raise DomainValidationError(f"Invalid kline payload type: {type(payload)}")
        except Exception as err:
            raise DomainValidationError(f"Failed to normalize kline payload: {err}") from err

    @staticmethod
    def normalize_book_ticker(payload: dict[str, Any]) -> Quote:
        """Normalizes WebSocket bookTicker payload into a Quote."""
        try:
            symbol = payload.get("s")
            bid_price = payload.get("b")
            ask_price = payload.get("a")
            bid_size = payload.get("B", "0")
            ask_size = payload.get("A", "0")
            ts_val = payload.get("E") or payload.get("time") or payload.get("timestamp")

            if not symbol or bid_price is None or ask_price is None:
                raise DomainValidationError("Missing required fields (s, b, a) in book ticker")

            ts = parse_timestamp_value(ts_val) if ts_val is not None else datetime.now(UTC)

            return Quote(
                timestamp=ts,
                symbol=str(symbol),
                bid_price=Decimal(str(bid_price)),
                ask_price=Decimal(str(ask_price)),
                bid_size=Decimal(str(bid_size)),
                ask_size=Decimal(str(ask_size)),
            )
        except Exception as err:
            raise DomainValidationError(f"Failed to normalize book ticker: {err}") from err

    @staticmethod
    def normalize_trade(payload: dict[str, Any]) -> Trade:
        """Normalizes WebSocket trade / aggTrade payload into a Trade."""
        try:
            symbol = payload.get("s")
            price = payload.get("p")
            quantity = payload.get("q")
            trade_id = payload.get("t") or payload.get("a") or payload.get("trade_id")
            ts_val = payload.get("T") or payload.get("E") or payload.get("time")
            # In Binance: "m" is True if buyer is market maker -> sell trade
            is_buyer_maker = payload.get("m")
            side = OrderSide.SELL if is_buyer_maker is True else OrderSide.BUY

            if not symbol or price is None or quantity is None or trade_id is None:
                raise DomainValidationError("Missing required trade fields (s, p, q, t)")

            ts = parse_timestamp_value(ts_val) if ts_val is not None else datetime.now(UTC)

            return Trade(
                timestamp=ts,
                symbol=str(symbol),
                price=Decimal(str(price)),
                quantity=Decimal(str(quantity)),
                side=side,
                trade_id=str(trade_id),
            )
        except Exception as err:
            raise DomainValidationError(f"Failed to normalize trade payload: {err}") from err

    @staticmethod
    def normalize_funding_rate(payload: dict[str, Any]) -> FundingRate:
        """Normalizes perpetual contract funding rate payload."""
        try:
            symbol = payload.get("symbol") or payload.get("s")
            rate = payload.get("fundingRate") or payload.get("r")
            funding_time_val = payload.get("fundingTime") or payload.get("T")
            ts_val = payload.get("time") or payload.get("E")

            if not symbol or rate is None or funding_time_val is None:
                raise DomainValidationError("Missing required funding fields")

            ts = parse_timestamp_value(ts_val) if ts_val is not None else datetime.now(UTC)
            next_funding_time = parse_timestamp_value(funding_time_val)

            return FundingRate(
                timestamp=ts,
                symbol=str(symbol),
                rate=Decimal(str(rate)),
                next_funding_time=next_funding_time,
            )
        except Exception as err:
            raise DomainValidationError(f"Failed to normalize funding rate: {err}") from err

    def handle_ws_kline_message(
        self,
        symbol: str,
        timeframe: str,
        payload: dict[str, Any],
    ) -> Bar:
        """Handles incoming WS kline message, dispatching appropriate domain events."""
        bar, is_closed = self.normalize_kline(symbol, timeframe, payload)
        if self._event_bus:
            if is_closed:
                self._event_bus.publish(BarCompletedEvent(bar=bar))
            else:
                self._event_bus.publish(BarUpdatedEvent(bar=bar))
        return bar

    def handle_ws_ticker_message(self, payload: dict[str, Any]) -> Quote:
        """Handles incoming WS bookTicker message, dispatching QuoteUpdatedEvent."""
        quote = self.normalize_book_ticker(payload)
        if self._event_bus:
            self._event_bus.publish(QuoteUpdatedEvent(quote=quote))
        return quote

    def handle_ws_trade_message(self, payload: dict[str, Any]) -> Trade:
        """Handles incoming WS trade message, dispatching TradeReceivedEvent."""
        trade = self.normalize_trade(payload)
        if self._event_bus:
            self._event_bus.publish(TradeReceivedEvent(trade=trade))
        return trade
