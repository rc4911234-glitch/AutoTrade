"""Unit tests for BarStore and QuoteStore rolling buffers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar, Quote
from trad_auto.market_data.store import BarStore, QuoteStore


def create_bar(
    symbol: str = "BTCUSDT",
    timeframe: str = "1m",
    timestamp: datetime | None = None,
    close: Decimal = Decimal("60000"),
    volume: Decimal = Decimal("1.0"),
) -> Bar:
    ts = timestamp or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    return Bar(
        timestamp=ts,
        symbol=symbol,
        timeframe=timeframe,
        open=close - Decimal("10"),
        high=close + Decimal("20"),
        low=close - Decimal("30"),
        close=close,
        volume=volume,
    )


def test_bar_store_capacity_and_order() -> None:
    """Verifies that BarStore caps max_bars and drops oldest items FIFO."""
    store = BarStore(max_bars=3)
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    for i in range(5):
        store.add_bar(
            create_bar(
                timestamp=base_time + timedelta(minutes=i),
                close=Decimal(f"{60000 + i * 10}"),
            )
        )

    assert store.bar_count("BTCUSDT", "1m") == 3
    bars = store.get_bars("BTCUSDT", "1m")
    assert len(bars) == 3
    # The oldest kept bar should be i=2 (60020)
    assert bars[0].close == Decimal("60020")
    assert bars[1].close == Decimal("60030")
    assert bars[2].close == Decimal("60040")

    # Latest bar check
    latest = store.get_latest_bar("BTCUSDT", "1m")
    assert latest is not None
    assert latest.close == Decimal("60040")


def test_bar_store_rejects_out_of_order_timestamps() -> None:
    """Verifies that inserting a bar with timestamp <= previous bar raises DomainValidationError."""
    store = BarStore()
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    store.add_bar(create_bar(timestamp=base_time))

    # Duplicate timestamp
    with pytest.raises(DomainValidationError, match="must be strictly later"):
        store.add_bar(create_bar(timestamp=base_time))

    # Backward timestamp
    with pytest.raises(DomainValidationError, match="must be strictly later"):
        store.add_bar(create_bar(timestamp=base_time - timedelta(minutes=1)))


def test_bar_store_series_extraction() -> None:
    """Verifies get_series correctly extracts Decimals for all price fields."""
    store = BarStore()
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    for i in range(4):
        store.add_bar(
            create_bar(
                timestamp=base_time + timedelta(minutes=i),
                close=Decimal(f"{100 + i}"),
                volume=Decimal(f"{10 + i}"),
            )
        )

    closes = store.get_series("BTCUSDT", "1m", field_name="close")
    assert closes == [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")]

    # Limit count
    recent_closes = store.get_series("BTCUSDT", "1m", field_name="close", count=2)
    assert recent_closes == [Decimal("102"), Decimal("103")]

    # Volume series
    vols = store.get_series("BTCUSDT", "1m", field_name="volume")
    assert vols == [Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13")]

    # Invalid field name
    with pytest.raises(DomainValidationError, match="Invalid field_name"):
        store.get_series("BTCUSDT", "1m", field_name="invalid")  # type: ignore[arg-type]


def test_bar_store_clear_and_empty() -> None:
    """Verifies clearing bars and querying empty store."""
    store = BarStore()
    assert store.get_latest_bar("ETHUSDT", "5m") is None
    assert store.get_bars("ETHUSDT", "5m") == []
    assert store.get_series("ETHUSDT", "5m") == []

    store.add_bar(create_bar(symbol="ETHUSDT", timeframe="5m"))
    assert store.bar_count("ETHUSDT", "5m") == 1

    store.clear("ETHUSDT", "5m")
    assert store.bar_count("ETHUSDT", "5m") == 0


def test_quote_store_lifecycle() -> None:
    """Verifies QuoteStore updates, mid-price, spread calculations, and out-of-order rejection."""
    store = QuoteStore()
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    q1 = Quote(
        timestamp=t0,
        symbol="BTCUSDT",
        bid_price=Decimal("60000.00"),
        ask_price=Decimal("60001.00"),
        bid_size=Decimal("1.5"),
        ask_size=Decimal("2.0"),
    )
    store.update_quote(q1)

    assert store.get_latest_quote("BTCUSDT") == q1
    assert store.get_mid_price("BTCUSDT") == Decimal("60000.50")
    assert store.get_spread("BTCUSDT") == Decimal("1.00")
    expected_pct = Decimal("1.00") / Decimal("60000.00")
    assert store.get_spread_pct("BTCUSDT") == expected_pct

    # Updating with newer timestamp succeeds
    q2 = Quote(
        timestamp=t0 + timedelta(seconds=1),
        symbol="BTCUSDT",
        bid_price=Decimal("60005.00"),
        ask_price=Decimal("60006.00"),
        bid_size=Decimal("1.0"),
        ask_size=Decimal("1.0"),
    )
    store.update_quote(q2)
    assert store.get_latest_quote("BTCUSDT") == q2

    # Updating with older timestamp raises DomainValidationError
    q_old = Quote(
        timestamp=t0 - timedelta(seconds=1),
        symbol="BTCUSDT",
        bid_price=Decimal("59999.00"),
        ask_price=Decimal("60000.00"),
        bid_size=Decimal("1.0"),
        ask_size=Decimal("1.0"),
    )
    with pytest.raises(DomainValidationError, match="cannot precede existing quote timestamp"):
        store.update_quote(q_old)

    # Unknown symbol queries return None
    assert store.get_latest_quote("SOLUSDT") is None
    assert store.get_mid_price("SOLUSDT") is None
    assert store.get_spread("SOLUSDT") is None
    assert store.get_spread_pct("SOLUSDT") is None
