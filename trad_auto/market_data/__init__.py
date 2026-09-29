"""Market data engine package for Trad-Auto."""

from trad_auto.market_data.adapters import (
    BaseMarketDataAdapter,
    CryptoExchangeMarketDataAdapter,
    SimulatedMarketDataAdapter,
)
from trad_auto.market_data.bar_builder import (
    TIMEFRAME_SECONDS,
    BarBuilder,
    calculate_bar_window,
    timeframe_to_seconds,
)
from trad_auto.market_data.loader import HistoricalBarLoader, parse_timestamp_value
from trad_auto.market_data.store import BarStore, QuoteStore
from trad_auto.market_data.streaming import FeedWatchdog, StreamingMarketDataAdapter
from trad_auto.market_data.validator import DataQualityValidator

__all__ = [
    "BarStore",
    "QuoteStore",
    "BarBuilder",
    "DataQualityValidator",
    "HistoricalBarLoader",
    "BaseMarketDataAdapter",
    "SimulatedMarketDataAdapter",
    "CryptoExchangeMarketDataAdapter",
    "StreamingMarketDataAdapter",
    "FeedWatchdog",
    "TIMEFRAME_SECONDS",
    "timeframe_to_seconds",
    "calculate_bar_window",
    "parse_timestamp_value",
]
