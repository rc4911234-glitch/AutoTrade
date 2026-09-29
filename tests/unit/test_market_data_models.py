"""Unit tests for Phase 2 market data models and enums."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trad_auto.core.enums import (
    BarTimeframe,
    DataAnomalyType,
    DataFeedStatus,
    OrderSide,
)
from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import (
    DataQualityIssue,
    FundingRate,
    Trade,
)


def test_market_data_enums() -> None:
    """Verifies that all Phase 2 market data enums have valid string values."""
    assert BarTimeframe.M1.value == "1m"
    assert BarTimeframe.H1.value == "1h"
    assert BarTimeframe.D1.value == "1d"

    assert DataFeedStatus.CONNECTED == "CONNECTED"
    assert DataFeedStatus.STALE == "STALE"

    assert DataAnomalyType.PRICE_SPIKE == "PRICE_SPIKE"
    assert DataAnomalyType.TIMESTAMP_DRIFT == "TIMESTAMP_DRIFT"
    assert DataAnomalyType.STALE_DATA == "STALE_DATA"


def test_trade_valid_construction() -> None:
    """Verifies that valid Trade instances construct without error."""
    now = datetime.now(UTC)
    trade = Trade(
        timestamp=now,
        symbol="BTCUSDT",
        price=Decimal("64250.50"),
        quantity=Decimal("0.125"),
        side=OrderSide.BUY,
        trade_id="trade_12345",
    )
    assert trade.symbol == "BTCUSDT"
    assert trade.price == Decimal("64250.50")
    assert trade.quantity == Decimal("0.125")
    assert trade.side == OrderSide.BUY
    assert trade.trade_id == "trade_12345"


def test_trade_invariants() -> None:
    """Verifies that invalid Trade values trigger DomainValidationError."""
    now = datetime.now(UTC)

    # Naive timestamp
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        Trade(
            timestamp=datetime(2026, 1, 1),
            symbol="BTCUSDT",
            price=Decimal("100"),
            quantity=Decimal("1"),
            side=OrderSide.BUY,
            trade_id="t1",
        )

    # Non-positive price
    with pytest.raises(DomainValidationError, match="price must be strictly positive"):
        Trade(
            timestamp=now,
            symbol="BTCUSDT",
            price=Decimal("0"),
            quantity=Decimal("1"),
            side=OrderSide.BUY,
            trade_id="t1",
        )

    # Non-positive quantity
    with pytest.raises(DomainValidationError, match="quantity must be strictly positive"):
        Trade(
            timestamp=now,
            symbol="BTCUSDT",
            price=Decimal("100"),
            quantity=Decimal("-0.5"),
            side=OrderSide.BUY,
            trade_id="t1",
        )

    # Empty symbol or trade_id
    with pytest.raises(DomainValidationError, match="symbol must be non-empty"):
        Trade(
            timestamp=now,
            symbol="",
            price=Decimal("100"),
            quantity=Decimal("1"),
            side=OrderSide.BUY,
            trade_id="t1",
        )

    with pytest.raises(DomainValidationError, match="trade_id must be non-empty"):
        Trade(
            timestamp=now,
            symbol="BTCUSDT",
            price=Decimal("100"),
            quantity=Decimal("1"),
            side=OrderSide.BUY,
            trade_id="",
        )


def test_funding_rate_valid_and_invariants() -> None:
    """Verifies FundingRate construction, positive/negative rates, and bounds."""
    now = datetime.now(UTC)
    next_funding = now + timedelta(hours=8)

    # Positive rate (longs pay shorts)
    fr_pos = FundingRate(
        timestamp=now,
        symbol="BTCUSDT",
        rate=Decimal("0.0001"),  # 0.01%
        next_funding_time=next_funding,
    )
    assert fr_pos.rate == Decimal("0.0001")

    # Negative rate (shorts pay longs)
    fr_neg = FundingRate(
        timestamp=now,
        symbol="BTCUSDT",
        rate=Decimal("-0.00025"),  # -0.025%
        next_funding_time=next_funding,
    )
    assert fr_neg.rate == Decimal("-0.00025")

    # Next funding before timestamp
    with pytest.raises(DomainValidationError, match="cannot precede timestamp"):
        FundingRate(
            timestamp=now,
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            next_funding_time=now - timedelta(seconds=1),
        )

    # Naive timestamp
    with pytest.raises(DomainValidationError, match="timezone-aware"):
        FundingRate(
            timestamp=datetime(2026, 1, 1),
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            next_funding_time=next_funding,
        )


def test_data_quality_issue_invariants() -> None:
    """Verifies DataQualityIssue construction and severity validation."""
    now = datetime.now(UTC)
    issue = DataQualityIssue(
        timestamp=now,
        symbol="ETHUSDT",
        anomaly_type=DataAnomalyType.PRICE_SPIKE,
        severity="CRITICAL",
        details="15% flash wick detected",
    )
    assert issue.severity == "CRITICAL"
    assert issue.anomaly_type == DataAnomalyType.PRICE_SPIKE

    # Invalid severity
    with pytest.raises(DomainValidationError, match="severity must be WARNING or CRITICAL"):
        DataQualityIssue(
            timestamp=now,
            symbol="ETHUSDT",
            anomaly_type=DataAnomalyType.PRICE_SPIKE,
            severity="INFO",
            details="detail",
        )
