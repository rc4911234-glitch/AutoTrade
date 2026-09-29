"""Historical market data loaders for CSV and JSON crypto bar datasets."""

import csv
import io
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from trad_auto.core.exceptions import DomainValidationError
from trad_auto.core.models.market_data import Bar


def parse_timestamp_value(value: str | int | float) -> datetime:
    """Parses various timestamp formats (ISO-8601 string, Unix epoch seconds/ms) to UTC datetime."""
    if isinstance(value, (int, float)):
        # Check if milliseconds (> 1e11)
        if value > 1e11:
            return datetime.fromtimestamp(value / 1000.0, tz=UTC)
        return datetime.fromtimestamp(float(value), tz=UTC)

    val_str = str(value).strip()
    # Try numeric string
    try:
        num = float(val_str)
        if num > 1e11:
            return datetime.fromtimestamp(num / 1000.0, tz=UTC)
        return datetime.fromtimestamp(num, tz=UTC)
    except ValueError:
        pass

    # Try ISO-8601 string
    try:
        dt = datetime.fromisoformat(val_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError as err:
        raise DomainValidationError(f"Cannot parse timestamp value '{value}': {err}") from err


class HistoricalBarLoader:
    """Loads, validates, and standardizes historical candle datasets from CSV or JSON."""

    @classmethod
    def load_from_csv(
        cls,
        file_path_or_buffer: str | Path | io.StringIO,
        symbol: str,
        timeframe: str,
    ) -> list[Bar]:
        """Loads bars from CSV file or buffer.

        Supports CSV with header containing columns:
        timestamp (or open_time), open, high, low, close, volume.
        """
        if isinstance(file_path_or_buffer, (str, Path)):
            with open(file_path_or_buffer, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return cls._parse_dict_rows(reader, symbol, timeframe)
        else:
            reader = csv.DictReader(file_path_or_buffer)
            return cls._parse_dict_rows(reader, symbol, timeframe)

    @classmethod
    def load_from_json(
        cls,
        file_path_or_str: str | Path,
        symbol: str,
        timeframe: str,
    ) -> list[Bar]:
        """Loads bars from JSON file or JSON string.

        Supports standard Binance API klines format:
        [[open_time, open, high, low, close, volume, ...], ...]
        or list of dicts.
        """
        raw_text: str
        if isinstance(file_path_or_str, Path) or (
            isinstance(file_path_or_str, str)
            and not file_path_or_str.strip().startswith(("[", "{"))
        ):
            with open(file_path_or_str, encoding="utf-8") as f:
                raw_text = f.read()
        else:
            raw_text = str(file_path_or_str)

        data = json.loads(raw_text)
        if not isinstance(data, list):
            raise DomainValidationError("JSON klines data must be a list")

        bars: list[Bar] = []
        for index, item in enumerate(data):
            try:
                if isinstance(item, list):
                    # Standard exchange array: [open_time, open, high, low, close, volume, ...]
                    ts = parse_timestamp_value(item[0])
                    bar = Bar(
                        timestamp=ts,
                        symbol=symbol,
                        timeframe=timeframe,
                        open=Decimal(str(item[1])),
                        high=Decimal(str(item[2])),
                        low=Decimal(str(item[3])),
                        close=Decimal(str(item[4])),
                        volume=Decimal(str(item[5])),
                    )
                elif isinstance(item, dict):
                    ts_val = item.get("timestamp") or item.get("open_time")
                    if ts_val is None:
                        raise DomainValidationError(f"Row {index} missing timestamp/open_time")
                    ts = parse_timestamp_value(ts_val)
                    bar = Bar(
                        timestamp=ts,
                        symbol=symbol,
                        timeframe=timeframe,
                        open=Decimal(str(item["open"])),
                        high=Decimal(str(item["high"])),
                        low=Decimal(str(item["low"])),
                        close=Decimal(str(item["close"])),
                        volume=Decimal(str(item["volume"])),
                    )
                else:
                    raise DomainValidationError(f"Unsupported row item type: {type(item)}")

                bars.append(bar)
            except Exception as err:
                raise DomainValidationError(f"Failed to parse bar at row {index}: {err}") from err

        cls._validate_bar_sequence(bars)
        return bars

    @classmethod
    def _parse_dict_rows(
        cls,
        reader: csv.DictReader[str],
        symbol: str,
        timeframe: str,
    ) -> list[Bar]:
        bars: list[Bar] = []
        for index, row in enumerate(reader):
            # Normalize column keys (lowercase, stripped)
            norm_row = {k.lower().strip(): v for k, v in row.items() if k}
            ts_val = norm_row.get("timestamp") or norm_row.get("open_time") or norm_row.get("time")
            if ts_val is None:
                raise DomainValidationError(f"Row {index} missing timestamp column")

            try:
                ts = parse_timestamp_value(ts_val)
                bar = Bar(
                    timestamp=ts,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=Decimal(norm_row["open"]),
                    high=Decimal(norm_row["high"]),
                    low=Decimal(norm_row["low"]),
                    close=Decimal(norm_row["close"]),
                    volume=Decimal(norm_row["volume"]),
                )
                bars.append(bar)
            except Exception as err:
                raise DomainValidationError(
                    f"Failed to parse CSV bar at row {index}: {err}"
                ) from err

        cls._validate_bar_sequence(bars)
        return bars

    @classmethod
    def _validate_bar_sequence(cls, bars: list[Bar]) -> None:
        """Enforces strictly ascending chronological order across the loaded dataset."""
        for i in range(1, len(bars)):
            prev = bars[i - 1]
            curr = bars[i]
            if curr.timestamp <= prev.timestamp:
                raise DomainValidationError(
                    f"Out-of-order bars in dataset: bar {i} ({curr.timestamp}) "
                    f"is not strictly later than bar {i - 1} ({prev.timestamp})"
                )
