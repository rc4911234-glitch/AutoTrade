"""Unit tests for HistoricalBarLoader (CSV and JSON historical crypto data)."""

import io
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.market_data.loader import HistoricalBarLoader, parse_timestamp_value


def test_parse_timestamp_value() -> None:
    """Verifies timestamp parsing across epoch seconds, milliseconds, numeric strings, and ISO."""
    # Epoch seconds
    dt = parse_timestamp_value(1704110400)
    assert dt == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    # Epoch milliseconds (> 1e11)
    dt_ms = parse_timestamp_value(1704110400000)
    assert dt_ms == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    # String numeric
    dt_str = parse_timestamp_value("1704110400")
    assert dt_str == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    # ISO 8601
    dt_iso = parse_timestamp_value("2024-01-01T12:00:00Z")
    assert dt_iso == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    # Invalid timestamp
    with pytest.raises(DomainValidationError, match="Cannot parse timestamp"):
        parse_timestamp_value("not_a_date")


def test_load_from_csv_buffer() -> None:
    """Verifies loading bars from a CSV text buffer."""
    csv_data = """timestamp,open,high,low,close,volume
2026-01-01T12:00:00Z,60000.0,60100.0,59950.0,60050.0,12.5
2026-01-01T12:01:00Z,60050.0,60200.0,60000.0,60180.0,8.2
"""
    buffer = io.StringIO(csv_data)
    bars = HistoricalBarLoader.load_from_csv(buffer, symbol="BTCUSDT", timeframe="1m")

    assert len(bars) == 2
    assert bars[0].symbol == "BTCUSDT"
    assert bars[0].timeframe == "1m"
    assert bars[0].open == Decimal("60000.0")
    assert bars[0].high == Decimal("60100.0")
    assert bars[0].low == Decimal("59950.0")
    assert bars[0].close == Decimal("60050.0")
    assert bars[0].volume == Decimal("12.5")

    assert bars[1].close == Decimal("60180.0")


def test_load_from_csv_invalid_geometry_or_order() -> None:
    """Verifies that corrupted geometry (high < low) or out-of-order rows raise errors."""
    # Corrupt High < Low
    bad_geom = """timestamp,open,high,low,close,volume
2026-01-01T12:00:00Z,60000.0,59000.0,60100.0,60050.0,12.5
"""
    with pytest.raises(DomainValidationError, match="Failed to parse CSV bar"):
        HistoricalBarLoader.load_from_csv(io.StringIO(bad_geom), "BTCUSDT", "1m")

    # Out of order timestamps
    bad_order = """timestamp,open,high,low,close,volume
2026-01-01T12:01:00Z,60000.0,60100.0,59900.0,60050.0,12.5
2026-01-01T12:00:00Z,60050.0,60200.0,60000.0,60180.0,8.2
"""
    with pytest.raises(DomainValidationError, match="Out-of-order bars in dataset"):
        HistoricalBarLoader.load_from_csv(io.StringIO(bad_order), "BTCUSDT", "1m")


def test_load_from_json_binance_klines_array() -> None:
    """Verifies loading from Binance standard klines array format."""
    data = [
        [1704110400000, "60000.0", "60100.0", "59950.0", "60050.0", "10.0", 1704110459999],
        [1704110460000, "60050.0", "60200.0", "60000.0", "60150.0", "15.0", 1704110519999],
    ]
    raw_json = json.dumps(data)
    bars = HistoricalBarLoader.load_from_json(raw_json, symbol="BTCUSDT", timeframe="1m")

    assert len(bars) == 2
    assert bars[0].timestamp == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert bars[0].close == Decimal("60050.0")
    assert bars[1].timestamp == datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)
    assert bars[1].close == Decimal("60150.0")


def test_load_from_json_dicts() -> None:
    """Verifies loading from list-of-dicts JSON format."""
    data = [
        {
            "timestamp": "2026-01-01T12:00:00Z",
            "open": "100",
            "high": "110",
            "low": "95",
            "close": "105",
            "volume": "50",
        },
        {
            "timestamp": "2026-01-01T12:01:00Z",
            "open": "105",
            "high": "115",
            "low": "100",
            "close": "112",
            "volume": "40",
        },
    ]
    bars = HistoricalBarLoader.load_from_json(json.dumps(data), symbol="SOLUSDT", timeframe="1m")
    assert len(bars) == 2
    assert bars[0].symbol == "SOLUSDT"
    assert bars[1].close == Decimal("112")
