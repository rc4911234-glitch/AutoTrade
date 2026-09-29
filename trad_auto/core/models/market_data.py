"""Market data contracts for normalized bars and quotes."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trad_auto.core.constants import ZERO_DECIMAL
from trad_auto.core.enums import DataAnomalyType, OrderSide
from trad_auto.core.exceptions import DomainValidationError


@dataclass(frozen=True)
class Bar:
    """Normalized OHLCV candle bar."""

    timestamp: datetime  # Timezone-aware UTC
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("Bar timestamp must be timezone-aware (UTC)")
        if not self.symbol or not self.timeframe:
            raise DomainValidationError("Bar symbol and timeframe must be non-empty")
        if self.high < self.low:
            raise DomainValidationError(
                f"Bar High ({self.high}) cannot be lower than Low ({self.low})"
            )
        if self.open > self.high or self.open < self.low:
            err_msg = (
                f"Bar Open ({self.open}) must be within [Low, High] range [{self.low}, {self.high}]"
            )
            raise DomainValidationError(err_msg)
        if self.close > self.high or self.close < self.low:
            err_msg = (
                f"Bar Close ({self.close}) must be within [Low, High] "
                f"range [{self.low}, {self.high}]"
            )
            raise DomainValidationError(err_msg)
        if self.volume < ZERO_DECIMAL:
            raise DomainValidationError("Bar volume cannot be negative")


@dataclass(frozen=True)
class Quote:
    """Normalized top-of-book market quote."""

    timestamp: datetime  # Timezone-aware UTC
    symbol: str
    bid_price: Decimal
    ask_price: Decimal
    bid_size: Decimal
    ask_size: Decimal

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("Quote timestamp must be timezone-aware (UTC)")
        if not self.symbol:
            raise DomainValidationError("Quote symbol must be non-empty")
        if self.bid_price <= ZERO_DECIMAL:
            raise DomainValidationError("Quote bid_price must be strictly positive")
        if self.ask_price <= ZERO_DECIMAL:
            raise DomainValidationError("Quote ask_price must be strictly positive")
        if self.bid_size < ZERO_DECIMAL or self.ask_size < ZERO_DECIMAL:
            raise DomainValidationError("Quote bid_size and ask_size cannot be negative")
        if self.bid_price > self.ask_price:
            err_msg = f"Inverted market anomaly: bid ({self.bid_price}) > ask ({self.ask_price})"
            raise DomainValidationError(err_msg)

    @property
    def mid_price(self) -> Decimal:
        """Returns the volume-neutral mid-market price."""
        return (self.bid_price + self.ask_price) / Decimal("2")


@dataclass(frozen=True)
class Trade:
    """Normalized executed market trade (tick)."""

    timestamp: datetime  # Timezone-aware UTC
    symbol: str
    price: Decimal
    quantity: Decimal
    side: OrderSide
    trade_id: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("Trade timestamp must be timezone-aware (UTC)")
        if not self.symbol:
            raise DomainValidationError("Trade symbol must be non-empty")
        if not self.trade_id:
            raise DomainValidationError("Trade trade_id must be non-empty")
        if self.price <= ZERO_DECIMAL:
            raise DomainValidationError("Trade price must be strictly positive")
        if self.quantity <= ZERO_DECIMAL:
            raise DomainValidationError("Trade quantity must be strictly positive")


@dataclass(frozen=True)
class FundingRate:
    """Crypto perpetual contract funding rate snapshot."""

    timestamp: datetime  # Timezone-aware UTC
    symbol: str
    rate: Decimal  # e.g. Decimal("0.0001") for 0.01%
    next_funding_time: datetime  # Timezone-aware UTC

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("FundingRate timestamp must be timezone-aware (UTC)")
        if self.next_funding_time.tzinfo is None:
            raise DomainValidationError(
                "FundingRate next_funding_time must be timezone-aware (UTC)"
            )
        if not self.symbol:
            raise DomainValidationError("FundingRate symbol must be non-empty")
        if self.next_funding_time < self.timestamp:
            raise DomainValidationError("FundingRate next_funding_time cannot precede timestamp")


@dataclass(frozen=True)
class DataQualityIssue:
    """Record of a market data quality anomaly detected by safety filters."""

    timestamp: datetime  # Timezone-aware UTC
    symbol: str
    anomaly_type: DataAnomalyType
    severity: str  # e.g. "WARNING", "CRITICAL"
    details: str

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise DomainValidationError("DataQualityIssue timestamp must be timezone-aware (UTC)")
        if not self.symbol:
            raise DomainValidationError("DataQualityIssue symbol must be non-empty")
        if not self.details:
            raise DomainValidationError("DataQualityIssue details must be non-empty")
        if self.severity not in ("WARNING", "CRITICAL"):
            raise DomainValidationError("DataQualityIssue severity must be WARNING or CRITICAL")
