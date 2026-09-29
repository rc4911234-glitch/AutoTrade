"""Normalizer converting raw exchange WebSocket frames into domain models."""

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from trad_auto.core.enums import OrderSide
from trad_auto.core.models.market_data import FundingRate, Quote, Trade

logger = logging.getLogger(__name__)


class BinancePayloadNormalizer:
    """Normalizes Binance USD-M Futures WebSocket frames into typed domain contracts."""

    @staticmethod
    def normalize_trade(data: dict[str, Any]) -> Trade | None:
        """Parses a <symbol>@trade stream payload."""
        try:
            symbol = str(data["s"]).upper()
            price = Decimal(str(data["p"]))
            quantity = Decimal(str(data["q"]))
            trade_id = str(data["t"])
            # m = true means buyer was maker -> taker was seller (SELL side trade)
            is_buyer_maker = bool(data.get("m", False))
            side = OrderSide.SELL if is_buyer_maker else OrderSide.BUY

            ts_raw = data.get("T", data.get("E", 0))
            timestamp = datetime.fromtimestamp(ts_raw / 1000.0, tz=UTC)

            return Trade(
                timestamp=timestamp,
                symbol=symbol,
                price=price,
                quantity=quantity,
                side=side,
                trade_id=trade_id,
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            logger.debug("Failed to normalize trade payload: %s (%s)", data, exc)
            return None

    @staticmethod
    def normalize_book_ticker(data: dict[str, Any]) -> Quote | None:
        """Parses a <symbol>@bookTicker stream payload."""
        try:
            symbol = str(data["s"]).upper()
            bid_price = Decimal(str(data["b"]))
            bid_size = Decimal(str(data["B"]))
            ask_price = Decimal(str(data["a"]))
            ask_size = Decimal(str(data["A"]))

            ts_raw = data.get("T", data.get("E", 0))
            if ts_raw:
                timestamp = datetime.fromtimestamp(ts_raw / 1000.0, tz=UTC)
            else:
                timestamp = datetime.now(UTC)

            return Quote(
                timestamp=timestamp,
                symbol=symbol,
                bid_price=bid_price,
                ask_price=ask_price,
                bid_size=bid_size,
                ask_size=ask_size,
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            logger.debug("Failed to normalize bookTicker payload: %s (%s)", data, exc)
            return None

    @staticmethod
    def normalize_mark_price(data: dict[str, Any]) -> FundingRate | None:
        """Parses a <symbol>@markPrice stream payload."""
        try:
            symbol = str(data["s"]).upper()
            rate = Decimal(str(data["r"]))
            event_ts_raw = data.get("E")
            funding_ts_raw = data.get("T")

            if event_ts_raw is not None:
                timestamp = datetime.fromtimestamp(event_ts_raw / 1000.0, tz=UTC)
            elif funding_ts_raw is not None:
                timestamp = datetime.fromtimestamp(funding_ts_raw / 1000.0, tz=UTC)
            else:
                timestamp = datetime.now(UTC)

            if funding_ts_raw is not None:
                next_funding_time = datetime.fromtimestamp(funding_ts_raw / 1000.0, tz=UTC)
            else:
                next_funding_time = timestamp

            if next_funding_time < timestamp:
                next_funding_time = timestamp

            return FundingRate(
                timestamp=timestamp,
                symbol=symbol,
                rate=rate,
                next_funding_time=next_funding_time,
            )
        except (KeyError, ValueError, InvalidOperation) as exc:
            logger.debug("Failed to normalize markPrice payload: %s (%s)", data, exc)
            return None

    def normalize(self, data: dict[str, Any]) -> Trade | Quote | FundingRate | None:
        """Dispatches raw payload to appropriate normalizer based on event type 'e'."""
        event_type = data.get("e")
        if event_type == "trade":
            return self.normalize_trade(data)
        if event_type == "bookTicker":
            return self.normalize_book_ticker(data)
        if event_type == "markPriceUpdate":
            return self.normalize_mark_price(data)

        # Fallback heuristic for payloads without explicit 'e' field
        if "b" in data and "a" in data and "B" in data and "A" in data:
            return self.normalize_book_ticker(data)
        if "p" in data and "q" in data and "t" in data:
            return self.normalize_trade(data)

        return None
