"""Strategies engine package for Trad-Auto."""

from trad_auto.strategies.base import BaseStrategy
from trad_auto.strategies.concrete import (
    ATRBreakoutStrategy,
    BollingerMeanReversionStrategy,
)
from trad_auto.strategies.manager import StrategyManager

__all__ = [
    "BaseStrategy",
    "StrategyManager",
    "ATRBreakoutStrategy",
    "BollingerMeanReversionStrategy",
]
