"""Market data adapters package."""

from trad_auto.market_data.adapters.base import BaseMarketDataAdapter
from trad_auto.market_data.adapters.crypto import CryptoExchangeMarketDataAdapter
from trad_auto.market_data.adapters.simulated import SimulatedMarketDataAdapter

__all__ = [
    "BaseMarketDataAdapter",
    "SimulatedMarketDataAdapter",
    "CryptoExchangeMarketDataAdapter",
]
